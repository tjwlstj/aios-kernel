"""Authenticated local service control; never signal a PID read from disk."""
from __future__ import annotations

import os
import hashlib
import select
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from aios_hosted.boot import encoded
from .lifecycle import (IDENTITY, RPC_SECONDS, ServiceError, atomic_json, failure, lock_directory,
                        peer_credentials, prepare_directory, public, read_json, read_record_bytes, receive,
                        registry_at, socket_path, supported, validate_public)

START_SECONDS = 10.0
STOP_SECONDS = 8.0
_CHILDREN: dict[str, subprocess.Popen] = {}


def _latest(directory: Path, identity: dict) -> dict:
    try:
        return validate_public(read_json(directory / "latest.json"), identity)
    except FileNotFoundError:
        return public("STARTING", identity=identity)
    except ServiceError as exc:
        raise ServiceError("STATE_CORRUPT") from exc


def _alive_lock(directory: Path) -> bool:
    try:
        descriptor = lock_directory(directory)
    except ServiceError as exc:
        if exc.code == "BUSY":
            return True
        raise
    os.close(descriptor)
    return False


def _connect(directory: Path, identity: dict) -> tuple[socket.socket, dict, int]:
    """Keep the authenticated socket open while obtaining its process handle."""
    path = socket_path(directory)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(RPC_SECONDS)
        connection.connect(str(path))
        peer_pid, peer_uid, _peer_gid = peer_credentials(connection)
        if peer_uid != os.getuid() or peer_pid < 1:
            raise ServiceError("PEER_MISMATCH")
        request = {"schema_version": 1, "action": "status", **{key: identity[key] for key in IDENTITY}}
        connection.sendall(encoded(request))
        value = validate_public(receive(connection), identity)
        if value["error"] is not None:
            raise ServiceError(value["error"])
        if value["pid"] != peer_pid or value["boot_id"] is None:
            raise ServiceError("PEER_MISMATCH")
        # Compare a source boot identity as a lifetime boundary, not as AIOS identity.
        if Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip() != value["boot_id"]:
            raise ServiceError("STALE_INSTANCE")
        return connection, value, peer_pid
    except BaseException:
        connection.close()
        raise


def _status(directory: Path) -> dict:
    identity = registry_at(directory)
    if identity is None:
        return public()
    latest = _latest(directory, identity)
    try:
        connection, value, _pid = _connect(directory, identity)
        connection.close()
        return value
    except ServiceError as exc:
        return failure(latest, exc.code)
    except OSError:
        if _alive_lock(directory):
            return failure(latest, "START_IN_PROGRESS" if latest["state"] == "STARTING" else "SERVICE_UNREACHABLE")
        # A stopped snapshot is useful evidence only after its daemon lock is free.
        if latest["state"] in ("STOPPED", "FAILED"):
            runs = prepare_directory(directory / "runs")
            run = prepare_directory(runs / identity["instance_id"])
            result = read_json(run / "result.json")
            required = {"schema_version", *IDENTITY, "state", "exit_code", "reason",
                        "observation_sequence", "completed_at", "files"}
            if (set(result) != required or type(result["schema_version"]) is not int
                    or result["schema_version"] != 1
                    or any(result[key] != identity[key] or type(result[key]) is not type(identity[key]) for key in IDENTITY)
                    or result["state"] != latest["state"]
                    or type(result["observation_sequence"]) is not int
                    or result["observation_sequence"] != latest["observation_sequence"]
                    or type(result["exit_code"]) is not int
                    or not isinstance(result["files"], dict)
                    or not isinstance(result["completed_at"], str)):
                raise ServiceError("STATE_CORRUPT")
            if latest["state"] == "STOPPED" and (result["exit_code"] != 0 or result["reason"] is not None):
                raise ServiceError("STATE_CORRUPT")
            if latest["state"] == "FAILED" and (result["exit_code"] != 1 or result["reason"] != latest["error"]):
                raise ServiceError("STATE_CORRUPT")
            try:
                if datetime.fromisoformat(result["completed_at"]).utcoffset() is None:
                    raise ServiceError("STATE_CORRUPT")
            except ValueError as exc:
                raise ServiceError("STATE_CORRUPT") from exc
            names = {"start.json", "lifecycle.events.jsonl", "latest.json"}
            if latest["observation_sequence"]:
                names.add("observation.json")
            if set(result["files"]) != names:
                raise ServiceError("STATE_CORRUPT")
            for name, digest in result["files"].items():
                if (not isinstance(digest, str) or len(digest) != 64
                        or any(c not in "0123456789abcdef" for c in digest)
                        or hashlib.sha256(read_record_bytes(run / name)).hexdigest() != digest):
                    raise ServiceError("STATE_CORRUPT")
            if read_json(run / "latest.json") != latest:
                raise ServiceError("STATE_CORRUPT")
            return latest
        return failure(latest, "PROCESS_NOT_RUNNING", "STALE")


def _recoverable_failure(directory: Path, value: dict) -> bool:
    """Only a recorded, completed operational failure permits explicit restart."""
    if (value["state"] != "FAILED" or value["service_id"] is None
            or value["error"] not in ("BOOT_FAILED", "OBSERVATION_FAILED", "INTERNAL_ERROR")):
        return False
    identity = registry_at(directory)
    return identity is not None and value == _latest(directory, identity)


def _start(directory: Path) -> dict:
    current = _status(directory)
    if current["state"] == "RUNNING" and current["outcome"] == "OK":
        return failure(current, "ALREADY_RUNNING")
    if _alive_lock(directory):
        return failure(current, "START_IN_PROGRESS")
    if (current["error"] not in (None, "PROCESS_NOT_RUNNING")
            and not _recoverable_failure(directory, current)):
        return current
    previous = registry_at(directory)
    launch_id = str(uuid.uuid4())
    launches = directory / "launches"
    if launches.exists():
        prepare_directory(launches)
    else:
        launches.mkdir(mode=0o700)
    launch = launches / launch_id
    launch.mkdir(mode=0o700)
    command = [sys.executable, str(Path(__file__).resolve().parent.parent / "aios-service.py"),
               "--serve", "--state-dir", str(directory)]
    child = None
    try:
        stdout_fd = os.open(launch / "stdout.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stderr_fd = os.open(launch / "stderr.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(stdout_fd, "wb") as stdout, os.fdopen(stderr_fd, "wb") as stderr:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                     start_new_session=True, close_fds=True)
        atomic_json(launch / "launch.json", {"schema_version": 1, "launch_id": launch_id,
                    "command": command, "pid": child.pid, "source_only": True})
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            if child.poll() is not None:
                latest = _status(directory)
                return failure(latest, "ALREADY_RUNNING" if latest["state"] == "RUNNING" else "START_FAILED")
            try:
                latest = _status(directory)
            except (ServiceError, OSError):
                time.sleep(0.05)
                continue
            if latest["outcome"] == "OK" and latest["state"] == "RUNNING":
                # An overlapping start may not claim another client's daemon.
                if latest["pid"] != child.pid:
                    return failure(latest, "ALREADY_RUNNING")
                if (previous is not None and (latest["service_id"] != previous["service_id"]
                        or latest["generation"] <= previous["generation"]
                        or latest["instance_id"] == previous["instance_id"])):
                    return failure(latest, "STALE_INSTANCE")
                _CHILDREN[latest["instance_id"]] = child
                return latest
            time.sleep(0.05)
        return failure(_status(directory), "START_TIMEOUT")
    finally:
        # Only the Popen created by this invocation may be terminated on failure.
        if child is not None and child not in _CHILDREN.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)


def _stop(directory: Path) -> dict:
    current = _status(directory)
    if current["outcome"] == "OK" and current["state"] in ("ABSENT", "STOPPED"):
        return current
    if current["outcome"] != "OK" or current["state"] != "RUNNING":
        return current if current["outcome"] != "OK" else failure(current, "STOP_FAILED")
    identity = {key: current[key] for key in IDENTITY}
    connection, current, peer_pid = _connect(directory, identity)
    handle = None
    try:
        if not hasattr(os, "pidfd_open"):
            return failure(current, "PIDFD_UNAVAILABLE")
        try:
            handle = os.pidfd_open(peer_pid)
        except OSError:
            return failure(current, "PIDFD_UNAVAILABLE")
        # Opening pidfd occurs before stop. The authenticated daemon is still
        # alive (otherwise the following same-instance reply cannot succeed).
        connection.close()
        path = socket_path(directory)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(RPC_SECONDS)
        connection.connect(str(path))
        new_pid, new_uid, _gid = peer_credentials(connection)
        if (new_pid, new_uid) != (peer_pid, os.getuid()):
            return failure(current, "PEER_MISMATCH")
        request = {"schema_version": 1, "action": "stop", **identity}
        connection.sendall(encoded(request))
        ack = validate_public(receive(connection), identity)
        if (ack["outcome"] != "OK" or ack["state"] != "STOPPING"
                or ack["pid"] != peer_pid or ack["boot_id"] != current["boot_id"]):
            return failure(current, ack["error"] or "STOP_FAILED")
        if not select.select([handle], [], [], STOP_SECONDS)[0]:
            return failure(ack, "STOP_TIMEOUT")
        child = _CHILDREN.pop(identity["instance_id"], None)
        if child is not None:
            child.wait(timeout=1)
            if child.returncode != 0:
                return failure(ack, "STOP_FAILED")
        result = _status(directory)
        if (result["outcome"] != "OK" or result["state"] != "STOPPED"
                or any(result[key] != identity[key] for key in IDENTITY)):
            return failure(result, "STOP_FAILED")
        return result
    finally:
        connection.close()
        if handle is not None:
            os.close(handle)


def control(state_dir: Path, action: str) -> dict:
    """Return a bounded result for every ordinary input or substrate failure."""
    if not supported():
        return public("UNSUPPORTED", error="UNSUPPORTED_PLATFORM")
    if action not in ("start", "status", "stop", "restart"):
        return public("FAILED", error="INVALID_ACTION")
    try:
        try:
            directory = prepare_directory(Path(state_dir), create=action in ("start", "restart"), control_root=True)
        except FileNotFoundError:
            return public()
        if action == "status":
            return _status(directory)
        if action == "start":
            return _start(directory)
        if action == "stop":
            return _stop(directory)
        before = _status(directory)
        if before["state"] == "RUNNING" and before["outcome"] == "OK":
            stopped = _stop(directory)
            if stopped["outcome"] != "OK":
                return stopped
        elif (before["error"] not in (None, "PROCESS_NOT_RUNNING")
                and not _recoverable_failure(directory, before)):
            return before
        return _start(directory)
    except ServiceError as exc:
        return public("FAILED", error=exc.code)
    except (OSError, RuntimeError, ValueError):
        return public("FAILED", error="STATE_IO")
