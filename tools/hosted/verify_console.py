#!/usr/bin/env python3
"""Independent checks for one AIOS console session and its execution evidence."""
from __future__ import annotations

import argparse
import ast
import hashlib
import ipaddress
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from console_output_contract import validate_output
from newagent_output_contract import agent_result
from backend_output_contract import backend_result
from resource_output_contract import validate_sample
from verify_boot import keys, parse, require, verify_bundle

LIMIT = 4 * 1024 * 1024
LEGACY_SOURCES = ("aios-boot.py", "aios-console.py", "aios_hosted/__init__.py", "aios_hosted/boot.py",
                  "aios_hosted/hardware.py", "aios_console/__init__.py", "aios_console/shell.py", "aios_console/network.py")
SERVICE_SOURCES = (*LEGACY_SOURCES, "aios-service.py", "aios_service/__init__.py", "aios_service/client.py", "aios_service/lifecycle.py")
AGENT_SOURCES = (*SERVICE_SOURCES, "aios-agent.py", "aios_agent/__init__.py", "aios_agent/inference.py",
           "aios_agent/protocol.py", "aios_agent/client.py", "aios_agent/daemon.py",
           "aios_management/__init__.py", "aios_management/binding.py")
SOURCES = (*AGENT_SOURCES, 'aios_resources/__init__.py', 'aios_resources/proc.py',
           'aios_resources/backend.py', 'aios_resources/runtime.py', 'aios_management/resources.py')
MANAGED_SOURCES = (*SOURCES, "aios-backend.py", "aios_backend/__init__.py", "aios_backend/protocol.py",
                   "aios_backend/client.py", "aios_backend/daemon.py", "aios_agent/backend_binding.py")
SPACE_SOURCES = (*MANAGED_SOURCES, "aios_agent/space.py")
TASK_SOURCES = (*SPACE_SOURCES, "aios_agent/async_inference.py", "aios_agent/request_state.py",
                "aios_agent/request_runtime.py")
VERSIONS = {1: "0.1.0", 2: "0.2.0", 3: "0.3.0", 4: "0.4.0", 5: "0.5.0", 6: "0.6.0", 7: "0.7.0", 8: "0.8.0", 9: "0.9.0", 10: "0.10.0"}
SCHEMA_SOURCES = {1: LEGACY_SOURCES, 2: SERVICE_SOURCES, 3: AGENT_SOURCES, 4: SOURCES, 5: SOURCES, 6: MANAGED_SOURCES, 7: MANAGED_SOURCES, 8: SPACE_SOURCES, 9: SPACE_SOURCES, 10: TASK_SOURCES}
FILES = ("console.log", "session.events.jsonl", "boot/result.json")
NETWORK_ERRORS = {"invalid_host", "invalid_url", "unsupported_protocol", "invalid_timeout",
                  "invalid_max_bytes", "dns_failed", "timeout", "tls_certificate", "tls_failed",
                  "connection_failed", "http_protocol", "http_status", "worker_failed"}
SERVICE_ERRORS = {"UNSUPPORTED_PLATFORM", "INVALID_ACTION", "INVALID_STATE_DIRECTORY", "STATE_CORRUPT",
                  "STATE_IO", "ALREADY_RUNNING", "START_IN_PROGRESS", "START_FAILED", "START_TIMEOUT",
                  "SERVICE_UNREACHABLE", "STALE_INSTANCE", "PROCESS_NOT_RUNNING", "PEER_MISMATCH",
                  "PROTOCOL_ERROR", "STOP_TIMEOUT", "STOP_FAILED", "PIDFD_UNAVAILABLE", "OBSERVATION_FAILED",
                  "BOOT_FAILED", "INTERNAL_ERROR", "GENERATION_EXHAUSTED", "BUSY"}
SERVICE_STATES = {"ABSENT", "STARTING", "RUNNING", "STOPPING", "STOPPED", "FAILED", "STALE", "UNSUPPORTED"}


def read(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    require(len(raw) <= LIMIT, "artifact_size:" + path.name)
    return raw


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_version(raw: bytes) -> str:
    """Accept only an optional module docstring and one literal VERSION assignment."""
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (SyntaxError, UnicodeError) as exc:
        raise ValueError("console_source_version") from exc
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and type(body[0].value.value) is str:
        body = body[1:]
    require(len(body) == 1 and isinstance(body[0], ast.Assign) and len(body[0].targets) == 1
            and isinstance(body[0].targets[0], ast.Name) and body[0].targets[0].id == "VERSION"
            and isinstance(body[0].value, ast.Constant) and type(body[0].value.value) is str,
            "console_source_version")
    return body[0].value.value


def printable(value: object, maximum: int = 2048) -> bool:
    return type(value) is str and len(value) <= maximum and all(c.isprintable() for c in value)


def byte_text(value: object, maximum: int) -> bool:
    return printable(value, maximum) and len(value.encode("utf-8")) <= maximum


def normalized_host(value: object) -> str:
    require(printable(value, 253) and bool(value), "dns_request_host")
    require(not any(c.isspace() or c in "/\\@%[]?#" for c in value), "dns_request_host")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        result = value.encode("idna").decode("ascii").lower()
        labels = (result[:-1] if result.endswith(".") else result).split(".")
        require(len(result) <= 253 and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                          for label in labels), "dns_request_host")
        return result


def parsed_command(line: object) -> list[str]:
    # The AIOS console uses whitespace splitting, without shell quote/escape rules.
    require(type(line) is str and len(line) <= 2048
            and all(c.isprintable() or c == "\t" for c in line), "requested_command_text")
    fields = line.split()
    return [fields[0].lower(), *fields[1:]] if fields else [""]


def number(value: object, minimum: int = 0, maximum: int = 2**63 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def service_result(command: dict, schema: int) -> None:
    """Validate runtime-private service observations without importing its code.

    These identities are not canonical AIOS objects. This gate checks one
    reported command; the service lifecycle verifier supplies cross-process
    liveness, generation and shutdown proof from separate daemon artifacts.
    """
    if command["name"] != "service":
        return
    args, result = command["args"], command["result"]
    if schema == 1 or len(args) != 1 or args[0] not in ("status", "start", "stop", "restart"):
        require(command["outcome"] == "ERROR", "service_arguments")
        return
    keys(result, {"schema_version", "outcome", "error", "service_kind", "service_id", "instance_id",
                  "generation", "state", "pid", "observation_sequence", "heartbeat_monotonic_ns", "boot_id",
                  "source_only", "binding_status", "management_actions"}, "service_result")
    require(type(result["schema_version"]) is int and result["schema_version"] == 1, "service_schema")
    require(result["outcome"] == command["outcome"] and result["outcome"] in ("OK", "ERROR"), "service_outcome")
    require((result["error"] is None) == (result["outcome"] == "OK")
            and (result["error"] is None or result["error"] in SERVICE_ERRORS), "service_error")
    require(result["service_kind"] == "CONSOLE_RUNTIME" and result["source_only"] is True
            and result["binding_status"] == "UNBOUND" and result["management_actions"] == "UNSUPPORTED",
            "service_boundary")
    require(result["state"] in SERVICE_STATES, "service_state")
    for field in ("service_id", "instance_id", "boot_id"):
        value = result[field]
        require(value is None or type(value) is str and str(uuid.UUID(value)) == value, "service_identity:" + field)
    require(result["generation"] is None or number(result["generation"], 1), "service_generation")
    require(result["pid"] is None or number(result["pid"], 1), "service_pid")
    require(number(result["observation_sequence"]), "service_observations")
    require(result["heartbeat_monotonic_ns"] is None or number(result["heartbeat_monotonic_ns"], 1), "service_heartbeat")
    absent = result["service_id"] is None
    if absent:
        require(all(result[key] is None for key in ("instance_id", "generation", "pid", "heartbeat_monotonic_ns", "boot_id"))
                and result["observation_sequence"] == 0 and result["state"] in ("ABSENT", "UNSUPPORTED", "FAILED"),
                "service_absent_identity")
    else:
        require(all(result[key] is not None for key in ("instance_id", "generation")), "service_partial_identity")
        require(result["state"] not in ("ABSENT", "UNSUPPORTED"), "service_unexpected_identity")
        require((result["pid"] is None) == (result["boot_id"] is None), "service_process_identity")
        if result["pid"] is None:
            # The registry is published before the first daemon snapshot. If
            # it dies in that window, no Linux process observation exists yet.
            require(result["state"] in ("STARTING", "STALE") and result["observation_sequence"] == 0,
                    "service_missing_process")
    require((result["observation_sequence"] == 0) == (result["heartbeat_monotonic_ns"] is None),
            "service_observation_heartbeat")
    if result["state"] in ("RUNNING", "STOPPING", "STOPPED"):
        require(not absent and result["pid"] is not None and result["heartbeat_monotonic_ns"] is not None
                and result["observation_sequence"] >= 1, "service_running_evidence")
    if command["outcome"] == "OK":
        acceptable = {"status": {"ABSENT", "RUNNING", "STOPPED"},
                      "start": {"RUNNING"}, "restart": {"RUNNING"}, "stop": {"ABSENT", "STOPPED"}}
        require(result["state"] in acceptable[args[0]], "service_action_state")
    if result["state"] in ("FAILED", "STALE"):
        require(result["outcome"] == "ERROR", "service_failure_outcome")
    if result["state"] == "UNSUPPORTED":
        require(result["error"] == "UNSUPPORTED_PLATFORM", "service_unsupported")


def network_result(command: dict) -> tuple[bool, bool]:
    name, args, result = command["name"], command["args"], command["result"]
    if name not in ("resolve", "fetch"):
        return False, False
    if len(args) != 1:
        require(command["outcome"] == "ERROR" and result.get("operation") is None,
                "network_arguments")
        return False, False
    # A parser rejection is not a network observation.
    if result.get("operation") not in ("resolve", "fetch"):
        require(command["outcome"] == "ERROR", "network_result_missing")
        return False, False
    common = {"operation", "outcome", "error", "elapsed_ms"}
    require(result["operation"] == name and result["outcome"] == command["outcome"], "network_operation")
    require(number(result["elapsed_ms"]), "network_duration")
    require((result["error"] is None) == (result["outcome"] == "OK"), "network_error_state")
    require(result["error"] is None or result["error"] in NETWORK_ERRORS, "network_error")
    if name == "resolve":
        keys(result, common | {"host", "addresses"}, "resolve")
        require(printable(result["host"], 253), "dns_host")
        addresses = result["addresses"]
        require(type(addresses) is list and len(addresses) <= 32, "dns_addresses")
        for value in addresses:
            require(type(value) is str and str(ipaddress.ip_address(value)) == value, "dns_address_type")
        require(len(set(addresses)) == len(addresses), "dns_addresses")
        if result["outcome"] == "OK":
            require(bool(addresses) and result["host"] == normalized_host(args[0]), "dns_empty_or_wrong_host")
            try:
                ipaddress.ip_address(result["host"])
                return False, False  # Numeric input does not prove a DNS query.
            except ValueError:
                return True, False
        require(not addresses, "failed_dns_addresses")
        return False, False
    keys(result, common | {"url", "status", "protocol", "tls_verified", "received_bytes", "truncated",
                           "content_type", "body_preview", "body_sha256"}, "fetch")
    require(byte_text(result["url"], 2048) and result["protocol"] in (None, "http", "https"), "fetch_url")
    require(type(result["tls_verified"]) is bool and type(result["truncated"]) is bool, "fetch_flags")
    require(number(result["received_bytes"], 0, 65536), "fetch_size")
    require(result["status"] is None or number(result["status"], 100, 599), "fetch_status")
    require(result["content_type"] is None or byte_text(result["content_type"], 256), "fetch_content_type")
    require(byte_text(result["body_preview"], 2048), "fetch_preview")
    require(result["body_sha256"] is None or type(result["body_sha256"]) is str
            and bool(re.fullmatch(r"[0-9a-f]{64}", result["body_sha256"])), "fetch_body_hash")
    require(not result["tls_verified"] or result["protocol"] == "https", "http_cannot_verify_tls")
    require(result["body_sha256"] is not None or (result["received_bytes"] == 0
            and result["body_preview"] == "" and not result["truncated"]), "fetch_missing_body_hash")
    # This command surface has no max_bytes option and always requests 16 KiB.
    require(result["received_bytes"] <= 16384, "fetch_command_limit")
    require(not result["truncated"] or result["received_bytes"] == 16384, "fetch_truncated_size")
    if result["outcome"] == "OK":
        target = urlsplit(args[0])
        require(result["url"] == args[0] and result["protocol"] == target.scheme
                and target.scheme in ("http", "https") and bool(target.netloc)
                and target.username is None and target.password is None and not target.fragment,
                "fetch_request")
        normalized_host(target.hostname)
        require(target.port is None or 1 <= target.port <= 65535, "fetch_port")
        require(not target.netloc.endswith(":") and "\\" not in args[0]
                and not re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", args[0], re.I)
                and not re.search(r"%(?![0-9a-f]{2})", args[0], re.I), "fetch_request")
        require(number(result["status"], 200, 299) and result["body_sha256"] is not None, "fetch_success")
        require(result["protocol"] != "https" or result["tls_verified"], "https_not_verified")
        return False, result["protocol"] == "https" and result["received_bytes"] > 0
    if result["error"] == "http_status":
        require(type(result["status"]) is int and not 200 <= result["status"] < 300
                and result["body_sha256"] is not None, "fetch_error_status")
    return False, False


def verify_session(directory: Path, *, source_root: Path | None = None, require_live: bool = False,
                   require_internet: bool = False, expected_session_id: str | None = None,
                   process_exit: int | None = None, expected_commands: list[str] | None = None) -> dict:
    try:
        root = source_root or Path(__file__).resolve().parents[2] / "hosted/linux"
        result = parse(read(directory / "session-result.json"))
        keys(result, {"schema_version", "session_id", "capture_kind", "state", "exit_code", "boot_run_id", "source_hashes", "files"}, "result")
        schema = result["schema_version"]
        require(type(schema) is int and schema in VERSIONS, "schema")
        session_id = result["session_id"]
        require(str(uuid.UUID(session_id)) == session_id, "session_id")
        require(expected_session_id is None or session_id == expected_session_id, "stale_session")
        require(result["capture_kind"] in ("live", "fixture"), "capture_kind")
        require(not require_live or result["capture_kind"] == "live", "fixture_not_live")
        require(result["state"] == "CLOSED" and type(result["exit_code"]) is int and result["exit_code"] == 0, "session_not_clean")
        require(process_exit is None or type(process_exit) is int and process_exit == result["exit_code"], "process_exit")
        sources = SCHEMA_SOURCES[schema]
        keys(result["source_hashes"], set(sources), "sources")
        for name in sources:
            source_raw = read(root / name)
            require(digest(source_raw) == result["source_hashes"][name], "source_hash:" + name)
            if schema in (8, 9, 10) and name == "aios_console/__init__.py":
                require(source_version(source_raw) == VERSIONS[schema], "console_source_version")
        keys(result["files"], set(FILES), "files")
        raw = {name: read(directory / name) for name in FILES}
        for name in FILES:
            require(digest(raw[name]) == result["files"][name], "artifact_hash:" + name)
        boot = verify_bundle(directory / "boot", require_live=require_live, expected_run_id=result["boot_run_id"], source_root=root)
        require(boot["outcome"] == "PASS", "boot_not_ready:" + str(boot))
        require(boot["capture_kind"] == result["capture_kind"], "capture_mismatch")
        inventory = parse(read(directory / "boot/inventory.json"))["inventory"]
        events = [parse(line) for line in raw["session.events.jsonl"].splitlines()]
        require(2 <= len(events) <= 256, "event_count")
        prior_elapsed = -1
        for index, event in enumerate(events, 1):
            keys(event, {"schema_version", "session_id", "sequence", "elapsed_ns", "event", "data"}, "event")
            require(type(event["schema_version"]) is int and event["schema_version"] == schema, "event_schema")
            require(event["session_id"] == session_id and type(event["sequence"]) is int and event["sequence"] == index, "event_identity_order")
            require(number(event["elapsed_ns"], max(0, prior_elapsed)), "event_time")
            prior_elapsed = event["elapsed_ns"]
        require([e["event"] for e in events] == ["START", *(["COMMAND"] * (len(events) - 2)), "STOP"], "terminal_order")
        start = events[0]["data"]
        keys(start, {"product", "runtime_version", "runtime_kind", "capture_kind", "boot_run_id", "boot_state",
                     "binding_status", "management_actions", "hardware_scope", "network_io"}
                    | ({"source_process"} if schema >= 7 else set()), "start")
        if schema >= 7:
            sample = start["source_process"]
            if result["capture_kind"] == "live":
                validate_sample(sample)
            else:
                require(sample is None, "fixture_source_process")
        require({k: v for k, v in start.items() if k != "source_process"} == {"product": "AIOS", "runtime_version": VERSIONS[schema], "runtime_kind": "linux-hosted-userspace-console",
                          "capture_kind": result["capture_kind"], "boot_run_id": result["boot_run_id"], "boot_state": "READY",
                          "binding_status": "UNBOUND", "management_actions": "UNSUPPORTED", "hardware_scope": "linux-visible",
                          "network_io": "user-requested"}, "start_boundary")
        stop = events[-1]["data"]
        keys(stop, {"state", "exit_code", "reason"}, "stop")
        require(stop["state"] == "CLOSED" and type(stop["exit_code"]) is int and stop["exit_code"] == 0 and stop["reason"] in ("exit", "eof"), "stop_state")
        commands = []
        dns, https = False, False
        for event in events[1:-1]:
            command = event["data"]
            keys(command, {"name", "args", "outcome", "result"}, "command")
            require(printable(command["name"], 2048) and type(command["args"]) is list and len(command["args"]) <= 1024, "command_name_args")
            require(all(printable(a) for a in command["args"]), "command_args")
            require(len(command["name"]) + sum(len(a) + 1 for a in command["args"]) <= 2048,
                    "command_input_limit")
            require(command["outcome"] in ("OK", "ERROR") and type(command["result"]) is dict, "command_result")
            commands.append([command["name"], *command["args"]])
            service_result(command, schema)
            # A real boot cannot turn a fixture model response into a live
            # console claim. Explicit callback sessions are already fixtures.
            agent_result(command, schema, require_live=require_live or result["capture_kind"] == "live")
            if schema == 10 and command['result'].get('task') is not None:
                owner = start['source_process']
                if owner is not None:
                    from newagent_output_contract import same
                    require(same(command['result']['task']['owner'], {key: owner[key] for key in
                            ('host_boot_id', 'process_id', 'process_start_ticks', 'uid')}), 'task_console_owner')
            backend_result(command, schema, require_live=require_live or result["capture_kind"] == "live")
            observed_dns, observed_https = network_result(command)
            dns, https = dns or observed_dns, https or observed_https
        if expected_commands is not None:
            require(type(expected_commands) is list and len(expected_commands) <= 254,
                    "requested_commands_count")
            require(commands == [parsed_command(line) for line in expected_commands], "requested_commands_mismatch")
        if stop["reason"] == "exit":
            require(bool(commands) and commands[-1] == ["exit"], "exit_command_missing")
        require(not require_internet or dns and https, "internet_not_proven")
        validate_output(events, inventory, raw["console.log"])
        return {"schema_version": 1, "outcome": "PASS", "session_id": session_id, "capture_kind": result["capture_kind"],
                "state": "CLOSED", "dns_observed": dns, "https_observed": https, "command_count": len(commands), "reasons": []}
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, AttributeError, RecursionError) as exc:
        return {"schema_version": 1, "outcome": "FAIL", "reasons": [str(exc)]}


def verify_execution(directory: Path, **kwargs) -> dict:
    try:
        execution = parse(read(directory / "execution.json"))
        keys(execution, {"schema_version", "mode", "process_exit_code", "stdout_sha256", "stderr_sha256", "requested_commands"}, "execution")
        require(type(execution["schema_version"]) is int and execution["schema_version"] == 1, "execution_schema")
        require(execution["mode"] in ("interactive", "smoke"), "execution_mode")
        require(type(execution["process_exit_code"]) is int and execution["process_exit_code"] == 0, "process_not_clean")
        requested = execution["requested_commands"]
        require(requested is None or type(requested) is list and len(requested) <= 254, "requested_commands")
        if requested is not None:
            for line in requested:
                parsed_command(line)
        require((execution["mode"] == "smoke") == (requested is not None), "execution_script")
        stdout, stderr = read(directory / "stdout.log"), read(directory / "stderr.log")
        require(digest(stdout) == execution["stdout_sha256"] and digest(stderr) == execution["stderr_sha256"], "process_hash")
        require(not stderr, "process_stderr")
        verdict = verify_session(directory / "session", process_exit=execution["process_exit_code"], expected_commands=requested, **kwargs)
        require(verdict["outcome"] == "PASS", "session_verdict:" + str(verdict))
        require(stdout == read(directory / "session/console.log"), "console_stdout_mismatch")
        return verdict
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        return {"schema_version": 1, "outcome": "FAIL", "reasons": [str(exc)]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--execution", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--require-internet", action="store_true")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    verifier = verify_execution if args.execution else verify_session
    result = verifier(args.artifact_dir, source_root=args.source_root, require_live=args.require_live,
                      require_internet=args.require_internet, expected_session_id=args.session_id)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
