"""MAIN control through private Unix IPC and authenticated process lifetimes."""
from __future__ import annotations

import hashlib
import os
import select
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from aios_management.binding import Authority, validate_source
from aios_service.lifecycle import (ServiceError, atomic_json, lock_directory,
    peer_credentials, prepare_directory, read_json, regular_file, supported)
from . import protocol
from .daemon import IDENTITY_KEYS, MAX_STARTS, code_for, registry_at

START_SECONDS = 600
RPC_SECONDS = 435
STOP_SECONDS = 8
_CHILDREN = {}


def _busy(directory):
    try:
        descriptor = lock_directory(directory)
    except ServiceError as exc:
        if exc.code == "BUSY":
            return True
        raise
    os.close(descriptor)
    return False


def _latest(directory, identity):
    value = protocol.validate_reply(read_json(directory / "latest.json"), "status")
    source = validate_source(value["source_record"])
    if any(source[key] != identity[key] for key in IDENTITY_KEYS):
        raise ValueError("state-corrupt")
    snapshot = value["management_snapshot"]
    authority_keys = {"schema_version", "authority_namespace", "authority_instance", "initialized", "parent",
        "canonical", "current_source", "discovered_source", "binding", "source_trusted", "binding_confirmed", "retired_instances"}
    if not isinstance(snapshot, dict) or not authority_keys <= snapshot.keys():
        raise ValueError("state-corrupt")
    authority = Authority.from_state({key: snapshot[key] for key in authority_keys})
    if authority.snapshot() != snapshot:
        raise ValueError("state-corrupt")
    if value["state"] in ("STOPPED", "FAILED") and (source["lifecycle_state"] != "exited" or source["model_ready"]):
        raise ValueError("state-corrupt")
    if value["state"] == "STOPPED" and value["error"] is not None:
        raise ValueError("state-corrupt")
    return value


def _status(directory):
    identity = registry_at(directory)
    if identity is None:
        return protocol.reply("status", "ABSENT")
    latest = _latest(directory, identity)
    try:
        connection, value, _pid = protocol.connect(directory, identity["source_instance"], seconds=RPC_SECONDS)
        connection.close()
        validate_source(value["source_record"])
        return value
    except OSError:
        if _busy(directory):
            return {**latest, "outcome": "ERROR", "error": "start-in-progress" if latest["state"] == "STARTING" else "rpc-timeout"}
        if latest["state"] in ("STOPPED", "FAILED"):
            run = prepare_directory(directory / "runs" / identity["source_instance"])
            result = read_json(run / "result.json")
            expected = {"schema_version", *IDENTITY_KEYS, "state", "exit_code", "error", "completed_at",
                        "capture_kind", "source_record", "management_snapshot", "files"}
            if (set(result) != expected or type(result["schema_version"]) is not int or result["schema_version"] != 4
                    or any(result[key] != identity[key] for key in IDENTITY_KEYS)
                    or result["state"] != latest["state"] or result["error"] != latest["error"]
                    or result["source_record"] != latest["source_record"]
                    or result["management_snapshot"] != latest["management_snapshot"]
                    or result["capture_kind"] != latest["capture_kind"]
                    or type(result["files"]) is not dict or not 4 <= len(result["files"]) <= 137
                    or type(result["exit_code"]) is not int
                    or result["exit_code"] != (0 if latest["state"] == "STOPPED" else 1)):
                raise ValueError("state-corrupt")
            for name, digest in result["files"].items():
                if type(name) is not str or type(digest) is not str or len(digest) != 64:
                    raise ValueError("state-corrupt")
                path = Path(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name:
                    raise ValueError("state-corrupt")
                if name not in ("start.json", "config.json", "warmup.json", "source.json", "management.json", "events.jsonl", "backend-binding.json"):
                    if len(path.parts) != 2 or path.parts[0] not in ("requests", "resources") or path.suffix != ".json":
                        raise ValueError("state-corrupt")
                    if str(uuid.UUID(path.stem)) != path.stem:
                        raise ValueError("state-corrupt")
                regular_file(run / path)
                if (run / path).stat().st_size > 1024 * 1024 or hashlib.sha256((run / path).read_bytes()).hexdigest() != digest:
                    raise ValueError("state-corrupt")
            if not {"config.json", "start.json", "source.json", "management.json", "events.jsonl"} <= result["files"].keys():
                raise ValueError("state-corrupt")
            return latest
        authority = Authority.from_state(read_json(directory / "management.json"))
        authority.observe(None)
        return {**latest, "state": "STALE", "outcome": "ERROR", "error": "process-not-running",
                "management_snapshot": authority.snapshot()}


def _rpc(directory, action, prompt=None, backend_dir=None):
    identity = registry_at(directory)
    if identity is None:
        return protocol.reply(action, "ABSENT", error="process-not-running")
    connection, current, pid = protocol.connect(directory, identity["source_instance"], seconds=RPC_SECONDS)
    handle = None
    try:
        if action == "status":
            return current
        if action == "stop":
            if not hasattr(os, "pidfd_open"):
                return {**current, "action": action, "outcome": "ERROR", "error": "pidfd-unavailable"}
            handle = os.pidfd_open(pid)
        connection.close()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(RPC_SECONDS)
        connection.connect(str(protocol.socket_path(directory)))
        new_pid, uid, _gid = peer_credentials(connection)
        if (new_pid, uid) != (pid, os.getuid()):
            raise ValueError("peer-mismatch")
        protocol.send(connection, {"schema_version": 4, "action": action,
                                   "source_instance": identity["source_instance"], "prompt": prompt,
                                   "backend_dir": str(backend_dir) if backend_dir is not None else None})
        value = protocol.validate_reply(protocol.receive(connection, RPC_SECONDS), action)
        source = validate_source(value["source_record"])
        if any(source[key] != identity[key] for key in IDENTITY_KEYS) or source["process_id"] != pid:
            raise ValueError("peer-mismatch")
        if action != "stop" or value["outcome"] != "OK":
            return value
        if value["state"] != "STOPPING":
            raise ValueError("stop-failed")
        if not select.select([handle], [], [], STOP_SECONDS)[0]:
            return {**value, "outcome": "ERROR", "error": "stop-timeout"}
        child = _CHILDREN.pop(identity["source_instance"], None)
        if child is not None:
            child.wait(timeout=1)
            if child.returncode != 0:
                raise ValueError("stop-failed")
        terminal = _status(directory)
        if terminal["state"] != "STOPPED" or terminal["outcome"] != "OK":
            raise ValueError("stop-failed")
        return {**terminal, "action": action}
    finally:
        connection.close()
        if handle is not None:
            os.close(handle)


def _start(directory, config, fixture_backend, backend_dir=None):
    try:
        before = _status(directory)
    except FileNotFoundError:
        if _busy(directory):
            return protocol.reply("start", "STARTING", error="start-in-progress")
        raise
    if before["state"] == "RUNNING" and before["outcome"] == "OK":
        return {**before, "action": "start", "outcome": "ERROR", "error": "already-running"}
    if _busy(directory):
        return {**before, "action": "start", "outcome": "ERROR", "error": "start-in-progress"}
    identity = registry_at(directory)
    if identity is not None and identity["service_start_generation"] >= MAX_STARTS:
        return {**before, "action": "start", "outcome": "ERROR", "error": "generation-exhausted"}
    if before["error"] not in (None, "process-not-running", "backend-failed", "model-hash-mismatch", "backend-hash-mismatch"):
        return {**before, "action": "start"}
    config_path = Path(config).absolute() if config is not None else directory / "config.json"
    if not config_path.is_file():
        return {**before, "action": "start", "outcome": "ERROR", "error": "config-required"}
    launches = prepare_directory(directory / "launches", create=True)
    launch = launches / str(uuid.uuid4())
    launch.mkdir(mode=0o700)
    command = [sys.executable, str(Path(__file__).resolve().parent.parent / "aios-agent.py"),
               "--serve", "--state-dir", str(directory), "--config", str(config_path)]
    if backend_dir is not None:
        command += ['--backend-dir', str(backend_dir)]
    if fixture_backend:
        command.append("--fixture-backend")
    child = None
    try:
        stdout_fd = os.open(launch / "stdout.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stderr_fd = os.open(launch / "stderr.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(stdout_fd, "wb") as stdout, os.fdopen(stderr_fd, "wb") as stderr:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                     start_new_session=True, close_fds=True)
        atomic_json(launch / "launch.json", {"schema_version": 1, "command": command,
                    "pid": child.pid, "source_only": True, "capture_kind": "fixture" if fixture_backend else "live"})
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            if child.poll() is not None:
                try:
                    value = _status(directory)
                except (OSError, ValueError):
                    return protocol.reply("start", "FAILED", error="start-failed")
                return {**value, "action": "start", "outcome": "ERROR",
                        "error": "already-running" if value["state"] == "RUNNING" else value["error"] or "start-failed"}
            try:
                value = _status(directory)
            except (OSError, ValueError):
                time.sleep(0.05)
                continue
            if value["state"] == "RUNNING" and value["outcome"] == "OK":
                if value["source_record"]["process_id"] != child.pid:
                    return {**value, "action": "start", "outcome": "ERROR", "error": "already-running"}
                _CHILDREN[value["source_record"]["source_instance"]] = child
                return {**value, "action": "start"}
            time.sleep(0.05)
        return protocol.reply("start", "FAILED", error="start-timeout")
    finally:
        if child is not None and child not in _CHILDREN.values():
            _finish_failed_start(child)


def _finish_failed_start(child):
    """End only the new session owned by this still-live launch.

    The inference worker inherits this session. The external model backend was
    launched separately and is never selected by this cleanup operation.
    """
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=3)


def control(state_dir: Path, action: str, config=None, prompt=None, *, fixture_backend=False, backend_dir=None) -> dict:
    """Run an explicit action; cached data never restores live binding validity."""
    if not supported():
        return protocol.reply(action, "UNSUPPORTED", error="unsupported-platform")
    if action not in protocol.ACTIONS:
        return protocol.reply("status", "FAILED", error="invalid-action")
    try:
        try:
            directory = prepare_directory(Path(state_dir), create=action in ("start", "restart"), control_root=True)
        except FileNotFoundError:
            return protocol.reply(action, "ABSENT", error=None if action in ("status", "stop") else "process-not-running")
        if action == "status":
            return _status(directory)
        if action == "start":
            return _start(directory, config, fixture_backend, backend_dir)
        before = _status(directory)
        if action == "stop":
            if before["state"] != "RUNNING" or before["outcome"] != "OK":
                return {**before, "action": action}
            return _rpc(directory, action)
        if action == "restart":
            if before["state"] == "RUNNING" and before["outcome"] == "OK":
                stopped = _rpc(directory, "stop")
                if stopped["outcome"] != "OK":
                    return {**stopped, "action": action}
            value = _start(directory, config, fixture_backend, backend_dir)
            return {**value, "action": action}
        if before["state"] != "RUNNING" or before["outcome"] != "OK":
            return {**before, "action": action, "outcome": "ERROR", "error": before["error"] or "process-not-running"}
        return _rpc(directory, action, prompt, backend_dir)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        return protocol.reply(action, "FAILED", error=code_for(exc))
