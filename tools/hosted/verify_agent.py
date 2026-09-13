#!/usr/bin/env python3
"""Independent MAIN run evidence checks; saved state is not process liveness.

This module imports only host-side verification code. Run verification proves
receipt and management consistency. The QEMU workflow additionally verifies
actual model/backend bytes and process execution/termination evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from verify_boot import keys, parse, require

LEGACY_SOURCES = ("aios-agent.py", "aios_agent/__init__.py", "aios_agent/inference.py",
           "aios_agent/protocol.py", "aios_agent/client.py", "aios_agent/daemon.py",
           "aios_management/__init__.py", "aios_management/binding.py",
           "aios_service/__init__.py", "aios_service/lifecycle.py",
           "aios_hosted/__init__.py", "aios_hosted/boot.py", "aios_hosted/hardware.py")
SOURCES = (*LEGACY_SOURCES, 'aios_resources/__init__.py', 'aios_resources/proc.py',
           'aios_resources/backend.py', 'aios_resources/runtime.py', 'aios_management/resources.py')
MANAGED_SOURCES = (*SOURCES, 'aios-backend.py', 'aios_backend/__init__.py', 'aios_backend/protocol.py',
                   'aios_backend/client.py', 'aios_backend/daemon.py', 'aios_agent/backend_binding.py')
SPACE_SOURCES = (*MANAGED_SOURCES, 'aios_agent/space.py')
TASK_SOURCES = (*SPACE_SOURCES, 'aios_agent/async_inference.py', 'aios_agent/request_state.py',
                'aios_agent/request_runtime.py')
IDENTITY = ("source_id", "source_instance", "service_start_generation")
FIXED_FILES = {"start.json", "config.json", "warmup.json", "source.json", "management.json", "events.jsonl"}
RECEIPT_LINKS = {"source_before", "source_after", "authority_instance", "binding_generation"}
EVENT_KEYS = {"schema_version", "source_instance", "sequence", "monotonic_ns", "event", "action",
              "outcome", "error", "source_record", "management_snapshot", "receipt_file"}
CONFIG_KEYS = {"schema_version", "endpoint", "model_id", "model_path", "model_sha256",
               "backend_path", "backend_sha256", "provenance_sha256"}
LIMIT = 4 * 1024 * 1024
MODEL_SHA = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
BACKEND_SHA = "55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611"
MODEL_BYTES = 639446688
FIRST = ["agent start", "room status", "room discover", "room bind",
         "ask What is the capital of France? Answer in one short sentence.", "agent status", "exit"]
SECOND = ["agent status", "ask Say hello in one short sentence.", "agent restart", "room status", "ask Say hello.",
          "room reconcile", "room discover", "room reconcile", "ask What is the Moon? Answer in one short sentence.",
          "agent stop", "room status", "exit"]
RESOURCE_COMMANDS = ['agent start', 'resources status', 'room discover', 'room bind',
                     'resources link', 'resources sample',
                     'ask What is the capital of France? Answer in one short sentence.',
                     'resources status', 'resolve example.com', 'fetch https://example.com/',
                     'agent stop', 'exit']
CELL_COMMANDS = ['agent start', 'room discover', 'room bind', 'resources link', 'resources sample',
                 'cell status', 'cell deactivate', 'cell deactivate', 'cell status', 'agent status',
                 'ask Say hello.', 'resources status', 'cell activate', 'cell activate',
                 'ask Say hello.', 'room reconcile', 'room discover', 'room reconcile',
                 'resources status', 'resources link',
                 'ask What is the capital of France? Answer in one short sentence.',
                 'resources status', 'resolve example.com', 'fetch https://example.com/', 'agent stop', 'exit']
BACKEND_COMMANDS = ['backend status', 'agent start', 'room discover', 'room bind', 'resources link',
                    'backend restart', 'agent status', 'ask Say hello.', 'resources status',
                    'agent restart', 'room discover', 'room reconcile', 'resources link',
                    'ask What is the capital of France? Answer in one short sentence.', 'resources status',
                    'resolve example.com', 'fetch https://example.com/', 'agent stop',
                    'backend stop', 'backend status', 'exit']


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value < 1 << 63


def identifier(value: object) -> None:
    require(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0, "uuid")


def read(path: Path) -> bytes:
    require(not path.is_symlink() and path.is_file(), "artifact_file:" + path.name)
    with path.open("rb") as stream:
        data = stream.read(LIMIT + 1)
    require(len(data) <= LIMIT, "artifact_size")
    return data


def decode(raw: bytes) -> dict:
    value = parse(raw)
    require(type(value) is dict, "record_object")
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        require(count <= 16384 and depth <= 24, "record_complexity")
        children = item.values() if type(item) is dict else item if type(item) is list else ()
        stack.extend((child, depth + 1) for child in children)
    return value


def record(path: Path) -> dict:
    return decode(read(path))


def _verify_export_inventory(directory: Path, name: str, files: set[str]) -> None:
    """Regular-file exports may omit an empty optional evidence directory."""
    root = directory / name
    expected = {path for path in files if path.startswith(name + '/')}
    try:
        mode = root.lstat().st_mode
    except FileNotFoundError:
        require(not expected, name + '_directory')
        return
    # lstat also exposes dangling symlinks; only an actual directory is valid.
    require(stat.S_ISDIR(mode), name + '_directory')
    require({name + '/' + child.name for child in root.iterdir()} == expected,
            'unaccounted_' + name)


def timestamp(value: object) -> datetime:
    require(type(value) is str and len(value) <= 64, "timestamp")
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None and result.utcoffset() == timezone.utc.utcoffset(result), "timestamp_utc")
    return result


def same_identity(source: dict, start: dict) -> None:
    require(all(type(source[k]) is type(start[k]) and source[k] == start[k] for k in IDENTITY), "run_identity")


def receipt_config(value: dict, config: dict) -> None:
    for name in ("model_id", "model_sha256", "backend_sha256", "provenance_sha256"):
        require(value[name] == config[name], "receipt_config:" + name)


def source_semantics(left: dict, right: dict) -> bool:
    return {k: v for k, v in left.items() if k != "completed_requests"} == {
        k: v for k, v in right.items() if k != "completed_requests"}


def verify_execution_receipt(value: dict, binding: dict, config: dict, require_live: bool, expected_schema: int = 2) -> None:
    from execution_output_contract import validate_execution
    require(type(value['schema_version']) is int and value['schema_version'] == expected_schema, 'execution_receipt_schema')
    execution = value['backend_execution']
    if value['outcome'] == 'ERROR':
        require(execution is None, 'failed_execution_success_proof')
    elif binding['descriptor'] is None:
        require(binding['capture_kind'] == 'fixture' and execution is None, 'unbound_execution')
    else:
        validate_execution(execution, config=config, require_live=require_live, descriptor=binding['descriptor'])
        require(execution['capture_kind'] == binding['capture_kind'], 'execution_capture')


def _verify_run(directory: Path, source_root: Path, require_live: bool) -> dict:
    from newagent_output_contract import ERRORS, receipt, validate_snapshot, validate_source

    require(directory.is_dir() and not directory.is_symlink(), "run_directory")
    start, result = record(directory / "start.json"), record(directory / "result.json")
    keys(start, {"schema_version", *IDENTITY, "service_kind", "capture_kind", "started_at",
                 "config_sha256", "source_hashes"}, "agent_start")
    schema = start['schema_version']
    require(type(schema) is int and schema in (1, 2, 3, 4, 5, 6), "start_schema")
    for name in ("source_id", "source_instance"):
        identifier(start[name])
    require(integer(start["service_start_generation"], 1) and directory.name == start["source_instance"], "start_identity")
    require(start["service_kind"] == "AI_SERVICE" and start["capture_kind"] in ("live", "fixture"), "start_boundary")
    require(not require_live or start["capture_kind"] == "live", "fixture_not_live")
    sources = TASK_SOURCES if schema == 6 else LEGACY_SOURCES if schema == 1 else SPACE_SOURCES if schema in (5, 6) else MANAGED_SOURCES if schema == 4 else SOURCES
    keys(start["source_hashes"], set(sources), "agent_sources")
    for name in sources:
        require(digest(read(source_root / name)) == start["source_hashes"][name], "source_hash:" + name)
    config_bytes = read(directory / "config.json")
    require(digest(config_bytes) == start["config_sha256"], "config_hash")
    config = decode(config_bytes)
    keys(config, CONFIG_KEYS, "agent_config")
    require(type(config["schema_version"]) is int and config["schema_version"] == 1, "config_schema")
    for name in ("model_sha256", "backend_sha256", "provenance_sha256"):
        require(type(config[name]) is str and re.fullmatch(r"[0-9a-f]{64}", config[name]) is not None, "config_hash_value")
    for name in ("endpoint", "model_id", "model_path", "backend_path"):
        require(type(config[name]) is str and 0 < len(config[name]) <= 1024
                and all(char.isprintable() for char in config[name]), "config_text")
    endpoint = urlsplit(config["endpoint"])
    require(endpoint.scheme == "http" and endpoint.hostname in ("127.0.0.1", "10.0.2.2")
            and endpoint.username is None and endpoint.password is None and endpoint.path in ("", "/")
            and not endpoint.query and not endpoint.fragment and endpoint.port is not None
            and 1024 <= endpoint.port <= 65535, "config_endpoint")

    keys(result, {"schema_version", *IDENTITY, "state", "exit_code", "error", "completed_at",
                  "capture_kind", "source_record", "management_snapshot", "files"}, "agent_result")
    require(type(result["schema_version"]) is int and result["schema_version"] == schema, "result_schema")
    same_identity(result, start)
    require(result["capture_kind"] == start["capture_kind"], "capture_kind")
    require(result["state"] == "STOPPED" and type(result["exit_code"]) is int and result["exit_code"] == 0
            and result["error"] is None, "agent_not_stopped")
    require(timestamp(result["completed_at"]) >= timestamp(start["started_at"]), "completion_before_start")
    warmup = record(directory / "warmup.json")
    receipt(warmup, purpose="warmup")
    require(warmup["schema_version"] == (3 if schema in (5, 6) else 2 if schema == 4 else 1), "warmup_receipt_version")
    receipt_config(warmup, config)
    require(warmup["outcome"] == "OK", "warmup_failed")
    require(timestamp(start["started_at"]) <= timestamp(warmup["started_at"])
            <= timestamp(result["completed_at"]), "warmup_outside_run")
    execution_binding = None
    if schema in (4, 5, 6):
        execution_binding = record(directory / 'backend-binding.json')
        keys(execution_binding, {'schema_version', 'capture_kind', 'initial_proof', 'descriptor'}, 'execution_binding')
        require(type(execution_binding['schema_version']) is int and execution_binding['schema_version'] == 1
                and execution_binding['capture_kind'] == start['capture_kind'], 'execution_binding_schema')
        require((execution_binding['initial_proof'] is None) == (execution_binding['descriptor'] is None), 'execution_binding_pair')
        if execution_binding['descriptor'] is not None:
            from resource_output_contract import validate_backend_proof
            validate_backend_proof(execution_binding['initial_proof'], config=config, require_live=require_live)
            require(execution_binding['initial_proof']['descriptor'] == execution_binding['descriptor'], 'execution_binding_descriptor')
        require(start['capture_kind'] != 'live' or execution_binding['descriptor'] is not None, 'execution_binding_missing')
        verify_execution_receipt(warmup, execution_binding, config, require_live, 3 if schema in (5, 6) else 2)

    raw_events = read(directory / "events.jsonl")
    require(raw_events.endswith(b"\n"), "events_truncated")
    events = [decode(line) for line in raw_events.splitlines()]
    require(4 <= len(events) <= 64, "event_count")
    require([row["event"] for row in events[:2]] == ["STARTING", "RUNNING"]
            and [row["event"] for row in events[-2:]] == ["STOPPING", "STOPPED"]
            and all(row["event"] in (('COMMAND', 'BACKEND_INVALIDATED', 'REQUEST', 'REQUEST_RESULT') if schema == 6
                                    else ('COMMAND', 'BACKEND_INVALIDATED') if schema in (4, 5) else ('COMMAND',))
                    for row in events[2:-2]), "event_lifecycle")
    files, requests, previous_source, previous_snapshot, authority_id = set(FIXED_FILES), [], None, None, None
    if schema in (4, 5, 6):
        files.add('backend-binding.json')
    previous_ns = -1
    running_source = None
    request_ids = {warmup["request_id"]}
    resource_results = []
    space_results, cached_observation = [], None
    tasks = {}
    for index, event in enumerate(events, 1):
        keys(event, EVENT_KEYS | ({'resource_file'} if schema >= 2 else set())
             | ({'space_file'} if schema in (5, 6) else set()) | ({'task_file'} if schema == 6 else set()), "agent_event")
        require(type(event["schema_version"]) is int and event["schema_version"] == schema
                and event["source_instance"] == start["source_instance"], "event_identity")
        require(type(event["sequence"]) is int and event["sequence"] == index
                and integer(event["monotonic_ns"]) and event["monotonic_ns"] >= previous_ns, "event_order")
        prior_event_ns = previous_ns
        previous_ns = event["monotonic_ns"]
        require(event["outcome"] in ("OK", "ERROR") and (event["error"] is None) == (event["outcome"] == "OK"), "event_outcome")
        require(event["error"] is None or type(event["error"]) is str and (event["error"] in ERRORS
                or schema >= 2 and str(event['action']).startswith('resources-')
                and re.fullmatch(r'[a-z]+(?:-[a-z]+)*', event['error']) and len(event['error']) <= 64), "event_error")
        require(schema in (5, 6) or event['error'] not in {'space-invalid', 'space-overflow', 'space-future',
                                                     'space-source-mismatch', 'space-budget'}, 'legacy_space_error')
        snapshot = event["management_snapshot"]
        validate_snapshot(snapshot)
        require(snapshot["initialized"], "authority_uninitialized")
        if authority_id is None:
            authority_id = snapshot["authority_instance"]
        require(snapshot["authority_instance"] == authority_id, "authority_instance")
        if schema < 3 or previous_snapshot is None and start['service_start_generation'] == 1:
            require(snapshot["parent"]["generation"] == snapshot["canonical"]["generation"] == 1
                    and snapshot["parent"]["active"] is True, "unexpected_parent_transition")
        if schema >= 3 and previous_snapshot is not None:
            from cell_output_contract import validate_cell_transition, validate_parent_continuity
            if event['event'] == 'COMMAND' and event['action'] in ('cell-activate', 'cell-deactivate'):
                validate_cell_transition(previous_snapshot, snapshot, event['action'], event['error'])
            else:
                validate_parent_continuity(previous_snapshot, snapshot)
        source = event["source_record"]
        source_before_event = previous_source
        if event["event"] == "STARTING":
            require(source is None and event["receipt_file"] is None, "starting_source")
            require(not snapshot["binding_current"], "starting_binding_current")
        else:
            validate_source(source)
            same_identity(source, start)
            require(source["model_sha256"] == config["model_sha256"]
                    and source["warmup_request_sha256"] == warmup["request_sha256"]
                    and source["warmup_response_sha256"] == warmup["response_sha256"], "source_warmup")
            if snapshot["current_source"] is not None and snapshot["current_source"]["source_instance"] == source["source_instance"]:
                require(snapshot["current_source"] == source, "source_snapshot")
            if previous_source is not None:
                require(source["process_id"] == previous_source["process_id"]
                        and source["host_boot_id"] == previous_source["host_boot_id"], "source_host_identity")
                require(source["source_generation"] >= previous_source["source_generation"]
                        and source["completed_requests"] >= previous_source["completed_requests"], "source_rollback")
                require(source["source_generation"] != previous_source["source_generation"]
                        or source_semantics(source, previous_source), "unversioned_source_change")
            if event["event"] == "RUNNING":
                require(source["lifecycle_state"] == "active" and source["model_ready"] is True
                        and source["source_generation"] == 1 and source["completed_requests"] == 1
                        and event["receipt_file"] == "warmup.json", "running_readiness")
                require(not snapshot["binding_current"], "implicit_start_binding")
                running_source = source
            elif event['event'] == 'BACKEND_INVALIDATED':
                require(schema in (4, 5, 6) and execution_binding['descriptor'] is not None and previous_source is not None,
                        'unexpected_backend_invalidation')
                require(previous_source['model_ready'] and source == {**previous_source, 'model_ready': False,
                        'source_generation': previous_source['source_generation'] + 1}, 'backend_invalidation_source')
                require(not snapshot['binding_current'] and not snapshot['source_trusted']
                        and snapshot['discovered_source'] is None
                        and snapshot['binding'] == previous_snapshot['binding'], 'backend_invalidation_binding')
                require(event['error'] == 'backend-changed' and event['outcome'] == 'ERROR', 'backend_invalidation_error')
                require(event['receipt_file'] is None, 'backend_invalidation_receipt')
            elif event["event"] in ("STOPPING", "STOPPED"):
                require(source["lifecycle_state"] == "exited" and source["model_ready"] is False
                        and not snapshot["binding_current"] and event["receipt_file"] is None, "terminal_source")
                require(previous_source is not None, "missing_previous_source")
                expected_generation = previous_source["source_generation"] + int(event["event"] == "STOPPING")
                require(source["source_generation"] == expected_generation
                        and source["completed_requests"] == previous_source["completed_requests"], "terminal_generation")
                if event["event"] == "STOPPED":
                    require(source == previous_source and snapshot == previous_snapshot, "terminal_snapshot_changed")
            previous_source = source

        if schema == 6 and event['event'] in ('REQUEST', 'REQUEST_RESULT'):
            from task_output_contract import validate_task_envelope, validate_task_transition
            from newagent_output_contract import same
            require(event['action'] is None and event['space_file'] is None, 'task_event_action')
            task_path = event['task_file']
            require(type(task_path) is str and re.fullmatch(r'tasks/[0-9a-f-]{36}/[0-9]{2}\.json', task_path),
                    'task_path')
            task_id, revision_text = task_path.split('/')[1:]
            identifier(task_id)
            if event['event'] == 'REQUEST':
                require(task_path not in files and event['outcome'] == 'OK' and event['error'] is None
                        and event['receipt_file'] is None and event['resource_file'] is None
                        and same(source, source_before_event) and same(snapshot, previous_snapshot),
                        'task_revision_event')
                envelope = record(directory / task_path)
                validate_task_envelope(envelope, config=config, require_live=require_live)
                row = envelope['request_state']
                require(row['request_id'] == task_id and revision_text == format(row['revision'], '02d') + '.json'
                        and row['updated_ns'] <= event['monotonic_ns'], 'task_revision_path')
                entry = tasks.get(task_id)
                if entry is None:
                    require(len(tasks) < 16 and task_id not in request_ids
                            and not any(item['result_file'] is None for item in tasks.values()), 'task_admission_busy')
                    require(row['revision'] == 1 and row['phase'] == 'ACCEPTED'
                            and row['accepted_ns'] == row['updated_ns'] and row['backend_stop'] is None
                            and same(row['source_before'], source) and same(row['management_before'], snapshot)
                            and prior_event_ns <= row['accepted_ns'], 'task_admission')
                    require(execution_binding is not None and execution_binding['descriptor'] is not None
                            and same(row['backend_expected']['descriptor'], execution_binding['descriptor']),
                            'task_admission_backend')
                    context = row['space_context']
                    require(prior_event_ns <= context['checked_monotonic_ns'], 'task_space_check_order')
                    if cached_observation is None:
                        require(prior_event_ns <= context['observation']['observed_monotonic_ns'],
                                'task_space_initial_observation')
                        cached_observation = context['observation']
                    require(same(context['observation'], cached_observation), 'task_space_cached_observation')
                    entry = {'revisions': [], 'paths': [], 'result_file': None}
                    tasks[task_id] = entry
                    request_ids.add(task_id)
                else:
                    previous = entry['revisions'][-1]
                    validate_task_transition(previous['request_state'], row)
                    require(same(previous['receipt_template'], envelope['receipt_template']), 'task_template_rewrite')
                    if previous['worker_progress'] is not None and previous['worker_progress']['done']:
                        require(row['phase'] == 'FINISHED'
                                and same(previous['worker_progress'], envelope['worker_progress']), 'task_terminal_progress_rewrite')
                    if previous['backend_observation'] is not None:
                        require(same(previous['backend_observation'], envelope['backend_observation']),
                                'task_backend_observation_rewrite')
                entry['revisions'].append(envelope)
                entry['paths'].append(task_path)
                files.add(task_path)
            else:
                require(task_id in tasks, 'task_result_without_admission')
                entry = tasks[task_id]
                row = entry['revisions'][-1]['request_state']
                require(entry['result_file'] is None and task_path == entry['paths'][-1]
                        and row['phase'] == 'FINISHED', 'task_result_once')
                before = row['source_before']
                expected_source = dict(source_before_event)
                if row['model_outcome'] == 'ANSWERED':
                    expected_source['completed_requests'] += 1
                elif row['model_outcome'] == 'UNKNOWN' and expected_source['model_ready']:
                    expected_source.update(model_ready=False, source_generation=expected_source['source_generation'] + 1)
                require(same(source, expected_source)
                        and source_before_event['completed_requests'] == before['completed_requests'],
                        'task_result_source')
                require(same(snapshot['current_source'], source)
                        and all(same(snapshot[key], previous_snapshot[key]) for key in
                            ('parent', 'canonical', 'binding', 'retired_instances')), 'task_result_management')
                if not source['model_ready']:
                    require(not snapshot['source_trusted'] and not snapshot['binding_confirmed']
                            and snapshot['discovered_source'] is None, 'task_result_invalidated')
                else:
                    require(all(same(snapshot[key], previous_snapshot[key]) for key in
                        ('source_trusted', 'binding_confirmed', 'discovered_source')), 'task_result_binding')
                receipt_path = event['receipt_file']
                require((receipt_path is None) == (row['model_outcome'] == 'NOT_STARTED'), 'task_result_receipt_missing')
                item = None
                if receipt_path is not None:
                    require(receipt_path == 'requests/' + task_id + '.json' and receipt_path not in files,
                            'task_result_receipt_path')
                    item = record(directory / receipt_path)
                    require(set(item) == set(row['inference_receipt']) | RECEIPT_LINKS
                            and same({key: item[key] for key in row['inference_receipt']}, row['inference_receipt']),
                            'task_result_receipt')
                    require(same(item['source_before'], before) and same(item['source_after'], source)
                            and item['authority_instance'] == row['management_before']['authority_instance']
                            and same(item['binding_generation'], row['management_before']['binding']['generation']),
                            'task_result_admission')
                    require(timestamp(start['started_at']) <= timestamp(item['started_at']) <= timestamp(result['completed_at']),
                            'task_receipt_outside_run')
                    require(event['outcome'] == item['outcome'] and event['error'] == item['error'], 'task_result_outcome')
                    files.add(receipt_path)
                    requests.append(item)
                else:
                    require(event['outcome'] == 'OK' and event['error'] is None and event['resource_file'] is None,
                            'task_not_started_result')
                resource_path = event['resource_file']
                if resource_path is not None:
                    from resource_output_contract import validate_resource_result
                    require(resource_path == 'resources/' + task_id + '.json' and resource_path not in files,
                            'task_resource_path')
                    resource = record(directory / resource_path)
                    validate_resource_result(resource, source=source, snapshot=snapshot, receipt=item,
                                             config=config, require_live=require_live)
                    require(resource['action'] == 'request' and resource['capture_kind'] == start['capture_kind'],
                            'task_resource_join')
                    files.add(resource_path)
                    resource_results.append(resource)
                entry['result_file'] = task_path
            previous_snapshot = snapshot
            continue
        require(event.get('task_file') is None, 'unexpected_task_file')
        if event["event"] != "COMMAND":
            require(event["action"] is None and event["outcome"] ==
                    ('ERROR' if event['event'] == 'BACKEND_INVALIDATED' else 'OK'), "lifecycle_action")
            require(event.get('resource_file') is None and event.get('space_file') is None, 'lifecycle_resource')
        else:
            action = event["action"]
            require(schema != 6 or not any(entry['result_file'] is None for entry in tasks.values()),
                    'task_busy_mutation')
            require(schema != 6 or action != 'ask', 'task_sync_ask')
            allowed = ('room-discover', 'room-bind', 'room-reconcile', 'ask')
            if schema >= 2:
                allowed += ('resources-link', 'resources-status', 'resources-sample')
            if schema >= 3:
                allowed += ('cell-activate', 'cell-deactivate')
            if schema in (5, 6):
                allowed += ('space',)
            require(action in allowed, "event_action")
            require(action == 'space' or event.get('space_file') is None, 'unexpected_space_file')
            require(source["lifecycle_state"] == "active", "command_after_exit")
            item = None
            if action == "ask" and event["receipt_file"] is not None:
                path = event["receipt_file"]
                require(type(path) is str and re.fullmatch(r"requests/[0-9a-f-]{36}\.json", path) is not None, "receipt_path")
                require(path not in files, "reused_receipt")
                files.add(path)
                item = record(directory / path)
                require(RECEIPT_LINKS <= item.keys(), "receipt_links")
                base = {k: v for k, v in item.items() if k not in RECEIPT_LINKS}
                receipt(base, purpose="user")
                require(base["schema_version"] == (3 if schema in (5, 6) else 2 if schema == 4 else 1), "user_receipt_version")
                receipt_config(base, config)
                if schema in (4, 5, 6):
                    verify_execution_receipt(base, execution_binding, config, require_live, 3 if schema in (5, 6) else 2)
                require(item["request_id"] not in request_ids and path == "requests/" + item["request_id"] + ".json", "request_identity")
                request_ids.add(item["request_id"])
                for linked in (item["source_before"], item["source_after"]):
                    validate_source(linked)
                    same_identity(linked, start)
                before, after = item["source_before"], item["source_after"]
                require(after == source and before == source_before_event
                        and before["model_ready"] and before["lifecycle_state"] == "active", "request_source")
                require(item["authority_instance"] == authority_id and integer(item["binding_generation"], 1), "request_authority")
                require(previous_snapshot is not None and previous_snapshot["binding_current"]
                        and previous_snapshot["binding"]["generation"] == item["binding_generation"]
                        and source_semantics(previous_snapshot["binding"]["source"], before), "request_binding")
                require(base["outcome"] == event["outcome"] and base["error"] == event["error"], "request_outcome")
                if base["outcome"] == "OK":
                    require(source_semantics(before, after) and after["completed_requests"] == before["completed_requests"] + 1,
                            "request_counter")
                else:
                    require(not after["model_ready"] and after["source_generation"] == before["source_generation"] + 1
                            and after["completed_requests"] == before["completed_requests"], "request_failure_state")
                require(timestamp(start["started_at"]) <= timestamp(base["started_at"])
                        <= timestamp(result["completed_at"]), "request_outside_run")
                if schema in (5, 6):
                    from space_output_contract import validate_packet
                    context = item['space_context']
                    validate_packet(context, source=before, snapshot=previous_snapshot,
                                    model_id=config['model_id'], now_ns=event['monotonic_ns'])
                    require(prior_event_ns <= context['checked_monotonic_ns'], 'space_check_order')
                    if cached_observation is None:
                        require(prior_event_ns <= context['observation']['observed_monotonic_ns'], 'space_initial_observation')
                        cached_observation = context['observation']
                    require(context['observation'] == cached_observation, 'space_cached_observation')
                requests.append(item)
            elif action == "ask":
                require(event["outcome"] == "ERROR" and source == source_before_event, "ask_missing_receipt")
            elif action == 'space':
                from space_output_contract import validate_packet
                require(event['outcome'] == 'OK' and event['receipt_file'] is None
                        and event.get('resource_file') is None and source == source_before_event,
                        'space_changed_producer')
                require(snapshot == previous_snapshot, 'space_changed_management')
                space_path = event['space_file']
                require(type(space_path) is str and re.fullmatch(r'spaces/[0-9a-f-]{36}\.json', space_path)
                        and space_path not in files, 'space_path')
                identifier(Path(space_path).stem)
                context = record(directory / space_path)
                validate_packet(context, source=source, snapshot=snapshot,
                                model_id=config['model_id'], now_ns=event['monotonic_ns'])
                require(prior_event_ns <= context['observation']['observed_monotonic_ns'], 'space_refresh')
                if cached_observation is not None:
                    require(cached_observation['observation_id'] != context['observation']['observation_id'], 'space_not_refreshed')
                cached_observation = context['observation']
                files.add(space_path)
                space_results.append(context)
            elif action.startswith('cell-'):
                require(event['receipt_file'] is None and event.get('resource_file') is None
                        and source == source_before_event, 'cell_changed_producer')
            elif action.startswith('resources-'):
                require(event['receipt_file'] is None and source == source_before_event
                        and snapshot == previous_snapshot, 'resource_changed_producer')
                require(event.get('resource_file') is not None, 'resource_missing_record')
            else:
                require(event["receipt_file"] is None and source == source_before_event, "management_receipt")
                if event["outcome"] == "OK":
                    require(snapshot["current_source"] == source and snapshot["discovered_source"] is not None, "management_source")
                    if action == "room-discover":
                        require(snapshot["binding"] == previous_snapshot["binding"], "discovery_changed_binding")
                    else:
                        require(snapshot["binding_current"] and source_semantics(snapshot["binding"]["source"], source), "binding_source")
                        previous_binding = previous_snapshot["binding"]
                        require(previous_snapshot["discovered_source"] is not None
                                and source_semantics(previous_snapshot["discovered_source"], source), "binding_without_discovery")
                        if action == "room-bind":
                            require(previous_binding is None and snapshot["binding"]["generation"] == 1, "initial_binding_generation")
                        else:
                            require(previous_binding is not None and not previous_snapshot["binding_current"]
                                    and snapshot["binding"]["generation"] == previous_binding["generation"] + 1,
                                    "reconcile_generation")
            resource_path = event.get('resource_file')
            if resource_path is not None:
                from resource_output_contract import validate_resource_result
                require(type(resource_path) is str and re.fullmatch(r'resources/[0-9a-f-]{36}\.json', resource_path)
                        and resource_path not in files, 'resource_path')
                identifier(Path(resource_path).stem)
                resource_value = record(directory / resource_path)
                validate_resource_result(resource_value, source=source, snapshot=snapshot, receipt=item,
                                         config=config, require_live=require_live)
                require(resource_value['capture_kind'] == start['capture_kind'], 'resource_capture')
                if action == 'ask':
                    require(item is not None and resource_value['action'] == 'request'
                            and resource_path == 'resources/' + item['request_id'] + '.json', 'resource_request_link')
                else:
                    require(action == 'resources-' + resource_value['action']
                            and event['outcome'] == resource_value['outcome']
                            and event['error'] == resource_value['error'], 'resource_command_link')
                files.add(resource_path)
                resource_results.append(resource_value)
        previous_snapshot = snapshot

    require(result["source_record"] == previous_source and result["management_snapshot"] == previous_snapshot, "result_snapshot")
    require(previous_source["completed_requests"] == 1 + sum(item["outcome"] == "OK" for item in requests),
            "completed_request_accounting")
    require(record(directory / "source.json") == previous_source and record(directory / "management.json") == previous_snapshot, "latest_snapshot")
    if execution_binding is not None and execution_binding['descriptor'] is not None:
        proof = execution_binding['initial_proof']
        require(proof['descriptor']['host_boot_id'] == running_source['host_boot_id'], 'execution_main_boot')
        for item in (warmup, *requests):
            if item['outcome'] == 'OK':
                worker = item['backend_execution']['send']['client']
                parent = int(worker['raw_stat'].rsplit(') ', 1)[1].split()[1])
                require(parent == running_source['process_id'] and worker['uid'] == proof['peer_uid'], 'execution_main_worker')
    keys(result["files"], files, "result_files")
    require(schema != 6 or len(files) <= 256, 'task_file_count')
    for name in files:
        require(digest(read(directory / name)) == result["files"][name], "artifact_hash:" + name)
    _verify_export_inventory(directory, 'requests', files)
    if schema >= 2:
        _verify_export_inventory(directory, 'resources', files)
    if schema in (5, 6):
        _verify_export_inventory(directory, 'spaces', files)
    if schema == 6:
        require(all(entry['result_file'] is not None and entry['revisions'][-1]['request_state']['phase'] == 'FINISHED'
                    for entry in tasks.values()), 'task_unfinished_run')
        _verify_export_inventory(directory, 'tasks', {'tasks/' + key for key in tasks})
        for task_id, entry in tasks.items():
            task_root = directory / 'tasks' / task_id
            require(not task_root.is_symlink() and task_root.is_dir(), 'task_directory')
            require({path.name for path in task_root.iterdir()} == {Path(path).name for path in entry['paths']},
                    'unaccounted_task_revisions')
    return {"outcome": "PASS", "reasons": [], "state": "STOPPED", "source_record": previous_source,
            "management_snapshot": previous_snapshot, "running_source": running_source,
            "capture_kind": start["capture_kind"], "requests": requests, "config": config,
            "events": events, "warmup": warmup, 'resource_results': resource_results,
            'execution_binding': execution_binding, **({'space_results': space_results} if schema in (5, 6) else {}),
            **({'tasks': tasks} if schema == 6 else {}),
            "process_exit_verified": False, "model_bytes_verified": False}


def verify_run(directory: Path, source_root: Path | None = None, *, require_live: bool = False) -> dict:
    try:
        root = source_root or Path(__file__).resolve().parents[2] / "hosted/linux"
        return _verify_run(Path(directory), root, require_live)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def verify_agent_runs(directory: Path, *, source_root: Path | None = None, require_live: bool = True) -> dict:
    try:
        from newagent_output_contract import agent_result
        require(directory.is_dir() and not directory.is_symlink(), "state_directory")
        registry = record(directory / "registry.json")
        keys(registry, {"schema_version", *IDENTITY}, "agent_registry")
        require(type(registry["schema_version"]) is int and registry["schema_version"] == 1, "registry_schema")
        run_root = directory / "runs"
        require(run_root.is_dir() and not run_root.is_symlink(), "runs_directory")
        paths = list(run_root.iterdir())
        require(1 <= len(paths) <= 64, "run_count")
        runs = [verify_run(path, source_root, require_live=require_live) for path in paths]
        require(all(run["outcome"] == "PASS" for run in runs), "run_failure:" + json.dumps([r for r in runs if r["outcome"] != "PASS"]))
        runs.sort(key=lambda row: row["source_record"]["service_start_generation"])
        require([row["source_record"]["service_start_generation"] for row in runs] == list(range(1, len(runs) + 1)), "start_generation_gap")
        require(len({row["source_record"]["source_id"] for row in runs}) == 1
                and len({row["source_record"]["source_instance"] for row in runs}) == len(runs), "service_continuity")
        require(len({row["management_snapshot"]["authority_instance"] for row in runs}) == 1, "authority_continuity")
        ids = [item["request_id"] for row in runs for item in [row["warmup"], *row["requests"]]]
        require(len(ids) == len(set(ids)), "cross_run_request_reuse")
        relation = None
        for row in runs:
            for resource in row['resource_results']:
                if resource['action'] == 'link' and resource['outcome'] == 'OK':
                    from resource_output_contract import validate_relation_progression
                    if relation is None:
                        require(resource['relation']['relation_generation'] == 1, 'initial_resource_generation')
                    else:
                        validate_relation_progression(relation, resource['relation'])
                    relation = resource['relation']
                else:
                    require(resource['relation'] == relation, 'unrecorded_resource_relation')
        if relation is not None:
            saved_resource = record(directory / 'resource-state.json')
            keys(saved_resource, {'schema_version', 'relation', 'backend_dir'}, 'resource_state')
            require(type(saved_resource['schema_version']) is int and saved_resource['schema_version'] == 1
                    and saved_resource['relation'] == relation, 'resource_state_relation')
            require(type(saved_resource['backend_dir']) is str and 0 < len(saved_resource['backend_dir']) < 96
                    and saved_resource['backend_dir'].startswith('/'), 'resource_state_path')
        same_identity(registry, runs[-1]["source_record"])
        for previous, current in zip(runs, runs[1:]):
            require(current["events"][0]["management_snapshot"] == previous["management_snapshot"], "authority_restart_state")
            managed_source = current["management_snapshot"]["current_source"]
            if managed_source is not None and managed_source["source_instance"] == current["source_record"]["source_instance"]:
                retired = current["management_snapshot"]["retired_instances"]
                require(previous["source_record"]["source_instance"] in retired, "missing_retired_instance")
        latest = record(directory / "latest.json")
        agent_result({"name": "agent", "args": ["status"], "outcome": latest["outcome"], "result": latest},
                     {1: 3, 2: 4, 3: 5, 4: 6, 5: 8, 6: 10}.get(latest['schema_version'], 0))
        require(latest["state"] == "STOPPED" and latest["source_record"] == runs[-1]["source_record"]
                and latest["management_snapshot"] == runs[-1]["management_snapshot"], "state_latest")
        exported = record(directory / "management.json")
        expected = {k: runs[-1]["management_snapshot"][k] for k in exported}
        keys(exported, {"schema_version", "authority_namespace", "authority_instance", "initialized", "parent",
                        "canonical", "current_source", "discovered_source", "binding", "source_trusted",
                        "binding_confirmed", "retired_instances"}, "management_state")
        require(exported == expected, "authority_state")
        return {"outcome": "PASS", "reasons": [], "runs": runs, "state": "STOPPED",
                "process_exit_verified": False, "model_bytes_verified": False}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def verify_model_provenance(directory: Path, config: dict) -> dict:
    """Check pinned, retained upstream evidence without assuming an execution layout."""
    require(config["model_id"] == "aios-qwen3-0.6b-q8_0" and config["model_sha256"] == MODEL_SHA
            and config["backend_sha256"] == BACKEND_SHA, "model_pin")
    provenance_raw = read(directory / "inference-provenance.json")
    require(digest(provenance_raw) == config["provenance_sha256"], "provenance_hash")
    provenance = decode(provenance_raw)
    keys(provenance, {"schema_version", "prepared_at", "purpose", "repository_import", "repository_manifest_changed",
                      "host_global_install", "redistribution_approved", "dependency_license_review", "artifacts",
                      "source_receipts", "windows_probe", "linux_probe"}, "provenance")
    require(type(provenance["schema_version"]) is int and provenance["schema_version"] == 1
            and all(provenance[name] is False for name in ("repository_import", "repository_manifest_changed",
                                                          "host_global_install", "redistribution_approved")), "provenance_boundary")
    timestamp(provenance["prepared_at"])
    artifacts = provenance["artifacts"]
    require(type(artifacts) is list and len(artifacts) == 2, "provenance_artifact_count")
    expected = (("llamafile-0.10.5-thin.exe", BACKEND_SHA, 42328074, "486e6c5f9356eae50b851b07517bfae1f2420193",
                 "https://github.com/mozilla-ai/llamafile/releases/download/0.10.5/llamafile-0.10.5-thin"),
                ("Qwen3-0.6B-Q8_0.gguf", MODEL_SHA, MODEL_BYTES, "23749fefcc72300e3a2ad315e1317431b06b590a",
                 "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/Qwen3-0.6B-Q8_0.gguf"))
    for item, (name, sha, size, revision, url) in zip(artifacts, expected):
        keys(item, {"name", "upstream_name", "project", "version", "revision", "url", "license", "size", "sha256",
                    "hash_source", "local_integrity_verified"} | ({"llama_cpp_revision"} if name.startswith("llamafile") else set()),
             "provenance_artifact")
        require(item["name"] == name and item["sha256"] == sha and type(item["size"]) is int and item["size"] == size
                and item["revision"] == revision and item["url"] == url and item["local_integrity_verified"] is True,
                "provenance_artifact_pin")
        require(type(item["license"]) is str and bool(item["license"]), "provenance_license")
    source_receipts = provenance["source_receipts"]
    require(type(source_receipts) is list and len(source_receipts) == 11, "provenance_sources")
    paths = set()
    for item in source_receipts:
        keys(item, {"path", "url", "size", "sha256"}, "source_receipt")
        name = item["path"]
        require(type(name) is str and re.fullmatch(r"provenance/[a-zA-Z0-9._-]+", name) is not None
                and name not in paths, "provenance_source_path")
        paths.add(name)
        raw = read(directory / name)
        require(type(item["size"]) is int and len(raw) == item["size"] and digest(raw) == item["sha256"], "provenance_source_hash")
        require(type(item["url"]) is str and item["url"].startswith(("https://api.github.com/repos/mozilla-ai/llamafile/",
                "https://raw.githubusercontent.com/mozilla-ai/llamafile/", "https://raw.githubusercontent.com/ggml-org/llama.cpp/",
                "https://huggingface.co/Qwen/", "https://huggingface.co/api/models/Qwen/")), "provenance_source_url")
    return provenance


def verify_model(directory: Path, config: dict, *, allow_recovered=False, recovery_owner=None, require_start_control=True) -> dict:
    """Check pinned provenance against independently captured complete reads."""
    verify_model_provenance(directory, config)
    host = record(directory / "inference-integrity.json")
    keys(host, {"schema_version", "model_bytes", "model_sha256", "backend_sha256", "verification"}, "host_integrity")
    require(host == {"schema_version": 1, "model_bytes": MODEL_BYTES, "model_sha256": MODEL_SHA,
                     "backend_sha256": BACKEND_SHA, "verification": "host-read-complete"}
            and type(host["schema_version"]) is int and type(host["model_bytes"]) is int, "host_integrity_value")
    guest = record(directory / "model-integrity/integrity.json")
    keys(guest, {"schema_version", "model_bytes", "model_sha256", "verification", "read_only_source"}, "guest_integrity")
    require(guest == {"schema_version": 1, "model_bytes": MODEL_BYTES, "model_sha256": MODEL_SHA,
                      "verification": "guest-read-complete", "read_only_source": True}
            and type(guest["schema_version"]) is int and type(guest["model_bytes"]) is int
            and guest["read_only_source"] is True, "guest_integrity_value")
    if (directory / 'model-backend/registry.json').exists():
        from verify_backend import verify_backend_runs, verify_control
        store = verify_backend_runs(directory / 'model-backend', source_root=directory / 'runtime-source', require_live=True,
                                    allow_recovered=allow_recovered, recovery_owner=recovery_owner)
        require(store['outcome'] == 'PASS', 'backend_runs:' + json.dumps(store.get('reasons', [])))
        backend_runs = store['runs']
        require(all(row['config'] == config for row in backend_runs), 'managed_backend_config')
        for action in (('start', 'stop') if require_start_control else ('stop',)):
            control = verify_control(directory / ('backend-' + action), action, require_live=True)
            require(control['outcome'] == 'PASS', 'backend_control:' + json.dumps(control.get('reasons', [])))
            public = record(directory / ('backend-' + action) / 'stdout.log')
            selected = backend_runs[0] if action == 'start' else backend_runs[-1]
            expected = selected['events'][2]['service_record'] if action == 'start' else selected['result']['service_record']
            require(public['service_record'] == expected and public['state'] == ('RUNNING' if action == 'start' else 'STOPPED'),
                    'backend_control_delivery')
            require(public['descriptor'] == (selected['descriptor'] if action == 'start' else None), 'backend_control_descriptor')
        last = backend_runs[-1]
        require(last['state'] == 'STOPPED', 'managed_backend_final_normal_stop')
        return {'pid': last['descriptor']['process_id'], 'uid': last['result']['service_record']['child_identity']['uid'],
                'model_id': config['model_id'], 'model_sha256': MODEL_SHA, 'backend_sha256': BACKEND_SHA,
                'process_exit_code': last['result']['child_exit_code'], 'managed_runs': backend_runs}
    backend = record(directory / "model-backend/result.json")
    keys(backend, {"schema_version", "outcome", "uid", "model_id", "model_sha256", "backend_sha256", "ready", "error",
                   "host_killed", "process_exit_code", "startup_seconds", "pid", "elapsed_seconds"}, "backend_result")
    require(type(backend["schema_version"]) is int and backend["schema_version"] == 1 and backend["outcome"] == "PASS"
            and backend["ready"] is True and backend["error"] is None and backend["host_killed"] is False
            and type(backend["process_exit_code"]) is int and backend["process_exit_code"] in (0, -15)
            and integer(backend["pid"], 1) and integer(backend["uid"], 1), "backend_execution")
    for name in ("model_id", "model_sha256", "backend_sha256"):
        require(backend[name] == config[name], "backend_config")
    for name in ("startup_seconds", "elapsed_seconds"):
        require(type(backend[name]) in (int, float) and math.isfinite(backend[name]) and backend[name] >= 0, "backend_time")
    require(backend["elapsed_seconds"] >= backend["startup_seconds"], "backend_time_order")
    command = record(directory / "model-backend/command.json")
    keys(command, {"command", "source_only"}, "backend_command")
    require(command["source_only"] is True and command["command"] == ["/bin/sh", config["backend_path"], "--server",
        "-m", config["model_path"], "--gpu", "disable", "--host", "127.0.0.1", "--port", "18081", "--no-webui",
        "-c", "1024", "-b", "64", "-ub", "64", "-t", "2", "-np", "1", "--alias", config["model_id"], "--nologo"],
        "backend_launch")
    require(record(directory / "model-backend/health.json") == {"status": "ok"}, "backend_health")
    read(directory / "model-backend/stdout.log")
    read(directory / "model-backend/stderr.log")
    return backend


def verify_shutdown(directory: Path) -> None:
    vm = record(directory / "vm-verdict.json")
    require(type(vm.get("vm_exit_code")) is int and vm["vm_exit_code"] == 0 and vm.get("host_killed") is False
            and vm.get("shutdown_observed") is True, "vm_not_clean")
    serial = read(directory / "linux-serial.log")
    # With tty echo disabled, the known development shell prompt can remain on
    # the same line as the kernel's final record. No arbitrary prefix is valid.
    matches = list(re.finditer(rb"(?:^|\r?\n)(?:localhost:~# )?(?:\[[ 0-9.]+\] )?reboot: Power down\r?(?:\n|$)", serial))
    require(len(matches) == 1 and not serial[matches[0].end():].strip(), "vm_shutdown_record")


def verify_stop(directory: Path) -> dict:
    """Check the separately captured final control process, including ABSENT."""
    from newagent_output_contract import agent_result

    execution = record(directory / "agent-stop/execution.json")
    keys(execution, {"schema_version", "action", "process_exit_code", "stdout_sha256", "stderr_sha256"}, "stop_execution")
    require(type(execution["schema_version"]) is int and execution["schema_version"] == 1 and execution["action"] == "stop"
            and type(execution["process_exit_code"]) is int and execution["process_exit_code"] == 0, "stop_process_exit")
    stdout, stderr = read(directory / "agent-stop/stdout.log"), read(directory / "agent-stop/stderr.log")
    require(not stderr and digest(stdout) == execution["stdout_sha256"] and digest(stderr) == execution["stderr_sha256"], "stop_output")
    stopped = decode(stdout)
    agent_result({"name": "agent", "args": ["stop"], "outcome": stopped["outcome"], "result": stopped},
                 {1: 3, 2: 4, 3: 5, 4: 6, 5: 8, 6: 10}.get(stopped['schema_version'], 0), require_live=True)
    require(stopped["outcome"] == "OK" and stopped["state"] in ("ABSENT", "STOPPED"), "stop_receipt")
    return stopped


def verify_interactive(directory: Path, *, source_root: Path | None = None, require_shutdown: bool = True,
                       resource_smoke: bool = False, cell_smoke: bool = False, backend_smoke: bool = False,
                       recovery_smoke: bool = False, space_smoke: bool = False, task_smoke: bool = False) -> dict:
    """Verify a user-driven model-enabled console, even when MAIN is unused.

    Starting the model backend does not mean the MAIN daemon was started. An
    authenticated ABSENT stop and empty exported store prove only that no MAIN
    run was retained; they cannot prove warmup, requests, bindings or daemon exit.
    """
    try:
        from verify_console import verify_execution

        source_root = source_root or directory / "runtime-source"
        require(sum((resource_smoke, cell_smoke, backend_smoke, recovery_smoke, space_smoke, task_smoke)) <= 1, 'conflicting_workflows')
        console = verify_execution(directory, source_root=source_root, require_live=True,
                                   require_internet=resource_smoke or cell_smoke or backend_smoke or space_smoke or task_smoke)
        require(console["outcome"] == "PASS", "console_execution:" + json.dumps(console.get("reasons", [])))
        execution = record(directory / "execution.json")
        if task_smoke:
            require(execution["mode"] == "smoke" and type(execution["requested_commands"]) is list, "task_smoke_execution")
        elif space_smoke:
            from space_output_contract import SPACE_COMMANDS
            require(execution['mode'] == 'smoke' and execution['requested_commands'] == SPACE_COMMANDS, 'space_commands')
        elif recovery_smoke:
            from backend_recovery_contract import RECOVERY_COMMANDS
            require(execution['mode'] == 'smoke' and execution['requested_commands'] == RECOVERY_COMMANDS, 'recovery_commands')
        elif backend_smoke:
            require(execution['mode'] == 'smoke' and execution['requested_commands'] == BACKEND_COMMANDS, 'backend_commands')
        elif cell_smoke:
            require(execution['mode'] == 'smoke' and execution['requested_commands'] == CELL_COMMANDS, 'cell_commands')
        elif resource_smoke:
            require(execution['mode'] == 'smoke' and execution['requested_commands'] == RESOURCE_COMMANDS, 'resource_commands')
        else:
            require(execution["mode"] == "interactive" and execution["requested_commands"] is None, "interactive_execution")
        config = record(directory / "agent-config.json")
        events = [decode(line) for line in read(directory / "session/session.events.jsonl").splitlines()]
        recovery_owner = None
        if recovery_smoke:
            from resource_output_contract import validate_sample
            require(events[0]['event'] == 'START' and type(events[0]['schema_version']) is int
                    and events[0]['schema_version'] in (7, 8, 9, 10), 'recovery_session_schema')
            # verify_execution already joins each version to its exact retained
            # source set. These schemas carry the same raw CLI owner sample.
            require(events[0]['data']['runtime_version'] == {7: '0.7.0', 8: '0.8.0', 9: '0.9.0', 10: '0.10.0'}[events[0]['schema_version']],
                    'recovery_session_version')
            sample = events[0]['data']['source_process']
            validate_sample(sample)
            recovery_owner = {key: sample[key] for key in ('host_boot_id', 'process_id', 'process_start_ticks', 'uid')}
        backend = verify_model(directory, config, allow_recovered=recovery_smoke, recovery_owner=recovery_owner,
                               require_start_control=not (recovery_smoke or task_smoke))
        stopped = verify_stop(directory)
        agent_dir = directory / "agent"
        require(agent_dir.is_dir() and not agent_dir.is_symlink(), "state_directory")
        all_commands = [event['data'] for event in events if event['event'] == 'COMMAND']
        commands = [event["data"] for event in events if event["event"] == "COMMAND"
                    and event["data"]["name"] in ("agent", "room", "ask", 'resources', 'cell', 'space', 'task')]
        runs = []
        if stopped["state"] == "ABSENT":
            require(not list(agent_dir.iterdir()), "absent_store_not_empty")
            require(all(command["result"].get("source_record") is None
                        and command["result"].get("inference_receipt") is None for command in commands), "absent_console_source")
        else:
            store = verify_agent_runs(agent_dir, source_root=source_root, require_live=True)
            require(store["outcome"] == "PASS", "agent_runs:" + json.dumps(store.get("reasons", [])))
            runs = store["runs"]
            require(stopped["source_record"] == runs[-1]["source_record"]
                    and stopped["management_snapshot"] == runs[-1]["management_snapshot"], "stop_receipt")
            require(all(run["config"] == config for run in runs), "interactive_config")
            sources = [event["source_record"] for run in runs for event in run["events"]
                       if event["source_record"] is not None]
            receipts = {item["request_id"]: item for run in runs for item in run["requests"]}
            observed = []
            for command in commands:
                value = command["result"]
                if value.get("source_record") is not None:
                    require(value["source_record"] in sources, "interactive_source")
                item = value.get("inference_receipt")
                if item is not None:
                    require(receipts.get(item["request_id"]) == item, "interactive_receipt")
                    observed.append(item["request_id"])
            if events[0]['schema_version'] == 10:
                verify_task_delivery(runs, commands, events[0]['data']['source_process'])
                verify_task_backends(directory, runs, backend)
                require(not observed, 'task_top_receipt')
            else:
                require(len(observed) == len(set(observed)) and set(observed) == set(receipts), "interactive_request_accounting")
            resource_values = verify_resource_delivery(commands, runs, task_protocol=events[0]['schema_version'] == 10)
            if any(value['relation'] is not None for value in resource_values):
                verify_resource_attestations(directory, resource_values, backend)
            verify_cell_delivery(commands, runs)
            verify_space_delivery(commands, runs)
            verify_execution_backends(runs, backend)
        verify_backend_delivery(all_commands, backend)
        task_result = None
        if task_smoke:
            from task_smoke_contract import verify_task_workflow
            task_result = verify_task_workflow(directory, all_commands, runs, backend)
        if space_smoke:
            verify_space_workflow(all_commands, runs, backend)
        if backend_smoke:
            verify_backend_workflow(all_commands, runs, backend)
        if recovery_smoke:
            from backend_recovery_contract import verify_recovery_workflow
            verify_recovery_workflow(directory, all_commands, runs, backend)
        if cell_smoke:
            verify_cell_workflow(commands, runs)
        if resource_smoke:
            require(len(runs) == 1 and len(runs[0]['requests']) == 1, 'resource_run_count')
            require([c['outcome'] for c in commands] == ['OK', 'ERROR', 'OK', 'OK', 'OK', 'OK', 'OK', 'OK', 'OK'],
                    'resource_command_outcomes')
            values = runs[0]['resource_results']
            require([v['action'] for v in values] == ['status', 'link', 'sample', 'request', 'status'], 'resource_action_sequence')
            require(values[0]['error'] == 'resource-unlinked' and all(v['outcome'] == 'OK' for v in values[1:]), 'resource_outcomes')
            relation = values[1]['relation']
            require(relation['relation_generation'] == 1 and all(v['relation'] == relation for v in values[1:]), 'resource_relation')
            require(values[2]['observation']['observation_id'] != values[3]['observation']['observation_id']
                    and values[4]['observation'] == values[3]['observation'], 'resource_last_observation')
            require(values[3]['observation']['cpu']['backend']['cpu_time_ns'] > 0, 'resource_backend_workload')
        if require_shutdown:
            verify_shutdown(directory)
        value = {"outcome": "PASS", "reasons": [], "state": stopped["state"], "service_runs": len(runs),
                "main_started": bool(runs), "warmup_requests": len(runs),
                "user_requests": sum(len(run["requests"]) for run in runs),
                "process_exit_verified": bool(runs), "model_bytes_verified": True,
                "backend_process_exit_verified": True, "backend_process_exit_code": backend["process_exit_code"],
                "model_id": config["model_id"], "model_sha256": MODEL_SHA, "backend_sha256": BACKEND_SHA,
                'resource_observations': sum(v['action'] in ('sample', 'request') and v['outcome'] == 'OK'
                                             for run in runs for v in run['resource_results']),
                'resource_workflow_verified': resource_smoke,
                'cell_workflow_verified': cell_smoke,
                'backend_workflow_verified': backend_smoke,
                'backend_service_runs': len(backend.get('managed_runs', [])),
                "vm_shutdown_verified": require_shutdown}
        if task_smoke:
            value.update(task_result)
        if space_smoke:
            value.update(space_workflow_verified=True, space_current_answers=2, space_stale_answers=1,
                         space_target_rejections=3, space_observations=2)
        if recovery_smoke:
            recovered = [run for run in backend['managed_runs'] if run['state'] == 'RECOVERED']
            value.update(recovery_workflow_verified=True, backend_recovered_runs=len(recovered),
                         backend_normal_runs=len(backend['managed_runs']) - len(recovered))
        return value
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def verify_space_delivery(commands: list, runs: list) -> None:
    """Every explicit observation is delivered once; implicit ask capture stays in its receipt."""
    shown = [c['result']['space_context'] for c in commands if c['result'].get('space_context') is not None]
    retained = [value for run in runs for value in run.get('space_results', [])]
    require(shown == retained, 'space_delivery_accounting')
    displayed_events = [c['result'] for c in commands if c['name'] == 'space' and c['outcome'] == 'OK']
    recorded_events = [e for run in runs for e in run['events'] if e['event'] == 'COMMAND' and e['action'] == 'space']
    require(len(displayed_events) == len(recorded_events), 'space_delivery_count')
    for value, event in zip(displayed_events, recorded_events):
        require(all(value[key] == event[key] for key in ('action', 'outcome', 'error', 'source_record', 'management_snapshot')),
                'space_delivery_transition')


def verify_space_workflow(commands: list, runs: list, backend: dict) -> None:
    """Fixed actual-model lane, separate from generic arbitrary-question acceptance."""
    from space_output_contract import SPACE_COMMANDS, TTL_NS, validate_model_answer
    require([' '.join([c['name'], *c['args']]) for c in commands] == SPACE_COMMANDS, 'space_command_sequence')
    require(len(runs) == 2 and [len(run['requests']) for run in runs] == [1, 2]
            and len(backend.get('managed_runs', [])) == 2, 'space_run_counts')
    require(all(c['result'].get('schema_version') == 5 for c in commands
                if c['name'] in ('agent', 'room', 'cell', 'ask', 'space')), 'space_protocol')
    errors = {7: 'model-not-ready', 12: 'orphan', 14: 'stale'}
    for index, command in enumerate(commands):
        require(command['outcome'] == ('ERROR' if index in errors else 'OK'), 'space_command_outcomes')
        if index in errors:
            value = command['result']
            require(value['error'] == errors[index] and value['inference_receipt'] is None
                    and value['resource_result'] is None and value['space_context'] is None,
                    'space_target_rejection')
    answers = [commands[i]['result']['inference_receipt'] for i in (4, 18, 20)]
    require(answers == [item for run in runs for item in run['requests']], 'space_answer_accounting')
    for receipt in answers:
        validate_model_answer(receipt)
        require(receipt['backend_execution'] is not None, 'space_missing_model_execution')
    require([item['space_context']['validity'] for item in answers] == ['CURRENT', 'CURRENT', 'STALE'], 'space_answer_validity')
    for item in answers[:2]:
        facts = item['space_context']['observation']['normalized']
        require(all(facts[key] is not None for key in ('working_directory', 'logical_cpu_count', 'memory_total_bytes')),
                'space_current_facts_missing')
    require([len(run.get('space_results', [])) for run in runs] == [0, 2], 'space_explicit_observations')
    observations = runs[1]['space_results']
    require(answers[1]['space_context']['observation'] == observations[0]['observation']
            and answers[2]['space_context']['observation'] == observations[1]['observation'], 'space_refresh_delivery')
    require(answers[2]['space_context']['checked_monotonic_ns'] - observations[1]['checked_monotonic_ns'] > TTL_NS,
            'space_wait_not_observed')
    require(commands[6]['result']['source_record']['model_ready'] is False
            and not commands[6]['result']['management_snapshot']['binding_current'], 'space_backend_invalidated')
    require(runs[0]['execution_binding']['descriptor'] != runs[1]['execution_binding']['descriptor']
            and runs[0]['running_source']['source_instance'] != runs[1]['running_source']['source_instance'],
            'space_backend_replacement')
    snapshots = [commands[i]['result']['management_snapshot'] for i in (10, 11, 13, 16)]
    require([v['parent']['generation'] for v in snapshots] == [1, 2, 3, 3]
            and [v['parent']['active'] for v in snapshots] == [True, False, True, True]
            and [v['binding_current'] for v in snapshots] == [True, False, False, True], 'space_cell_recovery')
    require(snapshots[0]['binding']['generation'] == 2 and snapshots[3]['binding']['generation'] == 3,
            'space_explicit_rebind')


def verify_cell_delivery(commands: list, runs: list) -> None:
    """Cell mutations shown to the user must be the recorded transitions."""
    shown = [c['result'] for c in commands if c['name'] == 'cell'
             and c['args'] in (['activate'], ['deactivate'])
             and (c['outcome'] == 'OK' or c['result'].get('error') == 'overflow')]
    recorded = [e for run in runs for e in run['events']
                if e['event'] == 'COMMAND' and e['action'] in ('cell-activate', 'cell-deactivate')]
    require(len(shown) == len(recorded), 'cell_delivery_count')
    for value, event in zip(shown, recorded):
        require(all(value[key] == event[key] for key in
                    ('action', 'outcome', 'error', 'source_record', 'management_snapshot')),
                'cell_delivery_transition')


def verify_cell_workflow(commands: list, runs: list) -> None:
    """Bounded real-model proof; rejected asks never reach the backend."""
    require(len(runs) == 1 and len(runs[0]['requests']) == 1 and len(commands) == 23, 'cell_run_count')
    run = runs[0]
    errors = {10: 'orphan', 11: 'resource-relation-stale', 14: 'stale',
              15: 'not-discovered', 18: 'resource-relation-stale'}
    for index, command in enumerate(commands):
        require(command['outcome'] == ('ERROR' if index in errors else 'OK'), 'cell_command_outcome')
        if index in errors and errors[index] is not None:
            require(command['result']['error'] == errors[index], 'cell_rejection_reason')
    source = run['running_source']
    for command in commands[:20]:
        require(command['result']['source_record'] == source and command['result']['inference_receipt'] is None,
                'cell_lifecycle_changed_main')
    require(commands[22]['result']['state'] == 'STOPPED', 'cell_main_stop')
    # Every recorded mutation/request has exactly one delivered command, in order.
    traced = [e for e in run['events'] if e['event'] == 'COMMAND']
    selected = [c for c in commands if c['result']['action'] not in ('start', 'status', 'stop', 'cell-status')]
    require(len(traced) == len(selected), 'cell_trace_delivery_count')
    for command, event in zip(selected, traced):
        require(all(command['result'][key] == event[key] for key in
                    ('action', 'outcome', 'error', 'source_record', 'management_snapshot')), 'cell_trace_delivery')
    snapshots = [c['result']['management_snapshot'] for c in commands]
    require(snapshots[5] == snapshots[4] and snapshots[7] == snapshots[6] == snapshots[8]
            and snapshots[13] == snapshots[12], 'cell_idempotent_or_status')
    require([snapshots[i]['parent']['generation'] for i in (5, 6, 12)] == [1, 2, 3]
            and [snapshots[i]['parent']['active'] for i in (5, 6, 12)] == [True, False, True], 'cell_generations')
    require(not any(snapshots[i]['binding_current'] for i in range(6, 17))
            and snapshots[17]['binding_current'] and snapshots[17]['binding']['generation'] == 2, 'cell_rebinding')
    values = run['resource_results']
    require([v['action'] for v in values] == ['link', 'sample', 'status', 'status', 'link', 'request', 'status'],
            'cell_resource_sequence')
    require([v['outcome'] for v in values] == ['OK', 'OK', 'ERROR', 'ERROR', 'OK', 'OK', 'OK'], 'cell_resource_outcomes')
    old, new = values[0]['relation'], values[4]['relation']
    require(old['relation_id'] == new['relation_id'] and old['relation_generation'] == 1
            and new['relation_generation'] == 2 and new['binding_generation'] == 2
            and new['parent']['generation'] == new['canonical']['generation'] == 3, 'cell_resource_relink')
    require(old['main_identity'] == new['main_identity']
            and old['backend_proof']['descriptor'] == new['backend_proof']['descriptor'], 'cell_process_continuity')
    require(values[5]['observation'] is not None and values[6]['observation'] == values[5]['observation']
            and values[5]['observation']['cpu']['backend']['cpu_time_ns'] > 0, 'cell_model_resource_workload')


def verify_resource_attestations(directory: Path, values: list, backend: dict) -> None:
    """Cross-check the independently exported launcher proof stream."""
    from resource_output_contract import validate_resource_result
    if 'managed_runs' in backend:
        proofs = [proof for run in backend['managed_runs'] for proof in run['proofs']]
    else:
        descriptor = record(directory / 'model-backend/backend-source.json')
        raw = read(directory / 'model-backend/backend-attestations.jsonl')
        require(raw.endswith(b'\n'), 'attestations_truncated')
        proofs = [decode(line) for line in raw.splitlines()]
        require(1 <= len(proofs) <= 128 and len({p['nonce'] for p in proofs}) == len(proofs), 'attestation_count')
        require(descriptor['process_id'] == backend['pid'] and descriptor['model_id'] == backend['model_id']
                and descriptor['model_sha256'] == backend['model_sha256']
                and descriptor['backend_sha256'] == backend['backend_sha256'], 'backend_resource_identity')
        require(all(p['descriptor'] == descriptor and p['peer_uid'] == backend['uid']
                    and p['capture_kind'] == 'live' for p in proofs), 'attestation_backend')
    config = record(directory / 'agent-config.json')
    for value in values:
        validate_resource_result(value, config=config, require_live=True)
        relation = value['relation']
        if relation is not None:
            require(relation['backend_proof'] in proofs, 'relation_attestation')
        observation = value['observation']
        if observation is not None:
            for frame in (observation['before'], observation['after']):
                require(frame['backend_proof'] in proofs, 'observation_attestation')


def verify_execution_backends(runs: list, backend: dict) -> None:
    """MAIN execution proofs must refer to an independently verified lifetime."""
    if not any(run.get('execution_binding') is not None for run in runs):
        return
    require('managed_runs' in backend, 'execution_unmanaged_backend')
    for run in runs:
        binding = run['execution_binding']
        require(binding is not None and binding['descriptor'] is not None, 'execution_live_binding')
        selected = [row for row in backend['managed_runs'] if row['descriptor'] == binding['descriptor']]
        require(len(selected) == 1 and binding['initial_proof'] in selected[0]['proofs'], 'execution_backend_attestation')
        model = selected[0]
        first = model['events'][2]['monotonic_ns']
        last = (model['recovery']['recovery']['started_monotonic_ns'] if model['state'] == 'RECOVERED'
                else model['events'][3]['monotonic_ns'])
        for receipt in (run['warmup'], *run['requests']):
            if receipt['outcome'] == 'OK':
                evidence = receipt['backend_execution']
                require(first <= evidence['before']['read_start_ns'] <= evidence['after']['read_end_ns'] <= last,
                        'execution_outside_backend_lifetime')


def verify_resource_delivery(commands, runs, *, task_protocol=False):
    """Join displayed direct commands; Task request resources remain in run evidence."""
    values = [value for run in runs for value in run['resource_results']]
    expected = [value for value in values if not task_protocol or value['action'] != 'request']
    shown = [command['result']['resource_result'] for command in commands
             if command['result'].get('resource_result') is not None]
    require(shown == expected, 'resource_delivery_accounting')
    return values


def verify_task_delivery(runs, commands, source_process):
    """Repeated queries must deliver a saved revision of this CLI's admission."""
    from newagent_output_contract import same
    from resource_output_contract import validate_sample
    tasks = {key: entry for run in runs for key, entry in run.get('tasks', {}).items()}
    require(len(tasks) == sum(len(run.get('tasks', {})) for run in runs), 'task_cross_run_id')
    if tasks:
        validate_sample(source_process)
        owner = {key: source_process[key] for key in ('host_boot_id', 'process_id', 'process_start_ticks', 'uid')}
        for entry in tasks.values():
            row = entry['revisions'][0]['request_state']
            require(same(row['owner'], owner) and source_process['read_end_ns'] <= row['accepted_ns'], 'task_console_owner')
    admitted, seen_revisions, confirmed_main = [], {}, None
    possible_cancels, observed_cancels = set(), set()

    def uncertain_cancel(request_id, main):
        if request_id not in tasks or request_id not in admitted:
            return
        if request_id not in possible_cancels:
            initial = tasks[request_id]['revisions'][0]['request_state']
            require(main is not None and all(same(main[key], initial['source_before'][key])
                    for key in ('source_id', 'source_instance', 'service_start_generation',
                                'host_boot_id', 'process_id')), 'task_uncertain_cancel_instance')
        possible_cancels.add(request_id)

    for command in commands:
        value = command['result']
        prior_main = confirmed_main
        if value.get('source_record') is not None:
            confirmed_main = (value['source_record'] if value.get('state') == 'RUNNING'
                              and value.get('outcome') == 'OK' else None)
        elif (command['name'] == 'agent' and value.get('schema_version') == 6
              and command['args'] and command['args'][0] in ('start', 'restart', 'stop')):
            confirmed_main = None
        if command['name'] not in ('ask', 'task'):
            continue
        if value.get('schema_version') != 6:
            # The preceding exact console gate validates these local errors.
            # They carry no request UUID and cannot account for an admission.
            require(command['outcome'] == 'ERROR' and set(value) == {'error', 'message'}
                    and value['error'] in ('invalid_arguments', 'interrupted'), 'task_delivery_protocol')
            if (value['error'] == 'interrupted' and command['name'] == 'task'
                    and len(command['args']) == 2 and command['args'][0] == 'cancel'):
                # A console interrupt may hide the reply after MAIN persisted
                # cancellation; the command still retains its exact Task UUID.
                uncertain_cancel(command['args'][1], prior_main)
            continue
        row = value.get('task')
        request_id = value['request_id']
        if command['name'] == 'ask' and request_id in tasks:
            initial = tasks[request_id]['revisions'][0]['request_state']
            require(initial['user_prompt'] == ' '.join(command['args']), 'task_delivery_admission')
            require(request_id not in admitted, 'task_admission_accounting')
            if value['outcome'] == 'ERROR':
                # A lost reply is not a refusal. The independently saved
                # admission, exact prompt and CLI owner establish which task
                # the caller can inspect using its retained UUID.
                require(row is None and value.get('error') in ('rpc-timeout', 'protocol-error', 'state-io'),
                        'task_refusal_admitted')
                require(prior_main is not None and all(same(prior_main[key], initial['source_before'][key])
                        for key in ('source_id', 'source_instance', 'service_start_generation',
                                    'host_boot_id', 'process_id')), 'task_uncertain_main_instance')
            admitted.append(request_id)
        if row is None:
            require(value['outcome'] == 'ERROR', 'task_delivery_missing')
            if (command['name'] == 'task' and command['args'][0] == 'cancel'
                    and value.get('error') in ('rpc-timeout', 'protocol-error', 'state-io')):
                uncertain_cancel(request_id, prior_main)
            continue
        task_id = row['request_id']
        require(task_id in tasks and task_id in admitted, 'task_delivery_orphan')
        require(any(same(row, item['request_state']) for item in tasks[task_id]['revisions']), 'task_delivery_revision')
        require(row['revision'] >= seen_revisions.get(task_id, 0), 'task_delivery_rollback')
        seen_revisions[task_id] = row['revision']
        if command['name'] == 'task' and command['args'][0] == 'cancel':
            control = value.get('task_control')
            require(type(control) is dict, 'task_cancel_delivery_control')
            outcome = control['cancel_outcome']
            if outcome == 'ACCEPTED':
                require(task_id not in observed_cancels, 'task_cancel_duplicate_acceptance')
                possible_cancels.add(task_id)
            elif outcome == 'ALREADY_REQUESTED':
                require(task_id in possible_cancels, 'task_cancel_missing_admission')
            else:
                require(outcome == 'ALREADY_TERMINAL' and row['phase'] == 'FINISHED'
                        and row['model_outcome'] != 'UNKNOWN', 'task_cancel_terminal_outcome')
        if row['cancel_requested_ns'] is not None:
            require(task_id in possible_cancels, 'task_cancel_missing_admission')
            observed_cancels.add(task_id)
        if command['name'] == 'ask' and value['outcome'] == 'OK':
            require(row['revision'] == 1 and row['phase'] == 'ACCEPTED'
                    and row['user_prompt'] == ' '.join(command['args']), 'task_delivery_admission')
    require(len(admitted) == len(set(admitted)) and set(admitted) == set(tasks), 'task_admission_accounting')
    require(all(entry['revisions'][-1]['request_state']['cancel_requested_ns'] is None
                or task_id in possible_cancels for task_id, entry in tasks.items()), 'task_cancel_missing_admission')


def verify_task_backends(directory, runs, backend):
    """Join Task backend snapshots to verified lifetimes and immutable run bytes."""
    from newagent_output_contract import same
    from task_output_contract import validate_task_backend_observation
    task_entries = [entry for run in runs for entry in run.get('tasks', {}).values()]
    if not task_entries:
        return
    require('managed_runs' in backend, 'task_backend_lifetimes')
    def encoded(value):
        return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()
    for entry in task_entries:
        initial = entry['revisions'][0]['request_state']
        expected = initial['backend_expected']
        selected = [model for model in backend['managed_runs'] if same(model['descriptor'], expected['descriptor'])
                    and same(model['events'][2]['service_record'], expected['service_record'])]
        require(len(selected) == 1, 'task_backend_admission')
        model = selected[0]
        ended = (model['recovery']['recovery']['started_monotonic_ns'] if model['state'] == 'RECOVERED'
                 else model['events'][3]['monotonic_ns'])
        require(model['events'][2]['monotonic_ns'] <= initial['accepted_ns'] <= ended,
                'task_backend_admission_time')
        for envelope in entry['revisions']:
            observation = envelope['backend_observation']
            if observation is None:
                continue
            row = envelope['request_state']
            validate_task_backend_observation(observation, row)
            evidence = observation['evidence']
            require(model['state'] == 'STOPPED' and same(observation['backend_stop']['service_record'], model['service_record'])
                    and model['events'][-1]['monotonic_ns'] <= evidence['read_start_ns'], 'task_backend_terminal')
            run_directory = Path(directory) / 'model-backend/runs' / expected['service_record']['instance_id']
            for name, metadata in evidence['artifacts'].items():
                raw = read(run_directory / name)
                require(metadata == {'sha256': digest(raw), 'bytes': len(raw)}
                        and digest(raw) == model['result']['files'][name], 'task_backend_artifact')
            raw_result = read(run_directory / 'result.json')
            require(evidence['terminal_files']['result.json'] == {'sha256': digest(raw_result), 'bytes': len(raw_result)},
                    'task_backend_result_file')
            # Mutable registry/latest may since describe a newer generation.
            # Their exact canonical payloads are derived from this verified
            # lifetime; the immutable run result/files are read above.
            registry = {'schema_version': 1, **{key: expected['service_record'][key]
                        for key in ('service_id', 'instance_id', 'start_generation')}}
            latest = {**observation['backend_stop'], 'action': 'status'}
            for name, payload in (('registry.json', registry), ('latest.json', latest)):
                raw = encoded(payload)
                require(evidence['terminal_files'][name] == {'sha256': digest(raw), 'bytes': len(raw)},
                        'task_backend_terminal_snapshot')


def verify_backend_delivery(commands: list, backend: dict) -> None:
    selected = [command for command in commands if command['name'] == 'backend']
    if not selected:
        require(not any(row.get('state') == 'RECOVERED' for row in backend.get('managed_runs', [])),
                'backend_recovery_delivery_count')
        return
    require('managed_runs' in backend, 'backend_missing_lifetimes')
    states = [(row['events'][2]['service_record'], row['descriptor']) for row in backend['managed_runs']]
    terminal = [row['service_record'] for row in backend['managed_runs'] if row['state'] == 'STOPPED']
    recovered = [row for row in backend['managed_runs'] if row['state'] == 'RECOVERED']
    recovered_records = [row['service_record'] for row in recovered]
    delivered_recoveries = []
    for command in selected:
        value = command['result']
        if value['state'] == 'RUNNING':
            require((value['service_record'], value['descriptor']) in states, 'backend_running_delivery')
        elif value['state'] == 'STOPPED':
            require(value['service_record'] in terminal and value['descriptor'] is None, 'backend_stopped_delivery')
        elif value['state'] == 'RECOVERED':
            require(value['service_record'] in recovered_records and value['descriptor'] is None, 'backend_recovered_delivery')
        if value['action'] == 'recover' and value['outcome'] == 'OK':
            require(value['state'] == 'RECOVERED', 'backend_recovery_state')
            delivered_recoveries.append(value['service_record']['instance_id'])
    require(len(delivered_recoveries) == len(set(delivered_recoveries))
            and set(delivered_recoveries) == {row['start']['instance_id'] for row in recovered}, 'backend_recovery_delivery_count')


def verify_backend_workflow(commands: list, runs: list, backend: dict) -> None:
    shown = [c for c in commands if c['name'] in ('backend', 'agent', 'room', 'resources', 'ask')]
    require(len(shown) == 18 and len(runs) == 2 and len(backend.get('managed_runs', [])) == 2, 'backend_workflow_count')
    for index, command in enumerate(shown):
        require(command['outcome'] == ('ERROR' if index in (7, 8) else 'OK'), 'backend_workflow_outcome')
    require(shown[7]['result']['error'] == 'model-not-ready' and shown[7]['result']['inference_receipt'] is None
            and shown[8]['result']['error'] == 'resource-relation-stale', 'backend_stale_rejection')
    one, two = backend['managed_runs']
    require(one['descriptor']['endpoint'] == two['descriptor']['endpoint']
            and one['descriptor']['source_instance'] != two['descriptor']['source_instance'], 'backend_same_endpoint_replacement')
    require(shown[0]['result']['descriptor'] == one['descriptor'] and shown[5]['result']['descriptor'] == two['descriptor'],
            'backend_restart_delivery')
    invalid = [event for event in runs[0]['events'] if event['event'] == 'BACKEND_INVALIDATED']
    require(len(invalid) == 1 and not runs[0]['requests'] and len(runs[1]['requests']) == 1, 'backend_invalidation_count')
    require(one['events'][-1]['monotonic_ns'] <= two['events'][0]['monotonic_ns']
            <= invalid[0]['monotonic_ns'], 'backend_restart_order')
    require(shown[6]['result']['source_record'] == invalid[0]['source_record']
            and shown[6]['result']['management_snapshot'] == invalid[0]['management_snapshot'], 'backend_invalidation_delivery')
    require([r['execution_binding']['descriptor'] for r in runs] == [one['descriptor'], two['descriptor']], 'backend_main_recovery')
    require(shown[1]['result']['source_record'] == runs[0]['running_source']
            and shown[9]['result']['source_record'] == runs[1]['running_source']
            and shown[15]['result']['source_record'] == runs[1]['source_record'], 'backend_main_delivery')
    recorded = [event for run in runs for event in run['events'] if event['event'] == 'COMMAND']
    delivered = [c['result'] for c in shown if c['name'] != 'backend'
                 and c['result']['action'] not in ('start', 'status', 'stop', 'restart')]
    require(len(recorded) == len(delivered), 'backend_main_command_count')
    for event, value in zip(recorded, delivered):
        require(all(event[key] == value[key] for key in ('action', 'outcome', 'error', 'source_record', 'management_snapshot')),
                'backend_main_command_delivery')
    require(shown[11]['result']['management_snapshot']['binding']['generation'] == 2, 'backend_recovery_binding')
    values = [v for run in runs for v in run['resource_results']]
    require([v['action'] for v in values] == ['link', 'status', 'link', 'request', 'status']
            and [v['outcome'] for v in values] == ['OK', 'ERROR', 'OK', 'OK', 'OK'], 'backend_resource_workflow')
    require(values[0]['relation']['relation_id'] == values[2]['relation']['relation_id']
            and [values[i]['relation']['relation_generation'] for i in (0, 2)] == [1, 2]
            and values[0]['relation']['backend_proof']['descriptor'] == one['descriptor']
            and values[2]['relation']['backend_proof']['descriptor'] == two['descriptor'], 'backend_resource_relink')
    require(values[3]['observation'] is not None and values[4]['observation'] == values[3]['observation']
            and values[3]['observation']['cpu']['backend']['cpu_time_ns'] > 0, 'backend_real_workload')
    require(shown[15]['result']['state'] == shown[16]['result']['state'] == shown[17]['result']['state'] == 'STOPPED',
            'backend_workflow_stop')


def verify_workflow(directory: Path, *, source_root: Path | None = None, require_shutdown: bool = True) -> dict:
    """Verify the two-console MAIN scenario, process controls, and pinned model."""
    try:
        from verify_console import verify_execution

        source_root = source_root or directory / "runtime-source"
        store = verify_agent_runs(directory / "agent", source_root=source_root, require_live=True)
        require(store["outcome"] == "PASS", "agent_runs:" + json.dumps(store.get("reasons", [])))
        runs = store["runs"]
        require(len(runs) == 2, "expected_two_runs")
        config = record(directory / "agent-config.json")
        require(all(row["config"] == config for row in runs), "workflow_config")
        backend = verify_model(directory, config)
        session_commands = []
        verify_execution_backends(runs, backend)
        for session, expected in ((directory, FIRST), (directory / "second-console", SECOND)):
            verdict = verify_execution(session, source_root=source_root, require_live=True)
            require(verdict["outcome"] == "PASS", "console_execution:" + json.dumps(verdict.get("reasons", [])))
            require(record(session / "execution.json")["requested_commands"] == expected, "workflow_commands")
            events = [decode(line) for line in read(session / "session/session.events.jsonl").splitlines()]
            session_commands.append([event["data"] for event in events if event["event"] == "COMMAND"
                                     and event["data"]["name"] in ("agent", "room", "ask")])
        first, second = session_commands
        require(len(first) == 6 and len(second) == 11, "workflow_command_count")
        require([item["outcome"] for item in first] == ["OK", "ERROR", "OK", "OK", "OK", "OK"], "first_outcomes")
        require([item["outcome"] for item in second] == ["OK", "OK", "OK", "ERROR", "ERROR", "ERROR", "OK", "OK", "OK", "OK", "ERROR"], "second_outcomes")
        one, two = runs[0]["running_source"], runs[1]["running_source"]
        require(first[0]["result"]["source_record"] == one and second[2]["result"]["source_record"] == two, "start_receipt")
        require(first[4]["result"]["source_record"] == first[5]["result"]["source_record"]
                == second[0]["result"]["source_record"], "console_reconnect_instance")
        require(second[1]["result"]["source_record"]["source_instance"] == one["source_instance"], "reconnect_request_instance")
        for command in (first[0], first[1], second[2], second[3], second[4], second[5], second[6]):
            require(not command["result"]["management_snapshot"]["binding_current"], "implicit_binding")
        require(first[3]["result"]["management_snapshot"]["binding"]["generation"] == 1
                and second[7]["result"]["management_snapshot"]["binding"]["generation"] == 2, "workflow_binding_generations")
        require(second[4]["result"]["inference_receipt"] is None and second[5]["result"]["error"] in ("stale", "not-discovered"),
                "stale_request_not_rejected")
        all_receipts = {item["request_id"]: item for run in runs for item in run["requests"]}
        observed = [command["result"]["inference_receipt"] for commands in session_commands for command in commands
                    if command["name"] == "ask" and command["result"]["inference_receipt"] is not None]
        require(len(observed) == 3 and len(all_receipts) == 3
                and all(item["outcome"] == "OK" and all_receipts.get(item["request_id"]) == item for item in observed), "workflow_receipts")
        require(len({item["request_id"] for item in observed}) == 3, "workflow_request_reuse")
        require(second[9]["result"]["state"] == "STOPPED"
                and second[9]["result"]["source_record"] == runs[-1]["source_record"]
                and not second[10]["result"]["management_snapshot"]["binding_current"], "workflow_stop")
        stopped = verify_stop(directory)
        require(stopped["state"] == "STOPPED" and stopped["source_record"] == runs[-1]["source_record"], "stop_receipt")
        if require_shutdown:
            verify_shutdown(directory)
        return {"outcome": "PASS", "reasons": [], "state": "STOPPED", "service_runs": 2,
                "user_requests": 3, "warmup_requests": 2, "binding_generations": [1, 2],
                "source_instances": [one["source_instance"], two["source_instance"]],
                "authority_instance": runs[-1]["management_snapshot"]["authority_instance"],
                "model_id": config["model_id"], "model_sha256": MODEL_SHA, "backend_sha256": BACKEND_SHA,
                "backend_process_exit_code": backend["process_exit_code"], "process_exit_verified": True,
                "model_bytes_verified": True, "vm_shutdown_verified": require_shutdown}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--run", action="store_true", help="Verify one completed run instead of a service store")
    parser.add_argument("--workflow", action="store_true", help="Verify the complete two-console/model/VM scenario")
    parser.add_argument("--interactive", action="store_true", help="Verify a model-enabled interactive console and clean shutdown")
    parser.add_argument('--resources', action='store_true', help='Verify actual model resource observation and Internet workflow')
    parser.add_argument('--cells', action='store_true', help='Verify Cell lifecycle, explicit rebind, actual model and Internet')
    parser.add_argument('--backends', action='store_true', help='Verify backend replacement, explicit MAIN recovery, actual model and Internet')
    parser.add_argument('--recovery-smoke', action='store_true', help='Verify explicit owned backend recovery after a recorded supervisor crash')
    parser.add_argument("--space", action="store_true", help="Verify actual CURRENT/UNKNOWN/STALE context consumption and target rejection")
    parser.add_argument("--tasks", action="store_true", help="Verify the real model Task answer and cancellation scenario")
    parser.add_argument("--allow-fixture", action="store_true")
    args = parser.parse_args()
    if args.tasks:
        require(not any((args.run, args.workflow, args.interactive, args.resources, args.cells, args.backends,
                         args.recovery_smoke, args.space, args.allow_fixture)), "tasks_live_only")
        result = verify_interactive(args.artifact_dir, source_root=args.source_root, task_smoke=True)
    elif args.space:
        require(not any((args.run, args.workflow, args.interactive, args.resources, args.cells, args.backends,
                         args.recovery_smoke, args.allow_fixture)), 'space_live_only')
        result = verify_interactive(args.artifact_dir, source_root=args.source_root, space_smoke=True)
    elif args.recovery_smoke:
        require(not any((args.run, args.workflow, args.interactive, args.resources, args.cells, args.backends, args.allow_fixture)),
                'recovery_live_only')
        result = verify_interactive(args.artifact_dir, source_root=args.source_root, recovery_smoke=True)
    elif args.backends:
        require(not any((args.run, args.workflow, args.interactive, args.resources, args.cells, args.allow_fixture)), 'backends_live_only')
        result = verify_interactive(args.artifact_dir, source_root=args.source_root, backend_smoke=True)
    elif args.cells:
        require(not any((args.run, args.workflow, args.interactive, args.resources, args.allow_fixture)), 'cells_live_only')
        result = verify_interactive(args.artifact_dir, source_root=args.source_root, cell_smoke=True)
    elif args.resources:
        require(not args.run and not args.workflow and not args.interactive and not args.allow_fixture, 'resources_live_only')
        result = verify_interactive(args.artifact_dir, source_root=args.source_root, resource_smoke=True)
    elif args.interactive:
        require(not args.run and not args.workflow and not args.allow_fixture, "interactive_live_only")
        result = verify_interactive(args.artifact_dir, source_root=args.source_root)
    elif args.workflow:
        require(not args.run and not args.allow_fixture, "workflow_live_only")
        result = verify_workflow(args.artifact_dir, source_root=args.source_root)
    else:
        result = (verify_run if args.run else verify_agent_runs)(args.artifact_dir, source_root=args.source_root,
                                                                require_live=not args.allow_fixture)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
