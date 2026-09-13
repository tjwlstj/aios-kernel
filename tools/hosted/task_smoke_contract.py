"""Independent, bounded two-request Task acceptance above the raw run gates.

Call only after console, MAIN, Task delivery and backend run verification. This
module adds the scenario/driver joins; it never imports the runtime producer.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from newagent_output_contract import identity, same
from space_output_contract import SPACE_QUESTION, validate_model_answer

SCENARIO = "answered-task-then-running-task-cancel"
CANCEL_QUESTION = ("Write a detailed numbered explanation of how operating systems schedule processes, "
                   "manage memory, handle files and network traffic. Include at least twenty detailed points "
                   "and continue until all points are complete.")
PREFIX = ["backend start", "agent start", "room discover", "room bind", "space"]
SUFFIX = ["backend status", "agent status", "resolve example.com", "fetch https://example.com/",
          "agent stop", "backend stop", "exit"]
LIMITS = {"total_seconds": 5700, "command_seconds": 2450, "answer_seconds": 2450, "answer_poll_seconds": 30,
          "answer_polls": 84, "running_poll_seconds": 0.1, "running_polls": 20,
          "cancel_poll_seconds": 1, "cancel_polls": 60, "stream_bytes": 4 * 1024 * 1024}
PROMPT = re.compile(rb"(?:^|\n)aios> $")


def require(condition, reason):
    if not condition:
        raise ValueError("task_smoke:" + reason)


def keys(value, expected, reason):
    require(type(value) is dict and set(value) == set(expected), reason)


def uint(value, reason, minimum=0):
    require(type(value) is int and minimum <= value < 1 << 63, reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def command_text(row):
    return " ".join([row["name"], *row["args"]])


def task_row(command, request_id):
    value = command["result"]
    require(value.get("request_id") == request_id and type(value.get("task")) is dict
            and value["task"]["request_id"] == request_id, "command_task_identity")
    return value["task"]


def verify_plan(commands):
    try:
        return _verify_plan(commands)
    except (IndexError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("task_smoke:malformed_command_plan") from exc


def _verify_plan(commands):
    """Return scenario checkpoints; extra asks, retries or cancellations fail."""
    require(type(commands) is list and 20 <= len(commands) <= 200, "command_count")
    require(all(row["outcome"] == "OK" for row in commands), "command_outcomes")
    lines = [command_text(row) for row in commands]
    require(lines[:len(PREFIX)] == PREFIX, "prefix")
    position = len(PREFIX)
    require(lines[position] == "ask " + SPACE_QUESTION, "answer_question")
    first = commands[position]["result"]["request_id"]
    identity(first)
    require(task_row(commands[position], first)["phase"] == "ACCEPTED", "first_admission")
    position += 1

    def statuses(request_id, maximum):
        nonlocal position
        start = position
        while position < len(lines) and lines[position] == "task status " + request_id:
            position += 1
        require(1 <= position - start <= maximum, "status_count")
        return [task_row(row, request_id) for row in commands[start:position]]

    answers = statuses(first, LIMITS["answer_polls"])
    require(all(row["phase"] in ("ACCEPTED", "RUNNING") for row in answers[:-1])
            and answers[-1]["phase"] == "FINISHED" and answers[-1]["model_outcome"] == "ANSWERED",
            "first_answer_status")
    require(lines[position] == "task result " + first, "first_result_command")
    answer = task_row(commands[position], first)
    require(same(answer, answers[-1]), "first_result_join")
    position += 1
    require(lines[position:position + 2] == ["space", "ask " + CANCEL_QUESTION], "cancel_question")
    second = commands[position + 1]["result"]["request_id"]
    identity(second)
    require(second != first and task_row(commands[position + 1], second)["phase"] == "ACCEPTED",
            "separate_admission")
    position += 2
    running = statuses(second, LIMITS["running_polls"])
    require(all(row["phase"] == "ACCEPTED" for row in running[:-1])
            and running[-1]["phase"] == "RUNNING" and running[-1]["worker_process_id"] is not None,
            "running_before_cancel")
    require(lines[position] == "task cancel " + second, "single_cancel")
    cancelled = task_row(commands[position], second)
    control = commands[position]["result"]["task_control"]
    require(control["cancel_outcome"] == "ACCEPTED" and control["backend_stop_attempt"] is not None
            and control["backend_stop_attempt"]["outcome"] == "OK"
            and control["backend_stop_attempt"]["state"] == "STOPPED", "cancel_stop_attempt")
    require(cancelled["cancel_requested_ns"] is not None
            and running[-1]["updated_ns"] <= cancelled["cancel_requested_ns"], "cancel_after_running")
    cancel_index = position
    position += 1
    terminal = statuses(second, LIMITS["cancel_polls"])
    require(all(row["phase"] in ("CANCEL_REQUESTED", "FINISHED") for row in terminal)
            and terminal[-1]["phase"] == "FINISHED" and terminal[-1]["model_outcome"] == "UNKNOWN"
            and terminal[-1]["backend_stop"] is not None, "cancel_terminal")
    require(all(not (row["phase"] == "FINISHED" and row["model_outcome"] == "ANSWERED")
                for row in (cancelled, *terminal)), "cancel_not_answered")
    require(lines[position] == "task result " + second, "cancel_result_command")
    result = task_row(commands[position], second)
    require(same(result, terminal[-1]), "cancel_result_join")
    position += 1
    require(lines[position:] == SUFFIX, "suffix")
    require(commands[position]["result"]["state"] == "STOPPED", "backend_status_stopped")
    main = commands[position + 1]["result"]
    require(main["state"] == "RUNNING" and main["source_record"]["model_ready"] is False
            and main["management_snapshot"]["binding_current"] is False, "main_invalidation")
    require(commands[-3]["result"]["state"] == commands[-2]["result"]["state"] == "STOPPED",
            "normal_cleanup")
    return {"first": first, "second": second, "answer": answer, "cancelled": result,
            "cancel_index": cancel_index}


def verify_driver(directory, commands):
    """Join timing/UUID records to raw stdin, stdout and exact COMMAND bytes."""
    from verify_agent import decode, read, record
    value = record(directory / "task-smoke.json")
    keys(value, {"schema_version", "scenario", "capture_kind", "source_only", "driver_source_sha256",
                "cli_process_id", "started_monotonic_ns", "finished_monotonic_ns", "outcome", "failure",
                "termination", "timed_out", "limits", "initial_prompt", "commands", "files"}, "driver_keys")
    require(same(value["schema_version"], 1) and value["scenario"] == SCENARIO
            and value["capture_kind"] == "live" and value["source_only"] is True, "driver_schema")
    require(value["outcome"] == "PASS" and value["failure"] is None and value["termination"] == "exit"
            and value["timed_out"] is False and same(value["limits"], LIMITS), "driver_failed")
    require(digest(read(directory / "verification-source/task_smoke_guest.py")) == value["driver_source_sha256"],
            "driver_source")
    uint(value["cli_process_id"], "driver_pid", 1)
    uint(value["started_monotonic_ns"], "driver_clock", 1)
    uint(value["finished_monotonic_ns"], "driver_clock", value["started_monotonic_ns"])
    streams = {name: read(directory / name) for name in ("stdin.log", "stdout.log", "stderr.log")}
    keys(value["files"], set(streams), "driver_files")
    for name, raw in streams.items():
        require(same(value["files"][name], {"sha256": digest(raw), "bytes": len(raw)}), "driver_file:" + name)
    require(not streams["stderr.log"], "driver_stderr")
    require(streams["stdin.log"] == "".join(command_text(row) + "\n" for row in commands).encode(),
            "driver_stdin")
    execution = record(directory / "execution.json")
    require(execution["mode"] == "smoke" and same(execution["process_exit_code"], 0)
            and execution["requested_commands"] == [command_text(row) for row in commands], "driver_execution")
    raw_events = read(directory / "session/session.events.jsonl").splitlines(keepends=True)
    require(raw_events and all(raw.endswith(b"\n") for raw in raw_events), "driver_event_truncated")
    events = [decode(raw) for raw in raw_events]
    require([event["event"] for event in events] == ["START", *["COMMAND"] * len(commands), "STOP"],
            "driver_event_order")
    require(same([event["data"] for event in events[1:-1]], commands), "driver_commands")
    owner = events[0]["data"]["source_process"]
    require(owner["process_id"] == value["cli_process_id"], "driver_console_owner")
    initial = value["initial_prompt"]
    keys(initial, {"event_sequence", "observed_monotonic_ns", "stdout_end"}, "initial_prompt_keys")
    require(same(initial["event_sequence"], 1), "initial_sequence")
    uint(initial["stdout_end"], "initial_stdout", 1)
    uint(initial["observed_monotonic_ns"], "initial_clock", value["started_monotonic_ns"])
    require(PROMPT.search(streams["stdout.log"][:initial["stdout_end"]]) is not None, "initial_prompt")
    require(type(value["commands"]) is list and len(value["commands"]) == len(commands), "driver_command_count")
    last_time, stdin_end, stdout_end = initial["observed_monotonic_ns"], 0, initial["stdout_end"]
    for index, (item, command) in enumerate(zip(value["commands"], commands), 1):
        keys(item, {"sequence", "command", "request_id", "sent_monotonic_ns", "event_observed_monotonic_ns",
                    "completed_monotonic_ns", "stdin_start", "stdin_end", "stdout_start", "stdout_end",
                    "event_sha256", "completion"}, "driver_command_keys")
        require(same(item["sequence"], index + 1) and same(events[index]["sequence"], index + 1)
                and item["command"] == command_text(command), "driver_sequence")
        require(item["request_id"] == command["result"].get("request_id"), "driver_request_id")
        for key in ("sent_monotonic_ns", "event_observed_monotonic_ns", "completed_monotonic_ns"):
            uint(item[key], "driver_command_clock", last_time)
            last_time = item[key]
        require(last_time <= value["finished_monotonic_ns"], "driver_time_end")
        require(item["stdin_start"] == stdin_end and item["stdout_start"] == stdout_end, "driver_offsets")
        uint(item["stdin_end"], "driver_stdin_end", stdin_end + 1)
        uint(item["stdout_end"], "driver_stdout_end", stdout_end + 1)
        stdin_end, stdout_end = item["stdin_end"], item["stdout_end"]
        require(streams["stdin.log"][item["stdin_start"]:stdin_end] == (item["command"] + "\n").encode()
                and stdout_end <= len(streams["stdout.log"]), "driver_stream_slice")
        require(item["event_sha256"] == digest(raw_events[index]), "driver_event_hash")
        require(item["completion"] == ("exit" if index == len(commands) else "prompt"), "driver_completion")
        if item["completion"] == "prompt":
            require(PROMPT.search(streams["stdout.log"][item["stdout_start"]:stdout_end]) is not None,
                    "command_prompt")
    require(stdin_end == len(streams["stdin.log"]) and stdout_end == len(streams["stdout.log"]), "driver_drain")
    return value


def verify_task_workflow(directory: Path, commands: list, runs: list, backend: dict):
    """Scenario gate after independently verified live console/MAIN/backend runs."""
    from resource_output_contract import validate_sample
    from task_output_contract import validate_task_backend_observation
    checkpoints = verify_plan(commands)
    driver = verify_driver(Path(directory), commands)
    first, second = checkpoints["first"], checkpoints["second"]
    require(len(runs) == 1 and len(backend["managed_runs"]) == 1, "single_service_lifetime")
    run, model = runs[0], backend["managed_runs"][0]
    require(run["capture_kind"] == "live" and model["start"]["capture_kind"] == "live"
            and model["state"] == "STOPPED", "live_lifetimes")
    require(set(run["tasks"]) == {first, second}
            and [row["request_id"] for row in run["requests"]] == [first, second], "two_requests_only")
    answer = run["tasks"][first]["revisions"][-1]
    terminal = run["tasks"][second]["revisions"][-1]
    require(same(answer["request_state"], checkpoints["answer"])
            and same(terminal["request_state"], checkpoints["cancelled"]), "delivered_terminal_revision")
    for envelope in (answer, terminal):
        row, progress = envelope["request_state"], envelope["worker_progress"]
        require(progress is not None and progress["done"] is True and progress["worker_exit_code"] is not None
                and progress["worker_exit_observed_monotonic_ns"] is not None
                and progress["worker_exit_observed_monotonic_ns"] <= row["finished_ns"], "worker_exit")
        require(row["owner"]["process_id"] == driver["cli_process_id"], "same_cli_owner")
    answered, cancelled = answer["request_state"], terminal["request_state"]
    require(answered["cancel_requested_ns"] is None and answered["backend_stop"] is None
            and answered["worker_exit_code"] == 0 and answered["space_context"]["validity"] == "CURRENT",
            "normal_answer")
    require(answered["finished_ns"] < cancelled["accepted_ns"], "separate_question_order")
    validate_model_answer(answered["inference_receipt"])
    launcher = answered["inference_receipt"]["backend_execution"]["before"]["launcher"]
    validate_sample(launcher)
    require(int(launcher["raw_stat"].rsplit(")", 1)[1].split()[1]) == driver["cli_process_id"],
            "backend_launched_by_cli")
    require(cancelled["worker_process_id"] != answered["worker_process_id"]
            and cancelled["inference_receipt"] is not None and cancelled["inference_receipt"]["outcome"] == "ERROR"
            and terminal["worker_progress"]["cancel_requested"] is True
            and terminal["worker_progress"]["stop_reason"] == "cancel"
            and terminal["worker_progress"]["worker_exit_observed_monotonic_ns"] >= cancelled["cancel_requested_ns"],
            "cancel_worker_terminal")
    require(terminal["backend_observation"] is not None, "independent_main_backend_observation")
    validate_task_backend_observation(terminal["backend_observation"], cancelled)
    return {"task_workflow_verified": True, "task_requests": 2, "task_answered": 1,
            "task_running_cancelled": 1, "task_worker_exits_verified": 2,
            "task_backend_stop_independently_verified": True, "task_request_ids": [first, second]}
