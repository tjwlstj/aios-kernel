"""Durability/worker integration; no live Linux backend or model acceptance."""
from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'hosted/linux'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aios_agent import async_inference, inference
from aios_agent.request_runtime import RequestExecution, RequestExecutionFailure
import test_hosted_request_state as state_fixture


class FakePending:
    def __init__(self, request_id, trace):
        self.trace = trace
        self.closed = False
        self.value = {'done': False, 'request_id': request_id, 'worker_pid': 1009,
                      'worker_exit_code': None, 'receipt': None}

    def poll(self):
        return copy.deepcopy(self.value)

    def request_cancel(self):
        self.trace.append('signal')
        return True

    def close(self):
        self.trace.append('close')
        self.closed = True


class RequestRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = state_fixture.RequestStateTests()
        self.fixture.setUp()
        self.kwargs = {k: copy.deepcopy(v) for k, v in self.fixture.kwargs.items() if k != 'accepted_ns'}
        self.trace, self.saved, self.completed = [], [], []
        self.now = 100
        self.save_failure = None
        self.completion_failure = False
        self.pending = FakePending(self.kwargs['request_id'], self.trace)
        self.spawn = mock.patch.object(async_inference, 'start', side_effect=self.start)
        self.spawn_mock = self.spawn.start()
        self.addCleanup(self.spawn.stop)

    def clock(self):
        self.now += 1
        return self.now

    def save(self, row):
        phase = row['request_state']['phase']
        self.trace.append('save:' + phase)
        if phase == self.save_failure:
            raise OSError('fixture durable failure')
        self.saved.append(copy.deepcopy(row))

    def complete(self, row):
        self.trace.append('complete')
        self.completed.append(copy.deepcopy(row))
        if self.completion_failure:
            raise OSError('fixture partial source update')

    def start(self, prepared):
        self.assertEqual(self.saved[0]['request_state']['phase'], 'ACCEPTED')
        self.assertEqual(self.saved[0]['request_state']['request_body'].encode(), prepared.request_body)
        self.trace.append('spawn')
        return self.pending

    def execution(self):
        return RequestExecution(save=self.save, complete=self.complete, clock=self.clock, **self.kwargs)

    def answer(self):
        self.pending.value.update(done=True, worker_exit_code=0, receipt=self.fixture.answer())

    def backend_observation(self):
        return {'backend_stop': self.fixture.stopped(),
                'evidence': {'expected': copy.deepcopy(self.kwargs['backend_expected']), 'fixture': True}}

    def test_admission_is_durable_before_dispatch_and_reads_do_not_write(self):
        execution = self.execution()
        self.spawn_mock.assert_not_called()
        execution.dispatch()
        self.assertEqual(self.trace, ['save:ACCEPTED', 'spawn', 'save:RUNNING'])
        before = copy.deepcopy(self.saved)
        for _ in range(10):
            row = execution.poll()
            row['request_state']['owner']['uid'] = 0
        self.assertEqual(self.saved, before)
        self.assertEqual(execution.snapshot()['request_state']['owner'], self.kwargs['owner'])

    def test_failed_admission_never_invokes_worker(self):
        self.save_failure = 'ACCEPTED'
        with self.assertRaisesRegex(RequestExecutionFailure, 'persistence'):
            self.execution()
        self.spawn_mock.assert_not_called()

    def test_cancel_before_dispatch_is_the_only_verified_not_started_case(self):
        execution = self.execution()
        result = execution.cancel(self.kwargs['owner'])
        self.assertEqual(result['record']['request_state']['model_outcome'], 'NOT_STARTED')
        self.assertEqual(self.trace, ['save:ACCEPTED', 'save:CANCEL_REQUESTED', 'save:FINISHED', 'complete'])
        execution.dispatch()
        self.spawn_mock.assert_not_called()
        self.assertTrue(execution.terminal)

    def test_cancel_persists_before_signal_and_duplicate_is_inert(self):
        execution = self.execution()
        execution.dispatch()
        execution.cancel(self.kwargs['owner'])
        before = list(self.trace)
        self.assertLess(self.trace.index('save:CANCEL_REQUESTED'), self.trace.index('signal'))
        execution.cancel(self.kwargs['owner'])
        self.assertEqual(self.trace, before)
        self.assertIsNone(execution.snapshot()['request_state']['backend_stop'])

    def test_wrong_owner_cannot_observe_completion_or_signal(self):
        execution = self.execution()
        execution.dispatch()
        self.answer()
        before = list(self.trace)
        with self.assertRaisesRegex(ValueError, 'owner-mismatch'):
            execution.cancel({**self.kwargs['owner'], 'process_start_ticks': 999})
        self.assertEqual(self.trace, before)

    def test_completion_is_saved_then_finalized_exactly_once(self):
        execution = self.execution()
        execution.dispatch()
        self.answer()
        result = execution.poll()
        self.assertEqual(result['request_state']['model_outcome'], 'ANSWERED')
        self.assertEqual(self.trace[-3:], ['save:FINISHED', 'complete', 'close'])
        before = list(self.trace)
        for _ in range(5):
            execution.poll()
        self.assertEqual(execution.cancel(self.kwargs['owner'])['outcome'], 'ALREADY_TERMINAL')
        self.assertEqual(self.trace, before)
        self.assertEqual(len(self.completed), 1)

    def test_start_exception_does_not_rewrite_admission_as_not_started(self):
        execution = self.execution()
        self.spawn_mock.side_effect = OSError('failure after possible process creation')
        with self.assertRaisesRegex(RequestExecutionFailure, 'dispatch-uncertain'):
            execution.dispatch()
        row = execution.snapshot()
        self.assertEqual(row['request_state']['phase'], 'ACCEPTED')
        self.assertIsNone(row['request_state']['model_outcome'])
        self.assertEqual(row['control_failure']['code'], 'request-dispatch-uncertain')
        with self.assertRaises(RequestExecutionFailure):
            execution.dispatch()
        self.assertEqual(self.spawn_mock.call_count, 1)

    def test_running_persistence_failure_reaps_worker_without_finalizer_retry(self):
        execution = self.execution()
        self.save_failure = 'RUNNING'
        with self.assertRaises(RequestExecutionFailure):
            execution.dispatch()
        self.assertTrue(self.pending.closed)
        self.assertFalse(self.completed)
        with self.assertRaises(RequestExecutionFailure):
            execution.poll()
        self.assertEqual(self.spawn_mock.call_count, 1)

    def test_start_failure_retains_owned_worker_and_original_cleanup_uncertainty(self):
        execution = self.execution()
        evidence = {'request_id': self.kwargs['request_id'], 'dispatch_attempted': True,
                    'spawned': True, 'worker_pid': 1009, 'worker_exit_code': None,
                    'worker_exit_observed_monotonic_ns': None,
                    'cleanup_confirmed': False, 'cleanup_error': 'RuntimeError'}
        self.spawn_mock.side_effect = async_inference.SpawnFailure(evidence, self.pending)
        with self.assertRaises(RequestExecutionFailure):
            execution.dispatch()
        failure = execution.snapshot()['control_failure']
        self.assertEqual(failure['start_evidence'], evidence)
        self.assertTrue(failure['worker_cleanup_confirmed'])
        self.assertTrue(self.pending.closed)
        self.assertIsNone(execution.snapshot()['request_state']['model_outcome'])
        self.assertFalse(self.completed)

    def test_failed_cancel_record_does_not_issue_user_cancel_or_retry(self):
        execution = self.execution()
        execution.dispatch()
        self.save_failure = 'CANCEL_REQUESTED'
        with self.assertRaises(RequestExecutionFailure):
            execution.cancel(self.kwargs['owner'])
        self.assertNotIn('signal', self.trace)
        self.assertTrue(self.pending.closed)
        before = list(self.trace)
        with self.assertRaises(RequestExecutionFailure):
            execution.cancel(self.kwargs['owner'])
        self.assertEqual(self.trace, before)

    def test_terminal_persistence_failure_does_not_increment_source(self):
        execution = self.execution()
        execution.dispatch()
        self.save_failure = 'FINISHED'
        self.answer()
        with self.assertRaises(RequestExecutionFailure):
            execution.poll()
        self.assertFalse(self.completed)
        self.assertFalse(execution.terminal)
        self.assertTrue(self.pending.closed)

    def test_partial_finalizer_failure_is_never_retried(self):
        execution = self.execution()
        execution.dispatch()
        self.completion_failure = True
        self.answer()
        with self.assertRaises(RequestExecutionFailure):
            execution.poll()
        with self.assertRaises(RequestExecutionFailure):
            execution.poll()
        self.assertEqual(len(self.completed), 1)
        self.assertFalse(execution.terminal)

    def test_independent_backend_stop_does_not_finish_worker(self):
        execution = self.execution()
        execution.dispatch()
        execution.cancel(self.kwargs['owner'])
        verify = mock.Mock(return_value=self.backend_observation())
        result = execution.observe_backend_stop(self.kwargs['owner'], verify)
        self.assertEqual(result['request_state']['phase'], 'CANCEL_REQUESTED')
        self.assertIsNone(result['request_state']['model_outcome'])
        execution.observe_backend_stop(self.kwargs['owner'], verify)
        verify.assert_called_once_with(self.kwargs['backend_expected'])
        self.assertFalse(execution.terminal)

    def test_unverified_or_replaced_backend_is_not_saved_as_stopped(self):
        execution = self.execution()
        execution.dispatch()
        execution.cancel(self.kwargs['owner'])
        before = list(self.trace)
        with self.assertRaisesRegex(OSError, 'not dead'):
            execution.observe_backend_stop(self.kwargs['owner'], mock.Mock(side_effect=OSError('not dead')))
        changed = self.backend_observation()
        changed['backend_stop']['service_record']['start_generation'] += 1
        with self.assertRaises(ValueError):
            execution.observe_backend_stop(self.kwargs['owner'], lambda _expected: changed)
        self.assertEqual(self.trace, before)
        self.assertIsNone(execution.snapshot()['request_state']['backend_stop'])

    def test_backend_observation_must_retain_exact_admission_evidence(self):
        execution = self.execution()
        execution.dispatch()
        execution.cancel(self.kwargs['owner'])
        observation = self.backend_observation()
        observation['evidence']['expected']['service_record']['start_generation'] += 1
        with self.assertRaisesRegex(ValueError, 'request-backend-observation'):
            execution.observe_backend_stop(self.kwargs['owner'], lambda _expected: observation)
        with self.assertRaisesRegex(ValueError, 'request-backend-observation'):
            execution.observe_backend_stop(self.kwargs['owner'], lambda _expected: self.fixture.stopped())
        self.assertIsNone(execution.snapshot()['backend_observation'])

    def test_single_writer_prevents_background_state_mutation(self):
        execution = self.execution()
        errors = []
        def wrong_thread():
            try:
                execution.dispatch()
            except ValueError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(2)
        self.assertEqual(errors, ['request-writer-mismatch'])
        self.spawn_mock.assert_not_called()

    def test_actual_owned_worker_cancel_is_unknown_not_backend_stop(self):
        self.spawn.stop()
        real_popen = subprocess.Popen
        owned = []
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'owned_question_fixture.py'
            script.write_text('import sys,time\nsys.stdin.buffer.read()\ntime.sleep(10)\n', encoding='utf-8')
            def spawn(argv, **kwargs):
                self.assertEqual(argv, [sys.executable, str(Path(inference.__file__).resolve()), '--worker'])
                child = real_popen([sys.executable, '-B', str(script)], **kwargs)
                owned.append(child)
                return child
            execution = self.execution()
            try:
                with mock.patch.object(async_inference.subprocess, 'Popen', side_effect=spawn):
                    execution.dispatch()
                execution.cancel(self.kwargs['owner'])
                deadline = time.monotonic() + 6
                while not execution.terminal and time.monotonic() < deadline:
                    execution.poll()
                    time.sleep(.005)
                self.assertTrue(execution.terminal)
                row = execution.snapshot()['request_state']
                self.assertEqual(row['model_outcome'], 'UNKNOWN')
                self.assertIsNone(row['backend_stop'])
                self.assertEqual(len(self.completed), 1)
            finally:
                execution.close()
                for child in owned:
                    self.assertIsNotNone(child.poll())
                    self.assertTrue(child.stdout.closed and child.stderr.closed)
