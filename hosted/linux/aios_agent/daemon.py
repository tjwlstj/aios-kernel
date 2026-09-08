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
from aios_service.lifecycle import (ServiceError, atomic_json, is_uuid, lock_directory,
    peer_credentials, prepare_directory, read_json, regular_file, supported)
from . import inference, protocol

MAX_EVENTS = 64
MAX_REQUESTS = 64
MAX_STARTS = 64
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
               "protocol-error", "rpc-timeout", "stop-failed"}
    return message if message in allowed else "state-io"


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
    files = set()
    capture = "fixture" if fixture_backend else "live"

    def response(action="status", *, error=None, receipt=None, management_outcome=None, resource_result=None):
        return protocol.reply(action, state, source=copy.deepcopy(source),
            management=authority.snapshot() if authority else None, receipt=receipt,
            management_outcome=management_outcome, error=error, capture_kind=capture, resource_result=resource_result)

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

    def event(name, *, action=None, error=None, receipt_file=None, resource_file=None, cold=False):
        nonlocal sequence
        if sequence >= MAX_EVENTS:
            raise ValueError("request-limit")
        sequence += 1
        row = {"schema_version": 4, "source_instance": identity["source_instance"],
            "sequence": sequence, "monotonic_ns": time.monotonic_ns(), "event": name,
            "action": action, "outcome": "ERROR" if error else "OK", "error": error,
            "source_record": None if cold else copy.deepcopy(source),
            "management_snapshot": authority.snapshot(), "receipt_file": receipt_file, "resource_file": resource_file}
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
        save("start.json", {"schema_version": 4, **{key: identity[key] for key in IDENTITY_KEYS},
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
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            with connection:
                action = "status"
                resource_window = None
                try:
                    _pid, uid, _gid = peer_credentials(connection)
                    if uid != os.getuid():
                        raise ValueError("peer-mismatch")
                    request = protocol.receive(connection)
                    if (set(request) != {"schema_version", "action", "source_instance", "prompt", "backend_dir"}
                            or type(request["schema_version"]) is not int or request["schema_version"] != 4
                            or request["action"] not in protocol.ACTIONS - {"start", "restart"}
                            or (request["action"] != "ask" and request["prompt"] is not None)
                            or (request["action"] != "resources-link" and request["backend_dir"] is not None)):
                        raise ValueError("protocol-error")
                    action = request["action"]
                    if request["source_instance"] != source["source_instance"]:
                        raise ValueError("stale-instance")
                    check_backend()
                    if action == "status":
                        value = response(action)
                    elif action == "cell-status":
                        value = response(action, management_outcome="accepted")
                    elif action == "stop":
                        stop_requested = True
                        state = "STOPPING"
                        value = response(action)
                    elif action == "room-status":
                        managed = authority.observe(source)
                        persist()
                        value = response(action, error=None if managed["outcome"] == "accepted" else managed["reason"],
                                         management_outcome=managed["outcome"])
                    elif sequence >= MAX_EVENTS - 2:
                        value = response(action, error="request-limit")
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
                        inference.request_body(request["prompt"])
                        managed = authority.observe(source)
                        if managed["outcome"] != "accepted":
                            event("COMMAND", action=action, error=managed["reason"])
                            persist()
                            value = response(action, error=managed["reason"], management_outcome="rejected")
                        elif source["completed_requests"] >= MAX_REQUESTS + 1:
                            value = response(action, error="request-limit")
                        else:
                            before = copy.deepcopy(source)
                            binding_generation = authority.binding["generation"]
                            resource_window = resources.begin(source, authority.snapshot())
                            try:
                                receipt = inference.infer(config, request["prompt"],
                                    backend_descriptor=execution_binding.descriptor if execution_binding else None,
                                    backend_capture_kind=capture)
                            except BaseException:
                                if resource_window is not None and 'stack' in resource_window:
                                    resource_window['stack'].close()
                                raise
                            if receipt["outcome"] == "OK":
                                source["completed_requests"] += 1
                            else:
                                source["source_generation"] += 1
                                source["model_ready"] = False
                            managed = authority.observe(source)
                            receipt.update(source_before=before, source_after=copy.deepcopy(source),
                                authority_instance=authority.authority_instance, binding_generation=binding_generation)
                            receipt_name = "requests/" + receipt["request_id"] + ".json"
                            save(receipt_name, receipt)
                            resource_result = resources.finish(resource_window, source, authority.snapshot(),
                                                               request_id=receipt['request_id'])
                            resource_file = None
                            if resource_result is not None:
                                resource_file = 'resources/' + receipt['request_id'] + '.json'
                                save(resource_file, resource_result)
                            event("COMMAND", action=action, error=receipt["error"], receipt_file=receipt_name,
                                  resource_file=resource_file)
                            persist()
                            value = response(action, error=receipt["error"], receipt=receipt,
                                             management_outcome=managed["outcome"], resource_result=resource_result)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    value = response(action, error=code_for(exc))
                finally:
                    if resource_window is not None and 'stack' in resource_window:
                        resource_window['stack'].close()
                try:
                    connection.settimeout(5)
                    protocol.send(connection, value)
                except (OSError, ValueError):
                    pass
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
                atomic_json(run / "result.json", {"schema_version": 4,
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
