"""Six frozen public-context readouts; no World construction or actions."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import stat
import subprocess
import time
import uuid

PLAN_ID = "frozen-readout-order-v1"
KIND = "static-public-context-readout"
ORIGIN_MANIFEST_SHA256 = "6575e701107dd78a16d3a254a15a02abed8989a4295b3b53e5322965627e6ed8"
ORIGIN_RUN_ID = "9d42a778-ed01-470c-8033-5c62f7ee7e58"
SYSTEM_SHA256 = "cefb9168c4aa5ed20319a9d0b6e25f17db80870a03cb7b8df53e79558784390c"
DECISION_PINS = {
    "S0": "24c27fd755ca538dc4deadebee5f1d090018e5197e39448316e3c854bae6c4a8",
    "S1": "5d1c684ff91a0c9fc16f06ed17318be5fb9429cb8edeeb2daa0f63ce725462f5",
    "S11": "e15f63d9263d2f94bf56f182dc1626f21186ae130dfc81902f8048827dd309ef",
}
TRUSTED_PINS = {
    "self_reference_model": "4c9e8a6141bd8ca1995408a0eea9d14ff85e329c476b3231efab4db808c71664",
    "self_reference_replay": "2b6ebecde43d3d398504b659ca40ba7ffc13226aa1bc28622b3da274db21d4e2",
    "self_reference_contract": "14f7e8bd7116cde3d1798475be9559467c9dba1574612f34275aa1dd0281251d",
    "self_reference_grammar": "c71dfdd3d1f282f55bfd733daae3fcb804b870f783562d206ffe2f9eb86b727e",
}
SOURCE_NAMES = (*TRUSTED_PINS, "self_reference_readout", "self_reference_readout_replay")
SAMPLE_STEPS = {"S0": 0, "S1": 1, "S11": 11}
ORDER = ("S0C0", "S0C1", "S1C1", "S1C0", "S11C0", "S11C1")
ORIGINAL_KEYS = ("channel", "goal", "history", "identity", "last_result",
                 "last_set_step", "observation", "schema_version", "step")
VARIANT_KEYS = ("channel", "goal", "observation", "history", "identity",
                "last_result", "last_set_step", "schema_version", "step")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def save(path, value):
    write(path, encoded(value) + b"\n")


def write(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def safe_directory(path):
    """Inspect every lexical ancestor before canonicalizing; never follow links."""
    if ".." in path.parts:
        raise ValueError("artifact-root-traversal")
    original = path.absolute()
    for item in reversed((original, *original.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("unsafe-directory")
    return original.resolve()


def paths(args):
    root, origin, cache = (safe_directory(path) for path in (args.artifacts, args.origin, args.cache))
    for protected in (origin, cache):
        if root == protected or root in protected.parents or protected in root.parents:
            raise ValueError("artifact-root-overlap")
    if root.exists():
        raise ValueError("artifact-root-exists")
    return root, origin, cache


def trusted_modules():
    """Only current, pinned checkout modules execute. Retained sources are bytes."""
    folder = Path(__file__).resolve().parent
    for name, expected in TRUSTED_PINS.items():
        path = folder / (name + ".py")
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400 or sha(path.read_bytes()) != expected):
            raise ValueError("trusted-source-pin:" + name)
    modules = {name: importlib.import_module(name) for name in TRUSTED_PINS}
    for name, module in modules.items():
        if Path(module.__file__).resolve() != folder / (name + ".py"):
            raise ValueError("trusted-source-import-path")
    return (modules["self_reference_contract"], modules["self_reference_grammar"],
            modules["self_reference_model"], modules["self_reference_replay"])


def source_manifest():
    checkout = Path(__file__).resolve().parents[2]
    files = {"tools/research/" + name + ".py":
             sha((checkout / "tools/research" / (name + ".py")).read_bytes()) for name in SOURCE_NAMES}
    command = ["git", "-c", "safe.directory=" + checkout.as_posix()]
    head = subprocess.run(command + ["rev-parse", "HEAD"], cwd=checkout, capture_output=True,
                          text=True, check=True).stdout.strip()
    status = subprocess.run(command + ["status", "--porcelain"], cwd=checkout, capture_output=True,
                            text=True, check=True).stdout
    return {"head_sha": head, "dirty": bool(status.strip()), "files": files}


def inventory(root, replay):
    disk = replay.Disk(root)
    files = {name: sha(disk.raw(name, 64 * 1024 * 1024)) for name in sorted(disk.files)}
    return {"schema_version": 1, "file_count": len(files), "manifest_sha256": sha(encoded(files)), "files": files}


def check_origin(origin, replay):
    manifest = inventory(origin, replay)
    if manifest["file_count"] != 289 or manifest["manifest_sha256"] != ORIGIN_MANIFEST_SHA256:
        raise ValueError("origin-manifest-pin")
    result = replay.verify_artifacts(origin)
    if result.get("outcome") != "PASS":
        raise ValueError("origin-replay-not-pass")
    design = replay.decoded((origin / "design.json").read_bytes())
    if design["run_id"] != ORIGIN_RUN_ID or design["plan_id"] != "action-progress-v1":
        raise ValueError("origin-identity")
    return manifest, result


def render_variant(user, context, contract):
    """Move canonical top-level member bytes, preserving every nested serialization."""
    if user != contract.render_context(context, "relational"):
        raise ValueError("origin-user-not-canonical")
    if tuple(sorted(context)) != ORIGINAL_KEYS:
        raise ValueError("origin-context-fields")
    members = {key: encoded(key).decode() + ":" + encoded(context[key]).decode() for key in ORIGINAL_KEYS}
    result = '{"related_facts":{' + ",".join(members[key] for key in VARIANT_KEYS) + "}}"
    if encoded(json.loads(result)) != encoded(json.loads(user)):
        raise ValueError("transformation-loss")
    return result


def initialize(root, origin, manifest, validation, before, contract, grammar, replay):
    root.mkdir(parents=True, exist_ok=False)
    for folder in ("origin", "verification-source", "inputs", "ledger", "readouts"):
        (root / folder).mkdir()
    original_disk = replay.Disk(origin)
    for relative, expected in manifest["files"].items():
        raw = original_disk.raw(relative, 64 * 1024 * 1024)
        if sha(raw) != expected:
            raise ValueError("origin-changed-during-copy")
        dest = root / "origin" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        write(dest, raw)
    if inventory(root / "origin", replay) != manifest:
        raise ValueError("origin-copy")
    copied_validation = replay.verify_artifacts(root / "origin")
    if encoded(copied_validation) != encoded(validation):
        raise ValueError("origin-copy-replay")
    save(root / "origin-manifest.json", manifest)
    save(root / "origin-validation.json", copied_validation)
    checkout = Path(__file__).resolve().parents[2]
    for relative, expected in before["files"].items():
        raw = (checkout / relative).read_bytes()
        if sha(raw) != expected:
            raise ValueError("source-changed-before-retention")
        dest = root / "verification-source" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        write(dest, raw)
    save(root / "verification-source/manifest.json", before)
    system_raw = (root / "origin/prompts/action-first-public-feedback-v1.txt").read_bytes()
    if sha(system_raw) != SYSTEM_SHA256:
        raise ValueError("system-pin")
    write(root / "system.txt", system_raw)
    samples, contexts = [], {}
    for sample, step in SAMPLE_STEPS.items():
        relative = f"origin/00-normal-action-first-public-feedback-v1/decision-{step + 1:03d}.json"
        raw = (root / relative).read_bytes()
        if sha(raw) != DECISION_PINS[sample]:
            raise ValueError("selected-decision-pin")
        decision = replay.decoded(raw)
        context, user = decision["context"], decision["user"]
        if type(context["step"]) is not int or context["step"] != step:
            raise ValueError("selected-context-step")
        context_path = f"inputs/{sample}.context.json"
        save(root / context_path, context)
        write(root / f"inputs/{sample}C0.user.txt", user.encode("utf-8"))
        write(root / f"inputs/{sample}C1.user.txt", render_variant(user, context, contract).encode("utf-8"))
        samples.append({"sample_id": sample, "origin_decision_path": relative,
            "origin_decision_sha256": sha(raw), "context_step": step, "context_path": context_path,
            "context_sha256": contract.digest(context), "original_user_sha256": sha(user.encode("utf-8"))})
        contexts[sample] = context
    slots = []
    for number, slot_id in enumerate(ORDER, 1):
        sample, condition = slot_id[:-2], slot_id[-2:]
        user_path = f"inputs/{slot_id}.user.txt"
        slots.append({"ordinal": number, "slot_id": slot_id, "sample_id": sample, "condition": condition,
            "user_path": user_path, "user_sha256": sha((root / user_path).read_bytes()),
            "context_sha256": contract.digest(contexts[sample]),
            "readout_path": f"readouts/readout-{number:04d}.json", "query_artifact_dir": f"model/query-{number:04d}"})
    controls = {"cpu_threads": 2, "context_size": 2048, "max_tokens": 192, "seed": 1, "temperature": 0,
        "cache_prompt": False, "stream": False, "output_constraint": grammar.GRAMMAR_ID,
        "grammar_sha256": sha(grammar.DECISION_GRAMMAR.encode()), "score_version": contract.SCORE_VERSION,
        "feedback_projection": contract.MODEL_CONTEXT_PROJECTION, "max_query_invocations": 6, "retries": 0,
        "world_effects_permitted": False, "history_updates_from_new_outputs": False,
        "transformation": "move-observation-before-history-v1", "lossless": True, "system_prompt_equal": True,
        "generation_settings_reference_sha256": contract.digest(validation["generation_settings"])}
    design = {"schema_version": 1, "kind": KIND, "relationship": "RESEARCH", "plan_id": PLAN_ID,
        "run_id": str(uuid.uuid4()), "artifact_root": str(root), "source": before,
        "origin": {"file_count": 289, "manifest_sha256": manifest["manifest_sha256"],
                   "run_id": ORIGIN_RUN_ID, "plan_id": "action-progress-v1"},
        "system_path": "system.txt", "system_sha256": SYSTEM_SHA256,
        "samples": samples, "slots": slots, "controls": controls}
    save(root / "design.json", design)
    return design, contexts, system_raw.decode("utf-8")


def error_record(exc):
    return {"type": type(exc).__name__, "reason": str(exc), "code": getattr(exc, "code", None)}


def query_slot(root, design, slot, sample, system, user, model):
    """Reserve a slot before the no-retry wrapper, even if its preflight fails."""
    number = slot["ordinal"]
    if type(number) is not int or not 1 <= number <= 6 or slot != design["slots"][number - 1]:
        raise ValueError("query-slot-limit-or-plan")
    if (type(system) is not str or type(user) is not str or sha(system.encode("utf-8")) != SYSTEM_SHA256
            or sha(user.encode("utf-8")) != slot["user_sha256"]):
        raise ValueError("query-input-hash")
    prefix = root / f"ledger/query-{number:04d}"
    start = {"schema_version": 1, "kind": "query-start", "invocation_index": number,
        "run_id": design["run_id"], "plan_id": PLAN_ID, "slot_id": slot["slot_id"],
        "sample_id": slot["sample_id"], "condition": slot["condition"],
        "origin_decision_sha256": sample["origin_decision_sha256"],
        "context_path": sample["context_path"], "context_sha256": slot["context_sha256"],
        "user_path": slot["user_path"], "user_sha256": slot["user_sha256"],
        "system_sha256": SYSTEM_SHA256, "readout_path": slot["readout_path"],
        "query_artifact_dir": slot["query_artifact_dir"]}
    start_path = Path(str(prefix) + ".start.json")
    save(start_path, start)
    start_hash, started = sha(start_path.read_bytes()), time.monotonic()
    failure = None
    try:
        from self_reference_grammar import DECISION_GRAMMAR
        return model.query(system, user, grammar=DECISION_GRAMMAR)
    except BaseException as exc:
        failure = error_record(exc)
        raise
    finally:
        result_path = root / slot["query_artifact_dir"] / "result.json"
        save(Path(str(prefix) + ".finish.json"), {"schema_version": 1, "kind": "query-finish",
            "invocation_index": number, "start_sha256": start_hash,
            "status": "RAISED" if failure else "RETURNED", "query_artifact_dir": slot["query_artifact_dir"],
            "result_sha256": sha(result_path.read_bytes()) if result_path.is_file() else None,
            "error": failure, "elapsed_seconds": time.monotonic() - started})


def accounting(root):
    starts = [json.loads(path.read_bytes()) for path in sorted((root / "ledger").glob("*.start.json"))]
    finishes = [json.loads(path.read_bytes()) for path in sorted((root / "ledger").glob("*.finish.json"))]
    http = [json.loads(path.read_bytes()) for path in sorted((root / "model").glob("query-*/completion.http.json"))]
    return {"reserved": len(starts), "returned": sum(x["status"] == "RETURNED" for x in finishes),
        "raised": sum(x["status"] == "RAISED" for x in finishes), "unfinished": len(starts) - len(finishes),
        "completion_http_records": len(http), "completion_http_responses": sum(type(x.get("status")) is int for x in http),
        "completion_http_200": sum(x.get("status") == 200 for x in http),
        "completion_transport_errors": sum(x.get("outcome") == "INVALID" for x in http),
        "without_readout": sum(not (root / x["readout_path"]).is_file() for x in starts)}


def raw_cost(root):
    folders = sorted((root / "model").glob("query-*"))
    result = {"query_directories": len(folders), "prompt_tokens_known": 0, "generated_tokens_known": 0,
        "token_records_known": 0, "unknown_token_queries": [], "query_elapsed_seconds_known": 0,
        "elapsed_records_known": 0, "unknown_elapsed_queries": []}
    for folder in folders:
        relative = folder.relative_to(root).as_posix()
        try:
            http = json.loads((folder / "completion.http.json").read_bytes())
            response = json.loads((folder / "completion.response.json").read_bytes())
            p, g = response["tokens_evaluated"], response["tokens_predicted"]
            if type(http.get("status")) is not int or http["status"] != 200 or any(type(x) is not int or x < 0 for x in (p, g)):
                raise ValueError("unknown-tokens")
            result["prompt_tokens_known"] += p
            result["generated_tokens_known"] += g
            result["token_records_known"] += 1
        except (OSError, ValueError, KeyError, TypeError):
            result["unknown_token_queries"].append(relative)
        try:
            value = json.loads((folder / "result.json").read_bytes())["elapsed_seconds"]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("unknown-elapsed")
            result["query_elapsed_seconds_known"] += value
            result["elapsed_records_known"] += 1
        except (OSError, ValueError, KeyError, TypeError):
            result["unknown_elapsed_queries"].append(relative)
    return result


def aggregate(readouts):
    result = {}
    for condition in ("C0", "C1"):
        scores = [x["static_score"] for x in readouts if x["condition"] == condition]
        action = [s["action_correct_from_visible_facts"] and s["revision_correct"] for s in scores]
        result[condition] = {"readouts": len(scores), "action_revision_correct": sum(action),
            "attribution_correct": sum(s["attribution_correct"] for s in scores),
            "joint_correct": sum(a and s["attribution_correct"] for a, s in zip(action, scores)),
            "prediction_evaluable": sum(s["prediction_evaluable"] for s in scores),
            "prediction_correct": sum(s["prediction_matches_visible_expectation"] for s in scores),
            "schema_valid": sum(s["schema_valid"] for s in scores)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("origin", "artifacts", "cache"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    root, origin, cache = paths(args)
    contract, grammar, model_module, replay = trusted_modules()
    manifest, validation = check_origin(origin, replay)
    before = source_manifest()
    design, contexts, system = initialize(root, origin, manifest, validation, before, contract, grammar, replay)
    started, failure, readouts = time.monotonic(), None, []
    try:
        with model_module.LocalModel(cache, root / "model", context_size=2048, max_tokens=192, seed=1) as model:
            for slot in design["slots"]:
                print(json.dumps({"event": "readout-start", "ordinal": slot["ordinal"], "slot_id": slot["slot_id"]}), flush=True)
                sample = next(x for x in design["samples"] if x["sample_id"] == slot["sample_id"])
                user = (root / slot["user_path"]).read_bytes().decode("utf-8")
                query = query_slot(root, design, slot, sample, system, user, model)
                if encoded(query["raw_response"]["generation_settings"]) != encoded(validation["generation_settings"]):
                    raise ValueError("generation-settings-origin-drift")
                proposal = contract.parse_decision(query["content"])
                compact = json.dumps({key: proposal[key] for key in
                    ("action", "expected_revision", "prediction", "attribution")}, separators=(",", ":"))
                if query["content"] != compact:
                    raise ValueError("response-outside-static-grammar")
                score = contract.score_decision(contexts[slot["sample_id"]], proposal)
                prefix = root / f"ledger/query-{slot['ordinal']:04d}"
                row = {"schema_version": 1, "kind": "static-readout",
                    **{key: slot[key] for key in ("ordinal", "slot_id", "sample_id", "condition", "context_sha256", "user_sha256")},
                    "system_sha256": SYSTEM_SHA256,
                    "start_sha256": sha(Path(str(prefix) + ".start.json").read_bytes()),
                    "finish_sha256": sha(Path(str(prefix) + ".finish.json").read_bytes()),
                    "content": query["content"], "proposal": proposal, "static_score": score, "model_query": query}
                save(root / slot["readout_path"], row)
                readouts.append(row)
                print(json.dumps({"event": "readout-done", "ordinal": slot["ordinal"], "slot_id": slot["slot_id"],
                                  "static_score": score}), flush=True)
        # Exact raw receipts, pinned settings and owned cleanup use the old trusted verifier.
        disk = replay.Disk(root)
        replay.model_evidence(disk, design)
        disk.static_grammar = grammar.DECISION_GRAMMAR
        disk.generation_settings = validation["generation_settings"]
        for slot, row in zip(design["slots"], readouts):
            replay.query_check(disk, {**design, "system_prompt": system},
                               {**row, "user": (root / slot["user_path"]).read_text(encoding="utf-8")}, slot["ordinal"])
    except Exception as exc:
        failure = error_record(exc)
    after = source_manifest()
    final_origin = inventory(origin, replay)
    counts, cost = accounting(root), raw_cost(root)
    if before["files"] != after["files"] or final_origin != manifest:
        failure = error_record(ValueError("source-or-origin-changed"))
    if not failure and (len(readouts) != 6 or any(counts[key] != 6 for key in
            ("reserved", "returned", "completion_http_records", "completion_http_responses", "completion_http_200"))
            or any(counts[key] for key in ("raised", "unfinished", "completion_transport_errors", "without_readout"))
            or cost["query_directories"] != 6 or cost["token_records_known"] != 6 or cost["elapsed_records_known"] != 6):
        failure = error_record(ValueError("readout-coverage"))
    report = {"schema_version": 1, "kind": KIND, "relationship": "RESEARCH", "plan_id": PLAN_ID,
        "run_id": design["run_id"], "experiment_integrity": "FAIL" if failure else "PASS",
        "failure": failure, "evaluation_scope": "STATIC_ONLY", "planned_slots": 6, "completed_slots": len(readouts),
        "readout_paths": [slot["readout_path"] for slot in design["slots"][:len(readouts)]],
        "source_unchanged": before["files"] == after["files"], "source_before": before, "source_after": after,
        "origin_unchanged": manifest == final_origin, "origin_manifest_before": manifest["manifest_sha256"],
        "origin_manifest_after": final_origin["manifest_sha256"], "query_accounting": counts, "raw_cost": cost,
        "by_condition": aggregate(readouts), "elapsed_seconds": time.monotonic() - started}
    save(root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
