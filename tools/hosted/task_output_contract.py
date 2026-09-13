"""Independent Task admission, revision, worker and backend evidence contracts.

Copied state consistency is separate from same-run process and terminal joins.
This module never imports or executes the runtime producer.
"""
from __future__ import annotations

from newagent_output_contract import (SPACE_RECEIPT_KEYS, hash_value, identity, integer,
    keys, receipt, require, same, validate_snapshot, validate_source)


TASK_STATE_KEYS = {
    "schema_version", "request_id", "owner", "source_before", "management_before", "backend_expected",
    "space_context", "user_prompt", "request_body", "request_sha256", "revision", "phase", "model_outcome",
    "accepted_ns", "updated_ns", "started_ns", "cancel_requested_ns", "finished_ns", "worker_process_id",
    "worker_exit_code", "inference_receipt", "backend_stop", "backend_stopped_ns", "not_started_reason"}
TASK_STATIC_KEYS = {"schema_version", "request_id", "owner", "source_before", "management_before",
                    "backend_expected", "space_context", "user_prompt", "request_body", "request_sha256",
                    "accepted_ns"}
TASK_ENVELOPE_KEYS = {"schema_version", "request_state", "receipt_template", "worker_progress",
                      "control_failure", "backend_observation"}
TASK_WORKER_KEYS = {"request_id", "done", "worker_pid", "worker_exit_code", "worker_exit_observed_monotonic_ns",
                    "cancel_requested", "timed_out", "stop_reason", "terminate_requested", "kill_requested",
                    "signal_error", "io_error", "output_bytes", "receipt"}


def validate_task_state(value, *, config=None, require_live=False, owner=None):
    """Validate a copied Task state, without treating it as live process proof."""
    from backend_output_contract import process_identity, validate_backend_result
    from space_output_contract import validate_packet
    keys(value, TASK_STATE_KEYS, "task_keys")
    require(same(value["schema_version"], 1), "task_schema")
    identity(value["request_id"])
    process_identity(value["owner"])
    if owner is not None:
        require(same(value["owner"], owner), "task_owner")
    source, snapshot, expected = value["source_before"], value["management_before"], value["backend_expected"]
    validate_source(source)
    validate_snapshot(snapshot)
    validate_backend_result(expected, action="status", require_live=require_live)
    require(expected["outcome"] == "OK" and expected["state"] == "RUNNING", "task_backend_ready")
    descriptor = expected["descriptor"]
    require(source["lifecycle_state"] == "active" and source["model_ready"] is True
            and snapshot["binding_current"] is True and same(snapshot["current_source"], source),
            "task_admission_binding")
    require(value["owner"]["host_boot_id"] == source["host_boot_id"] == descriptor["host_boot_id"]
            and value["owner"]["uid"] == expected["service_record"]["supervisor_identity"]["uid"]
            and source["model_sha256"] == descriptor["model_sha256"], "task_boundary")
    if config is not None:
        require(all(same(descriptor[name], config[name]) for name in
                    ("model_id", "model_sha256", "backend_sha256", "endpoint")), "task_config")
    for name in ("accepted_ns", "updated_ns"):
        require(integer(value[name]), "task_clock")
    require(value["updated_ns"] >= value["accepted_ns"] and integer(value["revision"], 1, 8), "task_revision")
    for name in ("started_ns", "cancel_requested_ns", "finished_ns", "backend_stopped_ns"):
        require(value[name] is None or integer(value[name])
                and value["accepted_ns"] <= value[name] <= value["updated_ns"], "task_clock")
    validate_packet(value["space_context"], source=source, snapshot=snapshot,
                    model_id=descriptor["model_id"], now_ns=value["accepted_ns"])
    # Reuse only the independently implemented raw receipt/prompt validator.
    # This synthetic ERROR view proves request syntax, not a dispatched request.
    request_view = {"schema_version": 3, "request_id": value["request_id"],
        "started_at": "1970-01-01T00:00:00+00:00", "purpose": "user",
        **{name: descriptor[name] for name in ("model_id", "model_sha256", "backend_sha256")},
        "provenance_sha256": config["provenance_sha256"] if config is not None else "0" * 64,
        **{name: value[name] for name in ("request_body", "request_sha256", "user_prompt", "space_context")},
        "response_body": None, "response_sha256": None, "content": None, "tokens_predicted": 0,
        "elapsed_ns": 0, "outcome": "ERROR", "error": "backend-failed", "backend_execution": None}
    receipt(request_view, purpose="user", source=source, config=config, require_live=require_live)
    phase, outcome = value["phase"], value["model_outcome"]
    require(phase in ("ACCEPTED", "RUNNING", "CANCEL_REQUESTED", "FINISHED"), "task_phase")
    worker, code = value["worker_process_id"], value["worker_exit_code"]
    require(worker is None or integer(worker, 1), "task_worker")
    require((worker is None) == (value["started_ns"] is None), "task_worker_start")
    if worker is not None:
        require(worker not in (value["owner"]["process_id"], source["process_id"],
                expected["service_record"]["supervisor_identity"]["process_id"],
                expected["service_record"]["child_identity"]["process_id"]), "task_worker_alias")
    require(code is None or type(code) is int and -(1 << 31) <= code < 1 << 32, "task_worker_exit")
    if phase == "ACCEPTED":
        require(worker is None and value["cancel_requested_ns"] is None and value["revision"] == 1
                and value["updated_ns"] == value["accepted_ns"], "task_accepted")
    if phase == "RUNNING":
        require(worker is not None and value["cancel_requested_ns"] is None and value["revision"] == 2,
                "task_running")
    if phase == "CANCEL_REQUESTED":
        require(value["cancel_requested_ns"] is not None, "task_cancel")
    item = value["inference_receipt"]
    if phase != "FINISHED":
        require(outcome is None and value["finished_ns"] is None and code is None and item is None
                and value["not_started_reason"] is None, "task_unfinished")
    else:
        require(outcome in ("ANSWERED", "UNKNOWN", "NOT_STARTED") and value["finished_ns"] is not None,
                "task_finished")
        if outcome == "NOT_STARTED":
            require(worker is None and code is None and item is None
                    and value["not_started_reason"] in ("cancel-before-dispatch", "dispatch-failed"),
                    "task_not_started")
            require(value["not_started_reason"] != "cancel-before-dispatch"
                    or value["cancel_requested_ns"] is not None, "task_cancel_not_started")
        else:
            require(worker is not None and code is not None and value["not_started_reason"] is None
                    and value["finished_ns"] >= value["started_ns"], "task_finished_worker")
            if item is not None:
                keys(item, SPACE_RECEIPT_KEYS, "task_receipt_keys")
                receipt(item, purpose="user", source=source, config=config,
                        require_live=require_live, descriptor=descriptor)
                require(item["schema_version"] == 3 and all(same(item[name], value[name]) for name in
                        ("request_id", "request_body", "request_sha256", "user_prompt", "space_context")),
                        "task_receipt_admission")
                if config is not None:
                    require(all(same(item[name], config[name]) for name in
                        ("model_id", "model_sha256", "backend_sha256", "provenance_sha256")), "task_receipt_config")
                if item["outcome"] == "OK":
                    require(item["backend_execution"] is not None
                            and item["backend_execution"]["capture_kind"] == expected["capture_kind"]
                            and same(item["backend_execution"]["descriptor"], descriptor), "task_receipt_backend")
                    require(item["backend_execution"]["send"]["client"]["process_id"] == worker,
                            "task_receipt_worker")
                    require(value["accepted_ns"] <= item["backend_execution"]["before"]["read_start_ns"]
                            <= item["backend_execution"]["after"]["read_end_ns"] <= value["finished_ns"],
                            "task_receipt_time")
            require((outcome == "ANSWERED") == (item is not None and item["outcome"] == "OK"),
                    "task_model_outcome")
            require(outcome != "ANSWERED" or code == 0, "task_answer_worker")
        if outcome != "UNKNOWN" and value["cancel_requested_ns"] is not None:
            require(value["cancel_requested_ns"] <= value["finished_ns"], "task_late_cancel")
    stopped = value["backend_stop"]
    require((stopped is None) == (value["backend_stopped_ns"] is None), "task_backend_stop_time")
    if stopped is not None:
        validate_backend_result(stopped, action="stop", require_live=require_live)
        require(value["cancel_requested_ns"] is not None
                and value["backend_stopped_ns"] >= value["cancel_requested_ns"], "task_stop_without_cancel")
        require(stopped["outcome"] == "OK" and stopped["state"] == "STOPPED"
                and stopped["descriptor"] is None and stopped["capture_kind"] == expected["capture_kind"]
                and same(stopped["service_record"], {**expected["service_record"],
                          "lifecycle_state": "exited", "backend_ready": False}), "task_backend_stop_target")


def validate_task_transition(previous, current):
    """One persisted revision represents exactly one supported state change."""
    require(current["revision"] == previous["revision"] + 1
            and current["updated_ns"] >= previous["updated_ns"], "task_revision_order")
    require(all(same(current[key], previous[key]) for key in TASK_STATIC_KEYS), "task_admission_rewrite")
    at = current["updated_ns"]
    base = {**previous, "revision": current["revision"], "updated_ns": at}
    candidates = []
    if previous["phase"] == "ACCEPTED":
        candidates.append({**base, "phase": "RUNNING", "worker_process_id": current["worker_process_id"],
                           "started_ns": at})
    if previous["cancel_requested_ns"] is None and (previous["phase"] != "FINISHED"
                                                  or previous["model_outcome"] == "UNKNOWN"):
        candidates.append({**base, "phase": "FINISHED" if previous["phase"] == "FINISHED" else "CANCEL_REQUESTED",
                           "cancel_requested_ns": at})
    if previous["phase"] != "FINISHED" and previous["worker_process_id"] is None:
        candidates.append({**base, "phase": "FINISHED", "model_outcome": "NOT_STARTED",
                           "finished_ns": at, "not_started_reason": current["not_started_reason"]})
    if previous["phase"] in ("RUNNING", "CANCEL_REQUESTED") and previous["worker_process_id"] is not None:
        candidates.append({**base, "phase": "FINISHED", "finished_ns": at,
                           "model_outcome": current["model_outcome"],
                           "worker_exit_code": current["worker_exit_code"],
                           "inference_receipt": current["inference_receipt"]})
    if previous["cancel_requested_ns"] is not None and previous["backend_stop"] is None:
        candidates.append({**base, "backend_stop": current["backend_stop"], "backend_stopped_ns": at})
    require(any(same(current, candidate) for candidate in candidates), "task_transition")


def validate_task_envelope(value, *, config, require_live=False, owner=None):
    keys(value, TASK_ENVELOPE_KEYS, "task_envelope_keys")
    require(same(value["schema_version"], 1) and value["control_failure"] is None, "task_envelope_failure")
    row = value["request_state"]
    validate_task_state(row, config=config, require_live=require_live, owner=owner)
    template = value["receipt_template"]
    keys(template, SPACE_RECEIPT_KEYS, "task_template_keys")
    require(template["schema_version"] == 3 and template["outcome"] == "ERROR" and template["error"] is None
            and same(template["elapsed_ns"], 0) and same(template["tokens_predicted"], 0)
            and all(template[name] is None for name in ("response_body", "response_sha256", "content", "backend_execution")),
            "task_template")
    receipt({**template, "error": "backend-failed"}, purpose="user", source=row["source_before"], config=config)
    require(all(same(template[name], row[name]) for name in
                ("request_id", "request_body", "request_sha256", "user_prompt", "space_context"))
            and all(same(template[name], config[name]) for name in
                ("model_id", "model_sha256", "backend_sha256", "provenance_sha256")), "task_template_join")
    progress = value["worker_progress"]
    require((progress is None) == (row["worker_process_id"] is None), "task_worker_progress")
    if progress is not None:
        keys(progress, TASK_WORKER_KEYS, "task_worker_keys")
        require(progress["request_id"] == row["request_id"] and same(progress["worker_pid"], row["worker_process_id"]),
                "task_worker_join")
        for name in ("done", "cancel_requested", "timed_out", "terminate_requested", "kill_requested"):
            require(type(progress[name]) is bool, "task_worker_bool")
        code, observed = progress["worker_exit_code"], progress["worker_exit_observed_monotonic_ns"]
        require(code is None or type(code) is int and -(1 << 31) <= code < 1 << 32, "task_worker_exit")
        require((code is None) == (observed is None)
                and (observed is None or integer(observed) and row["accepted_ns"] <= observed <= row["updated_ns"]),
                "task_worker_exit_time")
        require(progress["stop_reason"] in (None, "cancel", "timeout", "io-failed", "close"),
                "task_worker_stop_reason")
        require(progress["signal_error"] in (None, "worker-terminate", "worker-kill"), "task_worker_signal")
        require(progress["io_error"] in (None, "stdout-limit", "stderr-limit", "stdout-read",
                                       "stderr-read", "stdout-close", "stderr-close"), "task_worker_io")
        keys(progress["output_bytes"], {"stdout", "stderr"}, "task_worker_output")
        require(all(integer(count) for count in progress["output_bytes"].values()), "task_worker_output")
        require(not progress["cancel_requested"] or row["cancel_requested_ns"] is not None, "task_worker_cancel")
        require(not progress["done"] or code is not None, "task_worker_done")
        if progress["done"]:
            require(type(progress["receipt"]) is dict, "task_worker_receipt")
            if progress["receipt"].get("outcome") == "OK":
                # The owned worker accepts output only before any stop, timeout
                # or pipe failure, with empty stderr and bounded stdout. A
                # self-consistent answer receipt cannot erase contrary worker
                # evidence, including an early exit observed before FINISHED.
                require(not any(progress[name] for name in ("cancel_requested", "timed_out",
                        "terminate_requested", "kill_requested"))
                        and all(progress[name] is None for name in ("stop_reason", "signal_error", "io_error"))
                        and integer(progress["output_bytes"]["stdout"], 1, 128 * 1024)
                        and progress["output_bytes"]["stderr"] == 0, "task_worker_answer_evidence")
            if row["phase"] == "FINISHED":
                require(same(progress["worker_exit_code"], row["worker_exit_code"])
                        and same(progress["receipt"], row["inference_receipt"]), "task_worker_terminal")
                if row["model_outcome"] == "ANSWERED":
                    require(row["inference_receipt"]["backend_execution"]["after"]["read_end_ns"] <= observed,
                            "task_receipt_after_worker_exit")
            else:
                # The initial poll can finish before mark_running is persisted.
                # The next revision must finalize this exact terminal progress.
                require(row["phase"] == "RUNNING" and row["revision"] == 2, "task_early_worker_terminal")
        else:
            require(progress["receipt"] is None and row["phase"] != "FINISHED", "task_worker_pending")
    observation = value["backend_observation"]
    require((observation is None) == (row["backend_stop"] is None), "task_backend_observation")
    if observation is not None:
        validate_task_backend_observation(observation, row)


def validate_task_backend_observation(value, row):
    from backend_output_contract import process_identity, validate_backend_result
    keys(value, {"backend_stop", "evidence"}, "task_backend_observation_keys")
    require(same(value["backend_stop"], row["backend_stop"]), "task_backend_observation_stop")
    evidence = value["evidence"]
    keys(evidence, {"schema_version", "expected", "read_start_ns", "read_end_ns", "lifetimes",
                    "state_directory", "registry", "terminal_files", "artifacts"}, "task_backend_evidence_keys")
    require(same(evidence["schema_version"], 1) and same(evidence["expected"], row["backend_expected"]),
            "task_backend_expected")
    require(integer(evidence["read_start_ns"]) and integer(evidence["read_end_ns"])
            and row["cancel_requested_ns"] <= evidence["read_start_ns"] <= evidence["read_end_ns"]
            <= row["backend_stopped_ns"], "task_backend_observation_time")
    keys(evidence["lifetimes"], {"backend", "launcher"}, "task_backend_lifetimes")
    for name, identity_key in (("backend", "child_identity"), ("launcher", "supervisor_identity")):
        observed = evidence["lifetimes"][name]
        keys(observed, {"identity", "exited", "observed_monotonic_ns"}, "task_backend_lifetime_keys")
        process_identity(observed["identity"])
        require(same(observed["identity"], row["backend_expected"]["service_record"][identity_key])
                and observed["exited"] is True and integer(observed["observed_monotonic_ns"])
                and evidence["read_start_ns"] <= observed["observed_monotonic_ns"] <= evidence["read_end_ns"],
                "task_backend_lifetime")
    stamp = evidence["state_directory"]
    keys(stamp, {"st_dev", "st_ino", "st_uid", "st_mode"}, "task_backend_directory")
    require(all(integer(item) for item in stamp.values()) and stamp["st_ino"] > 0
            and stamp["st_mode"] & 0o170000 == 0o040000 and stamp["st_uid"] == row["owner"]["uid"],
            "task_backend_directory")
    registry = evidence["registry"]
    keys(registry, {"schema_version", "service_id", "instance_id", "start_generation"}, "task_backend_registry")
    require(same(registry["schema_version"], 1) and all(same(registry[key],
            row["backend_expected"]["service_record"][key]) for key in ("service_id", "instance_id", "start_generation")),
            "task_backend_registry")
    keys(evidence["terminal_files"], {"registry.json", "latest.json", "result.json"}, "task_backend_terminal_files")
    from verify_backend import FILES
    keys(evidence["artifacts"], set(FILES), "task_backend_artifacts")
    for item in (*evidence["terminal_files"].values(), *evidence["artifacts"].values()):
        keys(item, {"sha256", "bytes"}, "task_backend_file_keys")
        hash_value(item["sha256"])
        require(integer(item["bytes"], 0, 2 * 1024 * 1024), "task_backend_file_size")
