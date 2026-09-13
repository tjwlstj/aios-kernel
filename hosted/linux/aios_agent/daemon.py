"""Long-lived MAIN producer with explicit hosted management authority.

Only this daemon owns its source tuple. The separate Authority owns canonical
relationships; local authenticated requests must explicitly discover and bind.
"""
from __future__ import annotations

import copy
import hashlib
import os
import signal
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aios_management.binding import Authority, MAX_GENERATION, validate_source
from aios_resources.runtime import ResourceManager
from aios_resources.proc import ProcessReader
from aios_service.lifecycle import (ServiceError, atomic_json, is_uuid, lock_directory,
    peer_credentials, prepare_directory, read_json, regular_file, supported)
from . import inference, protocol, space
from .request_runtime import RequestExecution, RequestExecutionFailure

MAX_EVENTS = 64
MAX_REQUESTS = 64
MAX_STARTS = 64
MAX_TASKS = 16
TASK_ACTIONS = frozenset(('ask-start', 'task-status', 'task-result', 'task-cancel'))
TASK_ERRORS = frozenset(('request-busy', 'request-id-reused', 'request-not-found',
    'request-owner-mismatch', 'request-owner-lost', 'request-backend-not-ready',
    'request-active-at-stop', 'request-execution-failed', 'request-persistence-failed',
    'request-finalization-failed', 'request-dispatch-uncertain', 'request-worker-uncertain',
    'request-cancel-uncertain', 'request-task-required'))
IDENTITY_KEYS = ("source_id", "source_instance", "service_start_generation")
SOURCE_FILES = ("aios-agent.py", "aios_agent/__init__.py", "aios_agent/inference.py",
    "aios_agent/protocol.py", "aios_agent/client.py", "aios_agent/daemon.py",
    "aios_management/__init__.py", "aios_management/binding.py",
    "aios_service/__init__.py", "aios_service/lifecycle.py",
    "aios_hosted/__init__.py", "aios_hosted/boot.py", "aios_hosted/hardware.py",
    "aios_resources/__init__.py", "aios_resources/proc.py", "aios_resources/backend.py",
    "aios_resources/runtime.py", "aios_management/resources.py")
SOURCE_FILES += ("aios-backend.py", "aios_backend/__init__.py", "aios_backend/protocol.py",
                 "aios_backend/client.py", "aios_backend/daemon.py", "aios_agent/backend_binding.py")
SOURCE_FILES += ("aios_agent/space.py",)
SOURCE_FILES += ("aios_agent/async_inference.py", "aios_agent/request_state.py",
                 "aios_agent/request_runtime.py")


class PersistenceFailure(RuntimeError):
    """An uncertain durable state ends this producer's lifetime."""


def source_hashes() -> dict:
    base = Path(__file__).resolve().parent.parent
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def registry_at(directory: Path) -> dict | None:
    try:
        value = read_json(directory / "registry.json")
    except FileNotFoundError:
        if any((directory / name).exists() for name in ("latest.json", "runs", "management.json")):
            raise ValueError("state-corrupt")
        return None
    if (set(value) != {"schema_version", *IDENTITY_KEYS} or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or not all(is_uuid(value[key]) for key in IDENTITY_KEYS[:2])
            or type(value["service_start_generation"]) is not int
            or not 1 <= value["service_start_generation"] <= MAX_STARTS):
        raise ValueError("state-corrupt")
    return value


def code_for(exc: Exception) -> str:
    if isinstance(exc, ServiceError):
        return {"BUSY": "start-in-progress", "STATE_CORRUPT": "state-corrupt",
                "INVALID_STATE_DIRECTORY": "state-corrupt"}.get(exc.code, "protocol-error")
    message = str(exc)
    allowed = {"state-corrupt", "request-limit", "model-hash-mismatch", "backend-hash-mismatch",
               "backend-failed", "prompt-invalid", "generation-exhausted", "peer-mismatch", "stale-instance",
               "protocol-error", "rpc-timeout", "stop-failed", "space-invalid", "space-overflow",
               "space-future", "space-source-mismatch", "space-budget"}
    return message if message in allowed | TASK_ERRORS else "state-io"


def serve(state_dir: Path, config_path: Path, *, fixture_backend: bool = False,
          backend_dir: Path | None = None) -> int:
    if not supported():
        return 3
    os.umask(0o077)
    directory = prepare_directory(state_dir, control_root=True)
    lock_fd = lock_directory(directory)
    listener = None
    run = None
    source = None
    execution_binding = None
    authority = None
    state = "STARTING"
    terminal_error = None
    exit_code = 1
    sequence = 0
    stop_requested = False
    space_observation = None
    files = set()
    capture = "fixture" if fixture_backend else "live"
    tasks = {}

    def response(action="status", *, error=None, receipt=None, management_outcome=None, resource_result=None,
                 space_context=None, request_id=None, task=None, task_control=None):
        return protocol.reply(action, state, source=copy.deepcopy(source),
            management=authority.snapshot() if authority else None, receipt=receipt,
            management_outcome=management_outcome, error=error, capture_kind=capture, resource_result=resource_result,
            space_context=space_context, request_id=request_id, task=task, task_control=task_control)

    def environment_context(*, refresh=False):
        nonlocal space_observation
        if refresh or space_observation is None:
            observed_at = time.monotonic_ns()
            # Read only this daemon's execution environment. A runtime cwd is
            # not a user-selected workspace or permission to read its files.
            try:
                working_directory = os.getcwd()
            except OSError:
                working_directory = None
            if working_directory is not None and len(working_directory.encode("utf-8")) > 512:
                working_directory = None
            mem_total_line = None
            try:
                with open("/proc/meminfo", "rb") as stream:
                    raw = stream.read(16385)
                if len(raw) <= 16384:
                    rows = [row for row in raw.decode("ascii").splitlines() if row.startswith("MemTotal:")]
                    if len(rows) == 1 and len(rows[0]) <= 128:
                        mem_total_line = rows[0]
            except (OSError, UnicodeError):
                pass
            space_observation = space.build_observation(host_boot_id=source["host_boot_id"],
                process_id=source["process_id"], observed_monotonic_ns=observed_at,
                working_directory=working_directory, logical_cpu_count=os.cpu_count(), mem_total_line=mem_total_line)
        return space.build_packet(space_observation, source_record=source, management_snapshot=authority.snapshot(),
                                  model_id=config["model_id"], checked_monotonic_ns=time.monotonic_ns())

    def save(name, value):
        try:
            atomic_json(run / name, value)
        except (OSError, ValueError) as exc:
            raise PersistenceFailure("state-io") from exc
        files.add(name)

    def persist():
        try:
            atomic_json(directory / "management.json", authority.export_state())
            save("management.json", authority.snapshot())
            if source is not None:
                save("source.json", source)
            atomic_json(directory / "latest.json", response(error=terminal_error))
        except (OSError, ValueError) as exc:
            raise PersistenceFailure("state-io") from exc

    def event(name, *, action=None, error=None, receipt_file=None, resource_file=None, space_file=None,
              task_file=None, cold=False):
        nonlocal sequence
        if sequence >= MAX_EVENTS:
            raise ValueError("request-limit")
        sequence += 1
        row = {"schema_version": 6, "source_instance": identity["source_instance"],
            "sequence": sequence, "monotonic_ns": time.monotonic_ns(), "event": name,
            "action": action, "outcome": "ERROR" if error else "OK", "error": error,
            "source_record": None if cold else copy.deepcopy(source),
            "management_snapshot": authority.snapshot(), "receipt_file": receipt_file, "resource_file": resource_file,
            "space_file": space_file, "task_file": task_file}
        path = run / "events.jsonl"
        try:
            regular_file(path, absent_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "ab") as stream:
                stream.write(inference.encoded(row) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, ValueError) as exc:
            raise PersistenceFailure("state-io") from exc
        files.add("events.jsonl")

    def terminate_source():
        nonlocal source
        if source is not None and source["lifecycle_state"] == "active":
            source = {**source, "source_generation": source["source_generation"] + 1,
                      "lifecycle_state": "exited", "model_ready": False}
            authority.observe(source)

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    def check_backend():
        if execution_binding is None or not source['model_ready']:
            return
        try:
            execution_binding.check()
        except (OSError, ValueError):
            if sequence >= MAX_EVENTS - 2:
                raise PersistenceFailure('request-limit')
            source.update(model_ready=False, source_generation=source['source_generation'] + 1)
            authority.observe(source)
            event('BACKEND_INVALIDATED', error='backend-changed')
            persist()

    def active_task():
        return next((entry for entry in tasks.values() if not entry['execution'].terminal), None)

    def reserved_task_events():
        # Five possible revisions plus one result per admitted task. Retain
        # the unused reservation even after completion for a late UNKNOWN
        # cancel/independent backend observation. Also reserve three global
        # events for backend invalidation and normal source shutdown.
        return sum(max(0, 6 - entry['revision'] - int(entry['result_saved'])) for entry in tasks.values())

    def save_task(entry, envelope):
        row = envelope['request_state']
        revision = row['revision']
        if not 1 <= revision <= 8 or revision != entry['revision'] + 1:
            raise PersistenceFailure('state-io')
        name = 'tasks/' + row['request_id'] + '/' + format(revision, '02d') + '.json'
        if (run / name).exists() or name in files:
            raise PersistenceFailure('state-io')
        save(name, envelope)
        entry.update(revision=revision, last_file=name)
        event('REQUEST', task_file=name)
        persist()

    def finish_task(entry, row):
        if entry['result_saved']:
            raise PersistenceFailure('state-io')
        if row['model_outcome'] == 'ANSWERED':
            source['completed_requests'] += 1
        elif row['model_outcome'] == 'UNKNOWN' and source['model_ready']:
            source.update(model_ready=False, source_generation=source['source_generation'] + 1)
        authority.observe(source)
        receipt = copy.deepcopy(row['inference_receipt'])
        receipt_name = None
        if receipt is not None:
            receipt.update(source_before=row['source_before'], source_after=copy.deepcopy(source),
                authority_instance=row['management_before']['authority_instance'],
                binding_generation=row['management_before']['binding']['generation'])
            receipt_name = 'requests/' + row['request_id'] + '.json'
            save(receipt_name, receipt)
        resource_window = entry['resource_window']
        entry['resource_window'] = None
        if row['model_outcome'] == 'NOT_STARTED':
            if resource_window is not None and 'stack' in resource_window:
                resource_window['stack'].close()
            resource_result = None
        else:
            resource_result = resources.finish(resource_window, source, authority.snapshot(),
                                               request_id=row['request_id'])
        resource_file = None
        if resource_result is not None:
            resource_file = 'resources/' + row['request_id'] + '.json'
            save(resource_file, resource_result)
        event('REQUEST_RESULT', error=receipt['error'] if receipt else None,
              receipt_file=receipt_name, resource_file=resource_file, task_file=entry['last_file'])
        entry['result_saved'] = True
        persist()

    def admit_task(request, peer_pid):
        nonlocal space_observation
        request_id = request['request_id']
        if request_id in tasks:
            raise ValueError('request-id-reused')
        if active_task() is not None:
            raise ValueError('request-busy')
        if (len(tasks) >= MAX_TASKS or source['completed_requests'] >= MAX_REQUESTS + 1
                or sequence + reserved_task_events() + 6 > MAX_EVENTS - 3):
            raise ValueError('request-limit')
        inference.request_body(request['prompt'])
        managed = authority.observe(source)
        if managed['outcome'] != 'accepted':
            persist()
            return response('ask-start', request_id=request_id, error=managed['reason'])
        if execution_binding is None:
            raise ValueError('request-backend-not-ready')
        entry = {'revision': 0, 'result_saved': False, 'resource_window': None,
                 'owner_reader': None, 'binding': None, 'execution': None}
        observation_before = space_observation
        admitted = False
        try:
            owner_reader = ProcessReader(peer_pid, expected_boot_id=source['host_boot_id'])
            entry['owner_reader'] = owner_reader
            owner_reader.sample()
            from aios_backend import client as backend_client
            from .backend_binding import ExecutionBinding
            backend_path = backend_dir or Path('/tmp/aios-model-backend')
            expected = backend_client.control(backend_path, 'status')
            try:
                binding = ExecutionBinding(backend_path, config, capture_kind=capture)
                entry['binding'] = binding
                binding.bind_terminal(expected)
                if binding.descriptor != execution_binding.descriptor:
                    raise ValueError('backend-changed')
            except (OSError, ValueError) as exc:
                raise ValueError('request-backend-not-ready') from exc
            context = environment_context()
            inference.request_body(request['prompt'], space_context=context)
            entry['resource_window'] = resources.begin(source, authority.snapshot())
            task_directory = run / 'tasks' / request_id
            task_directory.mkdir(mode=0o700)
            entry['execution'] = RequestExecution(save=lambda value: save_task(entry, value),
                complete=lambda row: finish_task(entry, row), request_id=request_id,
                owner=owner_reader.identity, config=config, source=source, management=authority.snapshot(),
                space_context=context, prompt=request['prompt'], backend_expected=expected)
            tasks[request_id] = entry
            admitted = True
            return response('ask-start', request_id=request_id,
                            task=entry['execution'].snapshot()['request_state'])
        finally:
            if not admitted:
                # An unrecorded first observation must not become subsequent
                # input. Uncertain durable writes already end the producer.
                space_observation = observation_before
                if entry['resource_window'] is not None and 'stack' in entry['resource_window']:
                    entry['resource_window']['stack'].close()
                for name in ('binding', 'owner_reader'):
                    if entry[name] is not None:
                        entry[name].close()

    def owned_task(request_id, peer_pid):
        entry = tasks.get(request_id)
        if entry is None:
            raise ValueError('request-not-found')
        owner = entry['owner_reader'].identity
        if peer_pid != owner['process_id']:
            raise ValueError('request-owner-mismatch')
        try:
            entry['owner_reader'].sample()
        except (OSError, ValueError) as exc:
            raise ValueError('request-owner-mismatch') from exc
        return entry

    def advance_tasks():
        entry = active_task()
        if entry is not None:
            try:
                entry['owner_reader'].sample()
            except (OSError, ValueError):
                entry['execution'].abort('request-owner-lost')
            execution = entry['execution']
            if execution.snapshot()['request_state']['phase'] == 'ACCEPTED':
                execution.dispatch()
            else:
                execution.poll()
        for entry in tasks.values():
            execution = entry['execution']
            row = execution.snapshot()['request_state']
            if row['cancel_requested_ns'] is None or row['backend_stop'] is not None:
                continue
            try:
                observation = entry['binding'].verify_stopped()
            except (OSError, ValueError):
                # Alive, failed or replaced is not a normal terminal proof.
                continue
            execution.observe_backend_stop(entry['owner_reader'].identity, lambda expected: observation)

    old_term = signal.signal(signal.SIGTERM, request_stop)
    old_int = signal.signal(signal.SIGINT, request_stop)
    try:
        previous = registry_at(directory)
        if previous and previous["service_start_generation"] >= MAX_STARTS:
            raise ValueError("generation-exhausted")
        if previous:
            authority = Authority.from_state(read_json(directory / "management.json"))
        else:
            authority = Authority()
            authority.initialize()
        path = protocol.socket_path(directory, missing=True)
        if path.exists():
            path.unlink()
        identity = {"schema_version": 1, "source_id": previous["source_id"] if previous else str(uuid.uuid4()),
                    "source_instance": str(uuid.uuid4()),
                    "service_start_generation": previous["service_start_generation"] + 1 if previous else 1}
        runs = prepare_directory(directory / "runs", create=True)
        run = runs / identity["source_instance"]
        run.mkdir(mode=0o700)
        prepare_directory(run)
        (run / "requests").mkdir(mode=0o700)
        (run / "resources").mkdir(mode=0o700)
        (run / "spaces").mkdir(mode=0o700)
        (run / "tasks").mkdir(mode=0o700)
        atomic_json(directory / "registry.json", identity)
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if not is_uuid(boot_id):
            raise ValueError("state-corrupt")
        source = {"schema_version": 1, "source_namespace": "linux-userspace-service",
            **{key: identity[key] for key in IDENTITY_KEYS}, "source_generation": 1,
            "source_kind": "ai-service", "source_role": "main", "lifecycle_state": "active",
            "producer_owned": True, "copied_read": True, "model_ready": False,
            "model_sha256": None, "warmup_request_sha256": None, "warmup_response_sha256": None,
            "completed_requests": 0, "host_boot_id": boot_id, "process_id": os.getpid(), "source_only": True}
        # Persisted management is never trusted as evidence for a new daemon.
        if authority.current_source is not None:
            authority.observe(source)
        config = inference.load_config(config_path, verify_artifacts=False)
        save("config.json", config)
        atomic_json(directory / "config.json", config)
        save("start.json", {"schema_version": 6, **{key: identity[key] for key in IDENTITY_KEYS},
            "service_kind": "AI_SERVICE", "capture_kind": capture,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": hashlib.sha256((run / "config.json").read_bytes()).hexdigest(),
            "source_hashes": source_hashes()})
        event("STARTING", cold=True)
        persist()
        config = inference.load_config(run / "config.json")
        if not fixture_backend or backend_dir is not None:
            from .backend_binding import ExecutionBinding
            execution_binding = ExecutionBinding(backend_dir or Path('/tmp/aios-model-backend'), config, capture_kind=capture)
        save('backend-binding.json', {'schema_version': 1, 'capture_kind': capture,
            'initial_proof': execution_binding.initial_proof if execution_binding else None,
            'descriptor': execution_binding.descriptor if execution_binding else None})
        resources = ResourceManager(directory, config, capture)
        source["model_sha256"] = config["model_sha256"]
        warmup = inference.infer(config, "Reply with the word ready.", warmup=True,
            backend_descriptor=execution_binding.descriptor if execution_binding else None,
            backend_capture_kind=capture)
        save("warmup.json", warmup)
        if warmup["outcome"] != "OK":
            raise ValueError("backend-failed")
        source.update(model_ready=True, warmup_request_sha256=warmup["request_sha256"],
                      warmup_response_sha256=warmup["response_sha256"], completed_requests=1)
        validate_source(source)
        if stop_requested:
            raise ValueError("backend-failed")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(8)
        listener.settimeout(0.2)
        state = "RUNNING"
        event("RUNNING", receipt_file="warmup.json")
        persist()
        while not stop_requested:
            check_backend()
            advance_tasks()
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            with connection:
                action = "status"
                request_id = None
                try:
                    peer_pid, uid, _gid = peer_credentials(connection)
                    if uid != os.getuid():
                        raise ValueError("peer-mismatch")
                    request = protocol.receive(connection, seconds=1)
                    if (set(request) != {"schema_version", "action", "source_instance", "prompt", "backend_dir", "request_id"}
                            or type(request["schema_version"]) is not int or request["schema_version"] != 6
                            or request["action"] not in protocol.ACTIONS - {"start", "restart"}
                            or (request["action"] not in ('ask', 'ask-start') and request["prompt"] is not None)
                            or (request["action"] != "resources-link" and request["backend_dir"] is not None)
                            or (request['action'] in TASK_ACTIONS and not protocol.valid_request_id(request['request_id']))
                            or (request['action'] not in TASK_ACTIONS and request['request_id'] is not None)):
                        raise ValueError("protocol-error")
                    action = request["action"]
                    request_id = request['request_id']
                    if request["source_instance"] != source["source_instance"]:
                        raise ValueError("stale-instance")
                    check_backend()
                    if action == "status":
                        value = response(action)
                    elif action == "cell-status":
                        value = response(action, management_outcome="accepted")
                    elif action in ('task-status', 'task-result', 'task-cancel'):
                        entry = owned_task(request_id, peer_pid)
                        task_control = None
                        if action == 'task-cancel':
                            cancelled = entry['execution'].cancel(entry['owner_reader'].identity)
                            task_control = {'cancel_outcome': cancelled['outcome'], 'backend_stop_attempt': None}
                        value = response(action, request_id=request_id,
                            task=entry['execution'].snapshot()['request_state'], task_control=task_control)
                    elif action == 'ask-start':
                        value = admit_task(request, peer_pid)
                    elif action == 'ask':
                        value = response(action, error='request-task-required')
                    elif active_task() is not None and action != 'room-status':
                        value = response(action, error='request-busy')
                    elif action == "stop":
                        stop_requested = True
                        state = "STOPPING"
                        value = response(action)
                    elif action == "room-status":
                        # While a question is active, this is only a copied
                        # view; it cannot mutate its admission authority.
                        managed = ({'outcome': 'accepted'} if active_task() is not None
                                   else authority.observe(source))
                        if active_task() is None:
                            persist()
                        value = response(action, error=None if managed["outcome"] == "accepted" else managed["reason"],
                                         management_outcome=managed["outcome"])
                    elif sequence + reserved_task_events() >= MAX_EVENTS - 3:
                        value = response(action, error="request-limit")
                    elif action == "space":
                        context = environment_context(refresh=True)
                        space_file = "spaces/" + str(uuid.uuid4()) + ".json"
                        save(space_file, context)
                        event("COMMAND", action=action, space_file=space_file)
                        persist()
                        value = response(action, space_context=context)
                    elif action in ("cell-activate", "cell-deactivate"):
                        managed = authority.set_parent(action == "cell-activate")
                        error = None if managed["outcome"] == "accepted" else managed["reason"]
                        event("COMMAND", action=action, error=error)
                        persist()
                        value = response(action, error=error, management_outcome=managed["outcome"])
                    elif action.startswith('resources-'):
                        resource_result = resources.execute(action.removeprefix('resources-'), source,
                            authority.snapshot(), request['backend_dir'])
                        resource_file = 'resources/' + str(uuid.uuid4()) + '.json'
                        save(resource_file, resource_result)
                        event('COMMAND', action=action, error=resource_result['error'], resource_file=resource_file)
                        persist()
                        value = response(action, error=resource_result['error'], resource_result=resource_result)
                    elif action.startswith("room-"):
                        if action == "room-discover":
                            managed = authority.discover([source])
                        elif action == "room-bind":
                            managed = authority.bind(source)
                        else:
                            managed = authority.reconcile(source)
                        error = None if managed["outcome"] == "accepted" else managed["reason"]
                        event("COMMAND", action=action, error=error)
                        persist()
                        value = response(action, error=error, management_outcome=managed["outcome"])
                    else:
                        value = response(action, error="protocol-error")
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    value = response(action, error=code_for(exc), request_id=request_id)
                try:
                    connection.settimeout(1)
                    protocol.send(connection, value)
                except (OSError, ValueError):
                    pass
        entry = active_task()
        if entry is not None:
            entry['execution'].abort('request-active-at-stop')
        state = "STOPPING"
        terminate_source()
        event("STOPPING")
        persist()
        state = "STOPPED"
        event("STOPPED")
        persist()
        exit_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        terminal_error = code_for(exc)
        state = "FAILED"
        for entry in tasks.values():
            if not entry['execution'].terminal:
                try:
                    entry['execution'].abort(terminal_error)
                except RequestExecutionFailure:
                    pass
        if run is not None and source is not None and authority is not None:
            # A failed startup has never exposed an active producer over IPC.
            if sequence <= 1:
                source.update(lifecycle_state="exited", model_ready=False)
                authority.observe(source)
            else:
                terminate_source()
            try:
                event("FAILED", error=terminal_error,
                      receipt_file="warmup.json" if "warmup.json" in files else None)
                persist()
            except (OSError, ValueError, PersistenceFailure):
                pass
    finally:
        for entry in tasks.values():
            try:
                entry['execution'].close()
            except (OSError, ValueError, RequestExecutionFailure):
                exit_code = 1
            window = entry['resource_window']
            if window is not None and 'stack' in window:
                window['stack'].close()
            for name in ('binding', 'owner_reader'):
                if entry[name] is not None:
                    entry[name].close()
        if execution_binding is not None:
            try:
                execution_binding.close()
            except OSError:
                exit_code = 1
        if listener is not None:
            listener.close()
            try:
                protocol.socket_path(directory).unlink()
            except (OSError, ValueError):
                exit_code = 1
        if run is not None and source is not None and authority is not None:
            try:
                atomic_json(run / "result.json", {"schema_version": 6,
                    **{key: identity[key] for key in IDENTITY_KEYS}, "state": state,
                    "exit_code": exit_code, "error": terminal_error,
                    "completed_at": datetime.now(timezone.utc).isoformat(), "capture_kind": capture,
                    "source_record": source, "management_snapshot": authority.snapshot(),
                    "files": {name: hashlib.sha256((run / name).read_bytes()).hexdigest() for name in sorted(files)}})
            except (OSError, ValueError):
                exit_code = 1
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
        os.close(lock_fd)
    return exit_code
