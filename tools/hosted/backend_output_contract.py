"""Independent MODEL_BACKEND public-response and console-output checks.

This module checks reported evidence consistency only. Same-run supervisor and
child liveness, journal continuity and termination belong to verify_backend.
No runtime producer or renderer is imported.
"""
from __future__ import annotations

import re
import unicodedata

from newagent_output_contract import identity, same
from resource_output_contract import endpoint

U64 = (1 << 64) - 1
PUBLIC_KEYS = {"schema_version", "action", "outcome", "error", "state", "service_kind",
               "capture_kind", "service_record", "descriptor", "resource_actions"}
RECORD_KEYS = {"schema_version", "service_id", "instance_id", "start_generation", "supervisor_identity",
               "child_identity", "lifecycle_state", "backend_ready", "config_sha256", "profile",
               "source_only", "backend_source_instance"}
IDENTITY_KEYS = {"host_boot_id", "process_id", "process_start_ticks", "uid"}
DESCRIPTOR_KEYS = {"schema_version", "source_namespace", "source_instance", "source_generation", "lifecycle_state",
    "source_only", "producer_owned", "host_boot_id", "process_id", "process_start_ticks", "launcher_process_id",
    "launcher_start_ticks", "model_id", "model_sha256", "backend_sha256", "endpoint", "listener_inode"}
STATES = {"ABSENT", "STARTING", "RUNNING", "STOPPING", "STOPPED", "STALE", "FAILED", "UNSUPPORTED", "RECOVERED"}
ACTIONS = {"status", "start", "stop", "restart", "recover"}


def require(condition, reason):
    if not condition:
        raise ValueError("backend_contract:" + reason)


def keys(value, expected, reason):
    require(type(value) is dict and value.keys() == expected, reason)


def integer(value, minimum=0, maximum=U64):
    require(type(value) is int and minimum <= value <= maximum, "integer")


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "hash")


def process_identity(value):
    keys(value, IDENTITY_KEYS, "process_identity_keys")
    identity(value["host_boot_id"])
    integer(value["process_id"], 1)
    integer(value["process_start_ticks"], 1)
    integer(value["uid"], 0, (1 << 32) - 1)


def validate_record(value):
    keys(value, RECORD_KEYS, "record_keys")
    require(same(value["schema_version"], 1), "record_schema")
    for name in ("service_id", "instance_id"):
        identity(value[name])
    integer(value["start_generation"], 1, 64)
    require(value["lifecycle_state"] in ("starting", "active", "exited")
            and type(value["backend_ready"]) is bool and value["source_only"] is True, "record_boundary")
    sha(value["config_sha256"])
    require(value["profile"] in ("llamafile-pinned", "python-fixture"), "record_profile")
    if value["backend_source_instance"] is not None:
        identity(value["backend_source_instance"])
    parent, child = value["supervisor_identity"], value["child_identity"]
    process_identity(parent)
    if child is not None:
        process_identity(child)
        require(child["process_id"] != parent["process_id"] and child["host_boot_id"] == parent["host_boot_id"]
                and child["uid"] == parent["uid"], "record_child")
    if value["backend_ready"]:
        require(value["lifecycle_state"] == "active" and child is not None
                and value["backend_source_instance"] is not None, "record_ready")


def validate_descriptor(value, record):
    keys(value, DESCRIPTOR_KEYS, "descriptor_keys")
    require(same(value["schema_version"], 1) and same(value["source_generation"], 1), "descriptor_schema")
    require(value["source_namespace"] == "linux-model-backend" and value["lifecycle_state"] == "active"
            and value["source_only"] is True and value["producer_owned"] is True, "descriptor_boundary")
    identity(value["source_instance"])
    identity(value["host_boot_id"])
    for name in ("process_id", "process_start_ticks", "launcher_process_id", "launcher_start_ticks", "listener_inode"):
        integer(value[name], 1)
    require(type(value["model_id"]) is str and 0 < len(value["model_id"].encode("utf-8")) <= 1024
            and all(c.isprintable() for c in value["model_id"]), "descriptor_model")
    sha(value["model_sha256"])
    sha(value["backend_sha256"])
    endpoint(value["endpoint"])
    child, parent = record["child_identity"], record["supervisor_identity"]
    require(child is not None and value["source_instance"] == record["backend_source_instance"]
            and all(same(value[name], child[name]) for name in ("host_boot_id", "process_id", "process_start_ticks"))
            and value["launcher_process_id"] == parent["process_id"]
            and value["launcher_start_ticks"] == parent["process_start_ticks"], "descriptor_record")


def validate_backend_result(value, *, action=None, require_live=False):
    """Validate one public response; no stored READY value proves liveness."""
    keys(value, PUBLIC_KEYS, "reply_keys")
    require(same(value["schema_version"], 1), "reply_schema")
    require(value["action"] in ACTIONS and (action is None or value["action"] == action), "reply_action")
    require(value["outcome"] in ("OK", "ERROR") and (value["error"] is None) == (value["outcome"] == "OK"), "reply_outcome")
    if value["error"] is not None:
        require(type(value["error"]) is str and len(value["error"]) <= 64
                and re.fullmatch(r"[a-z]+(?:-[a-z]+)*", value["error"]) is not None, "reply_error")
    state, record, descriptor = value["state"], value["service_record"], value["descriptor"]
    require(state in STATES and value["service_kind"] == "MODEL_BACKEND"
            and value["resource_actions"] == "UNSUPPORTED", "reply_boundary")
    require(value["capture_kind"] == "unsupported" if state == "UNSUPPORTED"
            else value["capture_kind"] in ("live", "fixture"), "reply_capture")
    require(not require_live or value["capture_kind"] != "fixture", "fixture_not_live")
    if record is not None:
        validate_record(record)
        require(value["capture_kind"] == ("fixture" if record["profile"] == "python-fixture" else "live"), "record_capture")
    if state in ("ABSENT", "UNSUPPORTED"):
        require(record is None and descriptor is None, "reply_absent")
    if state == "UNSUPPORTED":
        require(value["error"] == "unsupported-platform", "reply_unsupported")
    if state == "RUNNING":
        require(record is not None and record["backend_ready"] and descriptor is not None, "reply_running")
    elif record is not None:
        require(record["backend_ready"] is False, "reply_not_ready")
    if state == "STARTING":
        require(record is not None or value["error"] == "start-in-progress", "reply_starting_record")
        if record is not None:
            require(record["lifecycle_state"] == "starting", "reply_starting")
    if state == "STOPPING":
        require(record is not None and record["lifecycle_state"] in ("starting", "active"), "reply_stopping")
    if state in ("STOPPED", "RECOVERED"):
        require(record is not None and record["child_identity"] is not None, "reply_stopped_record")
    if state in ("STOPPED", "RECOVERED", "FAILED") and record is not None:
        require(record["lifecycle_state"] == "exited", "reply_terminal")
    if descriptor is not None:
        require(state == "RUNNING" and record is not None and record["backend_ready"], "reply_descriptor_state")
        validate_descriptor(descriptor, record)
    if value["outcome"] == "OK":
        allowed = {"status": {"ABSENT", "STARTING", "RUNNING", "STOPPED", "RECOVERED"}, "start": {"RUNNING"},
                   "stop": {"ABSENT", "STOPPED", "RECOVERED"}, "restart": {"RUNNING"}, "recover": {"RECOVERED"}}
        require(state in allowed[value["action"]], "reply_action_state")


def backend_result(command, schema, *, require_live=False):
    if command["name"] != "backend":
        return
    args = command["args"]
    allowed = ACTIONS if schema in (7, 8, 9, 10) else ACTIONS - {"recover"}
    if schema not in (6, 7, 8, 9, 10) or len(args) != 1 or args[0] not in allowed:
        require(command["outcome"] == "ERROR", "command_arguments")
        return
    validate_backend_result(command["result"], action=args[0], require_live=require_live)
    require(schema >= 7 or command["result"]["state"] != "RECOVERED", "recovery_schema")
    require(command["result"]["outcome"] == command["outcome"], "command_outcome")


def render_backend(command):
    """Reconstruct output independently after the response contract passes."""
    value = command["result"]
    def text(value, maximum):
        return "".join(c if c.isprintable() and unicodedata.category(c) != "Cf" else " "
                       for c in str(value))[:maximum]
    output = f"Backend {command['args'][0]} failed: {text(value['error'], 64)}.\n" if value["outcome"] == "ERROR" else ""
    record = value["service_record"]
    ready = "ready" if value["state"] == "RUNNING" and record is not None and record["backend_ready"] else "not ready"
    generation = record["start_generation"] if record else "none"
    instance = text(record["instance_id"][:8], 8) if record else "none"
    output += f"AIOS model backend: {text(value['state'], 16)}; backend {ready}\n"
    output += f"  Start generation {generation}; instance {instance}; identity: source-only.\n"
    output += "  MAIN service and Cell management state are separate; resource actions unsupported.\n"
    return output
