"""One supervisor instance owns exactly one Popen backend lifetime."""
from __future__ import annotations

import copy
import hashlib
import http.client
import os
import select
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from aios_agent.inference import encoded, load_config, parse
from aios_resources import ResourceError
from aios_resources.backend import BackendAttester
from aios_resources.proc import ProcessReader
from aios_service.lifecycle import (ServiceError, atomic_json, is_uuid, lock_directory,
    peer_credentials, prepare_directory, read_json, regular_file)
from . import BackendError, BACKEND_SHA, IDENTITY_KEYS, MAX_STARTS, MODEL_BYTES, MODEL_ID, MODEL_SHA
from . import protocol

READY_SECONDS = 180.0
STOP_SECONDS = 20.0
KILL_SECONDS = 5.0
MAX_LOG_BYTES = 1024 * 1024
MAX_EVENTS = 16
SOURCE_FILES = ("aios-backend.py", "aios_backend/__init__.py", "aios_backend/protocol.py",
    "aios_backend/client.py", "aios_backend/daemon.py", "aios_agent/__init__.py", "aios_agent/inference.py",
    "aios_resources/__init__.py", "aios_resources/proc.py", "aios_resources/backend.py",
    "aios_service/__init__.py", "aios_service/lifecycle.py", "aios_hosted/__init__.py",
    "aios_hosted/boot.py", "aios_hosted/hardware.py")
ERRORS = frozenset({"state-corrupt", "state-io", "invalid-action", "invalid-socket", "state-path-too-long",
    "unsupported-platform", "protocol-error", "rpc-timeout", "peer-mismatch", "stale-instance",
    "already-running", "start-in-progress", "process-not-running", "config-required", "config-invalid",
    "model-hash-mismatch", "backend-hash-mismatch", "generation-exhausted", "launch-limit", "start-failed",
    "start-timeout", "backend-exited", "backend-not-ready", "backend-unavailable", "backend-listener",
    "backend-profile", "backend-io", "log-limit", "event-limit", "stop-failed", "stop-timeout", "stop-forced",
    "recovery-invalid", "recovery-owner", "recovery-owner-required", "recovery-unavailable", "recovery-unsupported",
    "recovery-consumed", "recovery-timeout", "recovery-signal", "recovery-socket", "recovery-io",
    "supervisor-running", "already-recovered"})


class PersistenceFailure(RuntimeError):
    pass


def code_for(exc):
    if isinstance(exc, ServiceError):
        return "start-in-progress" if exc.code == "BUSY" else "state-corrupt"
    if isinstance(exc, ResourceError):
        return "backend-exited" if exc.code in ("backend-exited", "process-exited") else "backend-listener"
    value = getattr(exc, "code", str(exc))
    return value if value in ERRORS else "state-io" if isinstance(exc, OSError) else "backend-io"


def registry_at(directory):
    try:
        value = read_json(directory / "registry.json")
    except FileNotFoundError:
        if any((directory / name).exists() for name in ("latest.json", "runs", "config.json")):
            raise BackendError("state-corrupt")
        return None
    if (type(value) is not dict or set(value) != {"schema_version", *IDENTITY_KEYS}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not all(is_uuid(value[key]) for key in IDENTITY_KEYS[:2])
            or type(value["start_generation"]) is not int or not 1 <= value["start_generation"] <= MAX_STARTS):
        raise BackendError("state-corrupt")
    return value


def source_hashes():
    root = Path(__file__).resolve().parent.parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def launch_command(config, *, fixture_backend=False):
    endpoint = urlsplit(config["endpoint"])
    if endpoint.hostname != "127.0.0.1" or config["endpoint"] != "http://127.0.0.1:" + str(endpoint.port):
        raise BackendError("backend-profile")
    if not all(Path(config[key]).is_absolute() for key in ("model_path", "backend_path")):
        raise BackendError("backend-profile")
    if fixture_backend:
        return [sys.executable, config["backend_path"], str(endpoint.port)]
    if (config["model_id"] != MODEL_ID or config["model_sha256"] != MODEL_SHA
            or config["backend_sha256"] != BACKEND_SHA):
        raise BackendError("backend-profile")
    return ["/bin/sh", config["backend_path"], "--server", "-m", config["model_path"], "--gpu", "disable",
        "--host", "127.0.0.1", "--port", str(endpoint.port), "--no-webui", "-c", "1024", "-b", "64",
        "-ub", "64", "-t", "2", "-np", "1", "--alias", MODEL_ID, "--nologo"]


class LogDrain:
    """Continuously drain both child pipes into two bounded, private files."""
    def __init__(self, child, directory):
        self.selector = selectors.DefaultSelector()
        self.files = {}
        self.counts = {"stdout": 0, "stderr": 0}
        self.exceeded = False
        try:
            for name in self.counts:
                pipe = getattr(child, name)
                os.set_blocking(pipe.fileno(), False)
                descriptor = os.open(directory / (name + ".log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                self.files[name] = os.fdopen(descriptor, "wb")
                self.selector.register(pipe, selectors.EVENT_READ, name)
        except BaseException:
            self.close()
            raise

    def drain(self, timeout=0):
        for key, _mask in self.selector.select(timeout):
            try:
                block = os.read(key.fileobj.fileno(), 65536)
            except BlockingIOError:
                continue
            if not block:
                self.selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            name = key.data
            remaining = MAX_LOG_BYTES - self.counts[name]
            kept = block[:remaining]
            self.files[name].write(kept)
            self.counts[name] += len(kept)
            self.exceeded = self.exceeded or len(block) > remaining

    def finish(self):
        deadline = time.monotonic() + 2
        while self.selector.get_map() and time.monotonic() < deadline:
            self.drain(0.01)
        if self.selector.get_map():
            raise BackendError("backend-io")

    def close(self):
        for key in list(self.selector.get_map().values()):
            self.selector.unregister(key.fileobj)
            key.fileobj.close()
        self.selector.close()
        for stream in self.files.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        self.files.clear()


def health(config):
    port = urlsplit(config["endpoint"]).port
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        body = response.read(4097)
        if len(body) > 4096 or response.status != 200:
            return None
        value = parse(body)
        return value if value.get("status") == "ok" else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def serve(state_dir, config_path, *, fixture_backend=False):
    if not protocol.supported():
        return 3
    os.umask(0o077)
    directory = prepare_directory(Path(state_dir), control_root=True)
    lock_fd = lock_directory(directory)
    listener = socket_identity = child = child_pidfd = drain = attester = run = record = None
    state, terminal_error, exit_code, sequence = "STARTING", None, 1, 0
    child_exit_verified, forced, stop_requested = False, False, False
    last_descriptor = None
    capture = "fixture" if fixture_backend else "live"
    files = set()

    def response(action="status", error=None):
        return protocol.reply(action, state, record=copy.deepcopy(record),
            descriptor=copy.deepcopy(last_descriptor) if state == "RUNNING" and record["backend_ready"] else None,
            error=error, capture_kind=capture)

    def save(name, value):
        try:
            atomic_json(run / name, value)
            files.add(name)
        except (OSError, ValueError) as exc:
            raise PersistenceFailure("state-io") from exc

    def persist():
        try:
            save("service.json", record)
            atomic_json(directory / "latest.json", response(error=terminal_error))
        except (OSError, ValueError) as exc:
            raise PersistenceFailure("state-io") from exc

    def event(name, error=None):
        nonlocal sequence
        if sequence >= MAX_EVENTS:
            raise BackendError("event-limit")
        sequence += 1
        value = {"schema_version": 1, "instance_id": record["instance_id"], "sequence": sequence,
            "monotonic_ns": time.monotonic_ns(), "event": name, "outcome": "ERROR" if error else "OK",
            "error": error, "service_record": copy.deepcopy(record),
            "descriptor": copy.deepcopy(last_descriptor) if state == "RUNNING" else None}
        try:
            path = run / "events.jsonl"
            regular_file(path, absent_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "ab") as stream:
                stream.write(encoded(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            files.add("events.jsonl")
        except (OSError, ValueError) as exc:
            raise PersistenceFailure("state-io") from exc

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    def end_child():
        nonlocal child_exit_verified, forced
        if child is None:
            return
        if child.poll() is None:
            child.terminate()
            deadline = time.monotonic() + STOP_SECONDS
            while child.poll() is None and time.monotonic() < deadline:
                if drain is not None:
                    try:
                        drain.drain(0.05)
                    except OSError:
                        # A failed log sink must never prevent owned-child exit.
                        time.sleep(0.05)
                else:
                    time.sleep(0.05)
            if child.poll() is None:
                forced = True
                child.kill()
                deadline = time.monotonic() + KILL_SECONDS
                while child.poll() is None and time.monotonic() < deadline:
                    if drain is not None:
                        try:
                            drain.drain(0.05)
                        except OSError:
                            time.sleep(0.05)
                    else:
                        time.sleep(0.05)
        child.wait(timeout=1)
        child_exit_verified = child_pidfd is not None and bool(select.select([child_pidfd], [], [], 0)[0])
        if drain is not None:
            drain.finish()

    old_term = signal.signal(signal.SIGTERM, request_stop)
    old_int = signal.signal(signal.SIGINT, request_stop)
    try:
        previous = registry_at(directory)
        if previous and previous["start_generation"] >= MAX_STARTS:
            raise BackendError("generation-exhausted")
        config = load_config(Path(config_path), verify_artifacts=False)
        command = launch_command(config, fixture_backend=fixture_backend)
        identity = {"schema_version": 1, "service_id": previous["service_id"] if previous else str(uuid.uuid4()),
            "instance_id": str(uuid.uuid4()), "start_generation": previous["start_generation"] + 1 if previous else 1}
        with ProcessReader(os.getpid()) as supervisor:
            supervisor_identity = supervisor.identity
        runs = prepare_directory(directory / "runs", create=True)
        run = runs / identity["instance_id"]
        run.mkdir(mode=0o700)
        record = {**identity, "supervisor_identity": supervisor_identity, "child_identity": None,
            "lifecycle_state": "starting", "backend_ready": False,
            "config_sha256": hashlib.sha256(encoded(config) + b"\n").hexdigest(),
            "profile": "python-fixture" if fixture_backend else "llamafile-pinned",
            "source_only": True, "backend_source_instance": None}
        atomic_json(directory / "registry.json", identity)
        atomic_json(directory / "config.json", config)
        save("config.json", config)
        # atomic_json uses the same canonical bytes as encoded(config) + newline.
        record["config_sha256"] = hashlib.sha256((run / "config.json").read_bytes()).hexdigest()
        save("start.json", {**identity, "started_at": datetime.now(timezone.utc).isoformat(), "capture_kind": capture,
            "profile": record["profile"], "config_sha256": record["config_sha256"],
            "source_hashes": source_hashes(), "command": command})
        event("STARTING")
        persist()
        config = load_config(run / "config.json")
        if not fixture_backend and Path(config["model_path"]).stat().st_size != MODEL_BYTES:
            raise BackendError("model-hash-mismatch")
        if stop_requested:
            raise BackendError("backend-not-ready")
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        drain = LogDrain(child, run)
        files.update(("stdout.log", "stderr.log"))
        with ProcessReader(child.pid) as child_reader:
            child_pidfd = os.pidfd_open(child.pid)
            child_reader.sample()
            record["child_identity"] = child_reader.identity
        event("CHILD_STARTED")
        persist()
        attester = BackendAttester(run, child, config, capture_kind=capture, socket_dir=directory)
        path = protocol.socket_path(directory, missing=True)
        if path.exists():
            path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        info = path.lstat()
        socket_identity = (info.st_dev, info.st_ino)
        listener.listen(8)
        listener.setblocking(False)
        deadline = time.monotonic() + READY_SECONDS
        while not stop_requested:
            drain.drain()
            if drain.exceeded:
                raise BackendError("log-limit")
            if child.poll() is not None:
                raise BackendError("backend-exited")
            available = attester.poll()
            if state == "STARTING":
                ready = health(config) if available else None
                if ready is not None:
                    last_descriptor = attester.descriptor
                    record.update(lifecycle_state="active", backend_ready=True,
                                  backend_source_instance=last_descriptor["source_instance"])
                    save("health.json", ready)
                    state = "RUNNING"
                    event("RUNNING")
                    persist()
                elif time.monotonic() >= deadline:
                    raise BackendError("backend-not-ready")
            try:
                connection, _ = listener.accept()
            except BlockingIOError:
                time.sleep(0.02)
                continue
            with connection:
                action = "status"
                try:
                    _pid, uid, _gid = peer_credentials(connection)
                    if uid != os.getuid():
                        raise BackendError("peer-mismatch")
                    request = protocol.receive(connection, 0.5)
                    if (set(request) != {"schema_version", "action", *IDENTITY_KEYS}
                            or type(request["schema_version"]) is not int or request["schema_version"] != 1
                            or request["action"] not in ("status", "stop")):
                        raise BackendError("protocol-error")
                    action = request["action"]
                    if any(type(request[k]) is not type(record[k]) or request[k] != record[k] for k in IDENTITY_KEYS):
                        raise BackendError("stale-instance")
                    # Recheck the selected child/listener after reading a client.
                    attester.poll()
                    if action == "stop":
                        stop_requested = True
                        state = "STOPPING"
                        record["backend_ready"] = False
                    value = response(action)
                except ResourceError:
                    raise
                except (OSError, ValueError) as exc:
                    value = response(action, code_for(exc))
                try:
                    connection.settimeout(0.5)
                    protocol.send(connection, value)
                except (OSError, ValueError):
                    pass
        state = "STOPPING"
        record["backend_ready"] = False
        event("STOPPING")
        persist()
        attester.close()
        attester = None
        end_child()
        if forced:
            raise BackendError("stop-forced")
        if not child_exit_verified or child.returncode not in (0, -signal.SIGTERM):
            raise BackendError("stop-failed")
        state = "STOPPED"
        record["lifecycle_state"] = "exited"
        event("STOPPED")
        persist()
        exit_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        terminal_error = "state-io" if isinstance(exc, PersistenceFailure) else code_for(exc)
        state = "FAILED"
    finally:
        if attester is not None:
            try:
                attester.close()
            except (OSError, ValueError) as exc:
                state, terminal_error, exit_code = "FAILED", code_for(exc), 1
        try:
            end_child()
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state, terminal_error, exit_code = "FAILED", code_for(exc), 1
        if drain is not None:
            try:
                drain.close()
            except OSError:
                state, terminal_error, exit_code = "FAILED", "state-io", 1
        if child is not None:
            for pipe in (child.stdout, child.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        if child_pidfd is not None:
            os.close(child_pidfd)
        if listener is not None:
            listener.close()
            try:
                path = protocol.socket_path(directory)
                info = path.lstat()
                if socket_identity != (info.st_dev, info.st_ino):
                    raise BackendError("invalid-socket")
                path.unlink()
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                state, terminal_error, exit_code = "FAILED", "invalid-socket", 1
        if run is not None and record is not None:
            record.update(lifecycle_state="exited", backend_ready=False)
            if state != "STOPPED":
                state, exit_code = "FAILED", 1
                try:
                    event("FAILED", terminal_error or "backend-io")
                    persist()
                except (OSError, ValueError, PersistenceFailure):
                    pass
            for name in ("backend-source.json", "backend-attestations.jsonl"):
                if (run / name).exists():
                    files.add(name)
            try:
                atomic_json(run / "result.json", {"schema_version": 1, **{k: record[k] for k in IDENTITY_KEYS},
                    "state": state, "exit_code": exit_code, "error": terminal_error,
                    "completed_at": datetime.now(timezone.utc).isoformat(), "capture_kind": capture,
                    "service_record": record, "descriptor": last_descriptor,
                    "child_exit_code": child.returncode if child is not None else None,
                    "child_exit_verified": child_exit_verified, "forced": forced,
                    "log_bytes": dict(drain.counts) if drain is not None else {"stdout": 0, "stderr": 0},
                    "files": {name: hashlib.sha256((run / name).read_bytes()).hexdigest() for name in sorted(files)}})
            except (OSError, ValueError):
                exit_code = 1
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        os.close(lock_fd)
    return exit_code
