"""Read-only replay of retained RESEARCH files; never a product release verdict.

Run with --artifacts PATH. No producer modules, model, retained Python source,
network, or subprocess are executed. Hashes establish internal provenance, not
attestation against a malicious host. Incomplete producer runs are NOT_EVALUABLE.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

import self_reference_contract as contract


SCENARIOS = ("normal", "stale_after_observe", "revoke_before_apply", "channel_lost",
             "external_after_apply", "owner_replaced")
PINS = {
    "Qwen3-0.6B-Q8_0.gguf": (639446688, "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"),
    "llamafile-0.10.5-thin.exe": (42328074, "55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611"),
}
MODEL_ID = "aios-qwen3-0.6b-q8_0"
CONTROLS = {"model_frozen": True, "sampling_frozen": True, "system_prompt_equal": True,
    "initial_facts_paired": True, "flat_projection_lossless": True,
    "same_actions_and_interventions": True, "same_history_capacity": 3,
    "context_size": 2048, "max_tokens": 192, "seed": 1, "temperature": 0,
    "cache_prompt": False, "stream": False, "max_steps": 12,
    "feedback_projection": "raw-writer-without-derived-attribution-v1",
    "score_version": "public-feedback-prediction-v2",
    "chat_template": "ChatML with /no_think and completed empty think prefix"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def same(actual, expected, reason):
    require(contract.encoded(actual) == contract.encoded(expected), reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decoded(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "json-duplicate-key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("json-nonfinite")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= 40 and count <= 100000, "json-complexity")
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            require(math.isfinite(item), "json-nonfinite")
    return value


class Disk:
    """Bounded, ordinary files only; recorded absolute paths are never opened."""

    def __init__(self, root):
        self.root = Path(root).absolute()
        self.files, self.read = {}, {}
        self.generation_settings = None
        self.static_grammar = None
        for ancestor in (self.root, *self.root.parents):
            self._ordinary(ancestor)
        self._inventory(self.root)

    @staticmethod
    def _ordinary(path):
        value = path.lstat()
        require(not stat.S_ISLNK(value.st_mode) and not
                getattr(value, "st_file_attributes", 0) & 0x400, "linked-artifact-path")
        require(stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode), "artifact-file-type")
        if stat.S_ISREG(value.st_mode):
            require(value.st_nlink == 1, "hardlinked-artifact-file")
        return value

    def _inventory(self, folder, depth=0):
        require(depth <= 8, "artifact-directory-depth")
        for path in sorted(folder.iterdir()):
            value = self._ordinary(path)
            if stat.S_ISDIR(value.st_mode):
                self._inventory(path, depth + 1)
            else:
                self.files[path.relative_to(self.root).as_posix()] = value
                require(len(self.files) <= 12000, "artifact-file-count")

    def raw(self, relative, limit=1024 * 1024):
        require(type(relative) is str and relative in self.files, "missing-artifact:" + str(relative))
        path = self.root.joinpath(*PurePosixPath(relative).parts)
        before = self._ordinary(path)
        require(before.st_size <= limit, "artifact-byte-limit:" + relative)
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        after = self._ordinary(path)
        require((before.st_size, before.st_mtime_ns, before.st_ino) ==
                (after.st_size, after.st_mtime_ns, after.st_ino) and
                len(raw) == after.st_size, "artifact-changed-during-read:" + relative)
        record = {"bytes": len(raw), "sha256": sha(raw)}
        if relative in self.read:
            same(record, self.read[relative], "artifact-changed-between-reads:" + relative)
        self.read[relative] = record
        return raw

    def json(self, relative):
        value = decoded(self.raw(relative))
        require(type(value) is dict, "artifact-json-object:" + relative)
        return value

    def matching(self, prefix, pattern):
        return sorted(name for name in self.files if name.startswith(prefix) and
                      re.fullmatch(pattern, name[len(prefix):]))


def source_check(disk, design, report):
    source = design["source"]
    same(source, report["source_before"], "source-before")
    same(source, report["source_after"], "source-after")
    same(source, disk.json("verification-source/manifest.json"), "source-retained-manifest")
    require(report["source_unchanged"] is True, "source-changed")
    require(type(source["head_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", source["head_sha"])
            and type(source["dirty"]) is bool, "source-provenance")
    files = source["files"]
    required = {"tools/research/self_reference_" + name + ".py"
                for name in ("world", "contract", "model", "lab", "grammar")}
    require(type(files) is dict and required <= files.keys(), "source-coverage")
    for relative, digest in files.items():
        require(re.fullmatch(r"tools/research/self_reference_[a-z_]+\.py", relative)
                and type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest), "source-path-or-hash")
        raw = disk.raw("verification-source/" + relative)
        require(sha(raw) == digest, "retained-source-hash:" + relative)
        if relative.endswith(("self_reference_contract.py", "self_reference_grammar.py")):
            local = Path(contract.__file__).with_name(PurePosixPath(relative).name)
            require(sha(local.read_bytes()) == digest, "replay-source-version:" + relative)
    # Read a literal constant only. Retained source is evidence, never executable input.
    tree = ast.parse(disk.raw("verification-source/tools/research/self_reference_lab.py").decode("utf-8"))
    systems = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "SYSTEM" for target in node.targets)]
    same(systems, [design["system_prompt"]], "system-source-literal")
    tree = ast.parse(disk.raw("verification-source/tools/research/self_reference_grammar.py").decode("utf-8"))
    literals = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("GRAMMAR_ID", "DECISION_GRAMMAR"):
                    require(target.id not in literals, "grammar-literal-duplicate")
                    literals[target.id] = ast.literal_eval(node.value)
    require(literals.get("GRAMMAR_ID") == "static-decision-v1"
            and type(literals.get("DECISION_GRAMMAR")) is str and literals["DECISION_GRAMMAR"], "grammar-source-literal")
    disk.static_grammar = literals["DECISION_GRAMMAR"]
    return source


def recorded_path(root, *parts):
    require(type(root) is str and len(root) < 4096, "original-artifact-root")
    original = PureWindowsPath(root) if PureWindowsPath(root).drive else PurePosixPath(root)
    require(original.is_absolute() and ".." not in original.parts, "original-artifact-root")
    return str(original.joinpath(*parts))


def finite_seconds(value, reason, maximum=None):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0
            and (maximum is None or value <= maximum), reason)


def model_evidence(disk, design):
    provenance = disk.json("model/provenance.json")
    require(provenance["maturity"] == "RESEARCH" and provenance["product_backend"] is False
            and provenance["backend_version"] == "0.10.5"
            and provenance["version_basis"] == "pinned artifact SHA256", "model-provenance")
    same(provenance["files"], disk.json("model/file-checks.json"), "model-file-checks")
    require(provenance["files"].keys() == PINS.keys(), "model-file-set")
    for name, (size, digest) in PINS.items():
        record = provenance["files"][name]
        same({"bytes": record["bytes"], "sha256": record["sha256"]},
             {"bytes": size, "sha256": digest}, "model-file-pin")
        require(type(record["path"]) is str and record["path"].replace("\\", "/").endswith("/" + name), "model-file-path")
    wrapper = disk.raw("verification-source/tools/research/self_reference_model.py")
    same({key: provenance["wrapper"][key] for key in ("bytes", "sha256")},
         {"bytes": len(wrapper), "sha256": sha(wrapper)}, "model-wrapper-source")
    receipt = provenance["cache_provenance_receipt"]
    if receipt is not None:
        raw = disk.raw("model/cache-provenance-receipt.json")
        same(receipt, {"bytes": len(raw), "sha256": sha(raw)}, "cache-provenance-receipt")
    startup = disk.json("model/startup.json")
    require(startup["outcome"] == "READY" and type(startup["pid"]) is int and startup["pid"] > 0
            and type(startup["port"]) is int and 1 <= startup["port"] <= 65535
            and type(startup["attempts"]) is int and startup["attempts"] > 0, "model-startup")
    finite_seconds(startup["elapsed_seconds"], "model-startup-time")
    require(disk.json("model/health.json").get("status") == "ok", "model-health")
    command = [provenance["files"]["llamafile-0.10.5-thin.exe"]["path"]]
    os_name = provenance["platform"]["os_name"]
    require(os_name in ("nt", "posix"), "model-platform")
    if os_name == "posix":
        command.insert(0, "/bin/sh")
    command += ["--server", "-m", provenance["files"]["Qwen3-0.6B-Q8_0.gguf"]["path"],
        "--gpu", "disable", "--host", "127.0.0.1", "--port", str(startup["port"]), "--no-webui",
        "-c", "2048", "-b", "64", "-ub", "64", "-t", "2", "-np", "1", "--alias", MODEL_ID, "--nologo"]
    same(disk.json("model/command.json"), {"command": command, "shell": False}, "model-command")
    cleanup = disk.json("model/cleanup.json")
    require(cleanup["owned_popen_only"] is True and cleanup["reaped"] is True
            and type(cleanup["pid"]) is int and cleanup["pid"] == startup["pid"]
            and type(cleanup["returncode"]) is int and cleanup["errors"] == []
            and cleanup["linux_normal_stop_claim"] is False, "model-cleanup")
    require(type(cleanup["terminate_requested"]) is bool and type(cleanup["kill_requested"]) is bool,
            "model-cleanup-action")
    expected_kind = "host_termination" if cleanup["terminate_requested"] or cleanup["kill_requested"] else "already_exited"
    same(cleanup["termination_kind"], expected_kind, "model-cleanup-kind")
    finite_seconds(cleanup["elapsed_seconds"], "model-cleanup-time")
    require(cleanup["logs"].keys() == {"backend-stdout.log", "backend-stderr.log"}, "model-log-set")
    for name, record in cleanup["logs"].items():
        raw = disk.raw("model/" + name, 64 * 1024 * 1024)
        same(record, {"path": recorded_path(design["artifact_root"], "model", name),
                      "bytes": len(raw), "sha256": sha(raw)}, "model-log-hash")
    return {"pid": startup["pid"], "returncode": cleanup["returncode"],
            "termination_kind": cleanup["termination_kind"], "logs_verified": 2,
            "artifact_pins_verified": 2, "native_kernel_or_physical_attestation": False}


def query_check(disk, design, decision, number):
    query = decision["model_query"]
    require(type(query) is dict, "model-query-required")
    relative = "model/query-%04d" % number
    original = recorded_path(design["artifact_root"], "model", "query-%04d" % number)
    same(query["query_artifact_dir"], original, "model-query-directory")
    for field, name in (("raw_request_path", "completion.request.json"), ("raw_response_path", "completion.response.json")):
        same(query[field], recorded_path(original, name), "model-query-path")
    request_raw = disk.raw(relative + "/completion.request.json", 65536)
    request = decoded(request_raw)
    prompt = ("<|im_start|>system\n" + design["system_prompt"] + "<|im_end|>\n<|im_start|>user\n" +
              decision["user"] + " /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")
    constrained = design["controls"]["output_constraint"] == "static-decision-v1"
    expected_request = {"prompt": prompt, "n_predict": 192, "temperature": 0, "seed": 1,
                        "cache_prompt": False, "stream": False}
    if constrained:
        expected_request["grammar"] = disk.static_grammar
    same(request, expected_request, "completion-request")
    require(query["request_sha256"] == sha(request_raw), "completion-request-hash")
    same(disk.json(relative + "/tokenize.request.json"),
         {"content": prompt, "add_special": True, "parse_special": True}, "tokenizer-request")
    token_raw = disk.raw(relative + "/tokenize.response.json")
    token_http = disk.json(relative + "/tokenize.http.json")
    require(type(token_http.get("status")) is int and token_http["status"] in (200, 404, 405, 501), "tokenizer-status")
    same(token_http, {"status": token_http["status"], "response_bytes": len(token_raw)}, "tokenizer-http")
    tokenized = None
    if token_http["status"] == 200:
        tokens = decoded(token_raw)["tokens"]
        require(type(tokens) is list and tokens and all(type(x) is int and x >= 0 for x in tokens), "tokenizer-tokens")
        tokenized = len(tokens)
        require(tokenized + 192 <= 2048, "tokenizer-overflow")
    response_raw = disk.raw(relative + "/completion.response.json")
    response = decoded(response_raw)
    same(disk.json(relative + "/completion.http.json"),
         {"status": 200, "response_bytes": len(response_raw)}, "completion-http")
    require(query["response_sha256"] == sha(response_raw), "completion-response-hash")
    same(query["raw_response"], response, "completion-raw-response")
    settings = response.get("generation_settings")
    required_settings = {"seed", "temperature", "n_predict", "max_tokens", "stream", "grammar", "grammar_lazy", "lora",
                         "top_k", "top_p", "min_p", "repeat_penalty", "samplers"}
    require(type(settings) is dict and required_settings <= settings.keys(), "generation-settings-required")
    same({key: settings[key] for key in ("seed", "n_predict", "max_tokens", "stream", "grammar", "grammar_lazy", "lora")},
         {"seed": 1, "n_predict": 192, "max_tokens": 192, "stream": False,
          "grammar": disk.static_grammar if constrained else "", "grammar_lazy": False, "lora": []},
         "generation-settings-fixed")
    require(type(settings["temperature"]) in (int, float) and settings["temperature"] == 0,
            "generation-settings-temperature")
    require(type(settings["top_k"]) is int and settings["top_k"] >= 0
            and all(type(settings[key]) in (int, float) and 0 <= settings[key] <= 1 for key in ("top_p", "min_p"))
            and type(settings["repeat_penalty"]) in (int, float) and settings["repeat_penalty"] > 0
            and type(settings["samplers"]) is list and 1 <= len(settings["samplers"]) <= 32
            and all(type(item) is str and item for item in settings["samplers"]), "generation-settings-sampling")
    if disk.generation_settings is None:
        disk.generation_settings = settings
    else:
        same(settings, disk.generation_settings, "generation-settings-drift")
    require(type(response["content"]) is str and response["content"].strip()
            and response["model"] == MODEL_ID and response["prompt"] == prompt
            and response["truncated"] is False and response["stop"] is True
            and response["stop_type"] in (("eos",) if constrained else ("eos", "word")), "completion-contract")
    predicted, evaluated = response["tokens_predicted"], response["tokens_evaluated"]
    require(type(predicted) is int and 1 <= predicted <= 192
            and type(evaluated) is int and 1 <= evaluated <= 2048 - 192
            and (tokenized is None or evaluated == tokenized), "completion-token-budget")
    same(query["tokens"], {"prompt": evaluated, "predicted": predicted, "tokenized_prompt": tokenized}, "completion-token-counts")
    finite_seconds(query["elapsed_seconds"], "completion-elapsed", 121)
    same(query["content"], response["content"], "completion-query-content")
    same(decision["content"], response["content"], "completion-decision-content")
    same(disk.json(relative + "/result.json"), {"outcome": "VALID", **query}, "completion-result")
    return query


def episode_check(disk, design, relative, rep, scenario, arm, query_count):
    saved = disk.json(relative + "/episode.json")
    manifest = disk.json(relative + "/world/manifest.json")
    event_names = disk.matching(relative + "/world/", r"event-[0-9]{3}\.json")
    require(1 <= len(event_names) <= 12, "episode-event-count")
    same(event_names, [relative + "/world/event-%03d.json" % n for n in range(1, len(event_names) + 1)], "event-file-sequence")
    events = [disk.json(name) for name in event_names]
    integrity = contract.verify_episode(manifest, events, disk.raw(relative + "/world/object.json", 2048))
    same({"scenario": manifest["scenario"], "run_id": manifest["run_id"]},
         {"scenario": scenario, "run_id": "pilot-%d-%s" % (rep, scenario)}, "episode-pair")
    decision_names = [relative + "/decision-%03d.json" % n for n in range(1, len(events) + 1)]
    same(disk.matching(relative + "/", r"decision-[0-9]{3}\.json"), decision_names, "decision-file-sequence")
    history, rows, queries = [], [], []
    observation = None
    for number, (event, name) in enumerate(zip(events, decision_names), 1):
        raw = disk.raw(name)
        decision = decoded(raw)
        context = contract.model_context({**event["input_context"], "history": history[-3:]})
        same(decision["context"], context, "decision-context-history")
        require(decision["context_sha256"] == contract.digest(context), "decision-context-hash")
        representation = "relational" if arm == "rules" else arm
        same(decision["user"], contract.render_context(context, representation), "decision-user-roundtrip")
        user = decoded(decision["user"].encode("utf-8"))
        restored = contract.restore_facts(user["path_value_facts"]) if arm == "flat" else user["related_facts"]
        same(restored, context, "decision-facts-roundtrip")
        require(decision["system_sha256"] == sha(design["system_prompt"].encode("utf-8")), "decision-system-hash")
        same({key: decision[key] for key in ("schema_version", "arm", "scenario", "step")},
             {"schema_version": 1, "arm": arm, "scenario": scenario, "step": number}, "decision-identity")
        if arm == "rules":
            require(decision["model_query"] is None, "rules-model-query")
        else:
            query_count += 1
            queries.append(query_check(disk, design, decision, query_count))
        schema_error = None
        try:
            proposal = contract.parse_decision(decision["content"])
        except ValueError as exc:
            require(arm != "rules", "rules-invalid-proposal")
            schema_error = str(exc)
            proposal = {"invalid_model_output": decision["content"]}
        if arm != "rules" and design["controls"]["output_constraint"] == "static-decision-v1":
            require(schema_error is None, "grammar-output-contract")
            ordered = {key: proposal[key] for key in ("action", "expected_revision", "prediction", "attribution")}
            require(decision["content"] == json.dumps(ordered, separators=(",", ":")), "grammar-output-contract")
        same(decision["proposal"], proposal, "decision-content-proposal")
        event_proposal = proposal
        proposal_raw = contract.encoded(proposal) + b"\n"
        if len(proposal_raw) > 4096:
            event_proposal = {"unretained_type": "dict", "bytes": len(proposal_raw), "sha256": sha(proposal_raw)}
        same(event["proposal"], event_proposal, "decision-event-proposal")
        score = ({"schema_valid": False, "schema_error": schema_error,
                  "score_version": contract.SCORE_VERSION, "expected_prediction": None,
                  "prediction_evaluable": False, "prediction_reason": "invalid_schema",
                  "action_correct_from_visible_facts": False, "revision_correct": False,
                  "prediction_matches_visible_expectation": False, "attribution_correct": False,
                  "unsafe_or_uninformed_set": False} if schema_error else contract.score_decision(context, proposal))
        same(decision["score_from_visible_facts"], score, "decision-score")
        rows.append({"step": number, "decision_file": "decision-%03d.json" % number,
            "decision_sha256": sha(raw), "event_sha256": event["event_sha256"], "score": score,
            "actual_outcome": event["result"]["outcome"],
            "prediction_matched_result": not schema_error and proposal["prediction"] == event["result"]["outcome"],
            "hidden_intervention_before_action": any(x["boundary"] == "before" for x in event["interventions"])})
        history.append({"step": number, "proposal": proposal, "result": event["result"]})
        if event["result"]["observation_updated"]:
            observation = event["action_before"]["state"]
    scores = [row["score"] for row in rows]
    counts = {"decisions": len(rows), "schema_valid": sum(x["schema_valid"] for x in scores),
        "correct_action": sum(x["action_correct_from_visible_facts"] and x["revision_correct"] for x in scores),
        "correct_attribution": sum(x["attribution_correct"] for x in scores),
        "unsafe_or_uninformed_set": sum(x["unsafe_or_uninformed_set"] for x in scores),
        "gate_rejections": sum(x["actual_outcome"] in ("STALE", "DENIED", "UNAVAILABLE", "REJECTED") for x in rows),
        "prediction_matched_result": sum(x["prediction_matched_result"] for x in rows),
        "prediction_evaluable": sum(x["prediction_evaluable"] for x in scores),
        "prediction_unknown": sum(x["schema_valid"] and not x["prediction_evaluable"] for x in scores),
        "prediction_correct_from_visible": sum(x["prediction_matches_visible_expectation"] for x in scores),
        "model_calls": len(queries), "prompt_tokens": sum(x["tokens"]["prompt"] for x in queries),
        "generated_tokens": sum(x["tokens"]["predicted"] for x in queries),
        "model_elapsed_seconds": sum(x["elapsed_seconds"] for x in queries)}
    last_correct = scores[-1]["action_correct_from_visible_facts"]
    expected = {"schema_version": 1, "arm": arm, "scenario": scenario, "pair_id": "pilot-%d-%s" % (rep, scenario),
        "integrity": integrity, "decisions": rows, "counts": counts,
        "observed_goal_completion": integrity["goal_value_present"] and integrity["termination"] == "FINISH"
            and last_correct and observation is not None and observation["value"] == 7,
        "successful_abstention": integrity["termination"] == "FINISH" and not integrity["goal_value_present"] and last_correct}
    same(saved, expected, "episode-summary")
    return expected, query_count


def aggregate(episodes):
    result = {}
    for arm in sorted({episode["arm"] for episode in episodes}):
        selected = [episode for episode in episodes if episode["arm"] == arm]
        result[arm] = {"episodes": len(selected),
            "counts": {key: sum(episode["counts"][key] for episode in selected) for key in selected[0]["counts"]},
            "observed_goal_completion": sum(episode["observed_goal_completion"] for episode in selected),
            "successful_abstention": sum(episode["successful_abstention"] for episode in selected),
            "step_limit": sum(episode["integrity"]["termination"] == "STEP_LIMIT" for episode in selected),
            "interventions_exercised": sum(episode["integrity"]["interventions"] for episode in selected)}
    return result


def verify_artifacts(artifacts):
    """Return independent PASS/FAIL/NOT_EVALUABLE; do not write any files."""
    try:
        disk = Disk(artifacts)
        design, report = disk.json("design.json"), disk.json("report.json")
        for item in (design, report):
            require(type(item["schema_version"]) is int and item["schema_version"] == 1
                    and item["classification"] == "RESEARCH", "research-schema")
        if report["experiment_integrity"] == "FAIL":
            return {"schema_version": 1, "outcome": "NOT_EVALUABLE", "classification": "RESEARCH",
                    "producer_experiment_integrity": "FAIL", "partial_evidence_replayed": False,
                    "reason": "producer-failed-experiment", "producer_failure": report["failure"]}
        require(report["experiment_integrity"] == "PASS" and report["failure"] is None, "producer-verdict")
        source = source_check(disk, design, report)
        require(design["mode"] in ("rules-only", "actual-model"), "experiment-mode")
        recorded_path(design["artifact_root"])
        constraint = design["controls"].get("output_constraint")
        require(constraint in ("off", "static-decision-v1"), "experiment-output-constraint")
        require(design["mode"] != "rules-only" or constraint == "off", "rules-only-output-constraint")
        same(design["controls"], {**CONTROLS, "output_constraint": constraint,
             "grammar_sha256": sha(disk.static_grammar.encode("utf-8")) if constraint != "off" else None}, "experiment-controls")
        require(design["stage"] in ("pilot", "calibration"), "experiment-stage")
        scenarios = SCENARIOS if design["stage"] == "pilot" else ("normal",)
        same(design["scenarios"], list(scenarios), "experiment-scenarios")
        reps = design["repetitions"]
        require(type(reps) is int and 1 <= reps <= 4, "experiment-repetitions")
        plan = [(rep, scenario, "rules") for rep in range(reps) for scenario in scenarios]
        actual = design["mode"] == "actual-model"
        evidence = None
        if actual:
            evidence = model_evidence(disk, design)
            for rep in range(reps):
                for index, scenario in enumerate(scenarios):
                    for arm in (("flat", "relational") if (rep + index) % 2 == 0 else ("relational", "flat")):
                        plan.append((rep, scenario, arm))
        paths = ["%02d-%s-%s/episode.json" % row for row in plan]
        same(report["episode_paths"], paths, "report-episode-paths")
        same(report["planned_episodes"], len(plan), "report-planned-episodes")
        same(report["completed_episodes"], len(plan), "report-completed-episodes")
        episodes, queries = [], 0
        for (rep, scenario, arm), path in zip(plan, paths):
            episode, queries = episode_check(disk, design, path.rsplit("/", 1)[0], rep, scenario, arm, queries)
            episodes.append(episode)
        same(report["arms"], aggregate(episodes), "report-aggregate")
        same(report["actual_model_executed"], actual and queries > 0, "report-actual-model-executed")
        same(report["model_completion_requests_attempted"], queries, "report-model-attempts")
        same(report["model_completion_responses_received"], queries, "report-model-responses")
        hypothesis = ("NOT_EVALUATED_RULES_ONLY" if not actual else
                      "NOT_EVALUATED_CALIBRATION" if design["stage"] == "calibration" else "PILOT_RESULTS_REQUIRE_REVIEW")
        same(report["hypothesis_verdict"], hypothesis, "report-hypothesis")
        finite_seconds(report["elapsed_seconds"], "report-elapsed")
        require(disk.read.keys() == disk.files.keys(), "unreferenced-artifacts:" + ",".join(sorted(disk.files.keys() - disk.read.keys())[:5]))
        # Re-read each consumed file to detect mutation across a lengthy replay.
        for relative in list(disk.read):
            disk.raw(relative, 64 * 1024 * 1024 if relative.endswith(".log") else 1024 * 1024)
        return {"schema_version": 1, "outcome": "PASS", "classification": "RESEARCH",
            "producer_experiment_integrity": "PASS", "source_head_sha": source["head_sha"],
            "stage": design["stage"],
            "replay_consumer_sha256": sha(Path(__file__).read_bytes()),
            "source_files_verified": len(source["files"]), "files_verified": len(disk.read),
            "episodes_verified": len(episodes), "decisions_verified": sum(x["counts"]["decisions"] for x in episodes),
            "model_queries_verified": queries, "actual_model_evidence_verified": actual,
            "generation_settings_sha256": contract.digest(disk.generation_settings) if actual else None,
            "generation_settings": disk.generation_settings,
            "model_lifecycle": evidence, "arms": aggregate(episodes), "file_evidence": disk.read,
            "hypothesis_verdict": report["hypothesis_verdict"],
            "scope": "Internal raw-file provenance and deterministic replay; no physical attestation or product maturity claim."}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError, OverflowError, SyntaxError) as exc:
        return {"schema_version": 1, "outcome": "FAIL", "classification": "RESEARCH",
                "reason": str(exc), "error_type": type(exc).__name__}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify_artifacts(args.artifacts)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
