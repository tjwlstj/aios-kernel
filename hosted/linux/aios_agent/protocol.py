"""Private MAIN agent transport. Source identity never grants resource authority."""
from __future__ import annotations

import os
import socket
import stat
import time
from pathlib import Path

from aios_service.lifecycle import peer_credentials, strict_json
from .inference import encoded

ACTIONS = frozenset({"status", "start", "stop", "restart", "ask", "room-status", "room-discover", "room-bind", "room-reconcile",
                     "resources-link", "resources-status", "resources-sample",
                     "cell-status", "cell-activate", "cell-deactivate"})
STATES = frozenset({"ABSENT", "STARTING", "RUNNING", "STOPPING", "STOPPED", "STALE", "FAILED", "UNSUPPORTED"})
WIRE_LIMIT = 256 * 1024
PUBLIC_KEYS = frozenset({"schema_version", "outcome", "error", "action", "state", "service_kind", "source_record",
                         "management_snapshot", "management_outcome", "inference_receipt", "resource_actions", "capture_kind",
                         "resource_result"})


def reply(action: str, state: str, *, source: dict | None = None, management: dict | None = None,
          management_outcome: str | None = None, receipt: dict | None = None, error: str | None = None,
          capture_kind: str = "live", resource_result: dict | None = None) -> dict:
    return {"schema_version": 4, "outcome": "ERROR" if error else "OK", "error": error, "action": action,
            "state": state, "service_kind": "AI_SERVICE", "source_record": source, "management_snapshot": management,
            "management_outcome": management_outcome, "inference_receipt": receipt, "resource_actions": "UNSUPPORTED",
            "capture_kind": "unsupported" if state == "UNSUPPORTED" else capture_kind, "resource_result": resource_result}


def validate_reply(value: dict, action: str) -> dict:
    if (type(value) is not dict or set(value) != PUBLIC_KEYS or type(value["schema_version"]) is not int
            or value["schema_version"] != 4 or value["action"] != action or value["state"] not in STATES
            or value["service_kind"] != "AI_SERVICE" or value["resource_actions"] != "UNSUPPORTED"
            or value["outcome"] not in ("OK", "ERROR") or (value["outcome"] == "OK") != (value["error"] is None)
            or value["capture_kind"] not in ("live", "fixture", "unsupported")
            or value["management_outcome"] not in (None, "accepted", "rejected")):
        raise ValueError("protocol-error")
    for name in ("source_record", "management_snapshot", "inference_receipt", "resource_result"):
        if value[name] is not None and type(value[name]) is not dict:
            raise ValueError("protocol-error")
    if value["error"] is not None and (type(value["error"]) is not str or not 0 < len(value["error"]) <= 64
                                      or any(c not in "abcdefghijklmnopqrstuvwxyz-" for c in value["error"])):
        raise ValueError("protocol-error")
    return value


def receive(connection: socket.socket, seconds: float = 5) -> dict:
    deadline = time.monotonic() + seconds
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("rpc-timeout")
        connection.settimeout(remaining)
        chunk = connection.recv(min(4096, WIRE_LIMIT + 1 - len(data)))
        if not chunk:
            raise ValueError("protocol-error")
        data.extend(chunk)
        if len(data) > WIRE_LIMIT:
            raise ValueError("protocol-error")
    if data.count(b"\n") != 1 or not data.endswith(b"\n"):
        raise ValueError("protocol-error")
    return strict_json(bytes(data), limit=WIRE_LIMIT)


def send(connection: socket.socket, value: dict) -> None:
    data = encoded(value) + b"\n"
    if len(data) > WIRE_LIMIT:
        raise ValueError("protocol-error")
    connection.sendall(data)


def socket_path(directory: Path, *, missing: bool = False) -> Path:
    path = directory / "agent.sock"
    if len(os.fsencode(path)) >= 104:
        raise ValueError("state-path-too-long")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return path
        raise
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("invalid-socket")
    return path


def connect(directory: Path, instance: str, *, seconds: float = 5) -> tuple[socket.socket, dict, int]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(seconds)
        connection.connect(str(socket_path(directory)))
        pid, uid, _gid = peer_credentials(connection)
        if uid != os.getuid() or pid < 1:
            raise ValueError("peer-mismatch")
        send(connection, {"schema_version": 4, "action": "status", "source_instance": instance, "prompt": None,
                          "backend_dir": None})
        value = validate_reply(receive(connection, seconds), "status")
        source = value["source_record"]
        if (type(source) is not dict or source.get("process_id") != pid or source.get("source_instance") != instance
                or source.get("host_boot_id") != Path("/proc/sys/kernel/random/boot_id").read_text().strip()):
            raise ValueError("peer-mismatch")
        return connection, value, pid
    except BaseException:
        connection.close()
        raise
