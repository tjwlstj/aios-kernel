"""Read-only, fixed-plan RESEARCH readout verification; no World effects.

Only pinned local verification code is executed. Retained Python is evidence,
never an import source. A complete static readout is not a behavioral effect.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

COMMON_PINS = {
    "self_reference_model.py": "4c9e8a6141bd8ca1995408a0eea9d14ff85e329c476b3231efab4db808c71664",
    "self_reference_replay.py": "2b6ebecde43d3d398504b659ca40ba7ffc13226aa1bc28622b3da274db21d4e2",
    "self_reference_contract.py": "14f7e8bd7116cde3d1798475be9559467c9dba1574612f34275aa1dd0281251d",
    "self_reference_grammar.py": "c71dfdd3d1f282f55bfd733daae3fcb804b870f783562d206ffe2f9eb86b727e",
}
for _name in ("self_reference_replay.py", "self_reference_contract.py"):
    if hashlib.sha256(Path(__file__).with_name(_name).read_bytes()).hexdigest() != COMMON_PINS[_name]:
        raise ValueError("trusted-local-consumer-pin:" + _name)

import self_reference_replay as replay

contract = replay.contract
for _module, _name in ((replay, "self_reference_replay.py"), (contract, "self_reference_contract.py")):
    if Path(_module.__file__).resolve() != Path(__file__).with_name(_name).resolve():
        raise ValueError("trusted-local-import-path:" + _name)
require, same, sha, decoded = replay.require, replay.same, replay.sha, replay.decoded
PLAN_ID = "frozen-readout-order-v1"
ORIGIN_RUN_ID = "9d42a778-ed01-470c-8033-5c62f7ee7e58"
ORIGIN_MANIFEST_SHA256 = "6575e701107dd78a16d3a254a15a02abed8989a4295b3b53e5322965627e6ed8"
DECISION_PINS = {
    "S0": "24c27fd755ca538dc4deadebee5f1d090018e5197e39448316e3c854bae6c4a8",
    "S1": "5d1c684ff91a0c9fc16f06ed17318be5fb9429cb8edeeb2daa0f63ce725462f5",
    "S11": "e15f63d9263d2f94bf56f182dc1626f21186ae130dfc81902f8048827dd309ef",
}
SYSTEM_SHA256 = "cefb9168c4aa5ed20319a9d0b6e25f17db80870a03cb7b8df53e79558784390c"
GRAMMAR_SHA256 = "c800806866b94587eccedf085d902808d3730b518946f05b0e4805d2c1de7f2c"
SAMPLE_STEPS = (("S0", 0), ("S1", 1), ("S11", 11))
SLOT_ORDER = (("S0", "C0"), ("S0", "C1"), ("S1", "C1"),
              ("S1", "C0"), ("S11", "C0"), ("S11", "C1"))
ORIGINAL_ORDER = ("channel", "goal", "history", "identity", "last_result",
                  "last_set_step", "observation", "schema_version", "step")
SOURCE_FILES = {"tools/research/" + name for name in COMMON_PINS} | {
    "tools/research/self_reference_readout.py", "tools/research/self_reference_readout_replay.py"}
DESIGN_KEYS = {"schema_version", "kind", "relationship", "plan_id", "run_id", "artifact_root",
    "source", "origin", "system_path", "system_sha256", "samples", "slots", "controls"}
REPORT_KEYS = {"schema_version", "kind", "relationship", "plan_id", "run_id", "experiment_integrity",
    "failure", "evaluation_scope", "planned_slots", "completed_slots", "readout_paths",
    "source_unchanged", "source_before", "source_after", "origin_unchanged", "origin_manifest_before",
    "origin_manifest_after", "query_accounting", "raw_cost", "by_condition", "elapsed_seconds"}
READOUT_KEYS = {"schema_version", "kind", "ordinal", "slot_id", "sample_id", "condition",
    "context_sha256", "user_sha256", "system_sha256", "start_sha256", "finish_sha256",
    "content", "proposal", "static_score", "model_query"}


def render_variant(original_user, condition):
    """Move raw member slices, preserving every nested byte and other key order."""
    require(type(original_user) is str, "original-user-type")
    value = decoded(original_user.encode("utf-8"))
    require(type(value) is dict and list(value) == ["related_facts"]
            and type(value["related_facts"]) is dict, "original-user-wrapper")
    require(tuple(value["related_facts"]) == ORIGINAL_ORDER, "original-user-order")
    require(original_user == contract.render_context(value["related_facts"], "relational"),
            "original-user-canonical")
    require(condition in ("C0", "C1"), "readout-condition")
    if condition == "C0":
        return original_user
    # raw_decode only locates spans; decoded() above already rejected duplicates,
    # non-finite numbers and excessive complexity. No nested object is reencoded.
    prefix = '{"related_facts":{'
    position, fragments, decoder = len(prefix), {}, json.JSONDecoder()
    for index, name in enumerate(ORIGINAL_ORDER):
        start = position
        key, position = decoder.raw_decode(original_user, position)
        require(key == name and original_user[position] == ":", "original-member-boundary")
        _, position = decoder.raw_decode(original_user, position + 1)
        fragments[name] = original_user[start:position]
        if index < len(ORIGINAL_ORDER) - 1:
            require(original_user[position] == ",", "original-member-separator")
            position += 1
    require(original_user[position:] == "}}", "original-user-suffix")
    order = list(ORIGINAL_ORDER)
    order.remove("observation")
    order.insert(order.index("history"), "observation")
    rendered = prefix + ",".join(fragments[name] for name in order) + "}}"
    require(rendered != original_user, "readout-renderer-no-difference")
    same(decoded(rendered.encode("utf-8")), value, "readout-information-change")
    return rendered


def grammar_proposal(content):
    """Recognize this pinned static grammar's exact compact decision language."""
    proposal = contract.parse_decision(content)
    ordered = {name: proposal[name] for name in
               ("action", "expected_revision", "prediction", "attribution")}
    expected = json.dumps(ordered, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    require(content == expected, "readout-grammar-language")
    return proposal


def origin_check(disk):
    files = {name[7:]: sha(disk.raw(name, 64 * 1024 * 1024 if name.endswith(".log") else 1024 * 1024))
             for name in disk.files if name.startswith("origin/")}
    require(len(files) == 289 and contract.digest(files) == ORIGIN_MANIFEST_SHA256, "origin-manifest-pin")
    same(disk.json("origin-manifest.json"), {"schema_version": 1, "file_count": 289,
         "manifest_sha256": ORIGIN_MANIFEST_SHA256, "files": files}, "origin-manifest-record")
    verdict = replay.verify_artifacts(disk.root / "origin")
    require(verdict.get("outcome") == "PASS" and verdict.get("files_verified") == 289
            and verdict.get("run_id") == ORIGIN_RUN_ID and verdict.get("model_queries_verified") == 24
            and verdict.get("replay_consumer_sha256") == COMMON_PINS["self_reference_replay.py"],
            "origin-independent-replay:" + str(verdict.get("reason")))
    same(disk.json("origin-validation.json"), verdict, "origin-retained-validation")
    return verdict


def source_check(disk, design, report):
    source = design["source"]
    require(type(source) is dict and source.keys() == {"head_sha", "dirty", "files"}
            and type(source["head_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", source["head_sha"])
            and type(source["dirty"]) is bool, "readout-source-provenance")
    same(source, report["source_before"], "readout-source-before")
    same(source, report["source_after"], "readout-source-after")
    same(source, disk.json("verification-source/manifest.json"), "readout-retained-manifest")
    require(report["source_unchanged"] is True and source["files"].keys() == SOURCE_FILES,
            "readout-source-coverage")
    local_files = {}
    for relative, digest in source["files"].items():
        name = relative.rsplit("/", 1)[1]
        raw = disk.raw("verification-source/" + relative)
        require(type(digest) is str and sha(raw) == digest, "readout-retained-source-hash:" + name)
        local = Path(__file__).with_name(name).read_bytes()
        require(local == raw, "readout-source-version:" + name)
        if name in COMMON_PINS:
            require(digest == COMMON_PINS[name], "readout-common-source-pin:" + name)
        local_files[name] = {"bytes": len(local), "sha256": sha(local)}
    tree = ast.parse(disk.raw("verification-source/tools/research/self_reference_grammar.py").decode("utf-8"))
    literals = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("GRAMMAR_ID", "DECISION_GRAMMAR"):
                    require(target.id not in literals, "readout-grammar-duplicate")
                    literals[target.id] = ast.literal_eval(node.value)
    require(literals.get("GRAMMAR_ID") == "static-decision-v1"
            and type(literals.get("DECISION_GRAMMAR")) is str
            and sha(literals["DECISION_GRAMMAR"].encode("utf-8")) == GRAMMAR_SHA256, "readout-grammar-pin")
    disk.static_grammar = literals["DECISION_GRAMMAR"]
    return local_files


def input_plan(disk, design):
    system = disk.raw("system.txt", 65536)
    require(sha(system) == SYSTEM_SHA256 and system == disk.raw(
        "origin/prompts/action-first-public-feedback-v1.txt", 65536), "readout-system-pin")
    samples, contexts, users = [], {}, {}
    for sample, step in SAMPLE_STEPS:
        relative = "origin/00-normal-action-first-public-feedback-v1/decision-%03d.json" % (step + 1)
        raw = disk.raw(relative)
        require(sha(raw) == DECISION_PINS[sample], "origin-decision-pin:" + sample)
        decision = decoded(raw)
        context, user = decision["context"], decision["user"]
        require(type(context["step"]) is int and context["step"] == step, "readout-frozen-step")
        same(contract.model_context(context), context, "readout-derived-label-exposure")
        require(contract.render_context(context, "relational") == user, "readout-origin-user-context")
        path = "inputs/" + sample + ".context.json"
        require(disk.raw(path) == contract.encoded(context) + b"\n", "readout-context-bytes")
        samples.append({"sample_id": sample, "origin_decision_path": relative,
            "origin_decision_sha256": DECISION_PINS[sample], "context_step": step,
            "context_path": path, "context_sha256": contract.digest(context),
            "original_user_sha256": sha(user.encode("utf-8"))})
        contexts[sample] = context
        for condition in ("C0", "C1"):
            slot = sample + condition
            expected = render_variant(user, condition)
            require(disk.raw("inputs/" + slot + ".user.txt", 65536) == expected.encode("utf-8"),
                    "readout-renderer-bytes:" + slot)
            users[slot] = expected
    same(design["samples"], samples, "readout-sample-plan")
    slots = [{"ordinal": number, "slot_id": sample + condition, "sample_id": sample,
        "condition": condition, "user_path": "inputs/" + sample + condition + ".user.txt",
        "user_sha256": sha(users[sample + condition].encode("utf-8")),
        "context_sha256": contract.digest(contexts[sample]),
        "readout_path": "readouts/readout-%04d.json" % number,
        "query_artifact_dir": "model/query-%04d" % number}
        for number, (sample, condition) in enumerate(SLOT_ORDER, 1)]
    same(design["slots"], slots, "readout-slot-plan")
    return system.decode("utf-8"), contexts, users, slots


def raw_diagnostics(disk):
    """Inventory every new response, including invalid/unassigned tails.

    These unauthenticated counters are explicitly not a partial replay verdict.
    Missing or malformed costs stay unknown rather than becoming zero cost.
    """
    queries = sorted({name.split("/")[1] for name in disk.files
                      if re.match(r"model/query-[0-9]{4}/", name)})
    rows = []
    for query in queries:
        prefix = "model/" + query + "/"
        row = {"query_artifact_dir": prefix[:-1], "http_status": None,
               "prompt_tokens": None, "generated_tokens": None,
               "wrapper_elapsed_seconds": None, "result_outcome": None, "errors": []}
        for filename in ("completion.http.json", "completion.response.json", "result.json"):
            name = prefix + filename
            if name not in disk.files:
                continue
            try:
                record = disk.json(name)
                if filename == "completion.http.json":
                    status = record.get("status")
                    row["http_status"] = status if type(status) is int else None
                elif filename == "completion.response.json":
                    for source, target in (("tokens_evaluated", "prompt_tokens"),
                                           ("tokens_predicted", "generated_tokens")):
                        value = record.get(source)
                        row[target] = value if type(value) is int and value >= 0 else None
                else:
                    row["result_outcome"] = record.get("outcome")
                    value = record.get("elapsed_seconds")
                    replay.finite_seconds(value, "raw-result-elapsed")
                    row["wrapper_elapsed_seconds"] = value
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row["errors"].append({"path": name, "reason": str(exc)})
        rows.append(row)
    http200 = [row for row in rows if row["http_status"] == 200]
    return {"status": "UNVERIFIED_RAW_DIAGNOSTICS", "partial_evidence_replayed": False,
        "query_directories": len(rows), "completion_http_200": len(http200), "queries": rows,
        "http_200_costs": {key: {"known_sum": sum(row[key] for row in http200 if row[key] is not None),
            "unknown_queries": sum(row[key] is None for row in http200)}
            for key in ("prompt_tokens", "generated_tokens", "wrapper_elapsed_seconds")}}


def verify_complete(disk, design, report):
    require(design.keys() == DESIGN_KEYS and report.keys() == REPORT_KEYS, "readout-document-fields")
    require(type(design["run_id"]) is str and re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", design["run_id"]),
        "readout-run-id")
    identity = {"schema_version": 1, "kind": "static-public-context-readout", "relationship": "RESEARCH",
                "plan_id": PLAN_ID, "run_id": design["run_id"]}
    for item in (design, report):
        same({key: item[key] for key in identity}, identity, "readout-plan-identity")
    require(report["experiment_integrity"] == "PASS" and report["failure"] is None, "readout-producer-verdict")
    same(report["evaluation_scope"], "STATIC_ONLY", "readout-evaluation-scope")
    replay.recorded_path(design["artifact_root"])
    local_sources = source_check(disk, design, report)
    origin = origin_check(disk)
    same(design["origin"], {"file_count": 289, "manifest_sha256": ORIGIN_MANIFEST_SHA256,
         "run_id": ORIGIN_RUN_ID, "plan_id": "action-progress-v1"}, "readout-origin-identity")
    require(report["origin_unchanged"] is True, "readout-origin-changed")
    same(report["origin_manifest_before"], ORIGIN_MANIFEST_SHA256, "readout-origin-before")
    same(report["origin_manifest_after"], ORIGIN_MANIFEST_SHA256, "readout-origin-after")
    same({key: design[key] for key in ("system_path", "system_sha256")},
         {"system_path": "system.txt", "system_sha256": SYSTEM_SHA256}, "readout-system-record")
    controls = {"cpu_threads": 2, "context_size": 2048, "max_tokens": 192, "seed": 1,
        "temperature": 0, "cache_prompt": False, "stream": False,
        "output_constraint": "static-decision-v1", "grammar_sha256": GRAMMAR_SHA256,
        "score_version": "public-feedback-prediction-v2",
        "feedback_projection": "raw-writer-without-derived-attribution-v1",
        "max_query_invocations": 6, "retries": 0, "world_effects_permitted": False,
        "history_updates_from_new_outputs": False, "transformation": "move-observation-before-history-v1",
        "lossless": True, "system_prompt_equal": True,
        "generation_settings_reference_sha256": contract.digest(origin["generation_settings"])}
    same(design["controls"], controls, "readout-controls")
    system, contexts, users, slots = input_plan(disk, design)
    evidence = replay.model_evidence(disk, design)
    conditions = {condition: {"readouts": 0, "action_revision_correct": 0, "attribution_correct": 0,
        "joint_correct": 0, "prediction_evaluable": 0, "prediction_correct": 0, "schema_valid": 0}
        for condition in ("C0", "C1")}
    queries, results = [], []
    for slot in slots:
        number, sample, condition = slot["ordinal"], slot["sample_id"], slot["condition"]
        context, user = contexts[sample], users[slot["slot_id"]]
        readout = disk.json(slot["readout_path"])
        require(readout.keys() == READOUT_KEYS, "readout-fields")
        expected = {"schema_version": 1, "kind": "static-readout", "ordinal": number,
            "slot_id": slot["slot_id"], "sample_id": sample, "condition": condition,
            "context_sha256": slot["context_sha256"], "user_sha256": slot["user_sha256"],
            "system_sha256": SYSTEM_SHA256}
        same({key: readout[key] for key in expected}, expected, "readout-slot-identity")
        start_path = "ledger/query-%04d.start.json" % number
        finish_path = "ledger/query-%04d.finish.json" % number
        start_raw, finish_raw = disk.raw(start_path), disk.raw(finish_path)
        same(readout["start_sha256"], sha(start_raw), "readout-start-hash")
        same(readout["finish_sha256"], sha(finish_raw), "readout-finish-hash")
        same(decoded(start_raw), {"schema_version": 1, "kind": "query-start", "invocation_index": number,
            "run_id": design["run_id"], "plan_id": PLAN_ID, "slot_id": slot["slot_id"],
            "sample_id": sample, "condition": condition, "origin_decision_sha256": DECISION_PINS[sample],
            "context_path": "inputs/" + sample + ".context.json", "context_sha256": slot["context_sha256"],
            "user_path": slot["user_path"], "user_sha256": slot["user_sha256"],
            "system_sha256": SYSTEM_SHA256, "readout_path": slot["readout_path"],
            "query_artifact_dir": slot["query_artifact_dir"]}, "readout-ledger-start")
        finish = decoded(finish_raw)
        replay.finite_seconds(finish["elapsed_seconds"], "readout-ledger-elapsed")
        same(finish, {"schema_version": 1, "kind": "query-finish", "invocation_index": number,
            "start_sha256": sha(start_raw), "status": "RETURNED", "query_artifact_dir": slot["query_artifact_dir"],
            "result_sha256": sha(disk.raw(slot["query_artifact_dir"] + "/result.json")),
            "error": None, "elapsed_seconds": finish["elapsed_seconds"]}, "readout-ledger-finish")
        query = replay.query_check(disk, {**design, "system_prompt": system},
                                   {**readout, "user": user}, number)
        same(query["raw_response"]["generation_settings"], origin["generation_settings"],
             "readout-origin-generation-settings")
        proposal = grammar_proposal(readout["content"])
        same(readout["proposal"], proposal, "readout-raw-proposal")
        score = contract.score_decision(context, proposal)
        same(readout["static_score"], score, "readout-static-score")
        counts = conditions[condition]
        counts["readouts"] += 1
        action_revision = score["action_correct_from_visible_facts"] and score["revision_correct"]
        counts["action_revision_correct"] += int(action_revision)
        counts["attribution_correct"] += int(score["attribution_correct"])
        counts["joint_correct"] += int(action_revision and score["attribution_correct"])
        counts["prediction_evaluable"] += int(score["prediction_evaluable"])
        counts["prediction_correct"] += int(score["prediction_matches_visible_expectation"])
        counts["schema_valid"] += 1
        queries.append(query)
        results.append({"slot_id": slot["slot_id"], "proposal": proposal, "static_score": score,
                        "tokens": query["tokens"], "model_elapsed_seconds": query["elapsed_seconds"]})
    for suffix in ("start", "finish"):
        same(disk.matching("ledger/", r"query-[0-9]{4}\." + suffix + r"\.json"),
             ["ledger/query-%04d.%s.json" % (n, suffix) for n in range(1, 7)], "readout-ledger-coverage")
    same(disk.matching("model/", r"query-[0-9]{4}/completion\.http\.json"),
         ["model/query-%04d/completion.http.json" % n for n in range(1, 7)], "readout-http-coverage")
    same(report["readout_paths"], [slot["readout_path"] for slot in slots], "readout-report-paths")
    same(report["planned_slots"], 6, "readout-planned-coverage")
    same(report["completed_slots"], 6, "readout-completed-coverage")
    accounting = {"reserved": 6, "returned": 6, "raised": 0, "unfinished": 0,
        "completion_http_records": 6, "completion_http_responses": 6, "completion_http_200": 6,
        "completion_transport_errors": 0, "without_readout": 0}
    same(report["query_accounting"], accounting, "readout-query-accounting")
    cost = {"query_directories": 6, "prompt_tokens_known": sum(query["tokens"]["prompt"] for query in queries),
        "generated_tokens_known": sum(query["tokens"]["predicted"] for query in queries),
        "token_records_known": 6, "unknown_token_queries": [],
        "query_elapsed_seconds_known": sum(query["elapsed_seconds"] for query in queries),
        "elapsed_records_known": 6, "unknown_elapsed_queries": []}
    same(report["raw_cost"], cost, "readout-raw-cost")
    same(report["by_condition"], conditions, "readout-condition-summary")
    replay.finite_seconds(report["elapsed_seconds"], "readout-report-elapsed")
    require(disk.read.keys() == disk.files.keys(), "unreferenced-artifacts:" +
            ",".join(sorted(disk.files.keys() - disk.read.keys())[:5]))
    for relative in list(disk.read):
        disk.raw(relative, 64 * 1024 * 1024 if relative.endswith(".log") else 1024 * 1024)
    for name, before in local_sources.items():
        raw = Path(__file__).with_name(name).read_bytes()
        same({"bytes": len(raw), "sha256": sha(raw)}, before, "readout-local-source-changed:" + name)
    # A second inventory detects additions/removals during replay, as well as reads' byte checks.
    same(sorted(replay.Disk(disk.root).files), sorted(disk.files), "readout-inventory-changed")
    return {"schema_version": 1, "outcome": "PASS", "relationship": "RESEARCH", "plan_id": PLAN_ID,
        "run_id": design["run_id"], "evaluation_scope": "STATIC_ONLY", "producer_experiment_integrity": "PASS",
        "source_files_verified": 6, "files_verified": len(disk.read), "origin_files_verified": 289,
        "origin_manifest_sha256": ORIGIN_MANIFEST_SHA256, "origin_independent_replay": "PASS",
        "replay_consumer_sha256": sha(Path(__file__).read_bytes()), "model_queries_verified": 6,
        "query_accounting": accounting, "raw_cost": cost, "by_condition": conditions, "readouts": results,
        "model_lifecycle": evidence, "generation_settings": disk.generation_settings,
        "generation_settings_sha256": contract.digest(disk.generation_settings),
        "file_evidence": disk.read,
        "scope": "Six fixed-state decisions, not executed effects, goal completion or causal generalization."}


def verify_artifacts(artifacts):
    """Return an independent verdict without writes, model calls or artifact execution."""
    try:
        disk = replay.Disk(artifacts)
        design, report = disk.json("design.json"), disk.json("report.json")
        if report.get("experiment_integrity") == "FAIL":
            return {"schema_version": 1, "outcome": "NOT_EVALUABLE", "relationship": "RESEARCH",
                "plan_id": PLAN_ID, "evaluation_scope": "STATIC_ONLY", "producer_experiment_integrity": "FAIL",
                "reason": "producer-failed-experiment", "producer_failure": report.get("failure"),
                "partial_evidence_replayed": False, "raw_diagnostics": raw_diagnostics(disk),
                "replay_consumer_sha256": sha(Path(__file__).read_bytes())}
        return verify_complete(disk, design, report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError,
            RecursionError, OverflowError, SyntaxError) as exc:
        return {"schema_version": 1, "outcome": "FAIL", "relationship": "RESEARCH", "plan_id": PLAN_ID,
                "evaluation_scope": "STATIC_ONLY", "reason": str(exc), "error_type": type(exc).__name__}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify_artifacts(args.artifacts)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
