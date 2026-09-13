"""Unintegrated owned-worker primitive; worker exit is not backend cancellation.

Prepare before the daemon durably records a request, then start exactly once.
The owner must poll regularly and use close()/a context manager in finally.
No arbitrary command, PID adoption, model-server stop or automatic retry exists.
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import inference

STDOUT_LIMIT = inference.MAX_WORKER_OUTPUT
STDERR_LIMIT = 16 * 1024
TERM_SECONDS = 2.0
KILL_SECONDS = 3.0
DRAIN_SECONDS = 2.0


class SpawnFailure(RuntimeError):
    """Launch failure facts, not proof that HTTP was never sent.

The original exception is __cause__. evidence is an immutable initial snapshot;
owned_worker retains the actual capability if cleanup needs a further attempt.
spawned=False means no Popen was returned, not independent proof of no process.
"""
    def __init__(self, evidence, owned_worker):
        super().__init__('worker-start-failed')
        self._evidence = inference.encoded(evidence)
        self.owned_worker = owned_worker

    @property
    def evidence(self):
        return inference.parse(self._evidence)


class _StartLease:
    def __init__(self):
        self.lock = threading.Lock()
        self.used = False

    def claim(self):
        with self.lock:
            if self.used:
                raise ValueError('request-already-started')
            self.used = True


@dataclass(frozen=True)
class PreparedInference:
    """Immutable bytes; accessors return copies, never the eventual send objects."""
    _input: bytes
    _template: bytes
    _timeout: float
    _lease: _StartLease = field(default_factory=_StartLease, repr=False, compare=False)

    @property
    def request_id(self):
        return self.receipt_template['request_id']

    @property
    def request_body(self):
        return inference.parse(self._input)['request'].encode('utf-8')

    @property
    def receipt_template(self):
        return inference.parse(self._template, maximum=inference.MAX_WORKER_OUTPUT)


def prepare(config, prompt, *, request_id, warmup=False, backend_descriptor=None,
            backend_capture_kind='live', space_context=None):
    """Validate the complete immutable input without starting a process or HTTP."""
    if type(request_id) is not str:
        raise ValueError('request-id')
    try:
        identity = uuid.UUID(request_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError('request-id') from exc
    if str(identity) != request_id or identity.int == 0:
        raise ValueError('request-id')
    if type(warmup) is not bool or backend_capture_kind not in ('live', 'fixture'):
        raise ValueError('request-options')
    inference.validate_config(config)
    body = inference.request_body(prompt, warmup=warmup, space_context=space_context)
    if backend_descriptor is not None:
        from aios_resources.backend import validate_descriptor
        validate_descriptor(backend_descriptor, config)
    payload = inference.encoded({'config': config, 'request': body.decode('utf-8'),
        'backend_descriptor': backend_descriptor, 'backend_capture_kind': backend_capture_kind})
    # This is the same bounded stdin object the fixed worker actually reads.
    inference.parse(payload)
    template = inference.encoded(inference._new_receipt(config, prompt, body, warmup=warmup,
        space_context=space_context, request_id=request_id))
    inference.parse(template, maximum=inference.MAX_WORKER_OUTPUT)
    return PreparedInference(payload, template,
        inference.CONTEXT_TIMEOUT if space_context is not None else inference.TIMEOUT)


class _Drain:
    """Keep a bounded prefix, drain excess, and stop only this owned worker."""
    def __init__(self, owner, name, stream, limit):
        self.owner, self.name, self.stream, self.limit = owner, name, stream, limit
        self.buffer = bytearray()
        self.total = 0
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._read, name='aios-worker-' + name, daemon=True)
        self.started = False

    def start(self):
        self.thread.start()
        self.started = True

    def _read(self):
        try:
            while block := self.stream.read(4096):
                with self.lock:
                    self.total += len(block)
                    self.buffer.extend(block[:max(0, self.limit - len(self.buffer))])
                    overflow = self.total > self.limit
                if overflow:
                    self.owner._io_failed(self.name + '-limit')
        except (OSError, ValueError):
            self.owner._io_failed(self.name + '-read')
        finally:
            try:
                self.stream.close()
            except (OSError, ValueError):
                self.owner._io_failed(self.name + '-close')
            finally:
                self.done.set()

    def snapshot(self):
        with self.lock:
            return bytes(self.buffer), self.total

    def close(self, seconds):
        if self.started:
            self.thread.join(max(0.0, seconds))
            if self.thread.is_alive():
                raise RuntimeError('worker-pipe-cleanup')
        else:
            self.stream.close()
            self.done.set()


class PendingInference:
    """One still-owned Popen, bounded pipes, and an idempotent terminal snapshot.

poll() never waits for a process or a reader. Deadlines and TERM escalation are
checked on each poll. request_cancel() means local worker stop requested only.
Exit first observed after the deadline is conservatively timed out, even if the
worker might have exited earlier; there is no independently proved exit time.
If exit was observed first, cancellation is not accepted and its receipt wins.
After a stop request is accepted, any subsequent output remains an ERROR receipt
even if the worker races to exit 0; the upper layer retains that uncertainty.
"""
    def __init__(self, prepared):
        if type(prepared) is not PreparedInference:
            raise TypeError('prepared-inference-required')
        if type(prepared._input) is not bytes or type(prepared._template) is not bytes:
            raise ValueError('prepared-inference-changed')
        # Revalidate a constructed instance as well as immutable factory output.
        packet = inference.parse(prepared._input)
        template = prepared.receipt_template
        when = datetime.fromisoformat(template['started_at'])
        if when.tzinfo is None or when.utcoffset() != timezone.utc.utcoffset(when):
            raise ValueError('prepared-inference-changed')
        checked = prepare(packet['config'], template['user_prompt'], request_id=template['request_id'],
            warmup=template['purpose'] == 'warmup', backend_descriptor=packet['backend_descriptor'],
            backend_capture_kind=packet['backend_capture_kind'], space_context=template['space_context'])
        if (prepared._input != checked._input or prepared._timeout != checked._timeout
                or {k: v for k, v in template.items() if k != 'started_at'}
                != {k: v for k, v in checked.receipt_template.items() if k != 'started_at'}):
            raise ValueError('prepared-inference-changed')
        prepared._lease.claim()
        self._prepared, self._packet = prepared, packet
        self._lock = threading.RLock()
        self._process = None
        self._drains = []
        self._terminal = None
        self._started_ns = time.monotonic_ns()
        self._deadline_ns = self._started_ns + int(prepared._timeout * 1_000_000_000)
        self._stop_at = None
        self._stop_reason = None
        self._cancel_requested = self._timed_out = False
        self._terminate_requested = self._kill_requested = False
        self._signal_error = self._io_error = None
        self._exit_observed_ns = None
        self._closed = False
        dispatch_attempted = False
        try:
            # A bounded regular stdin avoids a pipe write blocking the daemon.
            with tempfile.TemporaryFile() as incoming:
                incoming.write(prepared._input)
                incoming.seek(0)
                environment = {k: v for k, v in os.environ.items()
                    if k.upper() != 'SSLKEYLOGFILE' and not k.lower().endswith('_proxy')}
                dispatch_attempted = True
                self._process = subprocess.Popen([sys.executable, str(Path(inference.__file__).resolve()), '--worker'],
                    stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, close_fds=True, env=environment)
            for name, stream, limit in (('stdout', self._process.stdout, STDOUT_LIMIT),
                                        ('stderr', self._process.stderr, STDERR_LIMIT)):
                drain = _Drain(self, name, stream, limit)
                self._drains.append(drain)
                drain.start()
        except BaseException as exc:
            cleanup_error = None
            try:
                self.close()
            except BaseException as cleanup:
                cleanup_error = type(cleanup).__name__
            spawned = self._process is not None
            evidence = {'request_id': prepared.request_id, 'dispatch_attempted': dispatch_attempted,
                'spawned': spawned, 'worker_pid': self._process.pid if spawned else None,
                'worker_exit_code': self._process.returncode if spawned else None,
                'worker_exit_observed_monotonic_ns': self._exit_observed_ns,
                'cleanup_confirmed': self._closed if spawned else None,
                'cleanup_error': cleanup_error}
            raise SpawnFailure(evidence, self if spawned else None) from exc

    @property
    def closed(self):
        return self._closed

    def _observe_exit(self):
        code = self._process.poll() if self._process is not None else None
        if code is not None:
            with self._lock:
                if self._exit_observed_ns is None:
                    self._exit_observed_ns = time.monotonic_ns()
                    if self._exit_observed_ns >= self._deadline_ns:
                        self._timed_out = True
                        if self._stop_reason is None:
                            self._stop_reason = 'timeout'
        return code

    def _request_stop(self, reason):
        with self._lock:
            if self._terminal is not None or self._process is None or self._observe_exit() is not None:
                return False
            if reason == 'cancel':
                self._cancel_requested = True
            if reason == 'timeout':
                self._timed_out = True
            if self._stop_reason is None:
                self._stop_reason, self._stop_at = reason, time.monotonic()
            if not self._terminate_requested:
                self._terminate_requested = True
                try:
                    self._process.terminate()
                except OSError:
                    self._signal_error = 'worker-terminate'
            return True

    def _io_failed(self, reason):
        with self._lock:
            if self._io_error is None:
                self._io_error = reason
        self._request_stop('io-failed')

    def request_cancel(self):
        """Accept a local stop request for a live owned worker; signals may fail."""
        self.poll()
        return self._request_stop('cancel')

    def _kill(self):
        with self._lock:
            if self._process is not None and self._observe_exit() is None and not self._kill_requested:
                self._kill_requested = True
                try:
                    self._process.kill()
                except OSError:
                    self._signal_error = 'worker-kill'

    def _snapshot(self, done, code, receipt=None):
        with self._lock:
            sizes = {drain.name: drain.snapshot()[1] for drain in self._drains}
            return {'request_id': self._prepared.request_id, 'done': done,
                'worker_pid': self._process.pid if self._process is not None else None,
                'worker_exit_code': code, 'worker_exit_observed_monotonic_ns': self._exit_observed_ns,
                'cancel_requested': self._cancel_requested, 'timed_out': self._timed_out,
                'stop_reason': self._stop_reason, 'terminate_requested': self._terminate_requested,
                'kill_requested': self._kill_requested, 'signal_error': self._signal_error,
                'io_error': self._io_error, 'output_bytes': sizes, 'receipt': receipt}

    def poll(self):
        """Nonblocking: no wait(), join(), pipe reads or counter mutations on completion."""
        if self._terminal is not None:
            return copy.deepcopy(self._terminal)
        code = self._observe_exit()
        if code is None:
            now = time.monotonic()
            if time.monotonic_ns() >= self._deadline_ns:
                self._request_stop('timeout')
            if self._stop_at is not None and now - self._stop_at >= TERM_SECONDS:
                self._kill()
            code = self._observe_exit()
        if code is None or not all(drain.done.is_set() for drain in self._drains):
            return self._snapshot(False, code)
        receipt = self._prepared.receipt_template
        receipt['error'] = 'backend-timeout' if self._timed_out else 'backend-failed'
        if self._stop_reason is None and self._io_error is None:
            output = {drain.name: drain.snapshot()[0] for drain in self._drains}
            body = self._prepared.request_body
            try:
                receipt.update(inference._validated_worker_result(output['stdout'], output['stderr'], code,
                    config=self._packet['config'], body=body, token_limit=inference.parse(body)['n_predict'],
                    backend_descriptor=self._packet['backend_descriptor'],
                    backend_capture_kind=self._packet['backend_capture_kind'], space_context=receipt['space_context']))
                receipt['error'] = None
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                pass
        receipt['elapsed_ns'] = self._exit_observed_ns - self._started_ns
        self._terminal = self._snapshot(True, code, receipt)
        return copy.deepcopy(self._terminal)

    def close(self):
        """Boundedly terminate/reap only this owned worker and close its pipes."""
        if self._closed:
            return
        if self._process is not None:
            if self._observe_exit() is None:
                self._request_stop('close')
                try:
                    self._process.wait(timeout=TERM_SECONDS)
                except subprocess.TimeoutExpired:
                    self._kill()
                    self._process.wait(timeout=KILL_SECONDS)
            self._observe_exit()
            deadline = time.monotonic() + DRAIN_SECONDS
            for drain in self._drains:
                drain.close(deadline - time.monotonic())
            # Also cover failure before one of the two drain objects was made.
            for stream in (self._process.stdout, self._process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            self.poll()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def start(prepared):
    """Start the fixed worker after the caller has durably recorded prepared input."""
    return PendingInference(prepared)
