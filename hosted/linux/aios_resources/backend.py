"""Authenticate an owned backend process and its exact local TCP listener."""
from __future__ import annotations

import copy
import errno
import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from aios_agent.inference import validate_config
from aios_service.lifecycle import (ServiceError, atomic_json, peer_credentials,
    prepare_directory, regular_file, strict_json)
from . import ResourceError
from .proc import ProcessReader, decimal, parse_stat, read_bounded, unsigned

MAX_ATTESTATIONS = 128
MAX_FDS = 1024
TCP_LIMIT = 256 * 1024
WIRE_LIMIT = 16384
POLL_SECONDS = 0.5
CLIENT_SECONDS = 3.0
DESCRIPTOR_KEYS = frozenset({"schema_version", "source_namespace", "source_instance", "source_generation",
    "lifecycle_state", "source_only", "producer_owned", "host_boot_id", "process_id", "process_start_ticks",
    "launcher_process_id", "launcher_start_ticks", "model_id", "model_sha256", "backend_sha256", "endpoint", "listener_inode"})
PROOF_KEYS = frozenset({"schema_version", "nonce", "descriptor", "listener_proof", "peer_pid", "peer_uid",
                        "observed_monotonic_ns", "capture_kind"})


def _uuid(value):
    try:
        parsed = uuid.UUID(value)
        if type(value) is not str or str(parsed) != value or parsed.int == 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ResourceError("backend-schema") from exc


def _endpoint(config):
    try:
        validate_config(config)
        address = urlsplit(config["endpoint"])
        if address.hostname != "127.0.0.1" or address.scheme != "http":
            raise ValueError()
        return address.port
    except (ValueError, TypeError, KeyError) as exc:
        raise ResourceError("backend-config") from exc


def _socket_path(directory, *, missing=False):
    path = directory / "backend.sock"
    if len(os.fsencode(path)) >= 104:
        raise ResourceError("backend-path")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return path
        raise ResourceError("backend-unavailable")
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ResourceError("backend-socket")
    return path


def _encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def _send(connection, value):
    raw = _encoded(value)
    if len(raw) > WIRE_LIMIT:
        raise ResourceError("backend-protocol")
    connection.sendall(raw)


def _receive(connection, seconds):
    deadline = time.monotonic() + seconds
    raw = bytearray()
    while b"\n" not in raw:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResourceError("backend-timeout")
        connection.settimeout(remaining)
        chunk = connection.recv(min(4096, WIRE_LIMIT + 1 - len(raw)))
        if not chunk:
            raise ResourceError("backend-protocol")
        raw.extend(chunk)
        if len(raw) > WIRE_LIMIT:
            raise ResourceError("backend-protocol")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ResourceError("backend-protocol")
    try:
        return strict_json(bytes(raw), limit=WIRE_LIMIT)
    except ServiceError as exc:
        raise ResourceError("backend-protocol") from exc


def tcp_listener(raw, port, uid):
    """Return the unique IPv4 loopback LISTEN row for this exact endpoint."""
    unsigned(port, positive=True, code="backend-config")
    unsigned(uid, code="backend-schema")
    if port > 65535 or type(raw) is not bytes or len(raw) > TCP_LIMIT:
        raise ResourceError("listener-format")
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise ResourceError("listener-format") from exc
    lines = text.splitlines()
    if not lines or len(lines) > 4096 or "local_address" not in lines[0]:
        raise ResourceError("listener-format")
    address = f"{int.from_bytes(socket.inet_aton('127.0.0.1'), sys.byteorder):08X}:{port:04X}"
    matches = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 10:
            raise ResourceError("listener-format")
        if fields[1] != address or fields[3] != "0A":
            continue
        if fields[2] != "00000000:0000" or decimal(fields[7], code="listener-format") != uid:
            raise ResourceError("listener-owner")
        inode = decimal(fields[9], positive=True, code="listener-format")
        matches.append({"listener_inode": inode, "raw_tcp_line": line})
    if not matches:
        raise ResourceError("listener-missing")
    if len(matches) != 1:
        raise ResourceError("listener-duplicate")
    return matches[0]


def listener_proof(reader, config):
    """Read only the selected process's descriptors and the fixed TCP table."""
    port = _endpoint(config)
    current = reader.sample()
    row = tcp_listener(read_bounded("/proc/net/tcp", TCP_LIMIT), port, current["uid"])
    target = "socket:[" + str(row["listener_inode"]) + "]"
    descriptor = None
    try:
        descriptor = os.open("fd", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=reader._dirfd)
        names = os.listdir(descriptor)
        if len(names) > MAX_FDS:
            raise ResourceError("listener-fd-limit")
        matching = []
        for name in names:
            number = decimal(name, code="listener-format")
            try:
                value = os.readlink(name, dir_fd=descriptor)
            except FileNotFoundError:
                continue
            if value == target:
                matching.append(number)
        if not matching:
            raise ResourceError("listener-owner")
        number = min(matching)
        # The process lifetime and the selected descriptor must still match
        # after the system table read. The copied view is explicitly sequential.
        reader.sample()
        if os.readlink(str(number), dir_fd=descriptor) != target:
            raise ResourceError("listener-changed")
        after = tcp_listener(read_bounded("/proc/net/tcp", TCP_LIMIT), port, current["uid"])
        if after["listener_inode"] != row["listener_inode"]:
            raise ResourceError("listener-changed")
        return {"listener_inode": row["listener_inode"], "raw_tcp_line": after["raw_tcp_line"],
                "fd_target": target, "fd_number": number}
    except OSError as exc:
        raise ResourceError("listener-io") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validate_descriptor(value, config):
    if type(value) is not dict or set(value) != DESCRIPTOR_KEYS:
        raise ResourceError("backend-schema")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["source_namespace"] != "linux-model-backend"
            or type(value["source_generation"]) is not int or value["source_generation"] != 1
            or value["lifecycle_state"] != "active" or value["source_only"] is not True
            or value["producer_owned"] is not True):
        raise ResourceError("backend-schema")
    for name in ("source_instance", "host_boot_id"):
        _uuid(value[name])
    for name in ("process_id", "process_start_ticks", "launcher_process_id", "launcher_start_ticks", "listener_inode"):
        unsigned(value[name], positive=True, code="backend-schema")
    for name in ("model_id", "model_sha256", "backend_sha256", "endpoint"):
        if value[name] != config[name]:
            raise ResourceError("backend-config")
    return value


class BackendAttester:
    def __init__(self, output_dir, ownedPopen, config, capture_kind="live", *, socket_dir=None):
        if not isinstance(ownedPopen, subprocess.Popen) or ownedPopen.poll() is not None:
            raise ResourceError("backend-exited")
        if capture_kind not in ("live", "fixture"):
            raise ResourceError("backend-capture")
        _endpoint(config)
        try:
            self.directory = prepare_directory(Path(output_dir), control_root=socket_dir is None)
            self.socket_directory = (self.directory if socket_dir is None
                                     else prepare_directory(Path(socket_dir), control_root=True))
        except (OSError, ServiceError) as exc:
            raise ResourceError("backend-path") from exc
        self.process = ownedPopen
        self.config = copy.deepcopy(config)
        self.capture_kind = capture_kind
        self._descriptor = None
        self._count = 0
        self._listener = None
        self._child = None
        self._launcher = None
        self._socket_identity = None
        try:
            path = _socket_path(self.socket_directory, missing=True)
            if path.exists() or (self.directory / "backend-source.json").exists() or (self.directory / "backend-attestations.jsonl").exists():
                raise ResourceError("attester-already-exists")
            self._child = ProcessReader(ownedPopen.pid)
            self._launcher = ProcessReader(os.getpid())
            self._instance = str(uuid.uuid4())
            child_stat = parse_stat(self._child.sample()["raw_stat"], ownedPopen.pid)
            if child_stat["parent_pid"] != os.getpid():
                raise ResourceError("backend-owner")
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(str(path))
            os.chmod(path, 0o600)
            info = path.lstat()
            self._socket_identity = (info.st_dev, info.st_ino)
            self._listener.listen(8)
            self._listener.setblocking(False)
            fd = os.open(self.directory / "backend-attestations.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except BaseException:
            try:
                self.close()
            except (OSError, ResourceError):
                pass
            raise

    @property
    def descriptor(self):
        return copy.deepcopy(self._descriptor)

    def _refresh(self):
        try:
            return self._refresh_owned()
        except ServiceError as exc:
            raise ResourceError("backend-state") from exc
        except OSError as exc:
            raise ResourceError("backend-io") from exc

    def _refresh_owned(self):
        if self.process.poll() is not None:
            raise ResourceError("backend-exited")
        child = self._child.sample()
        launcher = self._launcher.sample()
        if parse_stat(child["raw_stat"], self.process.pid)["parent_pid"] != os.getpid():
            raise ResourceError("backend-owner")
        proof = listener_proof(self._child, self.config)
        candidate = {"schema_version": 1, "source_namespace": "linux-model-backend",
            "source_instance": self._instance, "source_generation": 1, "lifecycle_state": "active",
            "source_only": True, "producer_owned": True, "host_boot_id": child["host_boot_id"],
            "process_id": child["process_id"], "process_start_ticks": child["process_start_ticks"],
            "launcher_process_id": launcher["process_id"], "launcher_start_ticks": launcher["process_start_ticks"],
            **{key: self.config[key] for key in ("model_id", "model_sha256", "backend_sha256", "endpoint")},
            "listener_inode": proof["listener_inode"]}
        if self._descriptor is not None and candidate != self._descriptor:
            raise ResourceError("backend-changed")
        if self._descriptor is None:
            atomic_json(self.directory / "backend-source.json", candidate)
            self._descriptor = candidate
        return {key: proof[key] for key in ("raw_tcp_line", "fd_target", "fd_number")}

    def poll(self):
        if self._listener is None:
            raise ResourceError("attester-closed")
        proof = None
        try:
            proof = self._refresh()
        except ResourceError as exc:
            if exc.code != "listener-missing" or self._descriptor is not None:
                raise
        try:
            connection, _ = self._listener.accept()
        except BlockingIOError:
            return proof is not None
        with connection:
            connection.settimeout(POLL_SECONDS)
            try:
                _pid, uid, _gid = peer_credentials(connection)
                if uid != os.getuid():
                    raise ResourceError("backend-peer")
                request = _receive(connection, POLL_SECONDS)
                if (set(request) != {"schema_version", "action", "nonce"}
                        or type(request["schema_version"]) is not int or request["schema_version"] != 1
                        or request["action"] != "status"):
                    raise ResourceError("backend-protocol")
                _uuid(request["nonce"])
                if self._count >= MAX_ATTESTATIONS:
                    raise ResourceError("attestation-limit")
                if proof is None:
                    raise ResourceError("backend-not-ready")
                proof = self._refresh()
                value = {"schema_version": 1, "nonce": request["nonce"], "descriptor": self.descriptor,
                    "listener_proof": proof, "peer_pid": os.getpid(), "peer_uid": os.getuid(),
                    "observed_monotonic_ns": time.monotonic_ns(), "capture_kind": self.capture_kind}
                path = self.directory / "backend-attestations.jsonl"
                regular_file(path)
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
                with os.fdopen(fd, "ab") as stream:
                    stream.write(_encoded(value))
                    stream.flush()
                    os.fsync(stream.fileno())
                self._count += 1
                response = {"schema_version": 1, "outcome": "OK", "error": None, "proof": value}
            except (ResourceError, OSError, ServiceError) as exc:
                code = exc.code if isinstance(exc, ResourceError) else "backend-timeout" if isinstance(exc, TimeoutError) else "backend-io"
                response = {"schema_version": 1, "outcome": "ERROR", "error": code, "proof": None}
            try:
                connection.settimeout(POLL_SECONDS)
                _send(connection, response)
            except (OSError, ResourceError):
                pass
        return proof is not None

    def close(self):
        try:
            if self._listener is not None:
                self._listener.close()
                self._listener = None
            if self._socket_identity is not None:
                path = self.socket_directory / "backend.sock"
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    info = None
                if info is not None:
                    if ((info.st_dev, info.st_ino) != self._socket_identity
                            or not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()):
                        raise ResourceError("backend-socket-changed")
                    path.unlink()
                self._socket_identity = None
        finally:
            for name in ("_child", "_launcher"):
                reader = getattr(self, name, None)
                if reader is not None:
                    reader.close()
                    setattr(self, name, None)


def attest(output_dir, config):
    """Return a fresh proof after authenticating launcher and backend lifetime."""
    _endpoint(config)
    began = time.monotonic_ns()
    try:
        directory = prepare_directory(Path(output_dir), control_root=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CLIENT_SECONDS)
            connection.connect(str(_socket_path(directory)))
            peer_pid, peer_uid, _gid = peer_credentials(connection)
            if peer_uid != os.getuid() or peer_pid < 1:
                raise ResourceError("backend-peer")
            with ProcessReader(peer_pid) as launcher:
                nonce = str(uuid.uuid4())
                _send(connection, {"schema_version": 1, "action": "status", "nonce": nonce})
                response = _receive(connection, CLIENT_SECONDS)
                if (set(response) != {"schema_version", "outcome", "error", "proof"}
                        or type(response["schema_version"]) is not int or response["schema_version"] != 1
                        or response["outcome"] not in ("OK", "ERROR")):
                    raise ResourceError("backend-protocol")
                if response["outcome"] == "ERROR":
                    if response["proof"] is not None or type(response["error"]) is not str or re.fullmatch(r"[a-z]+(?:-[a-z]+)*", response["error"]) is None:
                        raise ResourceError("backend-protocol")
                    raise ResourceError(response["error"])
                value = response["proof"]
                if response["error"] is not None or type(value) is not dict or set(value) != PROOF_KEYS:
                    raise ResourceError("backend-protocol")
                descriptor = validate_descriptor(value["descriptor"], config)
                if (type(value["schema_version"]) is not int or value["schema_version"] != 1
                        or value["nonce"] != nonce or value["peer_pid"] != peer_pid or type(value["peer_pid"]) is not int
                        or value["peer_uid"] != peer_uid or type(value["peer_uid"]) is not int
                        or value["capture_kind"] not in ("live", "fixture")
                        or descriptor["launcher_process_id"] != peer_pid
                        or descriptor["launcher_start_ticks"] != launcher.identity["process_start_ticks"]
                        or descriptor["host_boot_id"] != launcher.identity["host_boot_id"]):
                    raise ResourceError("backend-peer")
                unsigned(value["observed_monotonic_ns"], positive=True, code="backend-schema")
                if not began <= value["observed_monotonic_ns"] <= time.monotonic_ns():
                    raise ResourceError("backend-stale")
                recorded = value["listener_proof"]
                if type(recorded) is not dict or set(recorded) != {"raw_tcp_line", "fd_target", "fd_number"}:
                    raise ResourceError("backend-schema")
                unsigned(recorded["fd_number"], code="backend-schema")
                if (type(recorded["raw_tcp_line"]) is not str or "\n" in recorded["raw_tcp_line"]
                        or recorded["fd_target"] != "socket:[" + str(descriptor["listener_inode"]) + "]"):
                    raise ResourceError("backend-schema")
                recorded_row = tcp_listener(("local_address\n" + recorded["raw_tcp_line"] + "\n").encode(), _endpoint(config), peer_uid)
                if recorded_row["listener_inode"] != descriptor["listener_inode"]:
                    raise ResourceError("backend-listener")
                with ProcessReader(descriptor["process_id"], descriptor["process_start_ticks"], descriptor["host_boot_id"]) as child:
                    current = listener_proof(child, config)
                    if (current["listener_inode"] != descriptor["listener_inode"]
                            or current["fd_target"] != recorded["fd_target"] or current["fd_number"] != recorded["fd_number"]
                            or parse_stat(child.sample()["raw_stat"])["parent_pid"] != peer_pid):
                        raise ResourceError("backend-listener")
                    launcher.sample()
                return value
    except OSError as exc:
        raise ResourceError("backend-timeout" if isinstance(exc, TimeoutError) else "backend-unavailable") from exc
    except ServiceError as exc:
        raise ResourceError("backend-path") from exc
