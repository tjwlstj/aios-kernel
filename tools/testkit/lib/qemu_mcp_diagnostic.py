"""Bounded, diagnostics-only qemu-mcp session for AIOS.

This module deliberately does not evaluate the normal kernel boot contract.
It observes one interactive prompt/ping/QMP sequence, preserves diagnostic
artifacts, and proves cleanup inside a dedicated OS containment boundary.
Only the regular testkit kernel/shell lanes may produce authoritative PASS or
FAIL claims.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import queue
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.common import (
    BUILD_DIR,
    DEFAULT_QEMU_TIMEOUT,
    REPO_ROOT,
    ToolError,
    host_name,
    print_step,
    shell_join,
)
from lib.shell_lane import (
    COMMAND_TIMEOUT_SEC,
    SHELL_PROMPT,
    build_kernel_iso,
    find_qemu,
)


PROTOCOL_VERSION = "2025-11-25"
DIAGNOSTIC_KIND = "aios.qemu_mcp_diagnostic"
DIAGNOSTIC_ROOT = BUILD_DIR / "qemu-mcp-diagnostic"
OUTCOMES = {
    "OBSERVED",
    "TIMEOUT",
    "VM_EXITED",
    "INFRA_ERROR",
    "CLEANUP_ERROR",
    "ABORTED",
}

INITIALIZE_TIMEOUT_SEC = 10
TOOLS_TIMEOUT_SEC = 10
LIST_TIMEOUT_SEC = 10
BOOT_CONNECT_TIMEOUT_SEC = 20
QMP_READ_TIMEOUT_SEC = 15
BOOT_HOST_TIMEOUT_SEC = 65
SERIAL_SEND_TIMEOUT_SEC = 20
QMP_HOST_TIMEOUT_SEC = 25
SCREENSHOT_HOST_TIMEOUT_SEC = 25
STOP_HOST_TIMEOUT_SEC = 25
SERVER_EXIT_TIMEOUT_SEC = 5
CONTAINMENT_DRAIN_TIMEOUT_SEC = 10
QEMU_EXIT_TIMEOUT_SEC = 5
MAX_COMMAND_TIMEOUT_SEC = 600
MAX_RPC_LINE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 32 * 1024 * 1024

REQUIRED_TOOL_PROPERTIES: dict[str, dict[str, str]] = {
    "qemu_boot": {
        "name": "string",
        "iso": "string",
        "arch": "string",
        "memory_mb": "integer",
        "extra_args": "string",
        "qmp_connect_timeout_s": "number",
        "qmp_read_timeout_s": "number",
    },
    "qemu_wait_serial": {
        "name": "string",
        "text": "string",
        "timeout_s": "integer",
    },
    "qemu_serial": {"name": "string", "tail_lines": "integer"},
    "qemu_serial_send": {"name": "string", "text": "string"},
    "qemu_qmp": {"name": "string", "command": "string"},
    "qemu_screenshot": {"name": "string"},
    "qemu_stop": {"name": "string", "force": "boolean"},
    "qemu_list": {},
}

REQUIRED_TOOL_REQUIRED: dict[str, set[str]] = {
    "qemu_boot": {"name"},
    "qemu_wait_serial": {"name", "text"},
    "qemu_serial": {"name"},
    "qemu_serial_send": {"name", "text"},
    "qemu_qmp": {"name", "command"},
    "qemu_screenshot": {"name"},
    "qemu_stop": {"name"},
    "qemu_list": set(),
}

STRING_RESULT_TOOLS = {
    "qemu_boot",
    "qemu_wait_serial",
    "qemu_serial",
    "qemu_serial_send",
    "qemu_qmp",
    "qemu_stop",
    "qemu_list",
}


class DiagnosticFailure(RuntimeError):
    def __init__(self, outcome: str, reason: str, detail: str) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown diagnostic outcome: {outcome}")
        self.outcome = outcome
        self.reason = reason
        self.detail = detail
        super().__init__(detail)


class QemuMcpDiagnosticError(ToolError):
    def __init__(self, outcome: str, artifact_dir: Path) -> None:
        self.outcome = outcome
        self.artifact_dir = artifact_dir
        self.exit_code = 1 if outcome in {"TIMEOUT", "VM_EXITED"} else 2
        super().__init__(
            f"qemu-mcp diagnostic ended as {outcome} "
            f"(see {artifact_dir / 'summary.json'})"
        )


class McpProtocolError(RuntimeError):
    pass


class McpHostTimeout(McpProtocolError):
    pass


class McpToolError(RuntimeError):
    def __init__(self, tool: str, text: str) -> None:
        self.tool = tool
        self.text = text
        super().__init__(f"{tool} returned isError=true: {text}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    stat = path.stat()
    if stat.st_size > limit:
        raise ValueError(f"{label} exceeds {limit} bytes: {stat.st_size}")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"{label} exceeds {limit} bytes while reading")
    return raw


def _file_entry(path: Path, base: Path | None = None) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    display = path.relative_to(base).as_posix() if base is not None else str(path)
    return {"path": display, "bytes": size, "sha256": digest.hexdigest()}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(token: str) -> None:
    raise ValueError(f"non-finite JSON token is forbidden: {token}")


def _strict_json_object(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise McpProtocolError(f"invalid {label}: {exc}") from exc
    if type(value) is not dict:
        raise McpProtocolError(f"{label} must be a JSON object")
    return value


def _strict_json_value(text: str, label: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise McpProtocolError(f"invalid {label}: {exc}") from exc


def _validate_rpc_envelope(payload: dict[str, Any]) -> None:
    if payload.get("jsonrpc") != "2.0":
        raise McpProtocolError("MCP envelope jsonrpc must equal string '2.0'")
    if "id" not in payload:
        if (
            type(payload.get("method")) is not str
            or not payload["method"]
            or "result" in payload
            or "error" in payload
            or ("params" in payload and type(payload["params"]) not in {dict, list})
        ):
            raise McpProtocolError("malformed MCP notification envelope")
    elif (
        type(payload["id"]) is not int
        or payload["id"] <= 0
        or "method" in payload
        or ("result" in payload) == ("error" in payload)
    ):
        raise McpProtocolError("malformed MCP response envelope")


def _type_strict_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(actual) is dict:
        return actual.keys() == expected.keys() and all(
            _type_strict_equal(actual[key], expected[key]) for key in actual
        )
    if type(actual) is list:
        return len(actual) == len(expected) and all(
            _type_strict_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def _safe_transcript_payload(value: Any) -> Any:
    if type(value) is dict:
        if value.get("type") == "image" and type(value.get("data")) is str:
            data = value["data"]
            return {
                **{
                    key: _safe_transcript_payload(item)
                    for key, item in value.items()
                    if key != "data"
                },
                "data": {
                    "redacted": True,
                    "base64_characters": len(data),
                    "base64_sha256": hashlib.sha256(data.encode("ascii")).hexdigest(),
                },
            }
        return {key: _safe_transcript_payload(item) for key, item in value.items()}
    if type(value) is list:
        return [_safe_transcript_payload(item) for item in value]
    return value


if os.name == "nt":
    from ctypes import wintypes

    _ULONG_PTR = ctypes.c_size_t

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", _ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]


class ProcessContainment:
    """Contain a dedicated MCP server and all of its QEMU children."""

    def __init__(self) -> None:
        self.server_pid: int | None = None
        self._closed = False
        self._job: int | None = None
        self._process_handles: dict[int, int] = {}
        if os.name == "nt":
            self._create_windows_job()

    def _create_windows_job(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self._job = int(handle)

    def popen_kwargs(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"start_new_session": True}
        # A pip console-script launcher may spawn Python before it reads any
        # MCP request. Keep its primary thread suspended until Job assignment,
        # so that interpreter and QEMU descendants inherit the same boundary.
        return {"creationflags": 0x00000004 | 0x08000000}

    def _resume_windows_primary_thread(self, pid: int) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.Thread32First.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_THREADENTRY32),
        ]
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_THREADENTRY32),
        ]
        kernel32.Thread32Next.restype = wintypes.BOOL
        kernel32.OpenThread.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenThread.restype = ctypes.c_void_p
        kernel32.GetProcessIdOfThread.argtypes = [ctypes.c_void_p]
        kernel32.GetProcessIdOfThread.restype = wintypes.DWORD
        kernel32.ResumeThread.argtypes = [ctypes.c_void_p]
        kernel32.ResumeThread.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "thread snapshot failed")
        thread_ids: list[int] = []
        try:
            entry = _THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread_ids.append(int(entry.th32ThreadID))
                entry.dwSize = ctypes.sizeof(entry)
                found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            error = ctypes.get_last_error()
            if error != 18:  # ERROR_NO_MORE_FILES is the only normal terminator.
                raise OSError(error, "thread enumeration failed")
        finally:
            kernel32.CloseHandle(snapshot)

        if len(thread_ids) != 1:
            raise OSError(
                f"suspended MCP launcher must have exactly one thread, got {len(thread_ids)}"
            )
        thread_handle = kernel32.OpenThread(
            0x0002 | 0x0800,  # THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION
            False,
            thread_ids[0],
        )
        if not thread_handle:
            raise OSError(ctypes.get_last_error(), "OpenThread failed")
        try:
            if kernel32.GetProcessIdOfThread(thread_handle) != pid:
                raise OSError("primary thread no longer belongs to the owned MCP launcher")
            previous_count = kernel32.ResumeThread(thread_handle)
            if previous_count == 0xFFFFFFFF:
                raise OSError(ctypes.get_last_error(), "ResumeThread failed")
            if previous_count != 1:
                raise OSError(
                    f"MCP primary thread suspend count must be 1, got {previous_count}"
                )
        finally:
            kernel32.CloseHandle(thread_handle)

    def attach(self, proc: subprocess.Popen[bytes]) -> None:
        self.server_pid = proc.pid
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.AssignProcessToJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            process_handle = kernel32.OpenProcess(
                0x0100 | 0x0001 | 0x1000,
                False,
                proc.pid,
            )
            if not process_handle:
                raise OSError(ctypes.get_last_error(), "OpenProcess failed")
            try:
                if not kernel32.AssignProcessToJobObject(self._job, process_handle):
                    raise OSError(
                        ctypes.get_last_error(),
                        "AssignProcessToJobObject failed",
                    )
            finally:
                kernel32.CloseHandle(process_handle)
            if not self.contains_pid(proc.pid):
                raise OSError("MCP server was not assigned to its Job Object")
            self._resume_windows_primary_thread(proc.pid)
            return

        if os.getpgid(proc.pid) != proc.pid:
            raise OSError("MCP server did not create a dedicated process group")

    def contains_pid(self, pid: int) -> bool:
        if type(pid) is not int or pid <= 0:
            return False
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.IsProcessInJob.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(wintypes.BOOL),
            ]
            kernel32.IsProcessInJob.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            process_handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
            if not process_handle:
                if ctypes.get_last_error() in {87, 1168}:
                    return False
                return False
            try:
                in_job = wintypes.BOOL()
                if not kernel32.IsProcessInJob(
                    process_handle,
                    self._job,
                    ctypes.byref(in_job),
                ):
                    raise OSError(ctypes.get_last_error(), "IsProcessInJob failed")
                if in_job.value and pid not in self._process_handles:
                    self._process_handles[pid] = int(process_handle)
                    process_handle = None
                return bool(in_job.value)
            finally:
                if process_handle:
                    kernel32.CloseHandle(process_handle)

        try:
            return os.getpgid(pid) == self.server_pid
        except (ProcessLookupError, PermissionError):
            return False

    def pid_exited(self, pid: int) -> bool:
        """Observe the owned process, not merely membership in its Job/group."""
        if os.name == "nt":
            handle = self._process_handles.get(pid)
            if handle is None:
                raise OSError("cannot observe an unowned process handle")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            result = kernel32.WaitForSingleObject(handle, 0)
            if result == 0:  # WAIT_OBJECT_0: the retained process handle is signaled.
                return True
            if result == 258:  # WAIT_TIMEOUT: process is still live.
                return False
            raise OSError(ctypes.get_last_error(), "owned process wait failed")
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            text = stat_path.read_text(encoding="ascii")
            fields = text[text.rfind(")") + 2:].split()
            return fields[0] == "Z" or int(fields[2]) != self.server_pid
        except FileNotFoundError:
            return not self.contains_pid(pid)

    def active_processes(self) -> int | None:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.QueryInformationJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
            ]
            kernel32.QueryInformationJobObject.restype = wintypes.BOOL
            info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
            if not kernel32.QueryInformationJobObject(
                self._job,
                1,
                ctypes.byref(info),
                ctypes.sizeof(info),
                None,
            ):
                raise OSError(
                    ctypes.get_last_error(),
                    "QueryInformationJobObject failed",
                )
            return int(info.ActiveProcesses)

        members = self._posix_live_group_members()
        return len(members) if members is not None else None

    def _posix_live_group_members(self) -> list[int] | None:
        if self.server_pid is None:
            return []
        proc_root = Path("/proc")
        if not proc_root.is_dir():
            return None
        members: list[int] = []
        for stat_path in proc_root.glob("[0-9]*/stat"):
            try:
                text = stat_path.read_text(encoding="ascii")
                tail = text[text.rfind(")") + 2 :].split()
                state = tail[0]
                process_group = int(tail[2])
                pid = int(stat_path.parent.name)
            except (OSError, ValueError, IndexError):
                continue
            if process_group == self.server_pid and state != "Z":
                members.append(pid)
        return members

    def drained(self) -> bool:
        count = self.active_processes()
        if count is not None:
            return count == 0
        if self.server_pid is None:
            return True
        try:
            os.killpg(self.server_pid, 0)
            return False
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    def terminate_all(self, timeout_sec: float) -> bool:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, wintypes.UINT]
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            if not kernel32.TerminateJobObject(self._job, 1):
                raise OSError(ctypes.get_last_error(), "TerminateJobObject failed")
            return _wait_until(self.drained, timeout_sec)

        if self.server_pid is None:
            return True
        try:
            os.killpg(self.server_pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        if _wait_until(self.drained, min(timeout_sec, 3.0)):
            return True
        try:
            os.killpg(self.server_pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        return _wait_until(self.drained, max(0.1, timeout_sec - 3.0))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if os.name == "nt" and self._job:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            for handle in self._process_handles.values():
                kernel32.CloseHandle(handle)
            self._process_handles.clear()
            kernel32.CloseHandle(self._job)
            self._job = None


def _wait_until(predicate: Any, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


class McpStdioClient:
    def __init__(
        self,
        server: Path,
        transcript_path: Path,
        stderr_path: Path,
        env: dict[str, str],
        *,
        command: list[str] | None = None,
    ) -> None:
        self.server = server
        self.transcript_path = transcript_path
        self.stderr_path = stderr_path
        self.containment = ProcessContainment()
        self.proc: subprocess.Popen[bytes] | None = None
        self.stream_usable = True
        self._request_id = 0
        self._seen_response_ids: set[int] = set()
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=256)
        self._transcript_lock = threading.Lock()
        self._transcript_sequence = 0
        self._transcript_bytes = 0
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._writer_threads: list[threading.Thread] = []
        self.reader_errors: list[str] = []

        command = command or [str(server)]
        print_step(f"RUN {shell_join(command)} (dedicated stdio MCP server)")
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                **self.containment.popen_kwargs(),
            )
            self.proc = proc
            self.containment.attach(proc)
        except BaseException:
            try:
                if self.proc is not None:
                    if self.containment.server_pid == self.proc.pid:
                        self.containment.terminate_all(CONTAINMENT_DRAIN_TIMEOUT_SEC)
                    if self.proc.poll() is None:
                        self.proc.kill()
                    self.proc.wait(timeout=SERVER_EXIT_TIMEOUT_SEC)
            finally:
                self.containment.close()
            raise

        try:
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                name="aios-qemu-mcp-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                name="aios-qemu-mcp-stderr",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
        except BaseException:
            try:
                self.containment.terminate_all(CONTAINMENT_DRAIN_TIMEOUT_SEC)
                proc.wait(timeout=SERVER_EXIT_TIMEOUT_SEC)
            finally:
                self.containment.close()
            raise

    def _queue_server_message(self, kind: str, value: Any) -> bool:
        try:
            self._messages.put_nowait((kind, value))
            return True
        except queue.Full:
            self.stream_usable = False
            self.reader_errors.append("MCP pending-message queue exceeded its limit")
            return False

    def _record(self, direction: str, payload: Any) -> None:
        with self._transcript_lock:
            self._transcript_sequence += 1
            record = {
                "sequence": self._transcript_sequence,
                "observed_at_utc": _utc_now(),
                "direction": direction,
                "payload": _safe_transcript_payload(payload),
            }
            line = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            self._transcript_bytes += len(line.encode("utf-8")) + 1
            if self._transcript_bytes > MAX_LOG_BYTES:
                self.stream_usable = False
                raise McpProtocolError("MCP transcript exceeds the artifact byte limit")
            with self.transcript_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")

    def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        total_bytes = 0
        try:
            while True:
                raw = self.proc.stdout.readline(MAX_RPC_LINE_BYTES + 1)
                if not raw:
                    self._queue_server_message("eof", None)
                    return
                total_bytes += len(raw)
                if total_bytes > 2 * MAX_LOG_BYTES:
                    raise McpProtocolError("MCP cumulative stdout exceeds its byte limit")
                if len(raw) > MAX_RPC_LINE_BYTES:
                    error = McpProtocolError("MCP response line exceeds the byte limit")
                    self.reader_errors.append(str(error))
                    self._record(
                        "server-invalid",
                        {"bytes": len(raw), "sha256": _sha256_bytes(raw), "error": str(error)},
                    )
                    self._queue_server_message("error", error)
                    return
                if not raw.endswith(b"\n"):
                    error = McpProtocolError("MCP response is missing its LF terminator")
                    self.reader_errors.append(str(error))
                    self._record(
                        "server-invalid",
                        {"bytes": len(raw), "sha256": _sha256_bytes(raw), "error": str(error)},
                    )
                    self._queue_server_message("error", error)
                    return
                try:
                    text = raw[:-1].decode("utf-8")
                    payload = _strict_json_object(text, "MCP response")
                    _validate_rpc_envelope(payload)
                except (UnicodeDecodeError, McpProtocolError) as exc:
                    error = McpProtocolError(f"invalid MCP stdout line: {exc}")
                    self.reader_errors.append(str(error))
                    self._record(
                        "server-invalid",
                        {"bytes": len(raw), "sha256": _sha256_bytes(raw), "error": str(error)},
                    )
                    self._queue_server_message("error", error)
                    return
                self._record("server", payload)
                if not self._queue_server_message("message", payload):
                    return
        except Exception as exc:
            self.reader_errors.append(f"stdout: {exc}")
            self._queue_server_message("error", exc)

    def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        try:
            with self.stderr_path.open("ab") as handle:
                total = 0
                while True:
                    raw = self.proc.stderr.read(4096)
                    if not raw:
                        return
                    if total + len(raw) > MAX_LOG_BYTES:
                        if total <= MAX_LOG_BYTES:
                            self.reader_errors.append("stderr exceeds the artifact byte limit")
                        total += len(raw)
                        continue  # Keep draining without unbounded artifact growth.
                    total += len(raw)
                    handle.write(raw)
                    handle.flush()
        except Exception as exc:
            self.reader_errors.append(f"stderr: {exc}")
            self._queue_server_message("error", exc)

    def _send(
        self, payload: dict[str, Any], timeout_sec: float = INITIALIZE_TIMEOUT_SEC,
    ) -> None:
        if not self.stream_usable:
            raise McpProtocolError("MCP stream is no longer usable")
        assert self.proc is not None and self.proc.stdin is not None
        raw = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        completed: queue.Queue[Exception | None] = queue.Queue(maxsize=1)

        def write_request() -> None:
            try:
                self._record("client", payload)
                assert self.proc is not None and self.proc.stdin is not None
                self.proc.stdin.write(raw)
                self.proc.stdin.flush()
            except Exception as exc:
                completed.put(exc)
            else:
                completed.put(None)

        writer = threading.Thread(target=write_request, name="aios-qemu-mcp-writer", daemon=True)
        self._writer_threads.append(writer)
        writer.start()
        try:
            error = completed.get(timeout=max(0.001, timeout_sec))
        except queue.Empty as exc:
            self.stream_usable = False
            raise McpHostTimeout("MCP request write exceeded its host deadline") from exc
        if error is not None:
            self.stream_usable = False
            raise McpProtocolError(f"cannot write to MCP server: {error}") from error

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def request(
        self,
        method: str,
        params: dict[str, Any] | None,
        timeout_sec: float,
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        deadline = time.monotonic() + timeout_sec
        self._send(payload, timeout_sec)
        while True:
            if not self.stream_usable:
                raise McpProtocolError("MCP stream became unusable while awaiting a response")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.stream_usable = False
                raise McpHostTimeout(
                    f"MCP request {method} id={request_id} exceeded {timeout_sec}s"
                )
            try:
                kind, item = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                self.stream_usable = False
                raise McpHostTimeout(
                    f"MCP request {method} id={request_id} exceeded {timeout_sec}s"
                ) from exc
            if kind == "eof":
                self.stream_usable = False
                code = self.proc.poll() if self.proc is not None else None
                raise McpProtocolError(
                    f"MCP server closed stdout while waiting for id={request_id} "
                    f"(exit_code={code})"
                )
            if kind == "error":
                self.stream_usable = False
                raise McpProtocolError(str(item))
            response = item
            try:
                _validate_rpc_envelope(response)
            except McpProtocolError:
                self.stream_usable = False
                raise
            if response.get("jsonrpc") != "2.0" or type(response.get("jsonrpc")) is not str:
                self.stream_usable = False
                raise McpProtocolError("MCP response jsonrpc must equal string '2.0'")
            if "id" not in response:
                if type(response.get("method")) is str:
                    continue
                self.stream_usable = False
                raise McpProtocolError("MCP response has neither id nor notification method")
            response_id = response["id"]
            if type(response_id) is not int or response_id <= 0:
                self.stream_usable = False
                raise McpProtocolError("MCP response id must be a positive integer")
            if response_id in self._seen_response_ids:
                self.stream_usable = False
                raise McpProtocolError(f"duplicate MCP response id: {response_id}")
            if response_id != request_id:
                self.stream_usable = False
                raise McpProtocolError(
                    f"MCP response id {response_id} does not match request id {request_id}"
                )
            self._seen_response_ids.add(response_id)
            has_result = "result" in response
            has_error = "error" in response
            if has_result == has_error:
                self.stream_usable = False
                raise McpProtocolError("MCP response must contain exactly one of result/error")
            if has_error:
                error = response["error"]
                raise McpProtocolError(f"MCP JSON-RPC error for {method}: {error!r}")
            result = response["result"]
            if type(result) is not dict:
                self.stream_usable = False
                raise McpProtocolError("MCP result must be an object")
            return result

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "aios-qemu-mcp-diagnostic",
                    "version": "1.0",
                },
            },
            INITIALIZE_TIMEOUT_SEC,
        )
        if result.get("protocolVersion") != PROTOCOL_VERSION:
            raise McpProtocolError(
                "MCP negotiated protocol does not match the fixed diagnostic lane"
            )
        server_info = result.get("serverInfo")
        if type(server_info) is not dict or server_info.get("name") != "qemu":
            raise McpProtocolError("MCP serverInfo.name must equal 'qemu'")
        if type(server_info.get("version")) is not str:
            raise McpProtocolError("MCP serverInfo.version must be a string")
        if type(result.get("capabilities")) is not dict:
            raise McpProtocolError("MCP capabilities must be an object")
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> tuple[dict[str, dict[str, Any]], str]:
        observed: dict[str, dict[str, Any]] = {}
        cursor: str | None = None
        for _ in range(8):
            params = {"cursor": cursor} if cursor is not None else None
            result = self.request("tools/list", params, TOOLS_TIMEOUT_SEC)
            tools = result.get("tools")
            if type(tools) is not list:
                raise McpProtocolError("tools/list result.tools must be an array")
            for entry in tools:
                if type(entry) is not dict or type(entry.get("name")) is not str:
                    raise McpProtocolError("tools/list contains a malformed tool entry")
                name = entry["name"]
                if name in observed:
                    raise McpProtocolError(f"duplicate MCP tool name: {name}")
                observed[name] = entry
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                break
            if type(next_cursor) is not str or not next_cursor:
                raise McpProtocolError("tools/list nextCursor must be a non-empty string")
            cursor = next_cursor
        else:
            raise McpProtocolError("tools/list exceeded the pagination limit")

        _validate_required_tool_schemas(observed)
        selected = {name: observed[name] for name in sorted(REQUIRED_TOOL_PROPERTIES)}
        fingerprint = _sha256_bytes(
            json.dumps(
                selected,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return observed, fingerprint

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout_sec: float,
    ) -> dict[str, Any]:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_sec,
        )
        is_error = result.get("isError")
        if type(is_error) is not bool:
            raise McpProtocolError("tool result.isError must be a boolean")
        content = result.get("content")
        if type(content) is not list:
            raise McpProtocolError("tool result.content must be an array")
        if is_error:
            texts = [
                item.get("text")
                for item in content
                if type(item) is dict
                and item.get("type") == "text"
                and type(item.get("text")) is str
            ]
            raise McpToolError(name, "\n".join(texts) or "unspecified tool error")
        return result

    def text_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout_sec: float,
    ) -> str:
        result = self.call_tool(name, arguments, timeout_sec)
        return _extract_text_tool_result(name, result)

    def screenshot_tool(self, name: str, timeout_sec: float) -> bytes:
        result = self.call_tool("qemu_screenshot", {"name": name}, timeout_sec)
        content = result["content"]
        if len(content) != 1 or type(content[0]) is not dict:
            raise McpProtocolError("qemu_screenshot must return exactly one image block")
        image = content[0]
        if image.get("type") != "image" or image.get("mimeType") != "image/png":
            raise McpProtocolError("qemu_screenshot must return image/png")
        data = image.get("data")
        if type(data) is not str:
            raise McpProtocolError("qemu_screenshot image data must be base64 text")
        if len(data) > ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 4:
            raise McpProtocolError("qemu_screenshot base64 payload exceeds the limit")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise McpProtocolError(f"invalid qemu_screenshot base64: {exc}") from exc
        if len(raw) > MAX_IMAGE_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise McpProtocolError("qemu_screenshot is not a bounded PNG image")
        return raw

    def close_stdin_and_wait(self, timeout_sec: float) -> bool:
        if self.proc is None:
            return True
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            return False
        return True

    def force_containment_cleanup(self, timeout_sec: float) -> bool:
        result = self.containment.terminate_all(timeout_sec)
        if self.proc is not None:
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        return result

    def drain_readers(self, timeout_sec: float = 2.0) -> bool:
        threads = [self._stdout_thread, self._stderr_thread, *self._writer_threads]
        for thread in threads:
            if thread is not None:
                thread.join(timeout=timeout_sec)
        drained = all(thread is None or not thread.is_alive() for thread in threads)
        if drained and self.proc is not None:
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        return drained

    def close_containment(self) -> None:
        self.containment.close()


def _schema_allows_type(schema: Any, expected: str) -> bool:
    if type(schema) is not dict:
        return False
    declared = schema.get("type")
    if declared == expected:
        return True
    variants = schema.get("anyOf")
    return type(variants) is list and any(
        type(item) is dict and item.get("type") == expected for item in variants
    )


def _validate_required_tool_schemas(
    observed: dict[str, dict[str, Any]],
) -> None:
    missing = sorted(set(REQUIRED_TOOL_PROPERTIES) - set(observed))
    if missing:
        raise McpProtocolError(f"required qemu-mcp tools are missing: {missing}")
    for name, properties in REQUIRED_TOOL_PROPERTIES.items():
        tool = observed[name]
        schema = tool.get("inputSchema")
        if type(schema) is not dict or schema.get("type") != "object":
            raise McpProtocolError(f"{name}.inputSchema must be an object schema")
        actual_properties = schema.get("properties")
        if type(actual_properties) is not dict:
            raise McpProtocolError(f"{name}.inputSchema.properties must be an object")
        actual_required = schema.get("required", [])
        if type(actual_required) is not list or not all(
            type(item) is str for item in actual_required
        ):
            raise McpProtocolError(f"{name}.inputSchema.required must be a string array")
        if set(actual_required) != REQUIRED_TOOL_REQUIRED[name]:
            raise McpProtocolError(f"{name} required arguments do not match diagnostic v1")
        for property_name, expected_type in properties.items():
            if property_name not in actual_properties or not _schema_allows_type(
                actual_properties[property_name], expected_type
            ):
                raise McpProtocolError(
                    f"{name}.{property_name} does not allow JSON {expected_type}"
                )
        if name in STRING_RESULT_TOOLS:
            output = tool.get("outputSchema")
            if type(output) is not dict:
                raise McpProtocolError(f"{name}.outputSchema is missing")
            output_properties = output.get("properties")
            if (
                output.get("type") != "object"
                or type(output_properties) is not dict
                or not _schema_allows_type(output_properties.get("result"), "string")
                or set(output.get("required", [])) != {"result"}
            ):
                raise McpProtocolError(f"{name}.outputSchema.result must be required string")


def _extract_text_tool_result(name: str, result: dict[str, Any]) -> str:
    content = result.get("content")
    if (
        type(content) is not list
        or len(content) != 1
        or type(content[0]) is not dict
        or content[0].get("type") != "text"
        or type(content[0].get("text")) is not str
    ):
        raise McpProtocolError(f"{name} must return exactly one text content block")
    text = content[0]["text"]
    structured = result.get("structuredContent")
    if structured is None:
        return text
    if type(structured) is not dict or type(structured.get("result")) is not str:
        raise McpProtocolError(f"{name}.structuredContent.result must be a string")
    if structured["result"] != text:
        raise McpProtocolError(f"{name} text and structured results disagree")
    return text


def _wait_status(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0] not in {"FOUND", "TIMEOUT", "VM EXITED"}:
        raise McpProtocolError("qemu_wait_serial returned an invalid first status line")
    return lines[0]


def _fresh_pong_record(raw: bytes) -> bool:
    # The current serial producer emits CRCRLF; LF and CRLF are also accepted
    # transport terminators. Do not strip leading space or partial-record tails.
    records = raw.split(b"\n")
    candidates = [
        (index, record) for index, record in enumerate(records)
        if record.startswith(b"[STATE] pong")
    ]
    if len(candidates) != 1:
        return False
    index, record = candidates[0]
    if index == len(records) - 1:
        return False  # A serial record needs its LF terminator.
    match = re.fullmatch(rb"\[STATE\] pong ticks=([0-9]{1,20})\r{0,2}", record)
    return match is not None and int(match.group(1)) <= 0xFFFFFFFFFFFFFFFF


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    return f"{stamp}-{os.getpid()}-{secrets.token_hex(4)}"


def _validate_run_id(run_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9-]{12,80}", run_id):
        raise ValueError(f"unsafe qemu-mcp diagnostic run id: {run_id!r}")


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _reject_link_components(path: Path) -> None:
    # Check before resolve(): resolving first erases the link/junction evidence.
    for component in (path, *path.parents):
        try:
            linked = _is_link_or_reparse(component)
        except FileNotFoundError:
            continue
        if linked:
            raise ValueError(f"symlink/reparse component is not allowed: {component}")


def _create_run_dir(run_id: str, root: Path) -> Path:
    _validate_run_id(run_id)
    _reject_link_components(root)
    root.mkdir(parents=True, exist_ok=True)
    _reject_link_components(root)
    run_dir = root / run_id
    run_dir.mkdir(exist_ok=False)
    _reject_link_components(run_dir)
    return run_dir


def _create_owned_temp(run_id: str) -> tuple[Path, Path]:
    base = Path(tempfile.gettempdir()).resolve()
    path = Path(
        tempfile.mkdtemp(prefix=f"aios-qemu-mcp-{run_id}-", dir=str(base))
    ).resolve()
    marker = path / ".aios-qemu-mcp-owner.json"
    marker.write_text(
        json.dumps({"run_id": run_id}, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path, base


def _remove_owned_temp(path: Path, base: Path, run_id: str) -> bool:
    try:
        _reject_link_components(path)
        resolved = path.resolve(strict=True)
        if resolved.parent != base.resolve(strict=True):
            return False
        if not resolved.name.startswith(f"aios-qemu-mcp-{run_id}-"):
            return False
        marker = resolved / ".aios-qemu-mcp-owner.json"
        if _is_link_or_reparse(marker):
            return False
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload != {"run_id": run_id}:
            return False
        shutil.rmtree(resolved)
        return not resolved.exists()
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _parse_boot_result(
    text: str,
    name: str,
    owned_temp: Path,
) -> tuple[int, Path, Path]:
    lines = text.splitlines()
    if len(lines) != 3:
        raise McpProtocolError("qemu_boot result must contain exactly three lines")
    first = re.fullmatch(
        rf"VM {re.escape(repr(name))} booted \(pid ([1-9][0-9]*), x86_64, 256 MB\)\.",
        lines[0],
    )
    if first is None:
        raise McpProtocolError("qemu_boot result does not match the owned VM tuple")
    if not lines[1].startswith("Serial log: "):
        raise McpProtocolError("qemu_boot result is missing the anchored serial path")
    if lines[2] != (
        "Next: qemu_wait_serial for a boot marker, or qemu_screenshot to see the display."
    ):
        raise McpProtocolError("qemu_boot result has an unexpected next-step line")
    pid = int(first.group(1))
    serial_literal = lines[1][len("Serial log: ") :]
    if not serial_literal or not Path(serial_literal).is_absolute():
        raise McpProtocolError("qemu_boot serial path must be absolute")
    try:
        _reject_link_components(Path(serial_literal))
        serial_path = Path(serial_literal).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise McpProtocolError(f"qemu_boot serial path is not readable: {exc}") from exc
    if serial_path.is_symlink() or serial_path.name != "serial.log":
        raise McpProtocolError("qemu_boot serial path must name a regular serial.log")
    workdir = serial_path.parent
    if workdir.parent != owned_temp.resolve(strict=True):
        raise McpProtocolError("qemu_boot serial path escapes the owned temp root")
    prefix = f"qemu-mcp-{name}-"
    if not workdir.name.startswith(prefix) or workdir.name == prefix:
        raise McpProtocolError("qemu_boot workdir is not bound to the owned VM name")
    if workdir.is_symlink() or not serial_path.is_file():
        raise McpProtocolError("qemu_boot serial source must be a regular file")
    try:
        _reject_link_components(workdir / "qemu.log")
        qemu_log = (workdir / "qemu.log").resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise McpProtocolError(f"qemu_boot qemu.log source is invalid: {exc}") from exc
    if qemu_log.parent != workdir or qemu_log.name != "qemu.log" or not qemu_log.is_file():
        raise McpProtocolError("qemu_boot qemu.log source is invalid")
    return pid, serial_path, qemu_log


def _snapshot_source_file(
    source: Path,
    destination: Path,
    limit: int,
) -> dict[str, Any]:
    for attempt in range(3):
        _reject_link_components(source)
        before = source.stat()
        raw = _read_bounded(source, limit, source.name)
        after = source.stat()
        changed = (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(raw) != after.st_size
        )
        if not changed or attempt == 2:
            break
        time.sleep(0.05)
    destination.write_bytes(raw)
    return {
        "source_bytes_before": before.st_size,
        "source_bytes_after": after.st_size,
        "source_changed_during_capture": changed,
        "captured_bytes": len(raw),
        "captured_sha256": _sha256_bytes(raw),
    }


def _git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return completed.stdout.strip()

    try:
        return {
            "head_sha": run("rev-parse", "HEAD"),
            "dirty": bool(run("status", "--porcelain=v1", "--untracked-files=normal")),
        }
    except (OSError, subprocess.SubprocessError):
        return {"head_sha": None, "dirty": None}


def _package_versions() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for distribution_name in ("qemu-mcp", "mcp", "mcp-types", "Pillow"):
        try:
            distribution = importlib.metadata.distribution(distribution_name)
            entry: dict[str, Any] = {"version": distribution.version}
            if distribution_name == "qemu-mcp":
                direct_url = distribution.read_text("direct_url.json")
                if direct_url:
                    try:
                        entry["direct_url"] = json.loads(direct_url)
                    except json.JSONDecodeError:
                        entry["direct_url"] = None
            result[distribution_name] = entry
        except importlib.metadata.PackageNotFoundError:
            result[distribution_name] = None
    return result


def _qemu_provenance() -> dict[str, Any] | None:
    qemu = find_qemu()
    if qemu is None:
        return None
    path = Path(qemu).resolve()
    entry = _file_entry(path)
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        version = completed.stdout.splitlines()[0] if completed.stdout else ""
    except (OSError, subprocess.SubprocessError):
        version = None
    return {**entry, "version": version}


def _initial_summary(
    run_id: str,
    name: str,
    timeout_sec: int,
    skip_build: bool,
) -> dict[str, Any]:
    boot_arguments = {
        "name": name,
        "iso": None,
        "arch": "x86_64",
        "memory_mb": 256,
        "extra_args": "-nic none -no-reboot",
        "qmp_connect_timeout_s": BOOT_CONNECT_TIMEOUT_SEC,
        "qmp_read_timeout_s": QMP_READ_TIMEOUT_SEC,
    }
    return {
        "schema_version": 1,
        "kind": DIAGNOSTIC_KIND,
        "diagnostic_only": True,
        "authoritative": False,
        "outcome": "INFRA_ERROR",
        "reasons": ["not-started"],
        "request": {
            "run_id": run_id,
            "timeout_sec": timeout_sec,
            "skip_build": skip_build,
            "smoke_profile": "minimal",
            "cpu_profile": "default",
            "boot_arguments": boot_arguments,
        },
        "provenance": {
            "started_at_utc": _utc_now(),
            "finished_at_utc": None,
            "host": host_name(),
            "platform": platform.platform(),
            "python": {
                "executable": sys.executable,
                "implementation": sys.implementation.name,
                "version": platform.python_version(),
            },
            "git": None,
            "mcp_executable": None,
            "harness_sources": None,
            "host_packages": None,
            "qemu_host_candidate": None,
            "iso": None,
        },
        "mcp": {
            "requested_protocol": PROTOCOL_VERSION,
            "negotiated_protocol": None,
            "server_info": None,
            "required_tools": sorted(REQUIRED_TOOL_PROPERTIES),
            "observed_tools": [],
            "required_schema_sha256": None,
            "request_count": 0,
        },
        "vm": {
            "name": name,
            "qemu_pid": None,
            "qemu_pid_known": False,
            "qemu_in_containment": False,
            "serial_source_path": None,
            "qemu_log_source_path": None,
        },
        "observations": {
            "preflight_registry_empty": False,
            "prompt_wait": None,
            "ping_sent": False,
            "pong_wait": None,
            "fresh_pong_record": False,
            "qmp_status": None,
            "serial_snapshot_complete": False,
            "serial_capture_scope": "none",
            "serial_complete_through_termination": False,
            "serial_capture": None,
            "qemu_log_capture": None,
            "failure_screenshot": False,
        },
        "termination": {
            "stage": "not-started",
            "primary_outcome": "INFRA_ERROR",
            "cleanup_status": "NOT_STARTED",
            "stop_rpc_succeeded": False,
            "stop_result": None,
            "registry_empty": None,
            "qemu_process_exited": None,
            "server_process_exited": False,
            "server_exit_code": None,
            "containment_kind": "windows-job" if os.name == "nt" else "posix-process-group",
            "containment_drained": False,
            "cleanup_recovered_by_containment": False,
            "owned_temp_removed": False,
            "cleanup_verified": False,
            "reader_drained": False,
            "duration_ms": None,
        },
        "artifacts": {},
    }


def run_qemu_mcp_diagnostic(
    mcp_server: str,
    timeout_sec: int = DEFAULT_QEMU_TIMEOUT,
    skip_build: bool = False,
    *,
    _run_id: str | None = None,
    _artifact_root: Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    run_id = _run_id or _new_run_id()
    _validate_run_id(run_id)
    vm_name = f"aios-diagnostic-{run_id}"
    root = _artifact_root or DIAGNOSTIC_ROOT
    run_dir = _create_run_dir(run_id, root)
    summary_path = run_dir / "summary.json"
    serial_artifact = run_dir / "serial.log"
    qemu_log_artifact = run_dir / "qemu.log"
    transcript_path = run_dir / "mcp-transcript.jsonl"
    stderr_path = run_dir / "stderr.log"
    screenshot_path = run_dir / "failure.png"
    for path in (serial_artifact, qemu_log_artifact, transcript_path, stderr_path):
        path.write_bytes(b"")

    summary = _initial_summary(run_id, vm_name, timeout_sec, skip_build)
    _atomic_write_json(summary_path, summary)

    reasons: list[str] = []
    primary_outcome = "INFRA_ERROR"
    primary_error: dict[str, str] | None = None
    interrupted = False
    client: McpStdioClient | None = None
    owned_temp: Path | None = None
    owned_temp_base: Path | None = None
    boot_attempted = False
    boot_response_received = False
    qemu_pid: int | None = None
    serial_source: Path | None = None
    qemu_log_source: Path | None = None
    normal_cleanup_failed = False

    def stage(value: str) -> None:
        summary["termination"]["stage"] = value

    def add_reason(value: str) -> None:
        if value not in reasons:
            reasons.append(value)

    def fail(outcome: str, reason: str, detail: str) -> None:
        raise DiagnosticFailure(outcome, reason, detail)

    try:
        if type(timeout_sec) is not int or not 1 <= timeout_sec <= MAX_COMMAND_TIMEOUT_SEC:
            fail(
                "INFRA_ERROR",
                "invalid-timeout",
                f"timeout must be an integer in [1, {MAX_COMMAND_TIMEOUT_SEC}]",
            )

        stage("preflight")
        summary["provenance"]["git"] = _git_metadata()
        summary["provenance"]["harness_sources"] = [
            _file_entry(path.resolve())
            for path in (
                Path(__file__),
                REPO_ROOT / "tools" / "testkit" / "aios-testkit.py",
            )
        ]
        summary["provenance"]["host_packages"] = _package_versions()
        summary["provenance"]["qemu_host_candidate"] = _qemu_provenance()
        summary["provenance"]["qemu_host_candidate_is_vm_identity"] = False
        server_path = Path(mcp_server)
        if not server_path.is_absolute():
            fail("INFRA_ERROR", "mcp-server-not-absolute", "--mcp-server must be absolute")
        try:
            server_path = server_path.resolve(strict=True)
        except OSError as exc:
            fail("INFRA_ERROR", "mcp-server-missing", str(exc))
        if not server_path.is_file():
            fail("INFRA_ERROR", "mcp-server-not-file", str(server_path))
        summary["provenance"]["mcp_executable"] = _file_entry(server_path)

        if not skip_build:
            stage("kernel-build")
            build_kernel_iso(timeout_sec, "default")

        stage("iso-preflight")
        iso_path = (BUILD_DIR / "aios-kernel.iso").resolve(strict=True)
        if not iso_path.is_file():
            fail("INFRA_ERROR", "iso-not-file", str(iso_path))
        iso_entry = _file_entry(iso_path)
        iso_entry.update(
            {
                "freshness": "unknown" if skip_build else "built-this-run",
                "built_from_current_head": False if skip_build else None,
            }
        )
        summary["provenance"]["iso"] = iso_entry
        summary["request"]["boot_arguments"]["iso"] = str(iso_path)
        _atomic_write_json(summary_path, summary)

        owned_temp, owned_temp_base = _create_owned_temp(run_id)
        server_env = os.environ.copy()
        for key in ("TMP", "TEMP", "TMPDIR"):
            server_env[key] = str(owned_temp)

        stage("mcp-launch")
        client = McpStdioClient(
            server_path,
            transcript_path,
            stderr_path,
            server_env,
        )

        stage("mcp-initialize")
        initialize_result = client.initialize()
        summary["mcp"]["negotiated_protocol"] = initialize_result["protocolVersion"]
        summary["mcp"]["server_info"] = initialize_result["serverInfo"]

        stage("mcp-tools")
        tools, schema_fingerprint = client.list_tools()
        summary["mcp"]["observed_tools"] = sorted(tools)
        summary["mcp"]["required_schema_sha256"] = schema_fingerprint

        stage("registry-preflight")
        initial_list = client.text_tool("qemu_list", {}, LIST_TIMEOUT_SEC)
        if initial_list != "no VMs":
            fail(
                "INFRA_ERROR",
                "registry-not-empty",
                "dedicated qemu-mcp server did not start with an empty registry",
            )
        summary["observations"]["preflight_registry_empty"] = True

        stage("qemu-boot")
        boot_attempted = True
        boot_text = client.text_tool(
            "qemu_boot",
            dict(summary["request"]["boot_arguments"]),
            BOOT_HOST_TIMEOUT_SEC,
        )
        boot_response_received = True
        qemu_pid, serial_source, qemu_log_source = _parse_boot_result(
            boot_text,
            vm_name,
            owned_temp,
        )
        summary["vm"].update(
            {
                "qemu_pid": qemu_pid,
                "qemu_pid_known": True,
                "serial_source_path": str(serial_source),
                "qemu_log_source_path": str(qemu_log_source),
            }
        )
        if not client.containment.contains_pid(qemu_pid):
            fail(
                "INFRA_ERROR",
                "qemu-outside-containment",
                "owned QEMU PID is not inside the dedicated containment boundary",
            )
        summary["vm"]["qemu_in_containment"] = True

        stage("shell-prompt")
        try:
            prompt_text = client.text_tool(
                "qemu_wait_serial",
                {"name": vm_name, "text": SHELL_PROMPT, "timeout_s": timeout_sec},
                timeout_sec + 5,
            )
        except McpToolError as exc:
            if "has exited (code" in exc.text:
                fail("VM_EXITED", "vm-exited-before-prompt", exc.text)
            raise
        prompt_status = _wait_status(prompt_text)
        summary["observations"]["prompt_wait"] = prompt_status
        if prompt_status == "TIMEOUT":
            fail("TIMEOUT", "shell-prompt-timeout", "AIOS shell prompt was not observed")
        if prompt_status == "VM EXITED":
            fail("VM_EXITED", "vm-exited-before-prompt", "VM exited before the shell prompt")

        pre_ping_raw = _read_bounded(serial_source, MAX_LOG_BYTES, "serial.log")
        pre_ping_offset = len(pre_ping_raw)

        stage("shell-ping-send")
        send_text = client.text_tool(
            "qemu_serial_send",
            {"name": vm_name, "text": "ping\n"},
            SERIAL_SEND_TIMEOUT_SEC,
        )
        expected_send = f"sent 5 bytes to {vm_name!r}'s serial console"
        if send_text != expected_send:
            fail("INFRA_ERROR", "serial-send-drift", "unexpected qemu_serial_send result")
        summary["observations"]["ping_sent"] = True

        stage("shell-pong")
        try:
            pong_text = client.text_tool(
                "qemu_wait_serial",
                {
                    "name": vm_name,
                    "text": "[STATE] pong ticks=",
                    "timeout_s": COMMAND_TIMEOUT_SEC,
                },
                COMMAND_TIMEOUT_SEC + 5,
            )
        except McpToolError as exc:
            if "has exited (code" in exc.text:
                fail("VM_EXITED", "vm-exited-before-pong", exc.text)
            raise
        pong_status = _wait_status(pong_text)
        summary["observations"]["pong_wait"] = pong_status
        if pong_status == "TIMEOUT":
            fail("TIMEOUT", "shell-pong-timeout", "fresh ping response was not observed")
        if pong_status == "VM EXITED":
            fail("VM_EXITED", "vm-exited-before-pong", "VM exited before the ping response")
        post_ping_raw = _read_bounded(serial_source, MAX_LOG_BYTES, "serial.log")
        if post_ping_raw[:pre_ping_offset] != pre_ping_raw:
            fail("INFRA_ERROR", "serial-truncated", "serial log was truncated or replaced")
        if not _fresh_pong_record(post_ping_raw[pre_ping_offset:]):
            fail(
                "ABORTED",
                "fresh-pong-record-mismatch",
                "wait returned FOUND without exactly one fresh canonical pong record",
            )
        summary["observations"]["fresh_pong_record"] = True

        stage("qmp-query-status")
        qmp_text = client.text_tool(
            "qemu_qmp",
            {"name": vm_name, "command": "query-status"},
            QMP_HOST_TIMEOUT_SEC,
        )
        qmp_status = _strict_json_value(qmp_text, "query-status result")
        if type(qmp_status) is not dict or type(qmp_status.get("status")) is not str:
            fail("INFRA_ERROR", "qmp-status-malformed", "query-status was not an object")
        summary["observations"]["qmp_status"] = qmp_status
        if qmp_status["status"] != "running" or (
            "running" in qmp_status and qmp_status["running"] is not True
        ):
            fail(
                "ABORTED",
                "qmp-not-running",
                f"QEMU query-status was {qmp_status.get('status')!r}",
            )

        primary_outcome = "OBSERVED"
        add_reason("interactive-sequence-observed")
    except KeyboardInterrupt:
        interrupted = True
        primary_outcome = "ABORTED"
        add_reason("user-interrupt")
        primary_error = {"type": "KeyboardInterrupt", "message": "interrupted by user"}
    except DiagnosticFailure as exc:
        primary_outcome = exc.outcome
        add_reason(exc.reason)
        primary_error = {"type": type(exc).__name__, "message": exc.detail}
    except McpHostTimeout as exc:
        primary_outcome = "INFRA_ERROR"
        add_reason("mcp-host-timeout")
        primary_error = {"type": type(exc).__name__, "message": str(exc)}
    except (McpProtocolError, McpToolError) as exc:
        primary_outcome = "INFRA_ERROR"
        add_reason("mcp-protocol-or-tool-error")
        primary_error = {"type": type(exc).__name__, "message": str(exc)}
    except Exception as exc:
        primary_outcome = "INFRA_ERROR"
        add_reason("unexpected-infrastructure-error")
        primary_error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        summary["termination"]["primary_outcome"] = primary_outcome
        if primary_error is not None:
            summary["error"] = primary_error

        # Preserve the live serial/QEMU-log snapshot before qemu_stop or
        # qemu_list can delete the server-owned workdir.
        if serial_source is not None:
            try:
                summary["observations"]["serial_capture"] = _snapshot_source_file(
                    serial_source,
                    serial_artifact,
                    MAX_LOG_BYTES,
                )
                summary["observations"]["serial_snapshot_complete"] = not summary[
                    "observations"
                ]["serial_capture"]["source_changed_during_capture"]
                if not summary["observations"]["serial_snapshot_complete"]:
                    add_reason("serial-snapshot-unstable")
                    if primary_outcome == "OBSERVED":
                        primary_outcome = "INFRA_ERROR"
                summary["observations"]["serial_capture_scope"] = (
                    "full-source-through-pre-stop-snapshot"
                )
            except Exception as exc:
                add_reason("serial-snapshot-error")
                if primary_outcome == "OBSERVED":
                    primary_outcome = "INFRA_ERROR"
                summary["observations"]["serial_capture_error"] = str(exc)
        elif boot_response_received and client is not None and client.stream_usable:
            try:
                tail = client.text_tool(
                    "qemu_serial",
                    {"name": vm_name, "tail_lines": 50},
                    QMP_HOST_TIMEOUT_SEC,
                )
                serial_artifact.write_text(tail, encoding="utf-8", newline="\n")
                summary["observations"]["serial_capture_scope"] = "mcp-tail-fallback"
            except Exception as exc:
                add_reason("serial-tail-unavailable")
                summary["observations"]["serial_capture_error"] = str(exc)

        if qemu_log_source is not None:
            try:
                summary["observations"]["qemu_log_capture"] = _snapshot_source_file(
                    qemu_log_source,
                    qemu_log_artifact,
                    MAX_LOG_BYTES,
                )
            except Exception as exc:
                add_reason("qemu-log-snapshot-error")
                summary["observations"]["qemu_log_capture_error"] = str(exc)

        if (
            primary_outcome != "OBSERVED"
            and primary_outcome != "VM_EXITED"
            and boot_response_received
            and client is not None
            and client.stream_usable
        ):
            try:
                screenshot_path.write_bytes(
                    client.screenshot_tool(vm_name, SCREENSHOT_HOST_TIMEOUT_SEC)
                )
                summary["observations"]["failure_screenshot"] = True
            except Exception as exc:
                add_reason("failure-screenshot-unavailable")
                summary["observations"]["failure_screenshot_error"] = str(exc)

        summary["termination"]["stage"] = "cleanup"
        if client is not None:
            if boot_response_received and client.stream_usable:
                try:
                    stop_text = client.text_tool(
                        "qemu_stop",
                        {"name": vm_name, "force": True},
                        STOP_HOST_TIMEOUT_SEC,
                    )
                    if not re.fullmatch(
                        rf"VM {re.escape(repr(vm_name))} stopped "
                        r"\((?:killed|already exited|ACPI powerdown|ACPI ignored, killed)\)",
                        stop_text,
                    ):
                        raise McpProtocolError("qemu_stop result has an unexpected shape")
                    summary["termination"]["stop_rpc_succeeded"] = True
                    summary["termination"]["stop_result"] = stop_text
                except Exception as exc:
                    normal_cleanup_failed = True
                    add_reason("qemu-stop-error")
                    summary["termination"]["stop_error"] = str(exc)

                if client.stream_usable:
                    try:
                        final_list = client.text_tool(
                            "qemu_list",
                            {},
                            LIST_TIMEOUT_SEC,
                        )
                        summary["termination"]["registry_empty"] = final_list == "no VMs"
                        if final_list != "no VMs":
                            normal_cleanup_failed = True
                            add_reason("qemu-registry-not-empty")
                    except Exception as exc:
                        normal_cleanup_failed = True
                        add_reason("qemu-list-error")
                        summary["termination"]["registry_empty"] = False
                        summary["termination"]["list_error"] = str(exc)
            elif boot_response_received:
                normal_cleanup_failed = True
                add_reason("qemu-stop-stream-unusable")

            if qemu_pid is not None and summary["vm"]["qemu_in_containment"]:
                try:
                    exited = _wait_until(
                        lambda: client.containment.pid_exited(qemu_pid),
                        QEMU_EXIT_TIMEOUT_SEC,
                    )
                except Exception:
                    exited = False
                summary["termination"]["qemu_process_exited"] = exited
                if boot_response_received and not exited:
                    normal_cleanup_failed = True
                    add_reason("qemu-process-still-live")

            graceful_server_exit = False
            if client.stream_usable:
                try:
                    graceful_server_exit = client.close_stdin_and_wait(
                        SERVER_EXIT_TIMEOUT_SEC
                    )
                except Exception as exc:
                    add_reason("server-shutdown-error")
                    summary["termination"]["server_shutdown_error"] = str(exc)
            try:
                containment_empty = _wait_until(
                    client.containment.drained, CONTAINMENT_DRAIN_TIMEOUT_SEC
                ) if graceful_server_exit else client.containment.drained()
            except Exception:
                containment_empty = False
            if not graceful_server_exit or not containment_empty:
                summary["termination"]["cleanup_recovered_by_containment"] = True
                try:
                    recovered = client.force_containment_cleanup(
                        CONTAINMENT_DRAIN_TIMEOUT_SEC
                    )
                except Exception as exc:
                    recovered = False
                    summary["termination"]["containment_error"] = str(exc)
                if boot_attempted:
                    normal_cleanup_failed = True
                if not recovered:
                    add_reason("containment-drain-failed")

            summary["termination"]["reader_drained"] = client.drain_readers()
            reader_errors = list(client.reader_errors)
            # A late extra response must not silently escape ID validation.
            while not client._messages.empty():
                try:
                    kind, pending = client._messages.get_nowait()
                except queue.Empty:
                    break
                if kind == "message":
                    try:
                        _validate_rpc_envelope(pending)
                    except McpProtocolError as exc:
                        reader_errors.append(str(exc))
                if kind == "error" or (
                    kind == "message" and "id" in pending
                ):
                    reader_errors.append("unconsumed or invalid MCP response during shutdown")
            if reader_errors or not summary["termination"]["reader_drained"]:
                normal_cleanup_failed = True
                add_reason("mcp-reader-error")
                summary["termination"]["reader_errors"] = reader_errors
            if client.proc is not None:
                summary["termination"]["server_exit_code"] = client.proc.poll()
                summary["termination"]["server_process_exited"] = (
                    client.proc.poll() is not None
                )
                if graceful_server_exit and client.proc.poll() != 0:
                    normal_cleanup_failed = True
                    add_reason("mcp-server-nonzero-exit")
            try:
                summary["termination"]["containment_drained"] = (
                    client.containment.drained()
                )
            except Exception as exc:
                summary["termination"]["containment_error"] = str(exc)
                summary["termination"]["containment_drained"] = False
            if qemu_pid is not None and summary["vm"]["qemu_in_containment"]:
                # Recompute after recovery; a pre-recovery live observation is
                # not the final state. The Job/process-group is the ownership
                # boundary, so no process-name or arbitrary PID kill is used.
                summary["termination"]["qemu_process_exited"] = bool(
                    summary["termination"]["containment_drained"]
                )
            client.close_containment()

        if owned_temp is not None and owned_temp_base is not None:
            if summary["termination"]["containment_drained"] or client is None:
                summary["termination"]["owned_temp_removed"] = _remove_owned_temp(
                    owned_temp,
                    owned_temp_base,
                    run_id,
                )
            if not summary["termination"]["owned_temp_removed"]:
                add_reason("owned-temp-cleanup-failed")
                normal_cleanup_failed = True

        cleanup_requirements = [
            bool(summary["termination"]["server_process_exited"])
            if client is not None
            else True,
            bool(summary["termination"]["containment_drained"])
            if client is not None
            else True,
            bool(summary["termination"]["reader_drained"])
            if client is not None
            else True,
            bool(summary["termination"]["owned_temp_removed"])
            if owned_temp is not None
            else True,
        ]
        if qemu_pid is not None:
            cleanup_requirements.append(
                bool(summary["termination"]["qemu_process_exited"])
            )
        # Physical cleanup can be verified by containment even if a broken RPC
        # prevented normal stop/registry confirmation. Such recovery remains a
        # CLEANUP_ERROR, never OBSERVED, with the individual RPC facts preserved.
        cleanup_verified = all(cleanup_requirements)
        summary["termination"]["cleanup_verified"] = cleanup_verified

        if not cleanup_verified or normal_cleanup_failed:
            final_outcome = "CLEANUP_ERROR"
            if "cleanup-verification-failed" not in reasons:
                add_reason("cleanup-verification-failed")
            summary["termination"]["cleanup_status"] = (
                "RECOVERED"
                if cleanup_verified
                and summary["termination"]["cleanup_recovered_by_containment"]
                else "FAILED"
            )
        else:
            final_outcome = primary_outcome
            summary["termination"]["cleanup_status"] = "CLEAN"

        summary["termination"]["primary_outcome"] = primary_outcome
        summary["outcome"] = final_outcome
        summary["reasons"] = reasons or ["unspecified"]
        summary["mcp"]["request_count"] = client._request_id if client is not None else 0
        summary["provenance"]["finished_at_utc"] = _utc_now()
        summary["termination"]["duration_ms"] = int(
            (time.monotonic() - started) * 1000
        )

        artifact_paths = [
            serial_artifact,
            qemu_log_artifact,
            transcript_path,
            stderr_path,
        ]
        if screenshot_path.is_file():
            artifact_paths.append(screenshot_path)
        summary["artifacts"] = {
            path.name: _file_entry(path, run_dir) for path in artifact_paths
        }
        _atomic_write_json(summary_path, summary)

    print_step(
        f"qemu-mcp diagnostic {summary['outcome']} -> {run_dir} "
        "(diagnostic_only=true authoritative=false)"
    )
    if interrupted:
        raise KeyboardInterrupt
    if summary["outcome"] != "OBSERVED":
        raise QemuMcpDiagnosticError(str(summary["outcome"]), run_dir)
    return summary
