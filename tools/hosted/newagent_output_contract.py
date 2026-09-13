"""Independent MAIN response, management and raw inference receipt checks.

These checks establish internal evidence consistency. Actual producer liveness,
backend provenance and same-run delivery are additional execution-verifier gates.
No producer or management implementation is imported here.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone

SOURCE_KEYS = frozenset({"schema_version", "source_namespace", "source_id", "source_instance",
    "source_generation", "service_start_generation", "source_kind", "source_role", "lifecycle_state",
    "producer_owned", "copied_read", "model_ready", "model_sha256", "warmup_request_sha256",
    "warmup_response_sha256", "completed_requests", "host_boot_id", "process_id", "source_only"})
STATE_KEYS = frozenset({"schema_version", "authority_namespace", "authority_instance", "initialized",
    "parent", "canonical", "current_source", "discovered_source", "binding", "source_trusted",
    "binding_confirmed", "retired_instances"})
SNAPSHOT_KEYS = STATE_KEYS | {"state", "binding_valid", "binding_current", "bound_nodes", "nodebits",
    "observation_only", "management_only", "resource_actions", "consistency"}
RECEIPT_KEYS = frozenset({"schema_version", "request_id", "started_at", "purpose", "model_id", "model_sha256",
    "backend_sha256", "provenance_sha256", "request_body", "request_sha256", "response_body",
    "response_sha256", "content", "tokens_predicted", "elapsed_ns", "outcome", "error"})
MANAGED_RECEIPT_KEYS = RECEIPT_KEYS | {"backend_execution"}
SPACE_RECEIPT_KEYS = MANAGED_RECEIPT_KEYS | {"user_prompt", "space_context"}
REQUEST_CONTEXT = frozenset({"source_before", "source_after", "authority_instance", "binding_generation"})
PUBLIC_KEYS = frozenset({"schema_version", "outcome", "error", "action", "state", "service_kind",
    "source_record", "management_snapshot", "management_outcome", "inference_receipt", "resource_actions", "capture_kind"})
RESOURCE_PUBLIC_KEYS = PUBLIC_KEYS | {"resource_result"}
SPACE_PUBLIC_KEYS = RESOURCE_PUBLIC_KEYS | {"space_context"}
TASK_PUBLIC_KEYS = SPACE_PUBLIC_KEYS | {"request_id", "task", "task_control"}
TASK_ACTIONS = {"ask-start", "task-status", "task-result", "task-cancel"}
TASK_ERRORS = {"task-request-id", "request-busy", "request-id-reused", "request-not-found",
    "request-owner-mismatch", "request-owner-lost", "request-backend-not-ready", "request-active-at-stop",
    "request-execution-failed", "request-persistence-failed", "request-finalization-failed",
    "request-dispatch-uncertain", "request-worker-uncertain", "request-cancel-uncertain", "request-task-required"}
STATES = {"ABSENT", "STARTING", "RUNNING", "STOPPING", "STOPPED", "STALE", "FAILED", "UNSUPPORTED"}
ERRORS = {"unsupported-platform", "invalid-action", "config-required", "already-running", "start-in-progress",
    "start-failed", "start-timeout", "process-not-running", "state-corrupt", "state-io", "peer-mismatch",
    "stale-instance", "protocol-error", "rpc-timeout", "stop-timeout", "stop-failed", "pidfd-unavailable",
    "backend-failed", "backend-timeout", "model-hash-mismatch", "backend-hash-mismatch", "request-limit",
    "prompt-invalid", "init-order", "missing", "schema", "overflow", "duplicate", "orphan", "namespace",
    "kind", "role", "instance", "zero-generation", "generation-rollback", "stale", "model-not-ready",
    "source-exited", "not-discovered", "already-bound", "retired-instance", "counter-regression", "unbound",
    "invalid-socket", "state-path-too-long", "config-schema", "config-hash", "config-text", "local-endpoint-required",
    "artifact-symlink", "artifact-size", "artifact-changed", "generation-exhausted", "backend-changed",
    "space-invalid", "space-overflow", "space-future", "space-source-mismatch", "space-budget"}
_PREFIX = ("<|im_start|>system\nYou are the AIOS MAIN assistant. Answer briefly. "
           "Describe only the provided facts; you cannot execute commands. /no_think<|im_end|>\n<|im_start|>user\n")
_SUFFIX = " /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("agent_contract:" + reason)


def same(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(same(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    return left == right


def keys(value: object, expected: set | frozenset, reason: str) -> None:
    require(type(value) is dict and set(value) == expected, reason)


def integer(value: object, minimum: int = 0, maximum: int = 2**63 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def identity(value: object) -> None:
    require(type(value) is str, "identity_type")
    parsed = uuid.UUID(value)
    require(parsed.int != 0 and str(parsed) == value, "identity")


def hash_value(value: object) -> None:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "hash")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def strict_object(text: object, maximum: int = 16384) -> dict:
    require(type(text) is str and len(text.encode("utf-8")) <= maximum, "json_size")
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "duplicate_key")
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("agent_contract:nonfinite")))
    require(type(value) is dict, "json_object")
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(count <= 4096 and depth <= 16, "json_complexity")
        children = item.values() if type(item) is dict else item if type(item) is list else ()
        pending.extend((child, depth + 1) for child in children)
    return value


def validate_source(value: dict) -> None:
    keys(value, SOURCE_KEYS, "source_keys")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1, "source_schema")
    require(value["source_namespace"] == "linux-userspace-service" and value["source_kind"] == "ai-service"
            and value["source_role"] == "main", "source_namespace_kind_role")
    for name in ("source_id", "source_instance", "host_boot_id"):
        identity(value[name])
    for name in ("source_generation", "service_start_generation", "process_id"):
        require(integer(value[name], 1), "source_integer:" + name)
    require(integer(value["completed_requests"]), "source_requests")
    require(value["lifecycle_state"] in ("active", "exited") and type(value["model_ready"]) is bool, "source_state")
    require(all(value[name] is True for name in ("producer_owned", "copied_read", "source_only")), "source_boundary")
    for name in ("model_sha256", "warmup_request_sha256", "warmup_response_sha256"):
        if value[name] is not None:
            hash_value(value[name])
    warm = value["warmup_request_sha256"] is not None
    require(warm == (value["warmup_response_sha256"] is not None) == (value["completed_requests"] > 0), "source_warmup")
    require(not warm or value["model_sha256"] is not None, "source_model")
    require(not value["model_ready"] or warm and value["lifecycle_state"] == "active", "source_readiness")


def source_semantics(left: dict, right: dict) -> bool:
    return all(same(left[key], right[key]) for key in SOURCE_KEYS - {"completed_requests"})


def validate_snapshot(value: dict) -> None:
    keys(value, SNAPSHOT_KEYS, "snapshot_keys")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1, "snapshot_schema")
    identity(value["authority_instance"])
    require(value["authority_namespace"] == "aios-hosted-management", "authority_namespace")
    require(value["observation_only"] is True and value["management_only"] is True
            and value["resource_actions"] == "UNSUPPORTED" and value["consistency"] == "copied-single-producer", "snapshot_boundary")
    for name in ("initialized", "source_trusted", "binding_confirmed", "binding_valid", "binding_current"):
        require(type(value[name]) is bool, "snapshot_bool:" + name)
    retired = value["retired_instances"]
    require(type(retired) is list and len(retired) <= 64, "retired_size")
    for instance in retired:
        identity(instance)
    require(len(set(retired)) == len(retired), "retired_duplicate")
    parent, node = value["parent"], value["canonical"]
    source, discovered, binding = value["current_source"], value["discovered_source"], value["binding"]
    valid = present_valid = present = False
    expected_bits = []
    if not value["initialized"]:
        require(all(v is None for v in (parent, node, source, discovered, binding)) and not retired
                and not value["source_trusted"] and not value["binding_confirmed"], "uninitialized_state")
        state = "UNINITIALIZED"
    else:
        keys(parent, {"namespace", "id", "generation", "active"}, "parent_keys")
        keys(node, {"namespace", "id", "kind", "generation", "parent_cell_id"}, "node_keys")
        require(parent["namespace"] == "cell" and same(parent["id"], 1) and type(parent["active"]) is bool, "parent_identity")
        require(node["namespace"] == "node" and same(node["id"], 101) and same(node["parent_cell_id"], 1)
                and node["kind"] == "ai-service", "node_identity")
        require(integer(parent["generation"], 1) and same(parent["generation"], node["generation"]), "parent_generation")
        for row in (source, discovered):
            if row is not None:
                validate_source(row)
        if source is None:
            require(not value["source_trusted"] and not retired and discovered is None and binding is None, "missing_source")
        else:
            require(source["source_instance"] not in retired, "retired_current")
        if discovered is not None:
            require(source is not None and parent["active"] and value["source_trusted"]
                    and discovered["lifecycle_state"] == "active" and source_semantics(discovered, source)
                    and discovered["completed_requests"] <= source["completed_requests"], "discovered_source")
        if binding is not None:
            keys(binding, {"generation", "canonical_generation", "parent_generation", "source"}, "binding_keys")
            require(all(integer(binding[name], 1) for name in ("generation", "canonical_generation", "parent_generation")), "binding_generations")
            require(binding["canonical_generation"] == binding["parent_generation"] <= parent["generation"], "binding_parent_generation")
            bound = binding["source"]
            validate_source(bound)
            require(bound["model_ready"] and bound["lifecycle_state"] == "active" and source is not None
                    and bound["source_id"] == source["source_id"], "binding_source")
            if bound["source_instance"] == source["source_instance"]:
                require(bound["source_generation"] <= source["source_generation"]
                        and bound["completed_requests"] <= source["completed_requests"], "binding_source_order")
                require(all(same(bound[name], source[name]) for name in ("host_boot_id", "process_id", "service_start_generation")), "binding_instance")
                if bound["source_generation"] == source["source_generation"]:
                    require(source_semantics(bound, source), "binding_semantics")
            else:
                require(bound["source_instance"] in retired and bound["service_start_generation"] < source["service_start_generation"], "binding_retired")
            valid = bool(parent["active"] and value["source_trusted"] and value["binding_confirmed"]
                         and source["model_ready"] and source["lifecycle_state"] == "active"
                         and binding["canonical_generation"] == node["generation"] and source_semantics(bound, source))
        require(not value["binding_confirmed"] or valid and discovered is not None, "binding_confirmation")
        require(parent["active"] or not value["source_trusted"], "inactive_parent")
        present_valid = bool(parent["active"] and value["source_trusted"] and source is not None)
        present = bool(present_valid and source["lifecycle_state"] == "active")
        state = "BOUND" if valid else "STALE" if binding else "DISCOVERED" if discovered else "UNBOUND"
        expected_bits = [{"namespace": "nodebit", "id": bit_id, "class": kind, "name": name,
                          "parent_node_id": 101, "parent_node_generation": node["generation"], "value": bit, "valid": validity}
                         for bit_id, kind, name, bit, validity in
                         ((1001, "state", "present", present, present_valid), (1002, "validity", "source-bound", valid, True))]
    require(value["state"] == state and value["binding_valid"] is valid and value["binding_current"] is valid
            and same(value["bound_nodes"], int(valid)) and same(value["nodebits"], expected_bits), "snapshot_derived")


def receipt(value: dict, *, prompt: str | None = None, purpose: str | None = None,
            source: dict | None = None, require_live: bool = False, config: dict | None = None,
            descriptor: dict | None = None) -> None:
    require(type(value) is dict and type(value.get("schema_version")) is int
            and value["schema_version"] in (1, 2, 3), "receipt_schema")
    receipt_keys = {1: RECEIPT_KEYS, 2: MANAGED_RECEIPT_KEYS, 3: SPACE_RECEIPT_KEYS}[value["schema_version"]]
    require(set(value) in (receipt_keys, receipt_keys | REQUEST_CONTEXT), "receipt_keys")
    identity(value["request_id"])
    require(type(value["started_at"]) is str and len(value["started_at"]) <= 40, "receipt_time")
    when = datetime.fromisoformat(value["started_at"])
    require(when.tzinfo is not None and when.utcoffset() == timezone.utc.utcoffset(when), "receipt_timezone")
    require(value["purpose"] in ("warmup", "user") and (purpose is None or purpose == value["purpose"]), "receipt_purpose")
    require(type(value["model_id"]) is str and 0 < len(value["model_id"]) <= 1024
            and all(c.isprintable() for c in value["model_id"]), "receipt_model_id")
    for name in ("model_sha256", "backend_sha256", "provenance_sha256", "request_sha256"):
        hash_value(value[name])
    request = strict_object(value["request_body"])
    context = value.get('space_context')
    prefix = _PREFIX
    if value['schema_version'] == 3:
        original = value['user_prompt']
        require(type(original) is str, 'receipt_user_prompt')
        require((context is None) == (value['purpose'] == 'warmup'), 'receipt_space_purpose')
        if context is not None:
            from space_output_contract import SPACE_PREFIX, prompt_with_context, validate_packet
            validate_packet(context, source=value.get('source_before', source), model_id=value['model_id'])
            require(context['consumer']['model_sha256'] == value['model_sha256']
                    and context['management']['binding_current'] is True, 'receipt_space_binding')
            prefix = SPACE_PREFIX
            encoded_prompt = prompt_with_context(original, context)
        else:
            require(original == 'Reply with the word ready.', 'receipt_warmup_prompt')
            encoded_prompt = original
    else:
        require(type(request.get('prompt')) is str and request['prompt'].startswith(prefix)
                and request['prompt'].endswith(_SUFFIX), 'receipt_chatml')
        original = request['prompt'][len(prefix):-len(_SUFFIX)]
        encoded_prompt = original
    require(bool(original.strip()) and len(original.encode("utf-8")) <= 4096
            and all(ord(c) >= 32 or c in "\n\t" for c in original)
            and (prompt is None or original == prompt), "receipt_prompt")
    expected = {"prompt": prefix + encoded_prompt + _SUFFIX, "n_predict": 8 if value["purpose"] == "warmup" else 192 if context is not None else 64,
                "temperature": 0.0, "seed": 1, "cache_prompt": False, "stream": False}
    require(same(request, expected) and value["request_body"] == json.dumps(expected, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False) and digest(value["request_body"]) == value["request_sha256"], "receipt_request")
    require(integer(value["elapsed_ns"]) and value["outcome"] in ("OK", "ERROR"), "receipt_outcome")
    if value["outcome"] == "OK":
        require(value["error"] is None, "receipt_success_error")
        response = strict_object(value["response_body"])
        require(type(value["content"]) is str and bool(value["content"].strip())
                and len(value["content"].encode("utf-8")) <= 16384
                and integer(value["tokens_predicted"], 1, expected["n_predict"]), "receipt_completion")
        require(same(response.get("content"), value["content"]) and same(response.get("tokens_predicted"), value["tokens_predicted"])
                and response.get("model") == value["model_id"], "receipt_response")
        if context is not None:
            require(response.get('prompt') == request['prompt'] and response.get('truncated') is False,
                    'receipt_consumed_prompt')
        hash_value(value["response_sha256"])
        require(digest(value["response_body"]) == value["response_sha256"], "receipt_response_hash")
    else:
        require(value["error"] in ("backend-timeout", "backend-failed") and value["response_body"] is None
                and value["response_sha256"] is None and value["content"] is None and same(value["tokens_predicted"], 0), "receipt_failed_evidence")
    if source is not None:
        validate_source(source)
        require(source["model_sha256"] == value["model_sha256"], "receipt_source_model")
        if value["purpose"] == "warmup" and value["outcome"] == "OK":
            require(source["warmup_request_sha256"] == value["request_sha256"]
                    and source["warmup_response_sha256"] == value["response_sha256"] and source["completed_requests"] >= 1, "receipt_source_warmup")
    if value["schema_version"] in (2, 3):
        execution = value["backend_execution"]
        if value["outcome"] == "ERROR":
            require(execution is None, "receipt_failed_execution")
        elif execution is None:
            require(not require_live, "receipt_missing_execution")
        else:
            from execution_output_contract import validate_execution
            validate_execution(execution, config=config, require_live=require_live, descriptor=descriptor)
            require(all(same(execution["descriptor"][name], value[name]) for name in
                        ("model_id", "model_sha256", "backend_sha256")), "receipt_execution_model")
            require(execution["after"]["read_end_ns"] - execution["before"]["read_start_ns"] <= value["elapsed_ns"],
                    "receipt_execution_window")
    if set(value) == receipt_keys | REQUEST_CONTEXT:
        before, after = value["source_before"], value["source_after"]
        validate_source(before)
        validate_source(after)
        identity(value["authority_instance"])
        require(integer(value["binding_generation"], 1) and value["purpose"] == "user", "receipt_binding")
        require(before["model_ready"] and before["lifecycle_state"] == "active"
                and before["model_sha256"] == value["model_sha256"], "receipt_before")
        if value["outcome"] == "OK":
            require(source_semantics(before, after) and after["completed_requests"] == before["completed_requests"] + 1, "receipt_after_success")
        else:
            expected_after = {**before, "model_ready": False, "source_generation": before["source_generation"] + 1}
            require(same(after, expected_after), "receipt_after_failure")
        require(source is None or same(source, after), "receipt_source_after")
    owner = value.get("source_before", source)
    if value["schema_version"] in (2, 3) and value["backend_execution"] is not None and owner is not None:
        worker = value["backend_execution"]["send"]["client"]
        parent_pid = int(worker["raw_stat"].rsplit(") ", 1)[1].split()[1])
        require(worker["host_boot_id"] == owner["host_boot_id"] and parent_pid == owner["process_id"],
                "receipt_execution_owner")
    if context is not None and value['backend_execution'] is not None:
        require(context['checked_monotonic_ns'] <= value['backend_execution']['before']['read_start_ns'],
                'receipt_space_execution_order')


def canonical_task_id(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def agent_result(command: dict, schema: int, *, require_live: bool = False) -> None:
    name, args, value = command["name"], command["args"], command["result"]
    if name not in ("agent", "room", "cell", "ask", "resources", "space", "task"):
        return
    valid = schema in (3, 4, 5, 6, 7, 8, 9, 10) and (name == "agent" and len(args) == 1 and args[0] in ("status", "start", "stop", "restart")
            or name == "room" and len(args) == 1 and args[0] in ("status", "discover", "bind", "reconcile")
            or schema in (5, 6, 7, 8, 9, 10) and name == "cell" and len(args) == 1 and args[0] in ("status", "activate", "deactivate")
            or schema in (4, 5, 6, 7, 8, 9, 10) and name == "resources" and len(args) == 1 and args[0] in ("link", "status", "sample")
            or schema in (8, 9, 10) and name == "space" and not args
            or schema == 10 and name == "task" and len(args) == 2
               and args[0] in ("status", "result", "cancel") and canonical_task_id(args[1])
            or name == "ask" and bool(args))
    if not valid:
        require(command["outcome"] == "ERROR", "command_arguments")
        return
    action = "ask-start" if schema == 10 and name == "ask" else name if name in ("ask", "space") else name + "-" + args[0] if name in ("room", "cell", "resources", "task") else args[0]
    keys(value, TASK_PUBLIC_KEYS if schema == 10 else SPACE_PUBLIC_KEYS if schema in (8, 9) else RESOURCE_PUBLIC_KEYS if schema in (4, 5, 6, 7) else PUBLIC_KEYS, "reply_keys")
    require(type(value["schema_version"]) is int and value["schema_version"] == {3: 1, 4: 2, 5: 3, 6: 4, 7: 4, 8: 5, 9: 5, 10: 6}[schema]
            and value["action"] == action, "reply_action")
    require(value["outcome"] == command["outcome"] and value["outcome"] in ("OK", "ERROR")
            and (value["error"] is None) == (value["outcome"] == "OK")
            and (value["error"] is None or value["error"] in ERRORS or schema == 10 and value["error"] in TASK_ERRORS
                 or name == "resources" and type(value["error"]) is str
                 and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value["error"]) is not None
                 and len(value["error"]) <= 64), "reply_outcome")
    require(schema >= 6 or value["error"] != "backend-changed", "legacy_backend_error")
    require(schema in (8, 9, 10) or value['error'] not in {'space-invalid', 'space-overflow', 'space-future',
                                                'space-source-mismatch', 'space-budget'}, 'legacy_space_error')
    require(value["state"] in STATES and value["service_kind"] == "AI_SERVICE" and value["resource_actions"] == "UNSUPPORTED", "reply_boundary")
    require(value["capture_kind"] == "unsupported" if value["state"] == "UNSUPPORTED"
            else value["capture_kind"] in ("live", "fixture"), "reply_capture")
    require(not require_live or value["capture_kind"] != "fixture", "fixture_reply_not_live")
    require(value["management_outcome"] in (None, "accepted", "rejected"), "management_outcome")
    source, snapshot, inference = value["source_record"], value["management_snapshot"], value["inference_receipt"]
    if source is not None:
        validate_source(source)
    if snapshot is not None:
        validate_snapshot(snapshot)
    if schema in (8, 9, 10):
        context = value['space_context']
        if name == 'space' and value['outcome'] == 'OK':
            from space_output_contract import validate_packet
            require(source is not None and snapshot is not None and context is not None, 'space_context_missing')
            validate_packet(context, source=source, snapshot=snapshot)
            require(snapshot['current_source'] is None or snapshot['current_source']['source_instance'] != source['source_instance']
                    or same(snapshot['current_source'], source), 'space_current_source')
        else:
            require(context is None, 'unexpected_space_context')
    if value["state"] in ("ABSENT", "UNSUPPORTED"):
        require(source is None and snapshot is None and inference is None, "reply_absent")
    if value["state"] in ("RUNNING", "STOPPED", "STOPPING"):
        require(source is not None and snapshot is not None, "reply_source_missing")
        require(source["lifecycle_state"] == ("exited" if value["state"] == "STOPPED" else "active"), "reply_lifecycle")
    if value["outcome"] == "OK":
        acceptable = {"status": {"ABSENT", "RUNNING", "STOPPED"}, "stop": {"ABSENT", "STOPPED"},
                      "start": {"RUNNING"}, "restart": {"RUNNING"}}
        require(value["state"] in acceptable.get(action, {"RUNNING"}), "reply_action_state")
        if action in ("start", "restart"):
            require(source["model_ready"], "reply_start_not_ready")
    if value["state"] == "UNSUPPORTED":
        require(value["error"] == "unsupported-platform", "reply_unsupported")
    if action.startswith("cell-"):
        require(inference is None, "cell_inference")
        require(value["management_outcome"] == "accepted" if value["outcome"] == "OK"
                else value["management_outcome"] in (None, "rejected"), "cell_outcome")
        if value["outcome"] == "OK":
            require(snapshot is not None and snapshot["initialized"] and snapshot["parent"] is not None,
                    "cell_parent_missing")
            if action != "cell-status":
                require(snapshot["parent"]["active"] is (action == "cell-activate"), "cell_activity")
    elif action.startswith("room-"):
        require(inference is None, "room_inference")
        if snapshot is not None:
            require(value["management_outcome"] == "accepted" if value["outcome"] == "OK"
                    else value["management_outcome"] in (None, "rejected"), "room_outcome")
            if value["outcome"] == "OK":
                require(source is not None and same(snapshot["current_source"], source), "room_current_source")
                require(snapshot["state"] == "BOUND" if action != "room-discover" else snapshot["discovered_source"] is not None, "room_state")
    elif action != "ask":
        require(inference is None and value["management_outcome"] is None, "lifecycle_inference")
    if action == "ask":
        if inference is None:
            require(value["outcome"] == "ERROR", "ask_missing_receipt")
        else:
            expected_receipt_keys = SPACE_RECEIPT_KEYS if schema in (8, 9) else MANAGED_RECEIPT_KEYS if schema >= 6 else RECEIPT_KEYS
            require(type(inference) is dict and set(inference) == expected_receipt_keys | REQUEST_CONTEXT
                    and type(inference["schema_version"]) is int and inference["schema_version"] == (3 if schema in (8, 9) else 2 if schema >= 6 else 1)
                    and snapshot is not None and source is not None, "ask_context")
            receipt(inference, prompt=" ".join(args), purpose="user", source=source,
                    require_live=require_live or value["capture_kind"] == "live")
            if schema >= 6 and inference["backend_execution"] is not None:
                require(inference["backend_execution"]["capture_kind"] == value["capture_kind"], "ask_execution_capture")
            require(inference["outcome"] == value["outcome"] and inference["error"] == value["error"]
                    and same(snapshot["current_source"], source)
                    and snapshot["authority_instance"] == inference["authority_instance"]
                    and snapshot["binding"] is not None and snapshot["binding"]["generation"] == inference["binding_generation"], "ask_snapshot")
            require(source_semantics(snapshot["binding"]["source"], inference["source_before"])
                    and snapshot["binding"]["source"]["completed_requests"] <= inference["source_before"]["completed_requests"], "ask_bound_source")
            require(value["outcome"] != "OK" or snapshot["binding_current"], "ask_unbound")
            require(value["management_outcome"] == ("accepted" if value["outcome"] == "OK" else "rejected"), "ask_management_outcome")
            if schema in (8, 9):
                from space_output_contract import validate_packet
                # Failed execution may invalidate current_source after sending;
                # the packet records the pre-request, authorized binding.
                before_snapshot = {**snapshot, 'state': 'BOUND', 'binding_current': True}
                validate_packet(inference['space_context'], source=inference['source_before'],
                                snapshot=before_snapshot, model_id=inference['model_id'])
    if schema == 10:
        if action in TASK_ACTIONS:
            from task_output_contract import validate_task_state
            identity(value["request_id"])
            require(name != "task" or value["request_id"] == args[1], "task_command_id")
            require(all(value[key] is None for key in ("management_outcome", "inference_receipt",
                    "resource_result", "space_context")), "task_top_payload")
            task = value["task"]
            if task is None:
                require(value["outcome"] == "ERROR" and value["task_control"] is None, "task_missing")
            else:
                validate_task_state(task, require_live=require_live or value["capture_kind"] == "live")
                require(task["request_id"] == value["request_id"], "task_reply_id")
                if source is not None:
                    require(all(same(task["source_before"][key], source[key]) for key in
                        ("source_id", "source_instance", "service_start_generation", "host_boot_id", "process_id")),
                        "task_reply_source")
                if name == "ask":
                    require(task["user_prompt"] == " ".join(args), "task_reply_prompt")
            control = value["task_control"]
            if action != "task-cancel":
                require(control is None, "task_unexpected_control")
            elif control is None:
                require(value["outcome"] == "ERROR", "task_missing_control")
            else:
                keys(control, {"cancel_outcome", "backend_stop_attempt"}, "task_control_keys")
                require(task is not None and control["cancel_outcome"] in
                        ("ACCEPTED", "ALREADY_REQUESTED", "ALREADY_TERMINAL"), "task_cancel_outcome")
                require(task["phase"] == "FINISHED" if control["cancel_outcome"] == "ALREADY_TERMINAL"
                        else task["cancel_requested_ns"] is not None, "task_cancel_state")
                attempt = control["backend_stop_attempt"]
                if attempt is not None:
                    from backend_output_contract import validate_backend_result
                    require(control["cancel_outcome"] == "ACCEPTED", "task_duplicate_stop_attempt")
                    validate_backend_result(attempt, action="stop", require_live=require_live)
                    if attempt["outcome"] == "OK":
                        require(attempt["state"] == "STOPPED" and same(attempt["service_record"],
                            {**task["backend_expected"]["service_record"], "lifecycle_state": "exited",
                             "backend_ready": False}), "task_cancel_attempt_target")
        else:
            require(all(value[key] is None for key in ("request_id", "task", "task_control")),
                    "unexpected_task_payload")
    if schema in (4, 5, 6, 7, 8, 9, 10):
        resource = value["resource_result"]
        if name not in ("resources", "ask"):
            require(resource is None, "unexpected_resource")
        elif resource is not None:
            # Lazy import keeps the independently implemented resource checker
            # able to consume source/receipt validators without an import cycle.
            from resource_output_contract import validate_resource_result
            validate_resource_result(resource, source=source, snapshot=snapshot,
                                     receipt=inference, require_live=require_live or value["capture_kind"] == "live")
            require(resource["action"] == ("request" if name == "ask" else args[0]), "resource_action")
            require(resource["capture_kind"] == value["capture_kind"], "resource_capture")
            if name == "resources":
                require(value["outcome"] == resource["outcome"] and value["error"] == resource["error"], "resource_reply_outcome")
        elif name == "resources":
            require(value["outcome"] == "ERROR" and value["error"] in ERRORS
                    and (value["state"] != "RUNNING" or value["error"] in
                         ("request-limit", "rpc-timeout", "state-io", "protocol-error")), "resource_missing_result")


def render_resource(value: dict) -> str:
    """Independently reconstruct the bounded resource display after validation."""
    text = lambda value, limit=2048: "".join(c if c.isprintable() and unicodedata.category(c) != "Cf" else " " for c in str(value))[:limit]
    relation = "linked" if value["relation_current"] else "stale" if value["relation"] else "unlinked"
    lines = []
    if value["outcome"] == "ERROR":
        lines.append(f"Resources {text(value['action'], 16)} failed: {text(value['error'], 64)}.")
    lines.append(f"AIOS resources: {relation}; observation only; ownership unverified.")
    observation = value["observation"]
    if observation is None:
        lines.append("  No resource observation recorded; resource actions unsupported.")
    else:
        label = "Last observation (cached)" if value["action"] == "status" or value["outcome"] == "ERROR" else "Observed process window"
        lines.append(f"  {label}: {text(observation['observation_id'][:8], 8)}; resource actions unsupported.")
        def milliseconds(ns):
            whole, remainder = divmod(ns, 1_000_000)
            return f"{whole}.{remainder // 1000:03d} ms"
        for role, title in (("main", "MAIN control process"), ("backend", "Model backend process")):
            cpu = observation["cpu"][role]
            resident = observation["after"][role]["rss_bytes_estimate"]
            rss = "unavailable" if resident is None else f"{resident / 1048576:.2f} MiB"
            lines.append(f"  {title}: CPU {milliseconds(cpu['cpu_time_ns'])} over {milliseconds(cpu['elapsed_ns'])}; RSS estimate {rss}.")
        lines.append("  Linux system PSI (unattributed):")
        for kind in ("cpu", "memory", "io"):
            row = observation["after"]["pressure"]["metrics"][kind]
            if row["state"] != "AVAILABLE":
                lines.append(f"    {kind.upper()}: unavailable ({text(row['error'], 64)}).")
                continue
            def percent(bp):
                whole, remainder = divmod(bp, 100)
                return f"{whole}.{remainder:02d}%"
            full = "undefined for system CPU" if kind == "cpu" else percent(row["full"]["avg10_bp"])
            lines.append(f"    {kind.upper()}: some avg10 {percent(row['some']['avg10_bp'])}; full avg10 {full}.")
    return "".join(line + "\n" for line in lines)


def _ask_failure_lines(value: dict) -> tuple[str, ...]:
    """CLI9 display contract; a missing receipt alone proves no execution outcome."""
    error, inference = value["error"], value["inference_receipt"]
    if inference is None:
        if error == "request-busy":
            return ("The MAIN service already has an active task.",
                    "Inspect that task using its saved UUID before submitting another question.")
        if error == "prompt-invalid":
            return ("The question was rejected before model execution.",
                    "Revise the question to fit the input limit and use supported text.")
        if error == "space-budget":
            return ("The question and observed context exceed the input budget; model execution was not started.",
                    "Shorten the question before asking again.")
        if error in ("space-invalid", "space-overflow", "space-future"):
            return ("Environment context was rejected before model execution.",
                    "Run space to refresh observations and inspect its result before asking again.")
        if error == "space-source-mismatch":
            return ("Environment context does not match the current MAIN source or binding; model execution was not started.",
                    "Check agent status and room status; after resolving the mismatch, run space.")
        if error == "orphan":
            return ("The request was refused before model execution because its parent Cell is inactive.",
                    "Check cell status and room status before choosing an explicit management action.")
        if error in ("unbound", "stale", "model-not-ready", "source-exited", "retired-instance"):
            return ("The request was refused before model execution because the MAIN target or binding is not current and ready.",
                    "Check backend status, agent status and room status before choosing an explicit recovery action.")
        if error == "process-not-running":
            return ("The request was refused because the MAIN service is not running.",
                    "Check backend status and agent status before choosing an explicit start action.")
        if error == "request-limit":
            return ("The request was refused before model execution because this MAIN session reached its request limit.",
                    "Check agent status before deciding whether to start a new service session.")
        if error == "unsupported-platform":
            return ("Model execution is unavailable on this platform; use the Linux-hosted runtime.",)
    return ("No verified answer is available; the request outcome is unknown.",
            "An inference receipt was returned, but it does not confirm that backend work finished or stopped."
            if inference is not None else
            "No inference receipt was returned; this does not prove that the model request was never sent.",
            "Check backend status, agent status and room status before deciding whether to ask again.")


def render_agent(command: dict, *, console_version: str) -> str:
    """Render a validated response using its validated console, not MAIN, version."""
    name, args, value = command["name"], command["args"], command["result"]
    def text(value, limit=2048):
        return "".join(c if c.isprintable() and unicodedata.category(c) != "Cf" else " " for c in str(value))[:limit]
    action = value["action"]
    if console_version == "0.10.0" and name in ("ask", "task"):
        output = ("Cancel scope: stop the entire local model executor owned by this console.\n"
                  if name == "task" and args[0] == "cancel" else "")
        if value["outcome"] == "ERROR":
            output += f"MAIN {action} failed: {text(value['error'], 64)}.\n"
        request_id = text(value["request_id"], 36)
        if name == "ask":
            if value["outcome"] == "OK":
                output += f"Task accepted: {request_id}.\n"
            else:
                output += "".join("  " + line + "\n" for line in _ask_failure_lines(value))
            return output + f"Use task status {request_id}, task result {request_id}, or task cancel {request_id}.\n"
        task = value["task"]
        if task is None:
            return output + (f"Task {request_id}: no authenticated task state is available.\n"
                             "  Query this UUID before deciding whether to submit another question.\n")
        output += f"AIOS task {request_id}: {text(task['phase'], 24)}.\n"
        output += f"  Model outcome: {text(task['model_outcome'] or 'PENDING', 24)}.\n"
        output += f"  Cancellation requested: {'yes' if task['cancel_requested_ns'] is not None else 'no'}.\n"
        code = task["worker_exit_code"]
        output += "  Worker exit: not confirmed.\n" if code is None else f"  Worker exit: observed (code {code}).\n"
        output += ("  Backend stop: independently confirmed.\n" if task["backend_stop"] is not None
                   else "  Backend stop: not independently confirmed.\n")
        control = value["task_control"]
        if control is not None:
            output += f"  Cancel request: {text(control['cancel_outcome'], 24)}.\n"
            attempt = control["backend_stop_attempt"]
            if attempt is not None:
                output += f"  Console backend stop attempt: {text(attempt['outcome'], 16)}"
                output += f" ({text(attempt['error'], 64)}).\n" if attempt["error"] else ".\n"
        if args[0] == "result":
            if value["outcome"] == "OK" and task["phase"] == "FINISHED" and task["model_outcome"] == "ANSWERED":
                output += "MAIN answer:\n  " + text(task["inference_receipt"]["content"], 4096) + "\n"
            elif task["model_outcome"] == "NOT_STARTED":
                output += "  This task was not dispatched to the model worker.\n"
            elif task["model_outcome"] == "UNKNOWN":
                output += "  The model result is unknown; worker exit or backend stop does not prove an answer.\n"
            else:
                output += "  No verified answer is available; query this UUID again when needed.\n"
        return output
    if name == "cell":
        output = f"Cell {args[0]} failed: {text(value['error'], 64)}.\n" if value["outcome"] == "ERROR" else ""
        snapshot = value["management_snapshot"]
        if snapshot is None or snapshot["parent"] is None:
            return output + "AIOS Cell 1: unavailable; no management snapshot.\n"
        parent = snapshot["parent"]
        activity = "active" if parent["active"] else "inactive"
        output += f"AIOS Cell 1: {activity}; generation {parent['generation']}\n"
        output += f"  Bound MAIN nodes: {snapshot['bound_nodes']}; binding: {text(snapshot['state'], 16)}.\n"
        output += f"  MAIN process: {text(value['state'], 16)}; Cell activity does not start or stop it.\n"
        return output
    if name == "resources":
        resource = value["resource_result"]
        if resource is None:
            return f"Resources {args[0]} failed: {text(value['error'], 64)}.\nAIOS resources: unavailable; no resource response.\n"
        return render_resource(resource)
    output = f"MAIN {action} failed: {text(value['error'], 64)}.\n" if value["outcome"] == "ERROR" else ""
    if name == "ask":
        if value["outcome"] == "OK":
            output += "MAIN answer:\n  " + text(value["inference_receipt"]["content"], 4096) + "\n"
        elif console_version == '0.9.0':
            output += ''.join('  ' + line + '\n' for line in _ask_failure_lines(value))
        elif console_version == '0.8.0':
            output += '  No verified answer is available; check agent status and room status before retrying.\n'
        if value.get("resource_result") is not None:
            output += render_resource(value["resource_result"])
    elif name == 'space':
        context = value['space_context']
        if context is None:
            return output + 'AIOS space: unavailable; the MAIN service must be running.\n'
        from space_output_contract import model_context
        facts, management, valid = model_context(context)['facts'], context['management'], context['validity']
        directory, cpus, memory = (facts[key] for key in ('working_directory', 'logical_cpu_count', 'memory_total_bytes'))
        output += f'AIOS space: Linux-hosted MAIN; observations {valid}.\n'
        output += '  Runtime directory: ' + (text(directory['value'], 512) if directory['status'] == 'CURRENT' else directory['status']) + '\n'
        memory_text = f"{memory['value'] / 1048576:.2f} MiB" if memory['status'] == 'CURRENT' else memory['status']
        output += f"  CPUs: {cpus['value'] if cpus['status'] == 'CURRENT' else cpus['status']}; RAM: {memory_text}.\n"
        output += '  Network reachability: UNKNOWN; selected workspace: UNKNOWN.\n'
        output += f"  Management: {management['state']}; Cell {management['cell_id']} -> Node {management['node_id']}; binding current: {'yes' if management['binding_current'] else 'no'}.\n"
        output += '  Run space to refresh observations; ask uses this context.\n'
    elif name == "room":
        snapshot = value["management_snapshot"]
        if snapshot is None:
            output += "AIOS Room: unavailable; no management snapshot.\n"
        else:
            generation = snapshot["binding"]["generation"] if snapshot["binding"] else "none"
            output += f"AIOS Room: {text(snapshot['state'], 16)}; authority {text(snapshot['authority_instance'][:8], 8)}\n"
            output += f"  Cell 1 -> Node 101 (MAIN); binding generation {generation}; bound nodes {snapshot['bound_nodes']}.\n"
    else:
        source = value["source_record"]
        ready = "ready" if source is not None and source["model_ready"] else "not ready"
        instance = text(source["source_instance"][:8], 8) if source else "none"
        generation = source["source_generation"] if source else "none"
        output += f"AIOS MAIN service: {text(value['state'], 16)}; model {ready}\n"
        output += f"  Source generation {generation}; instance {instance}; resource actions unsupported.\n"
    return output
