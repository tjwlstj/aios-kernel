"""User-owned Linux daemon with bounded observation and explicit lifecycle evidence.

The stable identity belongs to this unbound console service only. Linux PID and
boot identity are source metadata, never a canonical Room/Cell/Node binding.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import signal
import socket
import stat
import struct
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aios_hosted.boot import encoded, run_boot
from aios_hosted.hardware import collect_cpu, collect_memory
from . import ERRORS

MAX_JSON = 65536
MAX_WIRE = 4096
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 4096
TICK_SECONDS = 1.0
RPC_SECONDS = 2.0
MAX_GENERATION = (1 << 63) - 1
STATES = frozenset({"ABSENT", "STARTING", "RUNNING", "STOPPING", "STOPPED", "FAILED", "STALE", "UNSUPPORTED"})
IDENTITY = ("service_id", "instance_id", "generation")
PUBLIC_KEYS = frozenset({"schema_version", "outcome", "error", "service_kind", *IDENTITY,
                         "state", "pid", "observation_sequence", "heartbeat_monotonic_ns",
                         "boot_id", "source_only", "binding_status", "management_actions"})


class ServiceError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def supported() -> bool:
    return platform.system() == "Linux" and hasattr(socket, "AF_UNIX") and hasattr(socket, "SO_PEERCRED")


def is_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def strict_json(raw: bytes, limit: int = MAX_JSON) -> dict:
    if len(raw) > limit:
        raise ServiceError("PROTOCOL_ERROR")

    def pairs(rows: list) -> dict:
        result = {}
        for key, value in rows:
            if key in result:
                raise ServiceError("PROTOCOL_ERROR")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ServiceError("PROTOCOL_ERROR")))
        if not isinstance(value, dict):
            raise ServiceError("PROTOCOL_ERROR")
        # Decoder recursion behavior changes between Python versions. Enforce
        # the wire/record bounds explicitly, without recursive Python calls.
        pending = [(value, 1)]
        nodes = 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
                raise ServiceError("PROTOCOL_ERROR")
            children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
            pending.extend((child, depth + 1) for child in children)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ServiceError):
            raise
        raise ServiceError("PROTOCOL_ERROR") from exc


def prepare_directory(path: Path, *, create: bool = False, control_root: bool = False) -> Path:
    """Reject symlink components and require a private final directory."""
    path = Path(os.path.abspath(path))
    if control_root and len(os.fsencode(str(path / "control.sock"))) >= 104:
        raise ServiceError("INVALID_STATE_DIRECTORY")
    for item in reversed((path, *path.parents)):
        created = False
        try:
            info = item.lstat()
        except FileNotFoundError:
            if not create:
                raise
            try:
                item.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ServiceError("INVALID_STATE_DIRECTORY")
        if created:
            # A setgid home directory can add setgid even to mkdir(mode=0700).
            # Normalize only our own new inode; preexisting paths are read-only.
            descriptor = os.open(item, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_uid != os.getuid() or not stat.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                    raise ServiceError("INVALID_STATE_DIRECTORY")
                os.fchmod(descriptor, 0o700)
            finally:
                os.close(descriptor)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ServiceError("INVALID_STATE_DIRECTORY")
    return path


def regular_file(path: Path, *, absent_ok: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if absent_ok:
            return
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ServiceError("STATE_CORRUPT")


def read_record_bytes(path: Path) -> bytes:
    regular_file(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ServiceError("STATE_CORRUPT")
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ServiceError("STATE_CORRUPT")
    return raw


def read_json(path: Path) -> dict:
    raw = read_record_bytes(path)
    try:
        return strict_json(raw)
    except ServiceError as exc:
        raise ServiceError("STATE_CORRUPT") from exc


def atomic_json(path: Path, value: dict) -> None:
    regular_file(path, absent_ok=True)
    temporary = path.with_name("." + path.name + "." + str(uuid.uuid4()) + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def registry_at(directory: Path) -> dict | None:
    try:
        value = read_json(directory / "registry.json")
    except FileNotFoundError:
        if (directory / "latest.json").exists() or (directory / "runs").exists():
            raise ServiceError("STATE_CORRUPT")
        return None
    if (set(value) != {"schema_version", *IDENTITY} or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or not all(is_uuid(value[key]) for key in IDENTITY[:2])
            or type(value["generation"]) is not int or not 1 <= value["generation"] <= MAX_GENERATION):
        raise ServiceError("STATE_CORRUPT")
    return value


def public(state: str = "ABSENT", *, identity: dict | None = None, error: str | None = None,
           pid: int | None = None, boot_id: str | None = None, count: int = 0,
           heartbeat: int | None = None) -> dict:
    identity = identity or {}
    return {"schema_version": 1, "outcome": "ERROR" if error else "OK", "error": error,
            "service_kind": "CONSOLE_RUNTIME", **{key: identity.get(key) for key in IDENTITY},
            "state": state, "pid": pid, "observation_sequence": count,
            "heartbeat_monotonic_ns": heartbeat, "boot_id": boot_id, "source_only": True,
            "binding_status": "UNBOUND", "management_actions": "UNSUPPORTED"}


def validate_public(value: dict, identity: dict | None = None) -> dict:
    if (not isinstance(value, dict) or set(value) != PUBLIC_KEYS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["service_kind"] != "CONSOLE_RUNTIME" or not isinstance(value["state"], str)
            or value["state"] not in STATES
            or value["source_only"] is not True or value["binding_status"] != "UNBOUND"
            or value["management_actions"] != "UNSUPPORTED"
            or value["outcome"] not in ("OK", "ERROR")
            or (value["outcome"] == "OK") != (value["error"] is None)
            or (value["error"] is not None and (not isinstance(value["error"], str) or value["error"] not in ERRORS))
            or type(value["observation_sequence"]) is not int
            or not 0 <= value["observation_sequence"] <= MAX_GENERATION):
        raise ServiceError("PROTOCOL_ERROR")
    for key in ("pid", "generation", "heartbeat_monotonic_ns"):
        if value[key] is not None and (type(value[key]) is not int or not 1 <= value[key] <= MAX_GENERATION):
            raise ServiceError("PROTOCOL_ERROR")
    for key in ("service_id", "instance_id", "boot_id"):
        if value[key] is not None and not is_uuid(value[key]):
            raise ServiceError("PROTOCOL_ERROR")
    has_identity = value["service_id"] is not None
    if any((value[key] is not None) != has_identity for key in IDENTITY):
        raise ServiceError("PROTOCOL_ERROR")
    if ((value["pid"] is None) != (value["boot_id"] is None)
            or (value["heartbeat_monotonic_ns"] is None) != (value["observation_sequence"] == 0)):
        raise ServiceError("PROTOCOL_ERROR")
    if not has_identity and (value["pid"] is not None or value["observation_sequence"] != 0):
        raise ServiceError("PROTOCOL_ERROR")
    if value["state"] in ("ABSENT", "UNSUPPORTED") and has_identity:
        raise ServiceError("PROTOCOL_ERROR")
    if value["state"] in ("STARTING", "RUNNING", "STOPPING", "STOPPED", "STALE") and not has_identity:
        raise ServiceError("PROTOCOL_ERROR")
    if value["state"] in ("RUNNING", "STOPPING", "STOPPED") and (value["pid"] is None or value["observation_sequence"] == 0):
        raise ServiceError("PROTOCOL_ERROR")
    if value["state"] in ("FAILED", "STALE", "UNSUPPORTED") and value["outcome"] != "ERROR":
        raise ServiceError("PROTOCOL_ERROR")
    if identity is not None and any(value[key] != identity[key] for key in IDENTITY):
        raise ServiceError("STALE_INSTANCE")
    return value


def failure(value: dict, code: str, state: str | None = None) -> dict:
    return {**value, "outcome": "ERROR", "error": code, "state": state or value["state"]}


def lock_directory(directory: Path) -> int:
    import fcntl
    path = directory / "daemon.lock"
    regular_file(path, absent_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError as exc:
        os.close(descriptor)
        raise ServiceError("BUSY") from exc


def socket_path(directory: Path, *, absent_ok: bool = False) -> Path:
    path = directory / "control.sock"
    try:
        info = path.lstat()
    except FileNotFoundError:
        if absent_ok:
            return path
        raise
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ServiceError("STATE_CORRUPT")
    return path


def receive(connection: socket.socket) -> dict:
    raw = b""
    deadline = time.monotonic() + RPC_SECONDS
    while b"\n" not in raw:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        chunk = connection.recv(min(1024, MAX_WIRE + 1 - len(raw)))
        if not chunk or time.monotonic() >= deadline:
            raise ServiceError("PROTOCOL_ERROR")
        raw += chunk
        if len(raw) > MAX_WIRE:
            raise ServiceError("PROTOCOL_ERROR")
    if raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise ServiceError("PROTOCOL_ERROR")
    return strict_json(raw, MAX_WIRE)


def peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))


def source_hashes() -> dict:
    package = Path(__file__).resolve().parent
    base = package.parent
    paths = [base / "aios-service.py", *sorted(package.glob("*.py")), base / "aios-boot.py",
             *sorted((base / "aios_hosted").glob("*.py"))]
    return {path.relative_to(base).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def serve(directory: Path) -> int:
    if not supported():
        return 3
    os.umask(0o077)
    directory = prepare_directory(directory, create=False, control_root=True)
    lock_fd = lock_directory(directory)
    listener = None
    run = None
    identity = None
    value = None
    observations = 0
    event_count = 0
    stop_requested = False
    terminal_reason = None
    exit_code = 1

    def request_stop(_signal: int, _frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    def write_event(name: str, reason: str | None = None) -> None:
        nonlocal event_count
        event_count += 1
        if event_count > 4:
            raise ServiceError("INTERNAL_ERROR")
        row = {"schema_version": 1, **{key: identity[key] for key in IDENTITY},
               "sequence": event_count, "monotonic_ns": time.monotonic_ns(), "event": name,
               "observation_sequence": observations, "reason": reason}
        path = run / "lifecycle.events.jsonl"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(encoded(row))
            stream.flush()
            os.fsync(stream.fileno())

    def publish() -> None:
        atomic_json(run / "latest.json", value)
        atomic_json(directory / "latest.json", value)

    def observe() -> None:
        nonlocal observations, value
        cpu, memory = collect_cpu(Path("/proc")), collect_memory(Path("/proc"))
        if cpu["status"] != "observed" or memory["status"] != "observed":
            raise ServiceError("OBSERVATION_FAILED")
        if observations == MAX_GENERATION:
            raise ServiceError("OBSERVATION_FAILED")
        observations += 1
        heartbeat = time.monotonic_ns()
        row = {"schema_version": 1, **{key: identity[key] for key in IDENTITY},
               "observation_sequence": observations, "heartbeat_monotonic_ns": heartbeat,
               "cpu": cpu, "memory": memory, "source_only": True, "observation_only": True}
        atomic_json(run / "observation.json", row)
        value = {**value, "observation_sequence": observations, "heartbeat_monotonic_ns": heartbeat}
        publish()

    old_term = signal.signal(signal.SIGTERM, request_stop)
    old_int = signal.signal(signal.SIGINT, request_stop)
    try:
        previous = registry_at(directory)
        if previous is not None and previous["generation"] == MAX_GENERATION:
            raise ServiceError("GENERATION_EXHAUSTED")
        # The exclusive lock proves no daemon owning this directory is still live.
        path = socket_path(directory, absent_ok=True)
        if path.exists():
            path.unlink()
        identity = {"schema_version": 1,
                    "service_id": previous["service_id"] if previous else str(uuid.uuid4()),
                    "instance_id": str(uuid.uuid4()), "generation": previous["generation"] + 1 if previous else 1}
        atomic_json(directory / "registry.json", identity)
        runs = directory / "runs"
        if runs.exists():
            prepare_directory(runs)
        else:
            runs.mkdir(mode=0o700)
        run = runs / identity["instance_id"]
        run.mkdir(mode=0o700)
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if not is_uuid(boot_id):
            raise ServiceError("STATE_CORRUPT")
        value = public("STARTING", identity=identity, pid=os.getpid(), boot_id=boot_id)
        atomic_json(run / "start.json", {"schema_version": 1, **{key: identity[key] for key in IDENTITY},
                    "service_kind": "CONSOLE_RUNTIME", "pid": os.getpid(), "boot_id": boot_id,
                    "source_only": True, "binding_status": "UNBOUND", "management_actions": "UNSUPPORTED",
                    "started_at": datetime.now(timezone.utc).isoformat(), "source_hashes": source_hashes()})
        write_event("STARTING")
        publish()
        with (run / "boot.stdout.log").open("w", encoding="utf-8", newline="\n") as output:
            with contextlib.redirect_stdout(output):
                if run_boot(run / "boot") != 0:
                    raise ServiceError("BOOT_FAILED")
        observe()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(8)
        listener.settimeout(0.2)
        value = {**value, "state": "RUNNING"}
        write_event("RUNNING")
        publish()
        next_tick = time.monotonic() + TICK_SECONDS
        while not stop_requested:
            if time.monotonic() >= next_tick:
                observe()
                next_tick = time.monotonic() + TICK_SECONDS
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            with connection:
                try:
                    _peer_pid, peer_uid, _peer_gid = peer_credentials(connection)
                    if peer_uid != os.getuid():
                        raise ServiceError("PEER_MISMATCH")
                    request = receive(connection)
                    if (set(request) != {"schema_version", "action", *IDENTITY}
                            or type(request["schema_version"]) is not int or request["schema_version"] != 1
                            or request["action"] not in ("status", "stop")):
                        raise ServiceError("PROTOCOL_ERROR")
                    if any(request[key] != identity[key] or type(request[key]) is not type(identity[key]) for key in IDENTITY):
                        raise ServiceError("STALE_INSTANCE")
                    if request["action"] == "stop":
                        stop_requested = True
                        value = {**value, "state": "STOPPING"}
                    response = value
                except (ServiceError, OSError) as exc:
                    response = failure(value, exc.code if isinstance(exc, ServiceError) else "PROTOCOL_ERROR")
                try:
                    connection.settimeout(RPC_SECONDS)
                    connection.sendall(encoded(response))
                except OSError:
                    pass
        value = {**value, "state": "STOPPING"}
        write_event("STOPPING")
        publish()
        value = {**value, "state": "STOPPED"}
        write_event("STOPPED")
        publish()
        exit_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        terminal_reason = exc.code if isinstance(exc, ServiceError) else "INTERNAL_ERROR"
        if run is not None and value is not None:
            value = failure(value, terminal_reason, "FAILED")
            try:
                write_event("FAILED", terminal_reason)
                publish()
            except (OSError, ServiceError):
                pass
    finally:
        if listener is not None:
            listener.close()
            try:
                socket_path(directory).unlink()
            except (OSError, ServiceError):
                exit_code = 1
        if run is not None and value is not None:
            names = ("start.json", "lifecycle.events.jsonl", "latest.json", "observation.json")
            try:
                atomic_json(run / "result.json", {"schema_version": 1,
                    **{key: identity[key] for key in IDENTITY}, "state": value["state"], "exit_code": exit_code,
                    "reason": terminal_reason, "observation_sequence": observations,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "files": {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                              for name in names if (run / name).is_file()}})
            except (OSError, ServiceError):
                exit_code = 1
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        os.close(lock_fd)
    return exit_code
