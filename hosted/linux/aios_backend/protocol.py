"""Bounded private supervisor IPC; backend process IDs stay source-only."""
from __future__ import annotations

import os
import platform
import re
import socket
import stat
import time
from pathlib import Path

from aios_agent.inference import encoded, hash_text
from aios_resources.proc import ProcessReader
from aios_service.lifecycle import is_uuid, peer_credentials, strict_json
from . import BackendError, IDENTITY_KEYS, MAX_STARTS, PROCESS_KEYS, RECORD_KEYS

ACTIONS = frozenset({"status", "start", "stop", "restart", "recover"})
STATES = frozenset({"ABSENT", "STARTING", "RUNNING", "STOPPING", "STOPPED", "STALE", "FAILED", "RECOVERED", "UNSUPPORTED"})
PUBLIC_KEYS = frozenset({"schema_version", "action", "outcome", "error", "state", "service_kind",
    "capture_kind", "service_record", "descriptor", "resource_actions"})
WIRE_LIMIT = 16384
RPC_SECONDS = 3.0


def supported():
    return (platform.system() == "Linux" and hasattr(socket, "SO_PEERCRED")
            and hasattr(os, "pidfd_open") and Path("/proc/self/stat").is_file())


def validate_identity(value):
    if (type(value) is not dict or set(value) != PROCESS_KEYS or not is_uuid(value["host_boot_id"])
            or any(type(value[key]) is not int or not 1 <= value[key] < 1 << 64
                   for key in ("process_id", "process_start_ticks"))
            or type(value["uid"]) is not int or not 0 <= value["uid"] < 1 << 32):
        raise BackendError("state-corrupt")
    return value


def validate_record(value):
    if (type(value) is not dict or set(value) != RECORD_KEYS or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or not all(is_uuid(value[k]) for k in IDENTITY_KEYS[:2])
            or type(value["start_generation"]) is not int or not 1 <= value["start_generation"] <= MAX_STARTS
            or value["lifecycle_state"] not in ("starting", "active", "exited")
            or type(value["backend_ready"]) is not bool or value["source_only"] is not True
            or not hash_text(value["config_sha256"]) or value["profile"] not in ("llamafile-pinned", "python-fixture")
            or value["backend_source_instance"] is not None and not is_uuid(value["backend_source_instance"])):
        raise BackendError("state-corrupt")
    parent = validate_identity(value["supervisor_identity"])
    if value["child_identity"] is not None:
        child = validate_identity(value["child_identity"])
        if (child["host_boot_id"] != parent["host_boot_id"] or child["uid"] != parent["uid"]
                or child["process_id"] == parent["process_id"]):
            raise BackendError("state-corrupt")
    if value["backend_ready"] and (value["lifecycle_state"] != "active"
            or value["child_identity"] is None or value["backend_source_instance"] is None):
        raise BackendError("state-corrupt")
    return value


def reply(action, state, *, record=None, descriptor=None, error=None, capture_kind="live"):
    return {"schema_version": 1, "action": action, "outcome": "ERROR" if error else "OK", "error": error,
        "state": state, "service_kind": "MODEL_BACKEND", "capture_kind": "unsupported" if state == "UNSUPPORTED" else capture_kind,
        "service_record": record, "descriptor": descriptor, "resource_actions": "UNSUPPORTED"}


def validate_reply(value, action):
    if (type(value) is not dict or set(value) != PUBLIC_KEYS or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["action"] != action or action not in ACTIONS
            or value["state"] not in STATES or value["service_kind"] != "MODEL_BACKEND"
            or value["capture_kind"] not in ("live", "fixture", "unsupported")
            or value["resource_actions"] != "UNSUPPORTED" or value["outcome"] not in ("OK", "ERROR")
            or (value["error"] is None) != (value["outcome"] == "OK")
            or value["error"] is not None and (type(value["error"]) is not str
                or len(value["error"]) > 64 or re.fullmatch(r"[a-z]+(?:-[a-z]+)*", value["error"]) is None)):
        raise BackendError("protocol-error")
    record, descriptor = value["service_record"], value["descriptor"]
    if record is not None:
        validate_record(record)
        if value["capture_kind"] != ("fixture" if record["profile"] == "python-fixture" else "live"):
            raise BackendError("protocol-error")
    if descriptor is not None:
        if type(descriptor) is not dict or record is None or value["state"] != "RUNNING" or not record["backend_ready"]:
            raise BackendError("protocol-error")
        child, parent = record["child_identity"], record["supervisor_identity"]
        if (descriptor.get("source_instance") != record["backend_source_instance"]
                or any(descriptor.get(k) != child[k] for k in ("host_boot_id", "process_id", "process_start_ticks"))
                or descriptor.get("launcher_process_id") != parent["process_id"]
                or descriptor.get("launcher_start_ticks") != parent["process_start_ticks"]):
            raise BackendError("protocol-error")
    if value["state"] in ("ABSENT", "UNSUPPORTED") and (record is not None or descriptor is not None):
        raise BackendError("protocol-error")
    if value["state"] == "RUNNING" and (record is None or not record["backend_ready"] or descriptor is None):
        raise BackendError("protocol-error")
    if value["state"] in ("STOPPED", "FAILED", "RECOVERED") and record is not None and record["lifecycle_state"] != "exited":
        raise BackendError("protocol-error")
    if value["state"] == "RECOVERED" and (record is None or record["backend_ready"] or descriptor is not None):
        raise BackendError("protocol-error")
    return value


def socket_path(directory, *, missing=False):
    path = Path(directory) / "control.sock"
    if len(os.fsencode(path)) >= 104:
        raise BackendError("state-path-too-long")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return path
        raise
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise BackendError("invalid-socket")
    return path


def send(connection, value):
    data = encoded(value) + b"\n"
    if len(data) > WIRE_LIMIT:
        raise BackendError("protocol-error")
    connection.sendall(data)


def receive(connection, seconds=RPC_SECONDS):
    deadline, data = time.monotonic() + seconds, bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BackendError("rpc-timeout")
        connection.settimeout(remaining)
        block = connection.recv(min(4096, WIRE_LIMIT + 1 - len(data)))
        if not block:
            raise BackendError("protocol-error")
        data.extend(block)
        if len(data) > WIRE_LIMIT:
            raise BackendError("protocol-error")
    if data.count(b"\n") != 1 or not data.endswith(b"\n"):
        raise BackendError("protocol-error")
    return strict_json(bytes(data), limit=WIRE_LIMIT)


def connect(directory, identity, action="status", *, seconds=RPC_SECONDS):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(seconds)
        connection.connect(str(socket_path(directory)))
        pid, uid, _gid = peer_credentials(connection)
        if uid != os.getuid():
            raise BackendError("peer-mismatch")
        with ProcessReader(pid) as reader:
            send(connection, {"schema_version": 1, "action": action, **{k: identity[k] for k in IDENTITY_KEYS}})
            value = validate_reply(receive(connection, seconds), action)
            record = value["service_record"]
            if (record is None or any(record[k] != identity[k] for k in IDENTITY_KEYS)
                    or record["supervisor_identity"] != reader.identity):
                raise BackendError("peer-mismatch")
            reader.sample()
        return connection, value, pid
    except BaseException:
        connection.close()
        raise
