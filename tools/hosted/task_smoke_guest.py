#!/usr/bin/env python3
"""Run one CLI-owned, two-request actual-model scenario in a development guest.

The driver never submits replacement questions to manufacture cancellation.
Raw process/session evidence and a failed summary survive every handled failure.
It does not install code into, or claim acceptance of, an operating image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from task_smoke_contract import CANCEL_QUESTION, LIMITS, PREFIX, PROMPT, SCENARIO, SPACE_QUESTION, SUFFIX
from console_output_contract import _response as render_command

ROOT = Path(__file__).resolve().parents[2]


class SmokeFailure(RuntimeError):
    pass


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SmokeFailure("event_duplicate_key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(SmokeFailure("event_nonfinite")))
    if type(value) is not dict:
        raise SmokeFailure("event_not_object")
    return value


class EventJournal:
    """Read only complete, append-only JSON records; human output is no event."""
    def __init__(self, path):
        self.path, self.raw, self.events, self.lines = Path(path), b"", [], []
        self.session_id = None

    def refresh(self):
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(LIMITS["stream_bytes"] + 1)
        except FileNotFoundError:
            return
        if len(raw) > LIMITS["stream_bytes"] or not raw.startswith(self.raw):
            raise SmokeFailure("event_limit_or_rewrite")
        complete = raw[:raw.rfind(b"\n") + 1]
        for line in complete[len(self.raw):].splitlines(keepends=True):
            event = decode(line)
            if (type(event.get("schema_version")) is not int or event["schema_version"] != 10
                    or type(event.get("sequence")) is not int or event["sequence"] != len(self.events) + 1):
                raise SmokeFailure("event_schema_or_sequence")
            if not self.events:
                if event.get("event") != "START":
                    raise SmokeFailure("event_missing_start")
                self.session_id = event.get("session_id")
                try:
                    parsed = uuid.UUID(self.session_id)
                except (ValueError, AttributeError, TypeError) as exc:
                    raise SmokeFailure("event_session_id") from exc
                if str(parsed) != self.session_id or not parsed.int:
                    raise SmokeFailure("event_session_id")
            elif event.get("session_id") != self.session_id:
                raise SmokeFailure("event_session_changed")
            self.events.append(event)
            self.lines.append(line)
        self.raw = complete

    def command(self, index, command):
        self.refresh()
        if len(self.events) <= index:
            return None
        event = self.events[index]
        fields = command.split()
        if (event.get("event") != "COMMAND" or type(event.get("data")) is not dict
                or event["data"].get("name") != fields[0] or event["data"].get("args") != fields[1:]):
            raise SmokeFailure("event_command_mismatch")
        result = event["data"].get("result")
        if type(result) is not dict:
            raise SmokeFailure("event_command_result")
        if fields[0] == "task" and result.get("request_id") != fields[2]:
            raise SmokeFailure("event_request_id")
        if fields[0] == "ask":
            request_id = result.get("request_id")
            try:
                parsed = uuid.UUID(request_id)
            except (ValueError, AttributeError, TypeError) as exc:
                raise SmokeFailure("event_request_id") from exc
            if str(parsed) != request_id or not parsed.int:
                raise SmokeFailure("event_request_id")
        return event


class Capture:
    """Drain stdout/stderr continuously, including after process termination."""
    def __init__(self, stream, path):
        self.stream, self.path = stream, Path(path)
        self.raw, self.error = bytearray(), None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            with self.path.open("wb", buffering=0) as output:
                while True:
                    chunk = self.stream.read(65536)
                    if not chunk:
                        break
                    with self.lock:
                        available = LIMITS["stream_bytes"] - len(self.raw)
                        retained = chunk[:available]
                        output.write(retained)
                        self.raw.extend(retained)
                        if len(chunk) > available:
                            self.error = "stream_limit:" + self.path.name
        except (OSError, ValueError) as exc:
            self.error = "stream_read:" + self.path.name + ":" + type(exc).__name__

    def snapshot(self):
        with self.lock:
            return bytes(self.raw)

    def finish(self):
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise SmokeFailure("reader_not_drained:" + self.path.name)
        if self.error:
            raise SmokeFailure(self.error)


class Console:
    def __init__(self, process, destination, report, stdout, stderr):
        self.process, self.destination, self.report = process, destination, report
        self.stdout, self.stderr = stdout, stderr
        self.journal = EventJournal(destination / "session/session.events.jsonl")
        self.output_end, self.stdin_end = 0, 0

    def check(self):
        if time.monotonic_ns() - self.report["started_monotonic_ns"] >= LIMITS["total_seconds"] * 1_000_000_000:
            raise TimeoutError("scenario_total_deadline")
        if self.stdout.error or self.stderr.error:
            raise SmokeFailure(self.stdout.error or self.stderr.error)

    def pause(self, seconds):
        self.check()
        remaining = LIMITS["total_seconds"] - (time.monotonic_ns() - self.report["started_monotonic_ns"]) / 1e9
        if remaining <= seconds:
            raise TimeoutError("scenario_total_deadline")
        time.sleep(seconds)
        self.check()

    def initial(self):
        deadline = time.monotonic() + LIMITS["command_seconds"]
        while time.monotonic() < deadline:
            self.check()
            self.journal.refresh()
            raw = self.stdout.snapshot()
            if len(self.journal.events) == 1 and PROMPT.search(raw):
                self.output_end = len(raw)
                self.report["initial_prompt"] = {"event_sequence": 1,
                    "observed_monotonic_ns": time.monotonic_ns(), "stdout_end": len(raw)}
                return
            if self.process.poll() is not None:
                raise SmokeFailure("console_exited_before_initial_prompt")
            time.sleep(0.05)
        raise TimeoutError("initial_prompt_timeout")

    def exchange(self, command):
        self.check()
        if len(self.report["commands"]) >= 200:
            raise SmokeFailure("scenario_command_limit")
        index = len(self.report["commands"]) + 1
        payload = (command + "\n").encode("utf-8")
        row = {"sequence": index + 1, "command": command, "request_id": None,
            "sent_monotonic_ns": time.monotonic_ns(), "event_observed_monotonic_ns": None,
            "completed_monotonic_ns": None, "stdin_start": self.stdin_end,
            "stdin_end": self.stdin_end + len(payload), "stdout_start": self.output_end,
            "stdout_end": None, "event_sha256": None, "completion": None}
        self.report["commands"].append(row)
        # Preserve attempted stdin even if a closed pipe rejects the write.
        with (self.destination / "stdin.log").open("ab") as stream:
            stream.write(payload)
        self.stdin_end += len(payload)
        self.process.stdin.write(payload)
        self.process.stdin.flush()
        deadline = time.monotonic() + LIMITS["command_seconds"]
        expected_output = None
        while time.monotonic() < deadline:
            self.check()
            event = self.journal.command(index, command)
            if event is not None and row["event_observed_monotonic_ns"] is None:
                row["event_observed_monotonic_ns"] = time.monotonic_ns()
                row["event_sha256"] = digest(self.journal.lines[index])
                row["request_id"] = event["data"]["result"].get("request_id")
                # The exact independently rendered response anchors the following
                # prompt. An earlier prompt-like string cannot advance the script.
                expected_output = (render_command(event["data"], {}, self.journal.events[0]["data"], index - 1)
                                   + ("" if command == "exit" else "aios> ")).encode("utf-8")
            raw = self.stdout.snapshot()
            exited = self.process.poll() is not None
            if event is not None:
                chunk = raw[self.output_end:]
                if not expected_output.startswith(chunk) and chunk != expected_output:
                    raise SmokeFailure("command_output_mismatch")
                if command == "exit" and exited:
                    self.stdout.finish()
                    self.stderr.finish()
                    self.journal.refresh()
                    if (len(self.journal.events) != index + 2
                            or self.journal.events[-1]["event"] != "STOP"):
                        raise SmokeFailure("exit_missing_stop")
                    raw = self.stdout.snapshot()
                    if raw[self.output_end:] != expected_output:
                        raise SmokeFailure("command_output_mismatch")
                elif command == "exit" or chunk != expected_output:
                    if exited:
                        raise SmokeFailure("console_exited_before_command_prompt")
                    time.sleep(0.05)
                    continue
                self.output_end = len(raw)
                row.update(completed_monotonic_ns=time.monotonic_ns(), stdout_end=self.output_end,
                           completion="exit" if command == "exit" else "prompt")
                print("[task-smoke] " + command.split()[0] + " seq=" + str(row["sequence"]), flush=True)
                return event["data"]
            if exited:
                # Drain once before deciding that the final COMMAND is absent.
                self.stdout.finish()
                self.stderr.finish()
                if self.journal.command(index, command) is None:
                    raise SmokeFailure("console_exited_before_command_event")
            time.sleep(0.05)
        raise TimeoutError("command_timeout:" + command.split()[0])


def run_scenario(exchange, *, pause=time.sleep, clock=time.monotonic):
    """Bounded public-command driver; tests replace transport, never the model."""
    def call(command):
        row = exchange(command)
        if row["outcome"] != "OK":
            raise SmokeFailure("command_failed:" + command + ":" + str(row["result"].get("error")))
        return row["result"]

    def admitted(question):
        value = call("ask " + question)
        request_id = value["request_id"]
        if value["task"]["request_id"] != request_id or value["task"]["phase"] != "ACCEPTED":
            raise SmokeFailure("ask_not_accepted")
        return request_id

    def state(request_id):
        value = call("task status " + request_id)
        if value["task"]["request_id"] != request_id:
            raise SmokeFailure("status_request_mismatch")
        return value["task"]

    for command in PREFIX:
        call(command)
    first = admitted(SPACE_QUESTION)
    deadline = clock() + LIMITS["answer_seconds"]
    for index in range(LIMITS["answer_polls"]):
        row = state(first)
        if row["phase"] == "FINISHED":
            if row["model_outcome"] != "ANSWERED":
                raise SmokeFailure("first_task_not_answered")
            break
        if row["phase"] not in ("ACCEPTED", "RUNNING"):
            raise SmokeFailure("first_task_unexpected_phase")
        if index == LIMITS["answer_polls"] - 1 or clock() + LIMITS["answer_poll_seconds"] > deadline:
            raise TimeoutError("first_task_deadline")
        pause(LIMITS["answer_poll_seconds"])
    call("task result " + first)
    call("space")
    second = admitted(CANCEL_QUESTION)
    if second == first:
        raise SmokeFailure("reused_request_id")
    for index in range(LIMITS["running_polls"]):
        row = state(second)
        if row["phase"] == "RUNNING":
            break
        if row["phase"] != "ACCEPTED":
            raise SmokeFailure("second_task_not_running_no_retry")
        if index == LIMITS["running_polls"] - 1:
            raise TimeoutError("second_task_not_running")
        pause(LIMITS["running_poll_seconds"])
    # This is the only cancellation call, immediately after RUNNING evidence.
    cancelled = call("task cancel " + second)
    if cancelled["task_control"]["cancel_outcome"] != "ACCEPTED":
        raise SmokeFailure("cancel_not_accepted")
    for index in range(LIMITS["cancel_polls"]):
        row = state(second)
        if row["phase"] == "FINISHED" and row["backend_stop"] is not None:
            if row["model_outcome"] != "UNKNOWN":
                raise SmokeFailure("cancelled_task_not_unknown")
            break
        if row["phase"] not in ("CANCEL_REQUESTED", "FINISHED"):
            raise SmokeFailure("cancel_unexpected_phase")
        if index == LIMITS["cancel_polls"] - 1:
            raise TimeoutError("cancel_terminal_timeout")
        pause(LIMITS["cancel_poll_seconds"])
    call("task result " + second)
    for command in SUFFIX:
        call(command)


def run(destination, *, command=None):
    """Return driver exit status; a new destination prevents stale PASS reuse."""
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("stdin.log", "stdout.log", "stderr.log"):
        (destination / name).write_bytes(b"")
    report = {"schema_version": 1, "scenario": SCENARIO, "capture_kind": "live", "source_only": True,
        "driver_source_sha256": digest(Path(__file__).read_bytes()), "cli_process_id": None,
        "started_monotonic_ns": time.monotonic_ns(), "finished_monotonic_ns": None,
        "outcome": "FAIL", "failure": "incomplete", "termination": "launch-failed", "timed_out": False,
        "limits": dict(LIMITS), "initial_prompt": None, "commands": [], "files": {}}
    save(destination / "task-smoke.json", report)
    save(destination / "execution.json", {"schema_version": 1, "mode": "smoke", "process_exit_code": None,
        "stdout_sha256": digest(b""), "stderr_sha256": digest(b""), "requested_commands": []})
    process, captures = None, []
    try:
        argv = command if command is not None else [sys.executable, str(ROOT / "hosted/linux/aios-console.py"),
            "--artifact-dir", str(destination / "session"), "--service-dir", "/tmp/aios-runtime",
            "--agent-dir", "/tmp/aios-agent", "--agent-config", "/tmp/aios-agent-config.json",
            "--backend-dir", "/tmp/aios-model-backend"]
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        report["cli_process_id"] = process.pid
        report["termination"] = "running"
        captures = [Capture(process.stdout, destination / "stdout.log"), Capture(process.stderr, destination / "stderr.log")]
        console = Console(process, destination, report, *captures)
        console.initial()
        run_scenario(console.exchange, pause=console.pause)
        if process.returncode != 0:
            raise SmokeFailure("console_exit_code:" + str(process.returncode))
        report.update(outcome="PASS", failure=None, termination="exit")
    except BaseException as exc:
        report.update(outcome="FAIL", failure=type(exc).__name__ + ":" + str(exc), timed_out=isinstance(exc, TimeoutError))
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    # Close this driver's own CLI only. MAIN/backend cleanup remains
                    # the outer guest runner's job; cleanup is never scenario PASS.
                    try:
                        process.stdin.close()
                        process.wait(timeout=3)
                        report["termination"] = "eof-after-failure"
                    except (OSError, subprocess.TimeoutExpired):
                        process.terminate()
                        report["termination"] = "driver-terminated"
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            report["termination"] = "driver-killed"
                            process.wait(timeout=10)
                if report["termination"] == "running":
                    report["termination"] = "unexpected-exit"
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                report.update(outcome="FAIL", failure=report["failure"] or "cleanup:" + str(exc))
            for capture in captures:
                try:
                    capture.finish()
                except SmokeFailure as exc:
                    report.update(outcome="FAIL", failure=report["failure"] or str(exc))
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except OSError as exc:
                        report.update(outcome="FAIL", failure=report["failure"] or "stream_close:" + str(exc))
        report["finished_monotonic_ns"] = time.monotonic_ns()
        for name in ("stdin.log", "stdout.log", "stderr.log"):
            raw = (destination / name).read_bytes()
            report["files"][name] = {"sha256": digest(raw), "bytes": len(raw)}
        save(destination / "execution.json", {"schema_version": 1, "mode": "smoke",
            "process_exit_code": None if process is None else process.returncode,
            "stdout_sha256": report["files"]["stdout.log"]["sha256"],
            "stderr_sha256": report["files"]["stderr.log"]["sha256"],
            "requested_commands": [row["command"] for row in report["commands"]]})
        save(destination / "task-smoke.json", report)
    return 0 if report["outcome"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    return run(args.destination)


if __name__ == "__main__":
    raise SystemExit(main())
