"""Prepare one bounded AIOS Task question from the independently audited readout.

This reads existing evidence and creates a plan. It never calls a model, executes
retained research Python, or treats a Task answer as an execution verdict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys


SCENARIO = "evidence-report-feedback-v1"
ROOT = Path(__file__).resolve().parents[2]
READOUT_REL = "build/self-reference-readout-01"
AUDIT_REL = "build/self-reference-readout-audit-01"
CALLER_REL = "build/self-reference-readout-run-01/receipt.json"
PINS = {
    "audit": "1d28876744097f181cbb04511dc7fbf850f477546eaad62726b9c96d302bc776",
    "strict": "097ee4e035ff47defb368c22db324f9c3108da248e6d672a23c8ca3ce807af74",
    "caller": "6d412cd10f7a7a622843c6d6ccd520f1f023aa045c0df1a16b2c4fff0657d1a2",
}
RUN_ID = "51ad65ad-7081-4c90-8bb6-419e47f307f2"
SAMPLES = ("S0", "S1", "S11")
CONDITIONS = ("C0", "C1")
SLOT_ORDER = ("S0C0", "S0C1", "S1C1", "S1C0", "S11C0", "S11C1")
READOUT_FILES = tuple(f"readouts/readout-{index:04d}.json" for index in range(1, 7))
INPUT_FILES = ("design.json", "report.json", *READOUT_FILES)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("evidence_report_plan:" + reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ordinary(path: Path) -> None:
    """Reject links, junctions and non-ordinary inputs before reading them."""
    for part in (path, *path.parents):
        entry = part.lstat()
        _require(not stat.S_ISLNK(entry.st_mode) and not
                 getattr(entry, "st_file_attributes", 0) & 0x400, "linked-input")
        _require(stat.S_ISREG(entry.st_mode) if part == path else stat.S_ISDIR(entry.st_mode),
                 "input-type")


def _read(path: Path, maximum: int = 1024 * 1024) -> bytes:
    _ordinary(path)
    before = path.lstat()
    _require(before.st_size <= maximum, "input-size:" + path.name)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    after = path.lstat()
    _require((before.st_size, before.st_mtime_ns, before.st_ino) ==
             (after.st_size, after.st_mtime_ns, after.st_ino) and len(raw) == after.st_size,
             "input-changed:" + path.name)
    return raw


def _json(raw: bytes, reason: str) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        value = {}
        for key, child in items:
            _require(key not in value, reason + ":duplicate-key")
            value[key] = child
        return value

    def nonfinite(_value: str) -> None:
        raise ValueError("evidence_report_plan:" + reason + ":nonfinite")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=nonfinite)
    _require(type(value) is dict, reason + ":object")
    return value


def _rel(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    _require(bool(parts) and not PurePosixPath(relative).is_absolute()
             and all(part not in (".", "..") for part in parts), "relative-path")
    return root.joinpath(*parts)


def _record(path: Path, expected: dict | None = None) -> tuple[bytes, dict]:
    raw = _read(path)
    record = {"bytes": len(raw), "sha256": _sha(raw)}
    if expected is not None:
        _require(record == expected, "file-evidence:" + path.name)
    return raw, record


def _condition_counts(readouts: dict[str, dict], condition: str) -> dict:
    rows = [readouts[sample + condition] for sample in SAMPLES]
    scores = [row["static_score"] for row in rows]
    for score in scores:
        for key in ("action_correct_from_visible_facts", "revision_correct",
                    "attribution_correct", "prediction_evaluable",
                    "prediction_matches_visible_expectation", "schema_valid"):
            _require(type(score[key]) is bool, "score-bool:" + key)
    _require(all(row["condition"] == condition for row in rows), "slot-condition")
    _require(all(score["prediction_evaluable"] for score in scores), "prediction-coverage")
    return {"readouts": 3,
            "action_revision_correct": sum(score["action_correct_from_visible_facts"]
                                           and score["revision_correct"] for score in scores),
            "attribution_correct": sum(score["attribution_correct"] for score in scores),
            "prediction_correct": sum(score["prediction_matches_visible_expectation"]
                                      for score in scores),
            "schema_valid": sum(score["schema_valid"] for score in scores)}


def _proposal_text(proposal: dict) -> str:
    _require(set(proposal) == {"action", "expected_revision", "prediction", "attribution"},
             "proposal-fields")
    revision = proposal["expected_revision"]
    _require(revision is None or type(revision) is int and 0 <= revision < 1 << 63,
             "proposal-revision")
    fields = (proposal["action"], "null" if revision is None else str(revision),
              proposal["prediction"], proposal["attribution"])
    _require(all(type(value) is str and re.fullmatch(r"[A-Z_0-9]+|null", value)
                 for value in fields), "proposal-text")
    return "/".join(fields)


def question1(e0: dict, readouts: dict[str, dict]) -> str:
    """Show facts for a decision; omit the computed comparison/effect labels."""
    proposal = readouts["S0C0"]["proposal"]
    _require(all(row["proposal"] == proposal for row in readouts.values()),
             "compact-question-proposal-coverage")
    chunks = []
    for condition in CONDITIONS:
        counts = e0["per_condition"][condition]
        outputs = []
        for sample in SAMPLES:
            row = readouts[sample + condition]
            score = row["static_score"]
            vector = ",".join(str(int(value)) for value in (
                score["action_correct_from_visible_facts"] and score["revision_correct"],
                score["attribution_correct"], score["prediction_matches_visible_expectation"]))
            outputs.append(sample + ":" + vector)
        chunks.append(f"{condition} n={counts['readouts']} ar={counts['action_revision_correct']} "
                      f"attr={counts['attribution_correct']} pred={counts['prediction_correct']} "
                      f"schema={counts['schema_valid']} [{'|'.join(outputs)}]")
    question = (
        f"E0 run={e0['run_id']} audit={e0['audit_sha256']} "
        f"new_world_effects={e0['world_effects_executed']} "
        f"P={_proposal_text(proposal)} scores=ar,attr,pred; "
        + "; ".join(chunks) + ". "
        "Compare C0/C1 P+scores. New World effects? SAVE only for a truthful report. Four lines: "
        "comparison: SAME|DIFFERENT|UNKNOWN; "
        "effect: EXECUTED|NOT_EXECUTED|UNKNOWN; next: SAVE|STOP; evidence: E0."
    )
    _require(question.isascii() and "\n" not in question and "\r" not in question,
             "question-one-line-ascii")
    command = "ask " + question
    _require(" ".join(command.split()) == command, "cli-question-normalization")
    _require(len(command) <= 2048, "cli-command-limit")
    _require(len(question.encode("utf-8")) <= 4096, "question-byte-limit")
    return question


def _build_plan(readout_dir: Path, audit_dir: Path, caller_receipt: Path,
                source_root: Path, pins: dict, run_id: str) -> dict:
    root = source_root.absolute()
    readout = readout_dir.absolute()
    audit = audit_dir.absolute()
    caller = caller_receipt.absolute()
    _require(readout.resolve(strict=True) == _rel(root, READOUT_REL).resolve(strict=True)
             and audit.resolve(strict=True) == _rel(root, AUDIT_REL).resolve(strict=True)
             and caller.resolve(strict=True) == _rel(root, CALLER_REL).resolve(strict=True),
             "fixed-input-paths")

    audit_raw, audit_record = _record(audit / "verification.json")
    strict_raw, strict_record = _record(audit / "strict-replay.json")
    caller_raw, _ = _record(caller)
    _require(audit_record["sha256"] == pins["audit"] and
             strict_record["sha256"] == pins["strict"] and _sha(caller_raw) == pins["caller"],
             "receipt-pin")
    verdict = _json(audit_raw, "audit")
    strict = _json(strict_raw, "strict")
    caller_data = _json(caller_raw, "caller")
    _require(verdict.get("outcome") == "PASS" and verdict.get("evaluation_scope") == "STATIC_ONLY"
             and verdict.get("run_id") == run_id and verdict.get("replay_exit_code") == 0
             and verdict.get("actual_queries_observed") == 6 and verdict.get("errors") == [],
             "audit-verdict")
    _require(strict.get("outcome") == "PASS" and strict.get("evaluation_scope") == "STATIC_ONLY"
             and strict.get("run_id") == run_id and strict.get("files_verified") == 379
             and strict.get("model_queries_verified") == 6
             and strict.get("producer_experiment_integrity") == "PASS", "strict-verdict")
    _require({key: value for key, value in strict.items() if key != "file_evidence"}
             == verdict.get("strict_replay"), "audit-strict-join")
    _require(verdict.get("before_after_unchanged") and
             all(value is True for value in verdict["before_after_unchanged"].values())
             and verdict.get("raw_costs_vs_strict_replay", {}).get("matched") is True,
             "audit-integrity")
    observed_at = verdict.get("audit_finished_at")
    _require(type(observed_at) is str and
             re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+\+00:00", observed_at),
             "audit-observed-at")
    _require(caller_data.get("exit_code") == 0 and caller_data.get("sources_unchanged") is True
             and caller_data.get("driver_unchanged") is True
             and caller_data.get("source_before") == caller_data.get("source_after")
             and len(caller_data["source_after"]) == 19, "caller-source-receipt")
    for relative, digest in caller_data["source_after"].items():
        _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest),
                 "source-pin-type")
        _require(_sha(_read(_rel(root, relative))) == digest, "live-source:" + relative)

    file_evidence = strict.get("file_evidence")
    _require(type(file_evidence) is dict, "strict-file-evidence")
    inputs = {}
    source_files = {
        AUDIT_REL + "/verification.json": audit_record,
        AUDIT_REL + "/strict-replay.json": strict_record,
    }
    for relative in INPUT_FILES:
        _require(relative in file_evidence and type(file_evidence[relative]) is dict,
                 "missing-file-evidence:" + relative)
        raw, record = _record(readout / relative, file_evidence[relative])
        inputs[relative] = _json(raw, relative)
        source_files[READOUT_REL + "/" + relative] = record
    design, report = inputs["design.json"], inputs["report.json"]
    _require(report == verdict.get("producer_report") and report.get("run_id") == run_id
             and report.get("experiment_integrity") == "PASS"
             and report.get("evaluation_scope") == "STATIC_ONLY"
             and report.get("planned_slots") == report.get("completed_slots") == 6
             and report.get("failure") is None, "producer-report")
    _require(design.get("run_id") == run_id and design.get("plan_id") == "frozen-readout-order-v1"
             and design.get("controls", {}).get("world_effects_permitted") is False
             and design["controls"].get("history_updates_from_new_outputs") is False
             and strict.get("scope", "").find("not executed effects") >= 0
             and verdict.get("scope", "").find("no World effects") >= 0,
             "static-world-scope")
    _require(report.get("query_accounting") == strict.get("query_accounting") and
             strict["query_accounting"] == {"reserved": 6, "returned": 6, "raised": 0,
             "unfinished": 0, "completion_http_records": 6, "completion_http_responses": 6,
             "completion_http_200": 6, "completion_transport_errors": 0,
             "without_readout": 0}, "query-accounting")

    readouts = {}
    replay_rows = strict.get("readouts")
    _require(type(replay_rows) is list and len(replay_rows) == 6, "replay-readout-count")
    for index, (relative, replay_row) in enumerate(zip(READOUT_FILES, replay_rows), 1):
        row = inputs[relative]
        slot = SLOT_ORDER[index - 1]
        _require(row.get("ordinal") == index and row.get("slot_id") == slot
                 and row.get("sample_id") == slot[:-2] and row.get("condition") == slot[-2:]
                 and replay_row.get("slot_id") == slot, "readout-slot")
        _require(row.get("proposal") == replay_row.get("proposal") and
                 row.get("static_score") == replay_row.get("static_score") and
                 row.get("model_query", {}).get("tokens") == replay_row.get("tokens"),
                 "readout-replay-join")
        readouts[slot] = row
    _require(report.get("readout_paths") == list(READOUT_FILES), "readout-paths")
    per_condition = {condition: _condition_counts(readouts, condition)
                     for condition in CONDITIONS}
    for condition, counts in per_condition.items():
        published = report["by_condition"][condition]
        _require(all(published.get(key) == value for key, value in counts.items()) and
                 published == strict["by_condition"][condition], "condition-score:" + condition)
    paired_equal = all(
        readouts[sample + "C0"]["proposal"] == readouts[sample + "C1"]["proposal"]
        and readouts[sample + "C0"]["static_score"] == readouts[sample + "C1"]["static_score"]
        for sample in SAMPLES)
    e0 = {"run_id": run_id, "audit_sha256": audit_record["sha256"],
          "observed_at": observed_at, "per_condition": per_condition,
          "paired_decisions_and_scores_equal": paired_equal, "world_effects_executed": 0}
    return {"schema_version": 1, "scenario": SCENARIO, "e0": e0,
            "question1": question1(e0, readouts), "source_files": source_files}


def build_plan(readout_dir: Path, audit_dir: Path, caller_receipt: Path) -> dict:
    """Use only the frozen, independently audited actual-model evidence."""
    return _build_plan(Path(readout_dir), Path(audit_dir), Path(caller_receipt),
                       ROOT, PINS, RUN_ID)


def write_plan(output: Path, plan: dict) -> dict:
    """Exclusive output; an interrupted run must not overwrite an earlier plan."""
    raw = (json.dumps(plan, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")) + "\n").encode("utf-8")
    with Path(output).open("xb") as stream:
        stream.write(raw)
        stream.flush()
    return {"path": str(output), "bytes": len(raw), "sha256": _sha(raw)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readout", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--caller-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args.readout, args.audit, args.caller_receipt)
        receipt = write_plan(args.output, plan)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"outcome": "FAIL", "reason": str(exc)}, sort_keys=True),
              file=sys.stderr)
        return 1
    print(json.dumps({"outcome": "PREPARED", "plan": receipt}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
