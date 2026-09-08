"""Bounded process accounting with held Linux lifetime and proc handles."""
from __future__ import annotations

import errno
import os
import platform
import re
import select
import stat
import time
import uuid

from . import ResourceError

U64_MAX = (1 << 64) - 1
STAT_LIMIT = 4096
STATUS_LIMIT = 8192
PRESSURE_LIMIT = 1024


def unsigned(value, *, positive=False, code="counter-invalid"):
    if type(value) is not int or not (1 if positive else 0) <= value <= U64_MAX:
        raise ResourceError(code)
    return value


def decimal(value, *, positive=False, code="proc-format"):
    if type(value) is not str or len(value) > 20 or re.fullmatch(r"[0-9]+", value) is None:
        raise ResourceError(code)
    return unsigned(int(value), positive=positive, code=code)


def _text(raw, limit, code):
    if type(raw) is str:
        try:
            raw = raw.encode("utf-8")
        except UnicodeError as exc:
            raise ResourceError(code) from exc
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ResourceError(code)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ResourceError(code) from exc


def parse_stat(raw, expected_pid=None):
    """Parse from the final comm delimiter, including spaces and parentheses.

    utime already includes guest time. Child and guest fields are deliberately
    excluded from this single-process CPU accounting surface.
    """
    text = _text(raw, STAT_LIMIT, "stat-format")
    opening, closing = text.find(" ("), text.rfind(")")
    if opening < 1 or closing <= opening + 1 or text[closing + 1:closing + 2] != " ":
        raise ResourceError("stat-format")
    pid = decimal(text[:opening], positive=True, code="stat-format")
    if expected_pid is not None and pid != unsigned(expected_pid, positive=True, code="process-id-invalid"):
        raise ResourceError("process-identity")
    fields = text[closing + 2:].split()
    if len(fields) < 22 or fields[0] not in {"R", "S", "D", "Z", "T", "t", "X", "x", "K", "W", "P", "I"}:
        raise ResourceError("stat-format")
    result = {"process_id": pid, "state": fields[0],
              "parent_pid": decimal(fields[1], code="stat-format"),
              "user_ticks": decimal(fields[11], code="stat-format"),
              "system_ticks": decimal(fields[12], code="stat-format"),
              "process_start_ticks": decimal(fields[19], positive=True, code="stat-format"),
              "virtual_bytes": decimal(fields[20], code="stat-format"),
              "rss_pages": decimal(fields[21], code="stat-format")}
    unsigned(result["user_ticks"] + result["system_ticks"], code="counter-overflow")
    return result


def parse_status(raw, expected_pid=None):
    text = _text(raw, STATUS_LIMIT, "status-format")
    values = {}
    for line in text.splitlines():
        if ":" not in line:
            raise ResourceError("status-format")
        name, content = line.split(":", 1)
        if name in ("Pid", "Tgid", "Uid"):
            if name in values:
                raise ResourceError("status-format")
            values[name] = content.split()
    if set(values) != {"Pid", "Tgid", "Uid"} or len(values["Pid"]) != 1 or len(values["Tgid"]) != 1 or len(values["Uid"]) != 4:
        raise ResourceError("status-format")
    pid = decimal(values["Pid"][0], positive=True, code="status-format")
    tgid = decimal(values["Tgid"][0], positive=True, code="status-format")
    uids = [decimal(item, code="status-format") for item in values["Uid"]]
    if pid != tgid or len(set(uids)) != 1:
        raise ResourceError("process-identity")
    if expected_pid is not None and pid != unsigned(expected_pid, positive=True, code="process-id-invalid"):
        raise ResourceError("process-identity")
    return {"process_id": pid, "uid": uids[0]}


def accounting(parsed, clock_ticks, page_bytes):
    unsigned(clock_ticks, positive=True, code="clock-ticks-invalid")
    unsigned(page_bytes, positive=True, code="page-bytes-invalid")
    user = unsigned(parsed["user_ticks"])
    system = unsigned(parsed["system_ticks"])
    ticks = unsigned(user + system, code="counter-overflow")
    cpu = unsigned(ticks * 1_000_000_000 // clock_ticks, code="counter-overflow")
    pages = unsigned(parsed["rss_pages"])
    resident = unsigned(pages * page_bytes, code="counter-overflow")
    return {"user_ticks": user, "system_ticks": system, "cpu_total_ns": cpu,
            "rss_pages": pages, "rss_bytes_estimate": resident,
            "virtual_bytes": unsigned(parsed["virtual_bytes"])}


def read_bounded(path, limit, *, dir_fd=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ResourceError("proc-file-type")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(4096, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            raise ResourceError("proc-size")
        return bytes(data)
    finally:
        os.close(descriptor)


def boot_id():
    try:
        value = read_bounded("/proc/sys/kernel/random/boot_id", 64).decode("ascii").strip()
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.int == 0:
            raise ValueError()
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        raise ResourceError("boot-identity") from exc


def _process_error(exc):
    if exc.errno in (errno.ENOENT, errno.ESRCH):
        return ResourceError("process-exited")
    if exc.errno in (errno.EACCES, errno.EPERM):
        return ResourceError("process-permission")
    return ResourceError("proc-io")


class ProcessReader:
    def __init__(self, pid, expected_start_ticks=None, expected_boot_id=None):
        self._pidfd = None
        self._dirfd = None
        self._identity = None
        self._last_ticks = None
        unsigned(pid, positive=True, code="process-id-invalid")
        if expected_start_ticks is not None:
            unsigned(expected_start_ticks, positive=True, code="start-ticks-invalid")
        if platform.system() != "Linux" or not hasattr(os, "pidfd_open"):
            raise ResourceError("unsupported-platform")
        try:
            self._pidfd = os.pidfd_open(pid)
            self._dirfd = os.open("/proc/" + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._pid = pid
            self._alive()
            current_boot = boot_id()
            if expected_boot_id is not None and current_boot != expected_boot_id:
                raise ResourceError("boot-identity")
            self._clock_ticks = unsigned(os.sysconf("SC_CLK_TCK"), positive=True, code="clock-ticks-invalid")
            self._page_bytes = unsigned(os.sysconf("SC_PAGE_SIZE"), positive=True, code="page-bytes-invalid")
            parsed, status, _stat, _status = self._read_pair()
            if expected_start_ticks is not None and parsed["process_start_ticks"] != expected_start_ticks:
                raise ResourceError("process-identity")
            self._identity = {"host_boot_id": current_boot, "process_id": pid,
                "process_start_ticks": parsed["process_start_ticks"], "uid": status["uid"]}
            self._alive()
        except OSError as exc:
            self.close()
            raise _process_error(exc) from exc
        except BaseException:
            self.close()
            raise

    @property
    def identity(self):
        return dict(self._identity) if self._identity is not None else None

    def _alive(self):
        if self._pidfd is None or self._dirfd is None:
            raise ResourceError("process-reader-closed")
        if select.select([self._pidfd], [], [], 0)[0]:
            raise ResourceError("process-exited")
        if os.fstat(self._dirfd).st_uid != os.getuid():
            raise ResourceError("process-uid")

    def _read_pair(self):
        raw_stat = read_bounded("stat", STAT_LIMIT, dir_fd=self._dirfd)
        raw_status = read_bounded("status", STATUS_LIMIT, dir_fd=self._dirfd)
        parsed, status = parse_stat(raw_stat, self._pid), parse_status(raw_status, self._pid)
        if parsed["state"] in ("Z", "X", "x"):
            raise ResourceError("process-exited")
        if status["uid"] != os.getuid():
            raise ResourceError("process-uid")
        return parsed, status, raw_stat, raw_status

    def _match(self, parsed, status):
        if (parsed["process_start_ticks"] != self._identity["process_start_ticks"]
                or status["uid"] != self._identity["uid"] or boot_id() != self._identity["host_boot_id"]):
            raise ResourceError("process-identity")

    def sample(self):
        began = time.monotonic_ns()
        try:
            self._alive()
            parsed, status, raw_stat, raw_status = self._read_pair()
            self._match(parsed, status)
            values = accounting(parsed, self._clock_ticks, self._page_bytes)
            after, after_status, _unused_stat, _unused_status = self._read_pair()
            self._match(after, after_status)
            self._alive()
            if self._last_ticks is not None and (values["user_ticks"] < self._last_ticks[0]
                    or values["system_ticks"] < self._last_ticks[1]):
                raise ResourceError("cpu-regression")
            if after["user_ticks"] < values["user_ticks"] or after["system_ticks"] < values["system_ticks"]:
                raise ResourceError("cpu-regression")
            self._last_ticks = (values["user_ticks"], values["system_ticks"])
            return {"schema_version": 1, **self.identity, "read_start_ns": began,
                "read_end_ns": time.monotonic_ns(), "clock_ticks_per_second": self._clock_ticks,
                "page_bytes": self._page_bytes, "raw_stat": raw_stat.decode("utf-8"),
                "raw_status": raw_status.decode("utf-8"), **values, "scope": "single-linux-process",
                "consistency": "sequential-copied-read", "memory_accuracy": "kernel-approximate", "source_only": True}
        except OSError as exc:
            raise _process_error(exc) from exc

    def close(self):
        for name in ("_dirfd", "_pidfd"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def parse_pressure(raw, kind):
    if kind not in ("cpu", "memory", "io"):
        raise ResourceError("pressure-kind")
    text = _text(raw, PRESSURE_LIMIT, "pressure-format")
    rows = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0] not in ("some", "full") or fields[0] in rows:
            raise ResourceError("pressure-format")
        metrics = {}
        for item in fields[1:]:
            parts = item.split("=")
            if len(parts) != 2 or parts[0] in metrics:
                raise ResourceError("pressure-format")
            key, value = parts
            if key in ("avg10", "avg60", "avg300"):
                if re.fullmatch(r"[0-9]{1,3}\.[0-9]{2}", value) is None:
                    raise ResourceError("pressure-format")
                number = int(value.replace(".", ""))
                if number > 10000:
                    raise ResourceError("pressure-format")
                metrics[key + "_bp"] = number
            elif key == "total":
                metrics["total_us"] = decimal(value, code="pressure-format")
            else:
                raise ResourceError("pressure-format")
        if set(metrics) != {"avg10_bp", "avg60_bp", "avg300_bp", "total_us"}:
            raise ResourceError("pressure-format")
        rows[fields[0]] = metrics
    if "some" not in rows or (kind != "cpu" and "full" not in rows):
        raise ResourceError("pressure-format")
    return {"state": "AVAILABLE", "error": None, "raw": text, "some": rows["some"],
            "full": rows.get("full"), "full_valid": kind != "cpu"}


def pressure_sample():
    began = time.monotonic_ns()
    metrics = {}
    for kind in ("cpu", "memory", "io"):
        raw = None
        try:
            if platform.system() != "Linux":
                raise ResourceError("unsupported-platform")
            raw = read_bounded("/proc/pressure/" + kind, PRESSURE_LIMIT)
            metrics[kind] = parse_pressure(raw, kind)
        except (OSError, ResourceError) as exc:
            error = exc.code if isinstance(exc, ResourceError) else "pressure-missing" if exc.errno == errno.ENOENT else "pressure-io"
            metrics[kind] = {"state": "UNAVAILABLE", "error": error,
                "raw": raw.decode("utf-8", errors="replace") if raw is not None else None,
                "some": None, "full": None, "full_valid": False}
    return {"schema_version": 1, "read_start_ns": began, "read_end_ns": time.monotonic_ns(),
            "scope": "linux-system", "attribution": "unattributed", "source_only": True, "metrics": metrics}
