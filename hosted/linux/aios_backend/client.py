"""Explicit start/stop/restart without adopting a PID from persisted files."""
from __future__ import annotations

import copy
import atexit
import hashlib
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

from aios_resources.proc import ProcessReader, accounting, parse_stat, parse_status, unsigned
from aios_resources.backend import validate_descriptor
from aios_agent.inference import encoded
from aios_service.lifecycle import (ServiceError, atomic_json, lock_directory, peer_credentials,
    prepare_directory, read_json, regular_file)
from . import BackendError, IDENTITY_KEYS, MAX_STARTS
from . import protocol
from .daemon import MAX_LOG_BYTES, SOURCE_FILES, code_for, registry_at, source_hashes

START_SECONDS = 600
STOP_SECONDS = 35
MAX_LAUNCHES = 64
_CHILDREN = {}
_LEASES = {}
RECOVERY_SECONDS = 20.0
RECOVERY_KEYS = frozenset({"schema_version", *IDENTITY_KEYS, "capture_kind", "source_only", "outcome", "error",
    "lease", "recovery", "old_run_hashes", "source_hashes"})
LEASE_KEYS = frozenset({"acquired_monotonic_ns", "child_pidfd_acquired_monotonic_ns", "child_pidfd_live_at_acquisition",
    "lease_kind", "owner", "supervisor", "child", "service_record", "descriptor", "state_directory", "sockets"})
RECOVERY_PROOF_KEYS = frozenset({"started_monotonic_ns", "completed_monotonic_ns", "supervisor_returncode",
    "supervisor_reaped", "child_exit_observed", "child_exit_code", "signal", "signal_sent_monotonic_ns",
    "child_exit_monotonic_ns", "signal_via", "sockets"})
SOCKET_NAMES = frozenset({"control.sock", "backend.sock"})
RESULT_KEYS = frozenset({"schema_version", *IDENTITY_KEYS, "state", "exit_code", "error", "completed_at",
    "capture_kind", "service_record", "descriptor", "child_exit_code", "child_exit_verified", "forced", "log_bytes", "files"})
RUN_FILES = frozenset({"start.json", "config.json", "events.jsonl", "service.json", "stdout.log", "stderr.log",
    "backend-source.json", "backend-attestations.jsonl", "health.json"})


def _require(condition, code="recovery-invalid"):
    if not condition:
        raise BackendError(code)


def _same(left, right):
    return type(left) is type(right) and encoded(left) == encoded(right)


def _keys(value, expected):
    _require(type(value) is dict and set(value) == set(expected))


def _inode(value):
    _keys(value, {"device", "inode"})
    unsigned(value["device"])
    unsigned(value["inode"], positive=True)


def _stamp(info):
    return {"device": info.st_dev, "inode": info.st_ino}


def _sample(value):
    names = {"schema_version", "host_boot_id", "process_id", "process_start_ticks", "uid",
        "read_start_ns", "read_end_ns", "clock_ticks_per_second", "page_bytes", "raw_stat", "raw_status",
        "user_ticks", "system_ticks", "cpu_total_ns", "rss_pages", "rss_bytes_estimate", "virtual_bytes",
        "scope", "consistency", "memory_accuracy", "source_only"}
    _keys(value, names)
    _require(_same(value["schema_version"], 1) and value["source_only"] is True
        and value["scope"] == "single-linux-process" and value["consistency"] == "sequential-copied-read"
        and value["memory_accuracy"] == "kernel-approximate")
    identity = protocol.validate_identity({key: value[key] for key in ("host_boot_id", "process_id", "process_start_ticks", "uid")})
    parsed, status = parse_stat(value["raw_stat"], value["process_id"]), parse_status(value["raw_status"], value["process_id"])
    _require(parsed["state"] not in ("Z", "X", "x") and parsed["process_start_ticks"] == value["process_start_ticks"]
        and status["uid"] == value["uid"])
    unsigned(value["read_start_ns"], positive=True)
    unsigned(value["read_end_ns"], positive=True)
    _require(value["read_start_ns"] <= value["read_end_ns"])
    expected = accounting(parsed, value["clock_ticks_per_second"], value["page_bytes"])
    _require(all(_same(value[key], expected[key]) for key in expected))
    return identity, parsed


def _hashes(value, names):
    _keys(value, names)
    _require(all(type(digest) is str and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
                 for digest in value.values()))


def validate_recovery(value, identity, latest, config):
    """Validate stored recovery evidence; this never grants permission to signal."""
    _keys(value, RECOVERY_KEYS)
    _require(_same(value["schema_version"], 1) and value["source_only"] is True
        and all(_same(value[key], identity[key]) for key in IDENTITY_KEYS)
        and value["outcome"] in ("RECOVERED", "FAILED")
        and value["capture_kind"] == latest["capture_kind"])
    lease, proof = value["lease"], value["recovery"]
    _keys(lease, LEASE_KEYS)
    _keys(proof, RECOVERY_PROOF_KEYS)
    _require(lease["lease_kind"] == "owned-supervisor-child-pidfd" and lease["child_pidfd_live_at_acquisition"] is True)
    unsigned(lease["child_pidfd_acquired_monotonic_ns"], positive=True)
    record = protocol.validate_record(lease["service_record"])
    _require(record["backend_ready"] is True and record["lifecycle_state"] == "active"
        and latest["state"] == "RUNNING" and latest["outcome"] == "OK"
        and _same(record, latest["service_record"]) and _same(lease["descriptor"], latest["descriptor"]))
    validate_descriptor(lease["descriptor"], config)
    protocol.validate_reply(protocol.reply("status", "RUNNING", record=record, descriptor=lease["descriptor"],
                                          capture_kind=value["capture_kind"]), "status")
    owner, owner_stat = _sample(lease["owner"])
    supervisor, supervisor_stat = _sample(lease["supervisor"])
    child, child_stat = _sample(lease["child"])
    _require(_same(supervisor, record["supervisor_identity"]) and _same(child, record["child_identity"])
        and owner["host_boot_id"] == supervisor["host_boot_id"] == child["host_boot_id"]
        and owner["uid"] == supervisor["uid"] == child["uid"]
        and len({owner["process_id"], supervisor["process_id"], child["process_id"]}) == 3
        and supervisor_stat["parent_pid"] == owner["process_id"]
        and child_stat["parent_pid"] == supervisor["process_id"])
    _inode(lease["state_directory"])
    _keys(lease["sockets"], SOCKET_NAMES)
    _keys(proof["sockets"], SOCKET_NAMES)
    for name in SOCKET_NAMES:
        _inode(lease["sockets"][name])
        row = proof["sockets"][name]
        _keys(row, {"device", "inode", "removed"})
        _require(type(row["removed"]) is bool and _same({key: row[key] for key in ("device", "inode")}, lease["sockets"][name]))
    for number in (lease["acquired_monotonic_ns"], proof["started_monotonic_ns"], proof["completed_monotonic_ns"]):
        unsigned(number, positive=True)
    _require(lease["child_pidfd_acquired_monotonic_ns"] <= lease["child"]["read_start_ns"]
        and max(lease[name]["read_end_ns"] for name in ("owner", "supervisor", "child")) <= lease["acquired_monotonic_ns"]
        <= proof["started_monotonic_ns"] <= proof["completed_monotonic_ns"]
        and proof["supervisor_reaped"] is True and type(proof["supervisor_returncode"]) is int
        and -128 <= proof["supervisor_returncode"] <= 255 and proof["supervisor_returncode"] != 0
        and type(proof["child_exit_observed"]) is bool and proof["child_exit_code"] is None)
    _require(proof["signal"] in (None, "SIGTERM"))
    if proof["signal"] is None:
        _require(proof["signal_sent_monotonic_ns"] is None and proof["signal_via"] is None)
    else:
        _require(proof["signal_via"] == "retained-pidfd")
        unsigned(proof["signal_sent_monotonic_ns"], positive=True)
        _require(proof["started_monotonic_ns"] <= proof["signal_sent_monotonic_ns"] <= proof["completed_monotonic_ns"])
    if proof["child_exit_observed"]:
        unsigned(proof["child_exit_monotonic_ns"], positive=True)
        _require((proof["signal_sent_monotonic_ns"] or proof["started_monotonic_ns"])
                 <= proof["child_exit_monotonic_ns"] <= proof["completed_monotonic_ns"])
    else:
        _require(proof["child_exit_monotonic_ns"] is None)
    if value["outcome"] == "RECOVERED":
        _require(value["error"] is None and proof["child_exit_observed"]
                 and all(row["removed"] for row in proof["sockets"].values()))
    else:
        _require(value["error"] in ("recovery-timeout", "recovery-signal", "recovery-socket", "recovery-io"))
    _hashes(value["old_run_hashes"], RUN_FILES)
    _hashes(value["source_hashes"], SOURCE_FILES)
    return value


def _run_hashes(directory, instance):
    run = prepare_directory(directory / "runs" / instance)
    with os.scandir(run) as entries:
        names = set()
        for row in entries:
            _require(len(names) < len(RUN_FILES), "recovery-invalid")
            names.add(row.name)
    _require(names == RUN_FILES)
    hashes = {}
    for name in sorted(RUN_FILES):
        path = run / name
        regular_file(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and before.st_size <= 2 * MAX_LOG_BYTES)
            digest = hashlib.sha256()
            total = 0
            while True:
                block = os.read(fd, 65536)
                if not block:
                    break
                total += len(block)
                _require(total <= 2 * MAX_LOG_BYTES)
                digest.update(block)
            after = os.fstat(fd)
            _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                and total == before.st_size and _same(_stamp(after), _stamp(path.lstat())))
            hashes[name] = digest.hexdigest()
        finally:
            os.close(fd)
    return hashes


def _recovery_at(directory, identity, latest):
    try:
        recoveries = prepare_directory(directory / "recoveries")
        value = read_json(recoveries / (identity["instance_id"] + ".json"))
    except FileNotFoundError:
        return None
    config = read_json(directory / "runs" / identity["instance_id"] / "config.json")
    validate_recovery(value, identity, latest, config)
    _require(_same(value["old_run_hashes"], _run_hashes(directory, identity["instance_id"])))
    start = read_json(directory / "runs" / identity["instance_id"] / "start.json")
    _require(_same(value["source_hashes"], start["source_hashes"]))
    return value


def _recovered_reply(action, value):
    record = copy.deepcopy(value["lease"]["service_record"])
    record.update(lifecycle_state="exited", backend_ready=False)
    return protocol.reply(action, "RECOVERED", record=record, capture_kind=value["capture_kind"])


def _close_lease(instance):
    lease = _LEASES.pop(instance, None)
    if lease is not None:
        for name in ("supervisor_reader", "child_reader"):
            lease[name].close()


def close_leases():
    """Release local capabilities at client exit; never signal a stored PID."""
    for instance in list(_LEASES):
        _close_lease(instance)


atexit.register(close_leases)


def _socket_stamp(directory, name, *, dir_fd=None):
    info = (directory / name).lstat() if dir_fd is None else os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    _require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
             "recovery-socket")
    return _stamp(info)


def _acquire_lease(directory, owned, value):
    record = value["service_record"]
    supervisor_reader = child_reader = None
    try:
        _require(owned.poll() is None and record["supervisor_identity"]["process_id"] == owned.pid, "recovery-owner")
        supervisor = record["supervisor_identity"]
        child = record["child_identity"]
        supervisor_reader = ProcessReader(owned.pid, supervisor["process_start_ticks"], supervisor["host_boot_id"])
        child_reader = ProcessReader(child["process_id"], child["process_start_ticks"], child["host_boot_id"])
        pidfd_acquired = time.monotonic_ns()
        with ProcessReader(os.getpid()) as owner_reader:
            owner_sample = owner_reader.sample()
        supervisor_sample, child_sample = supervisor_reader.sample(), child_reader.sample()
        _require(_same(supervisor_reader.identity, supervisor) and _same(child_reader.identity, child)
            and parse_stat(supervisor_sample["raw_stat"])["parent_pid"] == os.getpid()
            and parse_stat(child_sample["raw_stat"])["parent_pid"] == owned.pid, "recovery-owner")
        config = read_json(directory / "config.json")
        validate_descriptor(value["descriptor"], config)
        proof = {"acquired_monotonic_ns": time.monotonic_ns(), "owner": owner_sample,
            "child_pidfd_acquired_monotonic_ns": pidfd_acquired, "child_pidfd_live_at_acquisition": True,
            "lease_kind": "owned-supervisor-child-pidfd",
            "supervisor": supervisor_sample, "child": child_sample, "service_record": copy.deepcopy(record),
            "descriptor": copy.deepcopy(value["descriptor"]), "state_directory": _stamp(directory.stat()),
            "sockets": {name: _socket_stamp(directory, name) for name in sorted(SOCKET_NAMES)}}
        connection, after, _pid = protocol.connect(directory, record)
        connection.close()
        _require(_same(after, value) and owned.poll() is None, "recovery-owner")
        supervisor_reader.sample()
        child_reader.sample()
        proof["acquired_monotonic_ns"] = time.monotonic_ns()
        _close_lease(record["instance_id"])
        _LEASES[record["instance_id"]] = {"directory": str(directory), "proof": proof,
            "supervisor_reader": supervisor_reader, "child_reader": child_reader}
    except BaseException:
        for reader in (supervisor_reader, child_reader):
            if reader is not None:
                reader.close()
        raise


def _recover(directory, before):
    record = before["service_record"]
    if before["state"] == "RECOVERED":
        return {**before, "action": "recover", "outcome": "ERROR", "error": "already-recovered"}
    if before["state"] in ("RUNNING", "STARTING", "STOPPING"):
        return {**before, "action": "recover", "outcome": "ERROR", "error": "supervisor-running"}
    if record is None or before["state"] != "STALE":
        return {**before, "action": "recover", "outcome": "ERROR", "error": "recovery-unavailable"}
    instance = record["instance_id"]
    lease, owned = _LEASES.get(instance), _CHILDREN.get(instance)
    _require(lease is not None and owned is not None, "recovery-owner-required")
    _require(hasattr(signal, "pidfd_send_signal"), "recovery-unsupported")
    proof = lease["proof"]
    _require(lease["directory"] == str(directory) and _same(proof["state_directory"], _stamp(directory.stat())), "recovery-owner")
    identity = registry_at(directory)
    latest = _latest(directory, identity)
    _require(latest["state"] == "RUNNING" and latest["outcome"] == "OK"
        and _same(latest["service_record"], proof["service_record"])
        and _same(latest["descriptor"], proof["descriptor"]), "recovery-owner")
    _require(owned.pid == proof["supervisor"]["process_id"] and owned.poll() is not None
        and bool(select.select([lease["supervisor_reader"]._pidfd], [], [], 0)[0]), "supervisor-running")
    returncode = owned.wait(timeout=1)
    _require(type(returncode) is int and returncode != 0, "recovery-unavailable")
    with ProcessReader(os.getpid()) as owner:
        _require(_same(owner.identity, {key: proof["owner"][key] for key in owner.identity}), "recovery-owner")
    recoveries = prepare_directory(directory / "recoveries", create=True)
    receipt_path = recoveries / (instance + ".json")
    _require(not receipt_path.exists(), "recovery-consumed")
    lock_fd = lock_directory(directory)
    directory_fd = None
    consumed = False
    try:
        _require(_LEASES.get(instance) is lease and _CHILDREN.get(instance) is owned
                 and not receipt_path.exists(), "recovery-consumed")
        _require(_same(registry_at(directory), identity) and _same(_latest(directory, identity), latest), "recovery-owner")
        old_hashes = _run_hashes(directory, instance)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _require(_same(_stamp(os.fstat(directory_fd)), proof["state_directory"]), "recovery-owner")
        for name in SOCKET_NAMES:
            _require(_same(_socket_stamp(directory, name, dir_fd=directory_fd), proof["sockets"][name]), "recovery-socket")
        child_reader = lease["child_reader"]
        exited = bool(select.select([child_reader._pidfd], [], [], 0)[0])
        if not exited:
            child_reader.sample()
        started = time.monotonic_ns()
        receipt = {"schema_version": 1, **{key: identity[key] for key in IDENTITY_KEYS},
            "capture_kind": latest["capture_kind"], "source_only": True, "outcome": "FAILED", "error": "recovery-io",
            "lease": copy.deepcopy(proof), "recovery": {"started_monotonic_ns": started,
                "completed_monotonic_ns": started, "supervisor_returncode": returncode, "supervisor_reaped": True,
                "child_exit_observed": False, "child_exit_code": None, "signal": None,
                "signal_via": None,
                "signal_sent_monotonic_ns": None, "child_exit_monotonic_ns": None,
                "sockets": {name: {**proof["sockets"][name], "removed": False} for name in sorted(SOCKET_NAMES)}},
            "old_run_hashes": old_hashes, "source_hashes": source_hashes()}
        # A durable pre-signal FAILED receipt consumes this one-shot capability.
        # A crash or write failure can never leave a reusable success claim.
        atomic_json(receipt_path, receipt)
        consumed = True
        evidence = receipt["recovery"]
        try:
            if not exited:
                try:
                    signal.pidfd_send_signal(child_reader._pidfd, signal.SIGTERM)
                    evidence["signal"] = "SIGTERM"
                    evidence["signal_via"] = "retained-pidfd"
                    evidence["signal_sent_monotonic_ns"] = time.monotonic_ns()
                except OSError as exc:
                    raise BackendError("recovery-signal") from exc
                if not select.select([child_reader._pidfd], [], [], RECOVERY_SECONDS)[0]:
                    raise BackendError("recovery-timeout")
            evidence["child_exit_observed"] = True
            evidence["child_exit_monotonic_ns"] = time.monotonic_ns()
            for name in sorted(SOCKET_NAMES):
                _require(_same(_socket_stamp(directory, name, dir_fd=directory_fd), proof["sockets"][name]), "recovery-socket")
                os.unlink(name, dir_fd=directory_fd)
                evidence["sockets"][name]["removed"] = True
            os.fsync(directory_fd)
            _require(_same(_run_hashes(directory, instance), old_hashes), "recovery-io")
            receipt.update(outcome="RECOVERED", error=None)
        except (OSError, ValueError) as exc:
            error = getattr(exc, "code", "recovery-io")
            receipt["error"] = error if error in ("recovery-timeout", "recovery-signal", "recovery-socket") else "recovery-io"
        evidence["completed_monotonic_ns"] = time.monotonic_ns()
        config = read_json(directory / "runs" / instance / "config.json")
        validate_recovery(receipt, identity, latest, config)
        atomic_json(receipt_path, receipt)
        if receipt["outcome"] == "RECOVERED":
            return _recovered_reply("recover", receipt)
        return {**before, "action": "recover", "outcome": "ERROR", "error": receipt["error"]}
    finally:
        if consumed:
            _close_lease(instance)
            _CHILDREN.pop(instance, None)
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(lock_fd)


def _busy(directory):
    try:
        handle = lock_directory(directory)
    except ServiceError as exc:
        if exc.code == "BUSY":
            return True
        raise
    os.close(handle)
    return False


def _latest(directory, identity):
    value = protocol.validate_reply(read_json(directory / "latest.json"), "status")
    record = value["service_record"]
    if record is None or any(record[k] != identity[k] for k in IDENTITY_KEYS):
        raise BackendError("state-corrupt")
    return value


def _terminal(directory, identity, latest):
    run = prepare_directory(directory / "runs" / identity["instance_id"])
    result = read_json(run / "result.json")
    if (set(result) != RESULT_KEYS or type(result["schema_version"]) is not int or result["schema_version"] != 1
            or any(result[k] != identity[k] for k in IDENTITY_KEYS)
            or any(result[k] != latest[k] for k in ("state", "error", "capture_kind", "service_record"))
            or type(result["exit_code"]) is not int or result["exit_code"] != (0 if latest["state"] == "STOPPED" else 1)
            or type(result["child_exit_verified"]) is not bool or type(result["forced"]) is not bool
            or result["child_exit_code"] is not None and type(result["child_exit_code"]) is not int
            or type(result["files"]) is not dict or not {"start.json", "config.json", "events.jsonl", "service.json"} <= result["files"].keys()
            or not result["files"].keys() <= RUN_FILES
            or type(result["log_bytes"]) is not dict or set(result["log_bytes"]) != {"stdout", "stderr"}
            or any(type(n) is not int or not 0 <= n <= MAX_LOG_BYTES for n in result["log_bytes"].values())):
        raise BackendError("state-corrupt")
    if latest["state"] == "STOPPED" and (latest["error"] is not None or result["forced"]
            or not result["child_exit_verified"] or result["child_exit_code"] not in (0, -signal.SIGTERM)
            or latest["service_record"]["child_identity"] is None):
        raise BackendError("state-corrupt")
    for name, digest in result["files"].items():
        if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BackendError("state-corrupt")
        path = run / name
        regular_file(path)
        if path.stat().st_size > 2 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise BackendError("state-corrupt")
    if read_json(run / "service.json") != latest["service_record"]:
        raise BackendError("state-corrupt")
    return result


def _status(directory):
    identity = registry_at(directory)
    if identity is None:
        return protocol.reply("status", "ABSENT")
    latest = _latest(directory, identity)
    recovered = _recovery_at(directory, identity, latest)
    if recovered is not None:
        if recovered["outcome"] == "RECOVERED":
            return _recovered_reply("status", recovered)
        record = copy.deepcopy(latest["service_record"])
        record["backend_ready"] = False
        return protocol.reply("status", "STALE", record=record, error=recovered["error"], capture_kind=latest["capture_kind"])
    try:
        connection, value, _pid = protocol.connect(directory, identity)
        connection.close()
        return value
    except OSError:
        if _busy(directory):
            # Stored identity is diagnostic; unavailable IPC cannot affirm readiness.
            record = copy.deepcopy(latest["service_record"])
            record["backend_ready"] = False
            return protocol.reply("status", "STARTING" if latest["state"] == "STARTING" else "STALE",
                record=record, error="start-in-progress" if latest["state"] == "STARTING" else "rpc-timeout",
                capture_kind=latest["capture_kind"])
        if latest["state"] in ("STOPPED", "FAILED"):
            _terminal(directory, identity, latest)
            owned = _CHILDREN.get(identity["instance_id"])
            if owned is None or owned.poll() is not None:
                _close_lease(identity["instance_id"])
                _CHILDREN.pop(identity["instance_id"], None)
            return latest
        record = copy.deepcopy(latest["service_record"])
        record["backend_ready"] = False
        return protocol.reply("status", "STALE", record=record, error="process-not-running", capture_kind=latest["capture_kind"])


def _finish_failed_start(child):
    for instance, lease in list(_LEASES.items()):
        if lease["proof"]["supervisor"]["process_id"] == child.pid:
            _close_lease(instance)
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            # Only this just-created supervisor handle is ours. An orphan backend
            # is never adopted by PID; missing terminal proof blocks later start.
            child.kill()
            child.wait(timeout=5)


def _reserve_launch(directory):
    """Reserve one bounded attempt; release this lock before launching anything."""
    import fcntl
    path = directory / "launch.lock"
    regular_file(path, absent_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise BackendError("state-corrupt")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackendError("start-in-progress") from exc
        launches = prepare_directory(directory / "launches", create=True)
        # Bound enumeration too, including malformed or failed attempt entries.
        # Reserved attempts stay counted even when Popen later fails.
        with os.scandir(launches) as entries:
            for count, _entry in enumerate(entries, 1):
                if count >= MAX_LAUNCHES:
                    raise BackendError("launch-limit")
        launch = launches / str(uuid.uuid4())
        launch.mkdir(mode=0o700)
        return launch
    finally:
        os.close(descriptor)


def _start(directory, config, fixture_backend):
    try:
        before = _status(directory)
    except FileNotFoundError:
        if _busy(directory):
            return protocol.reply("start", "STARTING", error="start-in-progress",
                                  capture_kind="fixture" if fixture_backend else "live")
        raise
    if before["state"] == "RUNNING" and before["outcome"] == "OK":
        return {**before, "action": "start", "outcome": "ERROR", "error": "already-running"}
    if _busy(directory):
        return {**before, "action": "start", "outcome": "ERROR", "error": "start-in-progress"}
    identity = registry_at(directory)
    if identity is not None:
        if identity["start_generation"] >= MAX_STARTS:
            return {**before, "action": "start", "outcome": "ERROR", "error": "generation-exhausted"}
        # In particular, a killed supervisor is not evidence that its child exited.
        if before["state"] not in ("STOPPED", "FAILED", "RECOVERED"):
            return {**before, "action": "start", "outcome": "ERROR", "error": "stop-failed"}
        if before["state"] == "RECOVERED":
            receipt = _recovery_at(directory, identity, _latest(directory, identity))
            _require(receipt is not None and receipt["outcome"] == "RECOVERED")
        else:
            terminal = _terminal(directory, identity, before)
            if terminal["child_exit_code"] is not None and not terminal["child_exit_verified"]:
                return {**before, "action": "start", "outcome": "ERROR", "error": "stop-failed"}
    config_path = Path(config).absolute() if config is not None else directory / "config.json"
    if not config_path.is_file():
        return {**before, "action": "start", "outcome": "ERROR", "error": "config-required"}
    try:
        launch = _reserve_launch(directory)
    except BackendError as exc:
        if exc.code not in ("launch-limit", "start-in-progress"):
            raise
        return {**before, "action": "start", "outcome": "ERROR", "error": exc.code}
    command = [sys.executable, str(Path(__file__).resolve().parent.parent / "aios-backend.py"),
        "--serve", "--state-dir", str(directory), "--config", str(config_path)]
    if fixture_backend:
        command.append("--fixture-backend")
    child = None
    try:
        # The supervisor never emits model output; its child pipes have their own
        # bounded drain. Do not create unbounded supervisor stdout/stderr files.
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        atomic_json(launch / "launch.json", {"schema_version": 1, "command": command,
            "pid": child.pid, "source_only": True, "capture_kind": "fixture" if fixture_backend else "live"})
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            if child.poll() is not None:
                try:
                    value = _status(directory)
                    return {**value, "action": "start", "outcome": "ERROR", "error": value["error"] or "start-failed"}
                except (OSError, ValueError):
                    return protocol.reply("start", "FAILED", error="start-failed", capture_kind="fixture" if fixture_backend else "live")
            try:
                value = _status(directory)
            except (OSError, ValueError):
                time.sleep(0.05)
                continue
            if value["state"] == "RUNNING" and value["outcome"] == "OK":
                if value["service_record"]["supervisor_identity"]["process_id"] != child.pid:
                    return {**value, "action": "start", "outcome": "ERROR", "error": "already-running"}
                _acquire_lease(directory, child, value)
                _CHILDREN[value["service_record"]["instance_id"]] = child
                return {**value, "action": "start"}
            time.sleep(0.05)
        return protocol.reply("start", "FAILED", error="start-timeout", capture_kind="fixture" if fixture_backend else "live")
    finally:
        if child is not None and child not in _CHILDREN.values():
            _finish_failed_start(child)


def _stop(directory, before):
    record = before["service_record"]
    identity = {k: record[k] for k in IDENTITY_KEYS}
    parent, child = record["supervisor_identity"], record["child_identity"]
    parent_handle = child_handle = None
    try:
        with ProcessReader(parent["process_id"], parent["process_start_ticks"], parent["host_boot_id"]) as reader:
            if reader.identity != parent:
                raise BackendError("peer-mismatch")
            parent_handle = os.pidfd_open(parent["process_id"])
            if child is not None:
                with ProcessReader(child["process_id"], child["process_start_ticks"], child["host_boot_id"]) as owned:
                    if owned.identity != child:
                        raise BackendError("peer-mismatch")
                    child_handle = os.pidfd_open(child["process_id"])
                    owned.sample()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(protocol.RPC_SECONDS)
                connection.connect(str(protocol.socket_path(directory)))
                pid, uid, _gid = peer_credentials(connection)
                if pid != parent["process_id"] or uid != parent["uid"]:
                    raise BackendError("peer-mismatch")
                reader.sample()
                protocol.send(connection, {"schema_version": 1, "action": "stop", **identity})
                value = protocol.validate_reply(protocol.receive(connection), "stop")
                if value["outcome"] != "OK":
                    return value
                if value["state"] != "STOPPING" or any(value["service_record"][k] != record[k]
                        for k in (*IDENTITY_KEYS, "supervisor_identity", "child_identity")):
                    raise BackendError("stop-failed")
        if not select.select([parent_handle], [], [], STOP_SECONDS)[0]:
            return {**value, "outcome": "ERROR", "error": "stop-timeout"}
        if child_handle is not None and not select.select([child_handle], [], [], 0)[0]:
            raise BackendError("stop-failed")
        owned_supervisor = _CHILDREN.pop(record["instance_id"], None)
        if owned_supervisor is not None:
            owned_supervisor.wait(timeout=1)
        terminal = _status(directory)
        if terminal["state"] != "STOPPED" or terminal["outcome"] != "OK":
            return {**terminal, "action": "stop", "outcome": "ERROR", "error": terminal["error"] or "stop-failed"}
        if owned_supervisor is not None and owned_supervisor.returncode != 0:
            raise BackendError("stop-failed")
        return {**terminal, "action": "stop"}
    finally:
        _close_lease(record["instance_id"])
        for handle in (parent_handle, child_handle):
            if handle is not None:
                os.close(handle)


def control(state_dir, action, config=None, *, fixture_backend=False):
    if not protocol.supported():
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
            return _start(directory, config, fixture_backend)
        before = _status(directory)
        if action == "recover":
            return _recover(directory, before)
        if action == "stop":
            if before["state"] not in ("RUNNING", "STARTING") or before["outcome"] != "OK":
                if before["state"] in ("STOPPED", "FAILED", "RECOVERED") and before["service_record"] is not None:
                    _close_lease(before["service_record"]["instance_id"])
                return {**before, "action": "stop"}
            return _stop(directory, before)
        if before["state"] in ("RUNNING", "STARTING") and before["outcome"] == "OK":
            stopped = _stop(directory, before)
            if stopped["outcome"] != "OK":
                return {**stopped, "action": "restart"}
        return {**_start(directory, config, fixture_backend), "action": "restart"}
    except (OSError, ValueError, RuntimeError) as exc:
        return protocol.reply(action, "FAILED", error=code_for(exc), capture_kind="fixture" if fixture_backend else "live")
