"""Independent contract for one evidence-report feedback Task scenario.

The source evidence is copied into the run. This verifier imports no scenario
producer or hosted runtime module. A model answer is data, never write proof.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path


SCENARIO = "evidence-report-feedback-v1"
AUDIT_SHA256 = "1d28876744097f181cbb04511dc7fbf850f477546eaad62726b9c96d302bc776"
STRICT_SHA256 = "097ee4e035ff47defb368c22db324f9c3108da248e6d672a23c8ca3ce807af74"
SOURCE_PATHS = (
    "build/self-reference-readout-audit-01/verification.json",
    "build/self-reference-readout-audit-01/strict-replay.json",
    "build/self-reference-readout-01/design.json",
    "build/self-reference-readout-01/report.json",
    *(f"build/self-reference-readout-01/readouts/readout-{number:04d}.json" for number in range(1, 7)),
)
SLOT_IDS = ("S0C0", "S0C1", "S1C1", "S1C0", "S11C0", "S11C1")
SAMPLES = ("S0", "S1", "S11")
SCORE_FIELDS = ("action_revision_correct", "attribution_correct", "prediction_correct", "schema_valid")
FIRST_LINE_PATTERN = re.compile(
    r"\Acomparison: (SAME|DIFFERENT|UNKNOWN)\n"
    r"effect: (EXECUTED|NOT_EXECUTED|UNKNOWN)\n"
    r"next: (SAVE|STOP)\n"
    r"evidence: E0\n?\Z"
)
SECOND_LINE_PATTERN = re.compile(
    r"\Astored: (CONFIRMED|UNCONFIRMED)\n"
    r"next: (FINISH|STOP)\n"
    r"evidence: E1\n?\Z"
)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("evidence_report:" + reason)


def same(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) == \
        json.dumps(right, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def identity(value: object) -> str:
    require(type(value) is str, "uuid")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("evidence_report:uuid") from exc
    require(str(parsed) == value and parsed.int != 0, "uuid")
    return value


def decode(raw: bytes) -> dict:
    def pairs(items):
        value = {}
        for key, child in items:
            require(key not in value, "duplicate_json_key")
            value[key] = child
        return value

    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("evidence_report:nonfinite")))
    require(type(value) is dict, "json_object")
    return value


def read(directory: Path, name: str, *, maximum: int = 4 * 1024 * 1024) -> bytes:
    path = directory / name
    require(path.is_file() and not path.is_symlink(), "missing_or_link:" + name)
    require(path.stat().st_size <= maximum, "file_limit:" + name)
    raw = path.read_bytes()
    require(len(raw) <= maximum, "file_limit:" + name)
    return raw


def _score(readout: dict) -> dict:
    score = readout["static_score"]
    require(type(score) is dict and all(type(score[key]) is bool for key in
            ("action_correct_from_visible_facts", "revision_correct", "attribution_correct",
             "prediction_matches_visible_expectation", "prediction_evaluable", "schema_valid")),
            "score_shape")
    require(score["prediction_evaluable"], "prediction_unavailable")
    return {
        "action_revision_correct": score["action_correct_from_visible_facts"] and score["revision_correct"],
        "attribution_correct": score["attribution_correct"],
        "prediction_correct": score["prediction_matches_visible_expectation"],
        "schema_valid": score["schema_valid"],
    }


def derive_e0(directory: Path) -> tuple[dict, dict, dict]:
    """Recompute a compact E0 from ten copied, pinned actual-run records."""
    directory = Path(directory)
    raw = {name: read(directory / "evidence-source", name) for name in SOURCE_PATHS}
    hashes = {name: {"bytes": len(value), "sha256": digest(value)} for name, value in raw.items()}
    require(hashes[SOURCE_PATHS[0]]["sha256"] == AUDIT_SHA256, "audit_pin")
    require(hashes[SOURCE_PATHS[1]]["sha256"] == STRICT_SHA256, "strict_pin")
    objects = {name: decode(value) for name, value in raw.items()}
    audit = objects[SOURCE_PATHS[0]]
    strict = objects[SOURCE_PATHS[1]]
    design = objects[SOURCE_PATHS[2]]
    report = objects[SOURCE_PATHS[3]]
    require(audit.get("outcome") == strict.get("outcome") == "PASS"
            and audit.get("evaluation_scope") == strict.get("evaluation_scope") == report.get("evaluation_scope") == "STATIC_ONLY"
            and audit.get("relationship") == strict.get("relationship") == report.get("relationship") == "RESEARCH"
            and report.get("experiment_integrity") == "PASS" and audit.get("errors") == [], "audit_scope")
    run_id = identity(audit.get("run_id"))
    require(run_id == strict.get("run_id") == report.get("run_id") == design.get("run_id"), "run_id_join")
    require(audit.get("actual_queries_observed") == strict.get("model_queries_verified") == report.get("completed_slots") == 6
            and strict.get("plan_id") == report.get("plan_id") == design.get("plan_id") == "frozen-readout-order-v1",
            "six_readouts")
    require({key: value for key, value in strict.items() if key != "file_evidence"}
            == audit.get("strict_replay") and report == audit.get("producer_report")
            and strict.get("files_verified") == 379 and strict.get("producer_experiment_integrity") == "PASS"
            and all(value is True for value in audit.get("before_after_unchanged", {}).values())
            and audit.get("raw_costs_vs_strict_replay", {}).get("matched") is True,
            "audit_replay_join")
    require(design.get("controls", {}).get("world_effects_permitted") is False
            and design["controls"].get("history_updates_from_new_outputs") is False
            and report.get("kind") == "static-public-context-readout"
            and "no World effects" in audit.get("scope", "")
            and "not executed effects" in strict.get("scope", ""), "no_world_effects")
    observed_at = audit.get("audit_finished_at")
    require(type(observed_at) is str, "audit_time")
    require(datetime.fromisoformat(observed_at).tzinfo is not None, "audit_time")
    require(audit.get("strict_replay", {}).get("by_condition") == strict.get("by_condition")
            and audit.get("producer_report", {}).get("by_condition") == report.get("by_condition"),
            "audit_aggregate_join")
    evidence = strict.get("file_evidence")
    require(type(evidence) is dict, "strict_file_evidence")
    for name in SOURCE_PATHS[2:]:
        source_name = name.removeprefix("build/self-reference-readout-01/")
        require(same(evidence.get(source_name), hashes[name]), "strict_source_hash:" + name)
    expected_paths = [f"readouts/readout-{number:04d}.json" for number in range(1, 7)]
    require(report.get("readout_paths") == expected_paths, "readout_paths")
    slots = design.get("slots")
    require(type(slots) is list and len(slots) == 6, "design_slots")
    summaries = strict.get("readouts")
    require(type(summaries) is list and len(summaries) == 6, "strict_readouts")
    readouts = {}
    for index, slot in enumerate(slots):
        name = SOURCE_PATHS[index + 4]
        value = objects[name]
        slot_id = SLOT_IDS[index]
        require(type(slot) is dict and slot.get("slot_id") == slot_id
                and slot.get("readout_path") == expected_paths[index]
                and value.get("slot_id") == slot_id and value.get("ordinal") == index + 1
                and value.get("condition") == slot.get("condition")
                and value.get("sample_id") == slot.get("sample_id"), "slot_join")
        require(type(summaries[index]) is dict and summaries[index].get("slot_id") == slot_id
                and same(summaries[index].get("proposal"), value.get("proposal"))
                and same(summaries[index].get("static_score"), value.get("static_score")), "strict_readout_join")
        query = value.get("model_query")
        require(type(query) is dict and type(value.get("content")) is str
                and query.get("content") == value["content"]
                and type(query.get("raw_response")) is dict
                and query["raw_response"].get("content") == value["content"]
                and query["raw_response"].get("truncated") is False,
                "raw_readout_join")
        _score(value)
        readouts[slot_id] = value
    per_condition = {}
    for condition in ("C0", "C1"):
        selected = [readouts[sample + condition] for sample in SAMPLES]
        per_condition[condition] = {"readouts": len(selected), **{
            key: sum(_score(row)[key] for row in selected) for key in SCORE_FIELDS}}
        require(all(strict["by_condition"][condition][key] == value
                    for key, value in per_condition[condition].items()), "aggregate_score")
        require(all(report["by_condition"][condition][key] == value
                    for key, value in per_condition[condition].items()), "producer_score")
    paired = all(same(readouts[sample + "C0"]["proposal"], readouts[sample + "C1"]["proposal"])
                 and same(readouts[sample + "C0"]["static_score"], readouts[sample + "C1"]["static_score"])
                 for sample in SAMPLES)
    e0 = {"run_id": run_id, "audit_sha256": AUDIT_SHA256, "observed_at": observed_at,
          "per_condition": per_condition, "paired_decisions_and_scores_equal": paired,
          "world_effects_executed": 0}
    return e0, hashes, readouts


def first_answer(content: object) -> tuple[str, str, str]:
    require(type(content) is str, "first_answer_type")
    match = FIRST_LINE_PATTERN.fullmatch(content)
    require(match is not None, "first_answer_format")
    return match.groups()


def second_answer(content: object) -> tuple[str, str]:
    require(type(content) is str, "second_answer_type")
    match = SECOND_LINE_PATTERN.fullmatch(content)
    require(match is not None, "second_answer_format")
    return match.groups()


def saved_report_bytes(run_id: str, first_uuid: str, response_sha256: str) -> bytes:
    identity(run_id)
    identity(first_uuid)
    require(type(response_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", response_sha256) is not None,
            "response_hash")
    return ("AIOS evidence report v1\n" + f"run_id={run_id}\nrequest_id={first_uuid}\n"
            + f"response_sha256={response_sha256}\ncomparison=SAME\neffect=NOT_EXECUTED\nnext=SAVE\n").encode("ascii")


def _proposal_text(proposal: dict) -> str:
    require(type(proposal) is dict and set(proposal) == {"action", "expected_revision", "prediction", "attribution"},
            "proposal_fields")
    revision = proposal["expected_revision"]
    require(revision is None or type(revision) is int and 0 <= revision < 1 << 63, "proposal_revision")
    values = (proposal["action"], "null" if revision is None else str(revision),
              proposal["prediction"], proposal["attribution"])
    require(all(type(value) is str and re.fullmatch(r"[A-Z_0-9]+|null", value)
                for value in values), "proposal_text")
    return "/".join(values)


def question1(e0: dict, readouts: dict) -> str:
    """Rebuild the exact CLI question without executing the producer's builder."""
    proposal = readouts["S0C0"]["proposal"]
    require(all(same(row["proposal"], proposal) for row in readouts.values()),
            "compact_proposal_coverage")
    pieces = []
    for condition in ("C0", "C1"):
        counts = e0["per_condition"][condition]
        outputs = []
        for sample in SAMPLES:
            row = readouts[sample + condition]
            score = _score(row)
            vector = ",".join(str(int(score[key])) for key in SCORE_FIELDS[:3])
            outputs.append(sample + ":" + vector)
        pieces.append(f"{condition} n={counts['readouts']} ar={counts['action_revision_correct']} "
                      f"attr={counts['attribution_correct']} pred={counts['prediction_correct']} "
                      f"schema={counts['schema_valid']} [{'|'.join(outputs)}]")
    result = (
        f"E0 run={e0['run_id']} audit={e0['audit_sha256']} "
        f"new_world_effects={e0['world_effects_executed']} "
        f"P={_proposal_text(proposal)} scores=ar,attr,pred; "
        + "; ".join(pieces) + ". "
        "Compare C0/C1 P+scores. New World effects? SAVE only for a truthful report. Four lines: "
        "comparison: SAME|DIFFERENT|UNKNOWN; "
        "effect: EXECUTED|NOT_EXECUTED|UNKNOWN; next: SAVE|STOP; evidence: E0."
    )
    require(result.isascii() and "\n" not in result and "\r" not in result
            and " ".join(("ask " + result).split()) == "ask " + result
            and len("ask " + result) <= 2048, "question1_budget")
    return result


def question2(first_uuid: str, action_id: str, writer: dict, expected_sha: str,
              readback_sha: str, report_bytes: int) -> str:
    identity(first_uuid)
    identity(action_id)
    require(type(writer) is dict and set(writer) == {"uid", "pid"}
            and all(type(writer[key]) is int and writer[key] >= 0 for key in writer), "writer")
    require(all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (expected_sha, readback_sha)) and expected_sha == readback_sha
            and type(report_bytes) is int and report_bytes > 0,
            "report_hash")
    result = (
        f"E1 first_task_uuid={first_uuid} action_id={action_id} "
        f"writer=driver(uid={writer['uid']},pid={writer['pid']}) report_created=1 "
        f"readback_sha256={readback_sha} reread_bytes={report_bytes} "
        "matches_expected=1 write_error=none new_readout_world_effects=0. "
        "Was this driver report stored? Finish if confirmed. "
        "This is driver storage, not research World action. Reply three lines: "
        "stored: CONFIRMED|UNCONFIRMED; "
        "next: FINISH|STOP; evidence: E1."
    )
    require(result.isascii() and "\n" not in result and "\r" not in result
            and " ".join(("ask " + result).split()) == "ask " + result
            and len("ask " + result) <= 2048, "question2_budget")
    return result


def verify_e0_plan(directory: Path) -> tuple[dict, dict]:
    plan = decode(read(Path(directory), "evidence-plan.json", maximum=1024 * 1024))
    require(set(plan) == {"schema_version", "scenario", "e0", "question1", "source_files"}
            and type(plan["schema_version"]) is int and plan["schema_version"] == 1
            and plan["scenario"] == SCENARIO, "plan_schema")
    e0, source_files, readouts = derive_e0(directory)
    require(same(plan["e0"], e0) and same(plan["source_files"], source_files), "plan_source_join")
    require(plan["question1"] == question1(e0, readouts), "question1_exact")
    return plan, readouts


def command_text(row: dict) -> str:
    require(type(row) is dict and type(row.get("name")) is str and type(row.get("args")) is list
            and all(type(arg) is str for arg in row["args"]), "command_shape")
    return " ".join((row["name"], *row["args"]))


def _task_command(row: dict, request_id: str) -> dict:
    value = row["result"]
    require(type(value) is dict and value.get("request_id") == request_id
            and type(value.get("task")) is dict
            and value["task"].get("request_id") == request_id, "task_command_join")
    return value["task"]


def verify_plan(commands: list, first_question: str, second_question: str) -> tuple[str, str]:
    """Exactly two successful answered UUID Tasks and no hidden retry/cancel."""
    require(type(commands) is list and 15 <= len(commands) <= 200
            and all(type(row) is dict and row.get("outcome") == "OK" for row in commands),
            "command_count_or_outcome")
    lines = [command_text(row) for row in commands]
    prefix = ["backend start", "agent start", "room discover", "room bind", "space"]
    suffix = ["agent stop", "backend stop", "exit"]
    require(lines[:len(prefix)] == prefix, "prefix")
    position = len(prefix)

    def task(question: str) -> str:
        nonlocal position
        require(lines[position] == "ask " + question, "question_exact")
        request_id = identity(commands[position]["result"].get("request_id"))
        admitted = _task_command(commands[position], request_id)
        require(admitted.get("phase") == "ACCEPTED" and admitted.get("revision") == 1,
                "task_admission")
        position += 1
        statuses = []
        while position < len(lines) and lines[position] == "task status " + request_id:
            statuses.append(_task_command(commands[position], request_id))
            position += 1
        require(1 <= len(statuses) <= 84 and all(row.get("phase") in ("ACCEPTED", "RUNNING")
                for row in statuses[:-1]) and statuses[-1].get("phase") == "FINISHED"
                and statuses[-1].get("model_outcome") == "ANSWERED", "task_statuses")
        require(lines[position] == "task result " + request_id, "task_result_command")
        final = _task_command(commands[position], request_id)
        require(same(final, statuses[-1]), "task_result_join")
        position += 1
        return request_id

    try:
        first = task(first_question)
        require(lines[position] == "space", "second_space")
        position += 1
        second = task(second_question)
        require(first != second and lines[position:] == suffix, "suffix_or_duplicate_task")
        require(commands[position]["result"].get("state") == "STOPPED"
                and commands[position + 1]["result"].get("state") == "STOPPED", "normal_stop")
    except (IndexError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("evidence_report:malformed_command_plan") from exc
    return first, second


def _verify_driver(directory: Path, commands: list, plan_raw: bytes) -> tuple[dict, dict]:
    """Join saved driver summary to raw CLI stdin, stdout, events and owner."""
    report = decode(read(directory, "evidence-report.json"))
    expected_keys = {"schema_version", "scenario", "capture_kind", "driver_source_sha256", "input_plan_sha256",
        "cli_process_id", "started_monotonic_ns", "finished_monotonic_ns", "outcome", "failure",
        "termination", "timed_out", "stage", "task_count", "initial_prompt", "commands", "files",
        "first_request_id", "second_request_id", "first_content_sha256", "second_content_sha256",
        "first_fields", "second_fields", "action_id", "writer", "report_expected_sha256",
        "report_readback_sha256", "report_bytes", "question2", "first_actual_input",
        "second_actual_input"}
    require(set(report) == expected_keys and type(report["schema_version"]) is int
            and report["schema_version"] == 1 and report["scenario"] == SCENARIO
            and report["capture_kind"] == "live" and report["outcome"] == "PASS"
            and report["failure"] is None and report["termination"] == "exit"
            and report["timed_out"] is False and report["stage"] == "COMPLETE"
            and report["task_count"] == 2, "driver_outcome")
    require(report["input_plan_sha256"] == digest(plan_raw)
            and read(directory, "input-plan.json") == plan_raw, "driver_plan_copy")
    driver_source = read(directory, "verification-source/evidence_report_guest.py")
    require(report["driver_source_sha256"] == digest(driver_source), "driver_source")
    require(type(report["cli_process_id"]) is int and report["cli_process_id"] > 0
            and type(report["started_monotonic_ns"]) is int and report["started_monotonic_ns"] > 0
            and type(report["finished_monotonic_ns"]) is int
            and report["finished_monotonic_ns"] >= report["started_monotonic_ns"], "driver_clock")
    streams = {name: read(directory, name) for name in ("stdin.log", "stdout.log", "stderr.log")}
    require(set(report["files"]) == set(streams), "driver_file_set")
    for name, raw in streams.items():
        require(same(report["files"][name], {"bytes": len(raw), "sha256": digest(raw)}), "driver_file:" + name)
    require(not streams["stderr.log"] and streams["stdin.log"] ==
            "".join(command_text(row) + "\n" for row in commands).encode("utf-8"), "driver_stdin_stderr")
    execution = decode(read(directory, "execution.json"))
    require(execution.get("schema_version") == 1 and execution.get("mode") == "smoke"
            and execution.get("process_exit_code") == 0
            and execution.get("requested_commands") == [command_text(row) for row in commands]
            and execution.get("stdout_sha256") == digest(streams["stdout.log"])
            and execution.get("stderr_sha256") == digest(streams["stderr.log"]), "driver_execution")
    raw_events = read(directory, "session/session.events.jsonl").splitlines(keepends=True)
    require(raw_events and all(line.endswith(b"\n") for line in raw_events), "event_truncated")
    events = [decode(line) for line in raw_events]
    require([row.get("event") for row in events] == ["START", *["COMMAND"] * len(commands), "STOP"]
            and [row.get("sequence") for row in events] == list(range(1, len(events) + 1))
            and all(row.get("schema_version") == 10 for row in events)
            and [row.get("data") for row in events[1:-1]] == commands, "event_order")
    source_process = events[0]["data"]["source_process"]
    require(type(source_process) is dict and source_process.get("process_id") == report["cli_process_id"],
            "cli_owner")
    initial = report["initial_prompt"]
    require(type(initial) is dict and set(initial) == {"event_sequence", "observed_monotonic_ns", "stdout_end"}
            and initial["event_sequence"] == 1 and type(initial["stdout_end"]) is int
            and 0 < initial["stdout_end"] <= len(streams["stdout.log"])
            and re.search(rb"(?:^|\n)aios> $", streams["stdout.log"][:initial["stdout_end"]]) is not None,
            "initial_prompt")
    require(type(report["commands"]) is list and len(report["commands"]) == len(commands),
            "driver_command_count")
    stdin_end, stdout_end = 0, initial["stdout_end"]
    clock = initial["observed_monotonic_ns"]
    for index, (capture, command) in enumerate(zip(report["commands"], commands), 1):
        require(type(capture) is dict and capture.get("sequence") == index + 1
                and capture.get("command") == command_text(command)
                and capture.get("request_id") == command["result"].get("request_id")
                and capture.get("event_sha256") == digest(raw_events[index])
                and capture.get("stdin_start") == stdin_end
                and capture.get("stdout_start") == stdout_end
                and capture.get("completion") == ("exit" if index == len(commands) else "prompt"),
                "driver_command_join")
        for name in ("sent_monotonic_ns", "event_observed_monotonic_ns", "completed_monotonic_ns"):
            value = capture.get(name)
            require(type(value) is int and value >= clock, "driver_command_clock")
            clock = value
        next_stdin, next_stdout = capture.get("stdin_end"), capture.get("stdout_end")
        require(type(next_stdin) is int and type(next_stdout) is int
                and next_stdin > stdin_end and next_stdout > stdout_end
                and streams["stdin.log"][stdin_end:next_stdin] == (capture["command"] + "\n").encode("utf-8")
                and next_stdout <= len(streams["stdout.log"]), "driver_stream_offset")
        if index < len(commands):
            require(re.search(rb"(?:^|\n)aios> $", streams["stdout.log"][stdout_end:next_stdout]) is not None,
                    "driver_command_prompt")
        stdin_end, stdout_end = next_stdin, next_stdout
    require(stdin_end == len(streams["stdin.log"]) and stdout_end == len(streams["stdout.log"])
            and clock <= report["finished_monotonic_ns"], "driver_stream_drain")
    return report, source_process


def _receipt(runs: list, request_id: str, expected_question: str) -> tuple[dict, dict]:
    run = runs[0]
    entry = run["tasks"][request_id]
    row = entry["revisions"][-1]["request_state"]
    require(row["request_id"] == request_id and row["phase"] == "FINISHED"
            and row["model_outcome"] == "ANSWERED" and row["cancel_requested_ns"] is None
            and row["worker_exit_code"] == 0 and row["user_prompt"] == expected_question,
            "terminal_task")
    receipt = row["inference_receipt"]
    require(type(receipt) is dict and receipt.get("outcome") == "OK"
            and receipt.get("request_id") == request_id
            and receipt.get("user_prompt") == expected_question
            and type(receipt.get("content")) is str, "receipt_join")
    request_raw = receipt["request_body"].encode("utf-8")
    response_raw = receipt["response_body"].encode("utf-8")
    request = decode(request_raw)
    response = decode(response_raw)
    expected_request_keys = {"prompt", "n_predict", "temperature", "seed", "cache_prompt", "stream"}
    require(receipt["request_sha256"] == digest(request_raw)
            and receipt["response_sha256"] == digest(response_raw)
            and row["request_body"] == receipt["request_body"]
            and row["request_sha256"] == receipt["request_sha256"]
            and set(request) == expected_request_keys
            and request_raw == json.dumps(request, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False, allow_nan=False).encode("utf-8")
            and type(request["n_predict"]) is int and request["n_predict"] == 192
            and type(request["temperature"]) is float and request["temperature"] == 0.0
            and type(request["seed"]) is int and request["seed"] == 1
            and request.get("cache_prompt") is False and request.get("stream") is False
            and response.get("prompt") == request.get("prompt") and response.get("truncated") is False
            and response.get("content") == receipt["content"], "raw_receipt")
    retained = [value for value in run["requests"] if value["request_id"] == request_id]
    require(len(retained) == 1 and all(same(retained[0][key], receipt[key]) for key in receipt),
            "retained_receipt")
    return row, receipt


def _prompt_payload(prompt: str) -> dict:
    from space_output_contract import SPACE_PREFIX

    suffix = " /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    require(type(prompt) is str and prompt.startswith(SPACE_PREFIX) and prompt.endswith(suffix),
            "hosted_prompt_shape")
    value = decode(prompt[len(SPACE_PREFIX):-len(suffix)].encode("utf-8"))
    require(set(value) == {"space_data", "question"}, "hosted_prompt_envelope")
    return value


def _preflight(directory: Path, ordinal: int, question: str, receipt: dict,
               state: dict, observed: dict, actual_input: dict) -> None:
    from space_output_contract import model_context, same as space_same, validate_packet

    record = decode(read(directory, f"preflight-{ordinal}.json"))
    expected_fields = {"ordinal", "question_sha256", "request_sha256", "request_bytes",
        "prompt_sha256", "token_count", "response_budget", "context_size", "token_drift_margin", "tokenize_status",
        "token_request_sha256", "token_response_sha256"}
    require(set(record) == expected_fields and record["ordinal"] == ordinal
            and record["question_sha256"] == digest(question.encode("ascii"))
            and record["response_budget"] == 192 and record["context_size"] == 1024
            and record["token_drift_margin"] == 24
            and record["tokenize_status"] == 200, "preflight_record")
    require(type(observed) is dict and type(state["space_context"]) is dict
            and state["space_context"]["validity"] == observed["validity"] == "CURRENT"
            and space_same(state["space_context"]["observation"], observed["observation"])
            and space_same(state["space_context"]["consumer"], observed["consumer"])
            and space_same(receipt["space_context"], state["space_context"]), "actual_context_join")
    validate_packet(observed)
    validate_packet(state["space_context"])
    prompt = decode(receipt["request_body"].encode("utf-8"))["prompt"]
    request_raw = read(directory, f"tokenize-{ordinal}/request.json", maximum=16384)
    response_raw = read(directory, f"tokenize-{ordinal}/response.json", maximum=65536)
    token_request = decode(request_raw)
    token_response = decode(response_raw)
    tokens = token_response.get("tokens")
    preflight_prompt = token_request.get("content")
    require(token_request == {"content": preflight_prompt, "add_special": True, "parse_special": True}
            and _prompt_payload(preflight_prompt)["question"] == question
            and space_same(_prompt_payload(preflight_prompt)["space_data"], model_context(observed))
            and _prompt_payload(prompt)["question"] == question
            and space_same(_prompt_payload(prompt)["space_data"], model_context(state["space_context"]))
            and type(tokens) is list and bool(tokens)
            and all(type(value) is int and value >= 0 for value in tokens)
            and record["prompt_sha256"] == digest(preflight_prompt.encode("utf-8"))
            and record["token_request_sha256"] == digest(request_raw)
            and record["token_response_sha256"] == digest(response_raw)
            and record["token_count"] == len(tokens)
            and len(tokens) + 192 + 24 <= 1024, "token_preflight")
    preflight_body = {"prompt": preflight_prompt, "n_predict": 192, "temperature": 0.0,
                      "seed": 1, "cache_prompt": False, "stream": False}
    preflight_body_raw = json.dumps(preflight_body, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")
    require(record["request_sha256"] == digest(preflight_body_raw)
            and record["request_bytes"] == len(preflight_body_raw), "preflight_body")
    actual_response = decode(receipt["response_body"].encode("utf-8"))
    count = actual_response.get("tokens_evaluated")
    require(type(actual_input) is dict and set(actual_input) == {"request_sha256", "actual_prompt_tokens"}
            and actual_input["request_sha256"] == state["request_sha256"] == receipt["request_sha256"]
            and state["request_body"] == receipt["request_body"]
            and type(count) is int and 0 < count <= 832
            and actual_input["actual_prompt_tokens"] == count, "actual_input")


def verify_evidence_report_workflow(directory: Path, commands: list, runs: list, backend: dict) -> dict:
    """Scenario gate after generic live console, MAIN and backend verification."""
    directory = Path(directory)
    plan, _readouts = verify_e0_plan(directory)
    require(type(runs) is list and len(runs) == 1 and type(backend) is dict
            and type(backend.get("managed_runs")) is list and len(backend["managed_runs"]) == 1
            and runs[0]["capture_kind"] == "live", "single_live_lifetime")
    driver, source_process = _verify_driver(directory, commands, read(directory, "evidence-plan.json"))
    first = identity(driver["first_request_id"])
    second = identity(driver["second_request_id"])
    identity(driver["action_id"])
    writer = driver["writer"]
    require(type(writer) is dict and set(writer) == {"uid", "pid"}
            and writer["uid"] == source_process["uid"]
            and writer["pid"] == int(source_process["raw_stat"].rsplit(")", 1)[1].split()[1]),
            "driver_writer_identity")
    report_raw = read(directory, "saved-report.txt", maximum=4096)
    first_row, first_receipt = _receipt(runs, first, plan["question1"])
    first_content = first_receipt["content"]
    require(first_answer(first_content) == ("SAME", "NOT_EXECUTED", "SAVE")
            and driver["first_fields"] == ["SAME", "NOT_EXECUTED", "SAVE"]
            and driver["first_content_sha256"] == digest(first_content.encode("utf-8")),
            "first_decision")
    expected_report = saved_report_bytes(plan["e0"]["run_id"], first,
                                         driver["first_content_sha256"])
    expected_hash = digest(expected_report)
    require(report_raw == expected_report
            and driver["report_expected_sha256"] == driver["report_readback_sha256"] == expected_hash
            and driver["report_bytes"] == len(report_raw), "stored_report_readback")
    expected_q2 = question2(first, driver["action_id"], writer, expected_hash, expected_hash, len(report_raw))
    require(driver["question2"] == expected_q2, "question2_exact")
    planned_first, planned_second = verify_plan(commands, plan["question1"], expected_q2)
    require((planned_first, planned_second) == (first, second), "plan_task_ids")
    require(set(runs[0]["tasks"]) == {first, second}
            and [value["request_id"] for value in runs[0]["requests"]] == [first, second],
            "single_live_lifetime")
    second_row, second_receipt = _receipt(runs, second, expected_q2)
    require(first_row["finished_ns"] < second_row["accepted_ns"]
            and first_row["owner"]["process_id"] == second_row["owner"]["process_id"] == driver["cli_process_id"]
            and second_answer(second_receipt["content"]) == ("CONFIRMED", "FINISH")
            and driver["second_fields"] == ["CONFIRMED", "FINISH"]
            and driver["second_content_sha256"] == digest(second_receipt["content"].encode("utf-8")),
            "second_feedback")
    first_space = commands[4]["result"]["space_context"]
    second_space = next((row["result"]["space_context"] for row in commands[5:]
                         if command_text(row) == "space"), None)
    require(type(second_space) is dict, "second_space_missing")
    _preflight(directory, 1, plan["question1"], first_receipt, first_row, first_space,
               driver["first_actual_input"])
    _preflight(directory, 2, expected_q2, second_receipt, second_row, second_space,
               driver["second_actual_input"])
    return {"evidence_report_verified": True, "task_requests": 2, "task_answered": 2,
            "task_request_ids": [first, second], "report_sha256": expected_hash,
            "report_bytes": len(report_raw), "report_action_id": driver["action_id"],
            "source_run_id": plan["e0"]["run_id"], "source_audit_sha256": AUDIT_SHA256,
            "model_world_effects_executed": 0}
