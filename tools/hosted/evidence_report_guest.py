#!/usr/bin/env python3
"""Run one bounded evidence report and feedback through a live AIOS Task CLI.

The first model answer can request one fixed report. This driver, not the
model, writes the report. It admits the second Task only after readback.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

from task_smoke_guest import Capture, Console, LIMITS, SmokeFailure, digest, save

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_agent.inference import CONTEXT_RESPONSE_TOKENS, request_body  # noqa: E402

SCENARIO = "evidence-report-feedback-v1"
FIRST_FORMAT = re.compile(
    r"comparison: (SAME|DIFFERENT|UNKNOWN)\neffect: (EXECUTED|NOT_EXECUTED|UNKNOWN)"
    r"\nnext: (SAVE|STOP)\nevidence: E0\n?\Z"
)
SECOND_FORMAT = re.compile(
    r"stored: (CONFIRMED|UNCONFIRMED)\nnext: (FINISH|STOP)\nevidence: E1\n?\Z"
)
PREFIX = ("backend start", "agent start", "room discover", "room bind", "space")
SUFFIX = ("agent stop", "backend stop", "exit")
MAX_QUESTION_CHARS = 2048
MAX_QUESTION_BYTES = 4096
MODEL_CONTEXT_TOKENS = 1024
TOKEN_DRIFT_MARGIN = 24
TOKENIZE_LIMIT = 65536


def require(value, reason):
    if not value:
        raise SmokeFailure(reason)


def exact_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate_json_key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(SmokeFailure("nonfinite_json")))


def preflight(destination, ordinal, question, context):
    """Use the live backend tokenizer on the exact hosted inference prompt."""
    require(type(question) is str and question.isascii() and "\n" not in question
            and "\r" not in question and question == " ".join(question.split()),
            "question_not_one_line_ascii")
    require(len("ask " + question) <= MAX_QUESTION_CHARS
            and len(question.encode("utf-8")) <= MAX_QUESTION_BYTES, "question_budget")
    body = request_body(question, space_context=context)
    request = exact_json(body)
    require(request["n_predict"] == CONTEXT_RESPONSE_TOKENS, "response_budget_drift")
    prompt = request["prompt"]
    token_request = json.dumps({"content": prompt, "add_special": True, "parse_special": True},
                               ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    require(len(token_request) < 16384, "token_request_budget")
    path = destination / ("tokenize-%d" % ordinal)
    path.mkdir()
    (path / "request.json").write_bytes(token_request)
    conn = http.client.HTTPConnection("127.0.0.1", 18081, timeout=30)
    try:
        conn.request("POST", "/tokenize", body=token_request,
                     headers={"Content-Type": "application/json", "Connection": "close"})
        response = conn.getresponse()
        status = response.status
        raw = response.read(TOKENIZE_LIMIT + 1)
    finally:
        conn.close()
    (path / "response.json").write_bytes(raw)
    require(status == 200 and len(raw) <= TOKENIZE_LIMIT, "tokenizer_unavailable_or_oversize")
    tokenized = exact_json(raw)
    tokens = tokenized.get("tokens")
    require(type(tokens) is list and tokens and all(type(item) is int and item >= 0 for item in tokens),
            "tokenizer_schema")
    # Admission rechecks the same observation at a later monotonic timestamp.
    # The exact prompt bytes (and potentially tokenization) may therefore move.
    require(len(tokens) + CONTEXT_RESPONSE_TOKENS + TOKEN_DRIFT_MARGIN <= MODEL_CONTEXT_TOKENS,
            "model_context_overflow")
    record = {"ordinal": ordinal, "question_sha256": digest(question.encode()),
              "request_sha256": digest(body), "request_bytes": len(body),
              "prompt_sha256": digest(prompt.encode()), "token_count": len(tokens),
              "response_budget": CONTEXT_RESPONSE_TOKENS, "context_size": MODEL_CONTEXT_TOKENS,
              "token_drift_margin": TOKEN_DRIFT_MARGIN,
              "tokenize_status": status, "token_request_sha256": digest(token_request),
              "token_response_sha256": digest(raw)}
    save(destination / ("preflight-%d.json" % ordinal), record)
    return record


def question_two(first_id, action_id, writer, expected_sha, readback_sha, count):
    require(expected_sha == readback_sha, "report_readback_mismatch")
    return (f"E1 first_task_uuid={first_id} action_id={action_id} "
            f"writer=driver(uid={writer['uid']},pid={writer['pid']}) "
            f"report_created=1 readback_sha256={readback_sha} "
            f"reread_bytes={count} matches_expected=1 write_error=none "
            "new_readout_world_effects=0. Was this driver report stored? Finish if confirmed. "
            "This is driver storage, not research World action. Reply three lines: "
            "stored: CONFIRMED|UNCONFIRMED; next: FINISH|STOP; evidence: E1.")


def finish_task(call, request_id, label, pause, clock):
    deadline = clock() + LIMITS["answer_seconds"]
    for index in range(LIMITS["answer_polls"]):
        state = call("task status " + request_id)["task"]
        require(state["request_id"] == request_id, label + "_status_id")
        if state["phase"] == "FINISHED":
            require(state["model_outcome"] == "ANSWERED", label + "_not_answered")
            break
        require(state["phase"] in ("ACCEPTED", "RUNNING"), label + "_phase")
        if index == LIMITS["answer_polls"] - 1 or clock() + LIMITS["answer_poll_seconds"] > deadline:
            raise TimeoutError(label + "_task_deadline")
        pause(LIMITS["answer_poll_seconds"])
    result = call("task result " + request_id)["task"]
    require(result["request_id"] == request_id and result["phase"] == "FINISHED"
            and result["model_outcome"] == "ANSWERED", label + "_result")
    receipt = result["inference_receipt"]
    require(type(receipt) is dict and receipt["outcome"] == "OK"
            and receipt["request_id"] == request_id and type(receipt["content"]) is str,
            label + "_receipt")
    return result, receipt


def check_admitted_input(state, receipt, question, observed):
    """Join a preflight observation to the actual Task without assuming equal timestamps."""
    current = state["space_context"]
    require(current["validity"] == "CURRENT"
            and current["observation"] == observed["observation"]
            and current["consumer"] == observed["consumer"]
            and receipt["space_context"] == current
            and state["user_prompt"] == question == receipt["user_prompt"],
            "admitted_context_or_question_changed")
    raw = state["request_body"].encode("utf-8")
    require(digest(raw) == state["request_sha256"] == receipt["request_sha256"]
            and receipt["request_body"] == state["request_body"],
            "admitted_request_body_changed")
    response = exact_json(receipt["response_body"].encode("utf-8"))
    count = response.get("tokens_evaluated")
    require(type(count) is int and count + CONTEXT_RESPONSE_TOKENS <= MODEL_CONTEXT_TOKENS
            and response.get("truncated") is False, "actual_context_or_completion_invalid")
    return {"request_sha256": state["request_sha256"], "actual_prompt_tokens": count}


def create_report(destination, e0, first_id, answer_sha, first_fields):
    """One exclusive, fixed-path write. Return only observed readback evidence."""
    require(first_fields == ("SAME", "NOT_EXECUTED", "SAVE"), "first_report_not_truthful")
    raw = (f"AIOS evidence report v1\nrun_id={e0['run_id']}\nrequest_id={first_id}\n"
           f"response_sha256={answer_sha}\ncomparison=SAME\neffect=NOT_EXECUTED\nnext=SAVE\n").encode()
    target = destination / "saved-report.txt"
    require(not target.exists() and not target.is_symlink(), "report_target_exists")
    with target.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    info = target.lstat()
    require(stat.S_ISREG(info.st_mode) and not target.is_symlink(), "report_not_regular")
    reread = target.read_bytes()
    expected_sha, readback_sha = digest(raw), digest(reread)
    require(reread == raw and readback_sha == expected_sha, "report_readback_mismatch")
    return expected_sha, readback_sha, len(reread)


def run_scenario(console, destination, plan, report):
    def call(command):
        row = console.exchange(command)
        require(row["outcome"] == "OK", "command_failed:" + command.split()[0] + ":"
                + str(row["result"].get("error")))
        return row["result"]

    def admit(question):
        value = call("ask " + question)
        request_id = value["request_id"]
        require(value["task"]["request_id"] == request_id and value["task"]["phase"] == "ACCEPTED",
                "ask_not_accepted")
        report["task_count"] += 1
        return request_id

    for command in PREFIX:
        result = call(command)
    context = result["space_context"]
    require(type(context) is dict and context.get("validity") == "CURRENT", "first_context_not_current")
    question1 = plan["question1"]
    preflight(destination, 1, question1, context)
    first_id = admit(question1)
    report["first_request_id"] = first_id
    first_state, first_receipt = finish_task(call, first_id, "first", console.pause, time.monotonic)
    report["first_actual_input"] = check_admitted_input(first_state, first_receipt, question1, context)
    answer1 = first_receipt["content"]
    report["first_content_sha256"] = digest(answer1.encode("utf-8"))
    match = FIRST_FORMAT.fullmatch(answer1)
    require(match is not None, "first_response_format")
    fields = match.groups()
    report["first_fields"] = fields
    require(fields == ("SAME", "NOT_EXECUTED", "SAVE"), "first_response_incorrect_or_stop")
    report["action_id"] = str(uuid.uuid4())
    report["writer"] = {"uid": os.getuid(), "pid": os.getpid()}
    expected, reread, count = create_report(destination, plan["e0"], first_id,
                                              report["first_content_sha256"], fields)
    report.update(report_expected_sha256=expected, report_readback_sha256=reread,
                  report_bytes=count, stage="REPORT_READ_BACK")
    save(destination / "evidence-report.json", report)

    context2 = call("space")["space_context"]
    require(type(context2) is dict and context2.get("validity") == "CURRENT",
            "second_context_not_current")
    question2 = question_two(first_id, report["action_id"], report["writer"], expected, reread, count)
    report["question2"] = question2
    preflight(destination, 2, question2, context2)
    second_id = admit(question2)
    report["second_request_id"] = second_id
    second_state, second_receipt = finish_task(call, second_id, "second", console.pause, time.monotonic)
    require(second_id != first_id, "reused_request_id")
    report["second_actual_input"] = check_admitted_input(second_state, second_receipt, question2, context2)
    answer2 = second_receipt["content"]
    report["second_content_sha256"] = digest(answer2.encode("utf-8"))
    match = SECOND_FORMAT.fullmatch(answer2)
    require(match is not None, "second_response_format")
    report["second_fields"] = match.groups()
    require(match.groups() == ("CONFIRMED", "FINISH"), "second_response_incorrect")
    report["stage"] = "SECOND_TASK_VERIFIED"


def run(destination, plan_path, *, command=None):
    destination = Path(destination)
    plan_path = Path(plan_path)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("stdin.log", "stdout.log", "stderr.log"):
        (destination / name).write_bytes(b"")
    plan_bytes = plan_path.read_bytes()
    (destination / "input-plan.json").write_bytes(plan_bytes)
    plan = exact_json(plan_bytes)
    require(set(plan) == {"schema_version", "scenario", "e0", "question1", "source_files"}
            and plan["schema_version"] == 1 and plan["scenario"] == SCENARIO, "plan_contract")
    report = {"schema_version": 1, "scenario": SCENARIO, "capture_kind": "live",
              "driver_source_sha256": digest(Path(__file__).read_bytes()),
              "input_plan_sha256": digest(plan_bytes), "cli_process_id": None,
              "started_monotonic_ns": time.monotonic_ns(), "finished_monotonic_ns": None,
              "outcome": "FAIL", "failure": "incomplete", "termination": "launch-failed",
              "timed_out": False, "stage": "INITIAL", "task_count": 0, "initial_prompt": None,
              "commands": [], "files": {}, "first_request_id": None, "second_request_id": None,
              "first_content_sha256": None, "second_content_sha256": None,
              "first_fields": None, "second_fields": None, "action_id": None,
              "first_actual_input": None, "second_actual_input": None,
              "writer": None, "report_expected_sha256": None, "report_readback_sha256": None,
              "report_bytes": None, "question2": None}
    save(destination / "evidence-report.json", report)
    save(destination / "execution.json", {"schema_version": 1, "mode": "smoke",
         "process_exit_code": None, "stdout_sha256": digest(b""), "stderr_sha256": digest(b""),
         "requested_commands": []})
    process, captures, console = None, [], None
    try:
        argv = command if command is not None else [
            sys.executable, str(ROOT / "hosted/linux/aios-console.py"),
            "--artifact-dir", str(destination / "session"), "--service-dir", "/tmp/aios-runtime",
            "--agent-dir", "/tmp/aios-agent", "--agent-config", "/tmp/aios-agent-config.json",
            "--backend-dir", "/tmp/aios-model-backend"]
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0)
        report["cli_process_id"] = process.pid
        report["termination"] = "running"
        captures = [Capture(process.stdout, destination / "stdout.log"),
                    Capture(process.stderr, destination / "stderr.log")]
        console = Console(process, destination, report, *captures)
        console.initial()
        run_scenario(console, destination, plan, report)
        report["stage"] = "MODEL_TASKS_COMPLETE"
    except BaseException as exc:
        report.update(outcome="FAIL", failure=type(exc).__name__ + ":" + str(exc),
                      timed_out=isinstance(exc, TimeoutError))
    finally:
        if console is not None and process is not None and process.poll() is None:
            for command_text in SUFFIX:
                if process.poll() is not None:
                    break
                try:
                    row = console.exchange(command_text)
                    if row["outcome"] != "OK":
                        raise SmokeFailure("cleanup_command:" + command_text)
                except (OSError, ValueError, SmokeFailure, TimeoutError) as exc:
                    report.update(outcome="FAIL", failure=report["failure"] if report["failure"] != "incomplete"
                                  else "cleanup:" + type(exc).__name__ + ":" + str(exc))
        if process is not None:
            if process.poll() is None:
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
            elif report["termination"] == "running":
                report["termination"] = "exit" if process.returncode == 0 else "unexpected-exit"
            for capture in captures:
                try:
                    capture.finish()
                except SmokeFailure as exc:
                    report.update(outcome="FAIL", failure="capture:" + str(exc))
        report["finished_monotonic_ns"] = time.monotonic_ns()
        report["files"] = {name: {"bytes": (destination / name).stat().st_size,
                                  "sha256": digest((destination / name).read_bytes())}
                           for name in ("stdin.log", "stdout.log", "stderr.log")}
        if (report["stage"] == "MODEL_TASKS_COMPLETE" and report["failure"] == "incomplete"
                and report["termination"] == "exit"
                and process is not None and process.returncode == 0
                and not (destination / "stderr.log").read_bytes()
                and all(row["command"] in SUFFIX or row["completion"] == "prompt"
                        for row in report["commands"][:-1])):
            report.update(outcome="PASS", failure=None, stage="COMPLETE")
        elif report["failure"] == "incomplete":
            report["failure"] = "scenario_or_cleanup_incomplete"
        save(destination / "evidence-report.json", report)
        save(destination / "execution.json", {"schema_version": 1, "mode": "smoke",
             "process_exit_code": process.returncode if process else None,
             "stdout_sha256": report["files"]["stdout.log"]["sha256"],
             "stderr_sha256": report["files"]["stderr.log"]["sha256"],
             "requested_commands": [row["command"] for row in report["commands"]]})
    return 0 if report["outcome"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args()
    return run(args.destination, args.plan)


if __name__ == "__main__":
    raise SystemExit(main())
