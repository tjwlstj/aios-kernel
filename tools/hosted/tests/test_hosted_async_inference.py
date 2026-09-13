"""Owned local children and fixture HTTP only: never actual model evidence."""
from __future__ import annotations

import copy
import hashlib
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aios_agent import async_inference as asynchronous, inference
from test_hosted_inference import config

REAL_POPEN = subprocess.Popen


@contextmanager
def fixture_http(*, blocked=False, changed=None):
    entered, release = threading.Event(), threading.Event()
    requests = []
    if not blocked:
        release.set()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            requests.append((self.path, raw))
            entered.set()
            if not release.wait(5):
                return
            request = json.loads(raw)
            body = {'content': 'fixture response', 'tokens_predicted': 3,
                    'model': 'fixture-model', 'prompt': request['prompt'], 'truncated': False}
            body.update(changed or {})
            encoded = inference.encoded(body)
            try:
                self.send_response(200)
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # The test deliberately stops its own HTTP client.

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield config(server.server_port), entered, release, requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


class AsyncInferenceTests(unittest.TestCase):
    def prepared(self, settings=None, prompt='fixture input', **kwargs):
        return asynchronous.prepare(settings or config(), prompt, request_id=str(uuid.uuid4()), **kwargs)

    def terminal(self, pending, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = pending.poll()
            if result['done']:
                return result
            time.sleep(.005)
        self.fail('owned fixture worker did not reach terminal state')

    @contextmanager
    def child(self, code, prepared=None):
        """Substitute only the private Popen boundary; public API has no command."""
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'owned_fixture.py'
            script.write_text(code, encoding='utf-8')
            processes = []

            def spawn(argv, **kwargs):
                self.assertEqual(argv, [sys.executable, str(Path(inference.__file__).resolve()), '--worker'])
                process = REAL_POPEN([sys.executable, '-B', str(script)], **kwargs)
                processes.append(process)
                return process

            pending = None
            try:
                with mock.patch.object(asynchronous.subprocess, 'Popen', side_effect=spawn):
                    pending = asynchronous.start(prepared or self.prepared())
                yield pending
            finally:
                if pending is not None:
                    pending.close()
                for process in processes:
                    self.assertIsNotNone(process.poll(), 'fixture child leaked')
                    self.assertTrue(process.stdout.closed and process.stderr.closed)

    def test_prepare_rejects_complete_bad_input_before_process_creation(self):
        cases = [({'schema_version': True}, 'hello', {}), ({}, '', {}), ({}, '\\' * 4096, {}),
                 ({}, 'hello', {'space_context': {}}), ({}, 'hello', {'backend_descriptor': {}}),
                 ({}, 'hello', {'warmup': 1}), ({}, 'hello', {'backend_capture_kind': 'other'})]
        with mock.patch.object(asynchronous.subprocess, 'Popen') as spawn:
            for changed, prompt, options in cases:
                with self.subTest(changed=changed, options=options, length=len(prompt)):
                    with self.assertRaises(ValueError):
                        self.prepared({**config(), **changed}, prompt, **options)
            for identity in (True, '', 'not-uuid', str(uuid.UUID(int=0)), str(uuid.uuid4()).upper()):
                with self.subTest(identity=identity), self.assertRaises(ValueError):
                    asynchronous.prepare(config(), 'hello', request_id=identity)
            spawn.assert_not_called()

    def test_immutable_input_durable_template_and_one_shot_start(self):
        with fixture_http() as (settings, _entered, _release, requests), tempfile.TemporaryDirectory() as temporary:
            prepared = self.prepared(settings)
            durable = Path(temporary) / 'request.json'
            durable.write_bytes(inference.encoded(prepared.receipt_template))
            preserved = durable.read_bytes()
            template = prepared.receipt_template
            template['user_prompt'] = 'tampered'
            settings['model_id'] = 'tampered'
            with self.assertRaises(FrozenInstanceError):
                prepared._input = b'{}'
            with asynchronous.start(prepared) as pending:
                result = self.terminal(pending)
                self.assertEqual(result['receipt']['outcome'], 'OK', result)
                self.assertEqual(result['receipt']['request_id'], json.loads(preserved)['request_id'])
                self.assertEqual(requests[0][1], prepared.request_body)
                self.assertEqual(durable.read_bytes(), preserved)
                with mock.patch.object(asynchronous.subprocess, 'Popen') as spawn:
                    with self.assertRaisesRegex(ValueError, 'request-already-started'):
                        asynchronous.start(prepared)
                    spawn.assert_not_called()

    def test_constructed_prepared_revalidated_before_spawn(self):
        prepared = self.prepared()
        template = prepared.receipt_template
        changed = {**template, 'request_sha256': '0' * 64}
        for hostile in (replace(prepared, _input=bytearray(prepared._input)),
                        replace(prepared, _template=inference.encoded(changed)),
                        replace(prepared, _timeout=1),
                        replace(prepared, _template=inference.encoded({**template, 'started_at': '2026-09-09'}))):
            with self.subTest(hostile=type(hostile._input)), mock.patch.object(asynchronous.subprocess, 'Popen') as spawn:
                with self.assertRaises(ValueError):
                    asynchronous.start(hostile)
                spawn.assert_not_called()

    def test_live_worker_poll_is_nonblocking_and_terminal_is_idempotent(self):
        with fixture_http(blocked=True) as (settings, entered, release, requests):
            with asynchronous.start(self.prepared(settings)) as pending:
                self.assertTrue(entered.wait(3))
                started = time.monotonic()
                with mock.patch.object(pending._process, 'wait', side_effect=AssertionError('poll waited')):
                    for _ in range(100):
                        progress = pending.poll()
                        self.assertFalse(progress['done'])
                        self.assertIsNone(progress['receipt'])
                self.assertLess(time.monotonic() - started, .5)
                release.set()
                completed = self.terminal(pending)
                receipt = completed['receipt']
                self.assertEqual((receipt['schema_version'], receipt['outcome']), (3, 'OK'))
                self.assertEqual(receipt['request_sha256'], hashlib.sha256(requests[0][1]).hexdigest())
                self.assertEqual(receipt['response_sha256'], hashlib.sha256(receipt['response_body'].encode()).hexdigest())
                self.assertEqual(completed['worker_exit_code'], 0)
                self.assertIsNotNone(completed['worker_exit_observed_monotonic_ns'])
                self.assertFalse(pending.request_cancel())
                changed = pending.poll()
                changed['receipt']['outcome'] = 'changed by caller'
                for _ in range(10):
                    self.assertEqual(pending.poll(), completed)
            self.assertTrue(pending.closed)
            self.assertEqual(pending.poll(), completed)

    def test_real_legacy_and_async_transport_receipts_remain_compatible(self):
        with fixture_http() as (settings, _entered, _release, requests):
            synchronous = inference.infer(settings, 'same question')
            with asynchronous.start(self.prepared(settings, 'same question')) as pending:
                asynchronous_receipt = self.terminal(pending)['receipt']
            self.assertEqual(synchronous['outcome'], 'OK')
            self.assertEqual(asynchronous_receipt['outcome'], 'OK')
            variable = {'request_id', 'started_at', 'elapsed_ns'}
            self.assertEqual({k: v for k, v in synchronous.items() if k not in variable},
                             {k: v for k, v in asynchronous_receipt.items() if k not in variable})
            self.assertEqual(requests[0], requests[1])

    def test_context_receipt_requires_exact_nontruncated_echo(self):
        from test_hosted_console import agent
        context = agent('ask', bound=True, prompt='hello')['inference_receipt']['space_context']
        original = copy.deepcopy(context)
        for changed, outcome in (({}, 'OK'), ({'truncated': True}, 'ERROR'), ({'prompt': 'lost context'}, 'ERROR')):
            with self.subTest(changed=changed), fixture_http(changed=changed) as (settings, _a, _b, _c):
                prepared = self.prepared(settings, space_context=context)
                self.assertEqual(prepared._timeout, inference.CONTEXT_TIMEOUT)
                with asynchronous.start(prepared) as pending:
                    receipt = self.terminal(pending)['receipt']
                self.assertEqual(receipt['outcome'], outcome, receipt)
                self.assertEqual(receipt['space_context'], original)

    def test_cancel_stops_only_owned_worker_and_does_not_claim_backend_cancelled(self):
        with fixture_http(blocked=True) as (settings, entered, release, _requests):
            with asynchronous.start(self.prepared(settings)) as pending:
                self.assertTrue(entered.wait(3))
                self.assertTrue(pending.request_cancel())
                result = self.terminal(pending)
                self.assertTrue(result['cancel_requested'])
                self.assertFalse(result['timed_out'])
                self.assertEqual(result['stop_reason'], 'cancel')
                self.assertEqual(result['receipt']['outcome'], 'ERROR')
                self.assertIsNone(result['receipt']['response_sha256'])
                self.assertIsNotNone(result['worker_exit_code'])
                self.assertFalse(release.is_set(), 'server work did not stop with client')
                self.assertNotIn('backend_stopped', result)
                self.assertFalse(pending.request_cancel())

    def test_accepted_cancel_then_zero_exit_never_becomes_success(self):
        with fixture_http(blocked=True) as (settings, entered, release, _requests):
            with asynchronous.start(self.prepared(settings)) as pending:
                self.assertTrue(entered.wait(3))
                with mock.patch.object(pending._process, 'terminate'):
                    self.assertTrue(pending.request_cancel())
                    release.set()
                    result = self.terminal(pending)
                self.assertEqual(result['worker_exit_code'], 0)
                self.assertTrue(result['cancel_requested'])
                self.assertEqual(result['receipt']['outcome'], 'ERROR')

    def test_timeout_observes_exit_separately_without_waiting_in_poll(self):
        with mock.patch.object(inference, 'TIMEOUT', .05):
            prepared = self.prepared()
            with self.child('import time\ntime.sleep(20)\n', prepared) as pending:
                result = self.terminal(pending)
                self.assertTrue(result['timed_out'])
                self.assertFalse(result['cancel_requested'])
                self.assertEqual(result['stop_reason'], 'timeout')
                self.assertEqual(result['receipt']['error'], 'backend-timeout')
                self.assertIsNotNone(result['worker_exit_code'])

    def test_exit_first_observed_after_deadline_cannot_create_late_success(self):
        raw = {'content': 'fixture response', 'tokens_predicted': 3, 'model': 'fixture-model'}
        output = {'response_body': json.dumps(raw), 'content': raw['content'],
                  'tokens_predicted': 3, 'backend_execution': None}
        code = ('import sys, time\ntime.sleep(.06)\nsys.stdout.buffer.write('
                + repr(inference.encoded(output)) + ')\n')
        with mock.patch.object(inference, 'TIMEOUT', .02):
            with self.child(code, self.prepared()) as pending:
                # Simulate a busy daemon that never polled while the deadline passed.
                pending._process.wait(timeout=3)
                result = self.terminal(pending)
                self.assertEqual(result['worker_exit_code'], 0)
                self.assertTrue(result['timed_out'])
                self.assertEqual(result['receipt']['error'], 'backend-timeout')
                self.assertEqual(result['receipt']['outcome'], 'ERROR')
                self.assertFalse(result['terminate_requested'])

    def test_signal_failure_retains_request_reason_until_actual_exit(self):
        with self.child('import time\ntime.sleep(20)\n') as pending:
            with mock.patch.object(pending._process, 'terminate', side_effect=OSError('fixture signal failed')):
                self.assertTrue(pending.request_cancel())
                running = pending.poll()
                self.assertTrue(running['cancel_requested'])
                self.assertEqual(running['signal_error'], 'worker-terminate')
                self.assertIsNone(running['worker_exit_code'])
                self.assertIsNone(running['receipt'])
                with mock.patch.object(asynchronous, 'TERM_SECONDS', .02):
                    pending.close()
            ended = pending.poll()
            self.assertIsNotNone(ended['worker_exit_code'])
            self.assertTrue(ended['kill_requested'])
            self.assertEqual(ended['stop_reason'], 'cancel')
            self.assertEqual(ended['signal_error'], 'worker-terminate')
            self.assertEqual(ended['receipt']['outcome'], 'ERROR')

    def test_spawn_failure_closes_input_and_consumes_start_attempt(self):
        incoming = []
        temporary_file = tempfile.TemporaryFile

        def opened():
            file = temporary_file()
            incoming.append(file)
            return file

        prepared = self.prepared()
        with mock.patch.object(asynchronous.tempfile, 'TemporaryFile', side_effect=opened), \
             mock.patch.object(asynchronous.subprocess, 'Popen', side_effect=OSError('fixture spawn failed')):
            with self.assertRaises(asynchronous.SpawnFailure) as caught:
                asynchronous.start(prepared)
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(str(caught.exception.__cause__), 'fixture spawn failed')
        evidence = caught.exception.evidence
        self.assertTrue(evidence['dispatch_attempted'])
        self.assertFalse(evidence['spawned'])
        self.assertIsNone(evidence['worker_pid'])
        self.assertIsNone(evidence['worker_exit_code'])
        self.assertIsNone(evidence['cleanup_confirmed'])
        self.assertIsNone(caught.exception.owned_worker)
        self.assertEqual(len(incoming), 1)
        self.assertTrue(incoming[0].closed)
        with mock.patch.object(asynchronous.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'request-already-started'):
                asynchronous.start(prepared)
            spawn.assert_not_called()

    def test_close_escalates_reaps_and_is_idempotent(self):
        with self.child('import time\ntime.sleep(20)\n') as pending:
            with mock.patch.object(pending._process, 'terminate'), mock.patch.object(asynchronous, 'TERM_SECONDS', .02):
                pending.close()
            result = pending.poll()
            self.assertTrue(pending.closed)
            self.assertTrue(result['terminate_requested'])
            self.assertTrue(result['kill_requested'])
            self.assertEqual(result['stop_reason'], 'close')
            self.assertEqual(result['receipt']['outcome'], 'ERROR')
            self.assertIsNotNone(result['worker_exit_code'])
            pending.close()
            self.assertEqual(pending.poll(), result)

    def test_context_manager_exception_reaps_its_worker(self):
        with self.child('import time\ntime.sleep(20)\n') as pending:
            with self.assertRaisesRegex(RuntimeError, 'caller failed'):
                with pending:
                    raise RuntimeError('caller failed')
            self.assertTrue(pending.closed)
            self.assertIsNotNone(pending.poll()['worker_exit_code'])

    def test_oversized_actual_stdout_and_stderr_are_bounded_and_rejected(self):
        for name, limit in (('stdout', asynchronous.STDOUT_LIMIT), ('stderr', asynchronous.STDERR_LIMIT)):
            code = 'import sys, time\nsys.' + name + '.buffer.write(b"x" * ' + str(limit + 8192) + ')\nsys.' + name + '.flush()\ntime.sleep(20)\n'
            with self.subTest(name=name), self.child(code) as pending:
                result = self.terminal(pending)
                drain = next(value for value in pending._drains if value.name == name)
                self.assertEqual(len(drain.snapshot()[0]), limit)
                self.assertGreater(result['output_bytes'][name], limit)
                self.assertEqual(result['io_error'], name + '-limit')
                self.assertEqual(result['receipt']['outcome'], 'ERROR')

    def test_actual_bad_worker_bytes_and_exit_cannot_create_ok_receipt(self):
        raw = {'content': 'fixture response', 'tokens_predicted': 3, 'model': 'fixture-model'}
        good = {'response_body': json.dumps(raw), 'content': raw['content'],
                'tokens_predicted': 3, 'backend_execution': None}
        variants = [(b'{}', b'', 0), (inference.encoded(good), b'warning', 0),
                    (inference.encoded(good), b'', 2),
                    (inference.encoded({**good, 'tokens_predicted': True}), b'', 0),
                    (inference.encoded({**good, 'backend_execution': {}}), b'', 0),
                    (inference.encoded({**good, 'content': 'invented'}), b'', 0)]
        for stdout, stderr, code in variants:
            script = ('import sys\nsys.stdout.buffer.write(' + repr(stdout) + ')\nsys.stderr.buffer.write('
                      + repr(stderr) + ')\nraise SystemExit(' + str(code) + ')\n')
            with self.subTest(code=code, stderr=stderr), self.child(script) as pending:
                receipt = self.terminal(pending)['receipt']
                self.assertEqual(receipt['outcome'], 'ERROR')
                self.assertIsNone(receipt['response_sha256'])
                self.assertIsNone(receipt['content'])

    def test_reader_start_failure_reaps_actual_child_and_preserves_exception(self):
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as temporary:
                script = Path(temporary) / 'wait.py'
                script.write_text('import time\ntime.sleep(20)\n')
                processes, starts = [], []
                real_start = threading.Thread.start
                original_error = RuntimeError('reader failed')

                def spawn(_argv, **kwargs):
                    process = REAL_POPEN([sys.executable, '-B', str(script)], **kwargs)
                    processes.append(process)
                    return process

                def thread_start(thread):
                    starts.append(thread)
                    if len(starts) == fail_at:
                        raise original_error
                    return real_start(thread)

                with mock.patch.object(asynchronous.subprocess, 'Popen', side_effect=spawn), \
                     mock.patch.object(asynchronous.threading.Thread, 'start', thread_start):
                    with self.assertRaises(asynchronous.SpawnFailure) as caught:
                        asynchronous.start(self.prepared())
                self.assertIs(caught.exception.__cause__, original_error)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())
                self.assertTrue(processes[0].stdout.closed and processes[0].stderr.closed)
                self.assertTrue(all(not thread.is_alive() for thread in starts))
                evidence = caught.exception.evidence
                self.assertTrue(evidence['dispatch_attempted'] and evidence['spawned'])
                self.assertEqual(evidence['worker_pid'], processes[0].pid)
                self.assertEqual(evidence['worker_exit_code'], processes[0].returncode)
                self.assertTrue(evidence['cleanup_confirmed'])
                self.assertIsNotNone(evidence['worker_exit_observed_monotonic_ns'])
                self.assertIsNone(evidence['cleanup_error'])
                changed = caught.exception.evidence
                changed['spawned'] = False
                self.assertEqual(caught.exception.evidence, evidence)
                self.assertTrue(caught.exception.owned_worker.closed)

    def test_launch_failure_before_popen_has_no_dispatch_attempt(self):
        with mock.patch.object(asynchronous.tempfile, 'TemporaryFile', side_effect=OSError('fixture input failed')), \
             mock.patch.object(asynchronous.subprocess, 'Popen') as spawn:
            with self.assertRaises(asynchronous.SpawnFailure) as caught:
                asynchronous.start(self.prepared())
            spawn.assert_not_called()
        self.assertFalse(caught.exception.evidence['dispatch_attempted'])
        self.assertFalse(caught.exception.evidence['spawned'])
        self.assertIsNone(caught.exception.evidence['cleanup_confirmed'])

    def test_failed_cleanup_keeps_owned_capability_and_original_failure_facts(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'wait.py'
            script.write_text('import time\ntime.sleep(20)\n')
            processes = []

            def spawn(_argv, **kwargs):
                process = REAL_POPEN([sys.executable, '-B', str(script)], **kwargs)
                processes.append(process)
                return process

            failure = None
            try:
                with mock.patch.object(asynchronous.subprocess, 'Popen', side_effect=spawn), \
                     mock.patch.object(asynchronous.threading.Thread, 'start', side_effect=RuntimeError('reader failed')), \
                     mock.patch.object(asynchronous.PendingInference, 'close', side_effect=OSError('cleanup failed')):
                    with self.assertRaises(asynchronous.SpawnFailure) as caught:
                        asynchronous.start(self.prepared())
                failure = caught.exception
                initial = failure.evidence
                self.assertTrue(initial['spawned'])
                self.assertFalse(initial['cleanup_confirmed'])
                self.assertEqual(initial['cleanup_error'], 'OSError')
                self.assertIsNone(initial['worker_exit_code'])
                self.assertIsNotNone(failure.owned_worker)
                self.assertIsNone(processes[0].poll())
                failure.owned_worker.close()
                self.assertIsNotNone(failure.owned_worker.poll()['worker_exit_code'])
                self.assertEqual(failure.evidence, initial)
            finally:
                if failure is not None and failure.owned_worker is not None:
                    failure.owned_worker.close()
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
                    self.assertTrue(process.stdout.closed and process.stderr.closed)


if __name__ == '__main__':
    unittest.main()
