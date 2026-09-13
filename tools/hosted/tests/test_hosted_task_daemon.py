"""Daemon control-loop integration with explicit OS/worker fixture boundaries.

These tests execute the real authority, task state, finalizer and file/event
production. Socket credentials, Linux lifetimes, persistence primitives and
model execution are simulated; this is not Linux or model acceptance evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'hosted/linux'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aios_agent import async_inference, backend_binding, daemon, inference, protocol
from aios_backend import client as backend_client
import test_hosted_request_state as state_fixture

REQUEST = '00000000-0000-4000-8000-000000000015'
OTHER = '00000000-0000-4000-8000-000000000016'


class Connection:
    def __init__(self, command):
        self.command = command

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        pass

    def settimeout(self, seconds):
        pass


class TaskDaemonTests(unittest.TestCase):
    def setUp(self):
        self.fixture = state_fixture.RequestStateTests()
        self.fixture.setUp()
        self.expected = copy.deepcopy(self.fixture.expected)
        self.owner = copy.deepcopy(self.fixture.owner)
        self.replies, self.spawned, self.trace, self.bindings, self.readers = [], [], [], [], []
        self.backend_dead = self.owner_dead = False
        self.fail_save = None
        self.window_stack = mock.Mock()
        self.resources = mock.Mock()
        self.resources.begin.return_value = {'stack': self.window_stack}
        self.resources.finish.return_value = None
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def command(self, action, *, request_id=None, before=None, peer=None, drop=False):
        return {'action': action, 'request_id': request_id or (REQUEST if action in daemon.TASK_ACTIONS else None),
                'before': before, 'peer': peer or self.owner['process_id'], 'drop': drop}

    def finish_worker(self, *, answer=True):
        pending = self.spawned[-1]
        receipt = copy.deepcopy(pending.template)
        if answer:
            raw = inference.encoded({'prompt': json.loads(receipt['request_body'])['prompt'],
                'truncated': False, 'model': receipt['model_id'], 'content': 'fixture answer', 'tokens_predicted': 2}).decode()
            receipt.update(outcome='OK', error=None, response_body=raw,
                response_sha256=hashlib.sha256(raw.encode()).hexdigest(), content='fixture answer', tokens_predicted=2,
                backend_execution={'schema_version': 1, 'descriptor': self.expected['descriptor'], 'capture_kind': 'fixture',
                                   'before': {'fixture': True}, 'send': {'fixture': True}, 'after': {'fixture': True}})
        else:
            receipt.update(outcome='ERROR', error='backend-timeout')
        pending.progress.update(done=True, worker_exit_code=0 if answer else -15, receipt=receipt)

    def run_script(self, commands):
        outer = self
        commands = iter([self.command('room-discover'), self.command('room-bind'), *commands])

        class Listener:
            def bind(self, path):
                Path(path).touch()
            def listen(self, backlog):
                pass
            def settimeout(self, seconds):
                pass
            def close(self):
                pass
            def accept(self):
                try:
                    command = next(commands)
                except StopIteration as exc:
                    raise RuntimeError('fixture script exhausted before terminal state') from exc
                if command['before']:
                    command['before']()
                return Connection(command), None

        class Reader:
            def __init__(self, pid, **unused):
                self.identity = {**outer.owner, 'process_id': pid}
                self.closed = False
                outer.readers.append(self)
            def sample(self):
                if outer.owner_dead:
                    raise ValueError('process-exited')
                return self.identity
            def close(self):
                self.closed = True

        class Binding:
            def __init__(self, *unused, **kwargs):
                self.descriptor = copy.deepcopy(outer.expected['descriptor'])
                self.initial_proof = {'fixture': True}
                self.closed = False
                outer.bindings.append(self)
            def check(self):
                if outer.backend_dead:
                    raise ValueError('backend-changed')
            def bind_terminal(self, expected):
                outer.assertEqual(expected, outer.expected)
            def verify_stopped(self):
                if not outer.backend_dead:
                    raise ValueError('backend-terminal-live')
                return {'backend_stop': outer.fixture.stopped(),
                        'evidence': {'expected': outer.expected, 'fixture': True}}
            def close(self):
                self.closed = True

        class Pending:
            def __init__(self, prepared):
                self.template = prepared.receipt_template
                self.closed = False
                self.progress = {'done': False, 'request_id': prepared.request_id, 'worker_pid': 1009,
                                 'worker_exit_code': None, 'receipt': None}
            def poll(self):
                return copy.deepcopy(self.progress)
            def request_cancel(self):
                if self.progress['done']:
                    return False
                outer.trace.append('worker-cancel')
                outer.finish_worker(answer=False)
                return True
            def close(self):
                self.closed = True

        def start(prepared):
            files = list(outer.directory.glob('runs/*/tasks/*/01.json'))
            outer.assertEqual(len(files), 1)
            outer.assertEqual(json.loads(files[0].read_text())['request_state']['phase'], 'ACCEPTED')
            outer.trace.append('spawn')
            pending = Pending(prepared)
            outer.spawned.append(pending)
            return pending

        def atomic(path, value):
            if self.fail_save == path.name:
                raise OSError('fixture persistence failure')
            path.write_bytes(inference.encoded(value) + b'\n')

        def prepare(path, **unused):
            path = Path(path)
            path.mkdir(parents=True, exist_ok=True)
            return path

        def receive(connection, **unused):
            command = connection.command
            identity = json.loads((self.directory / 'registry.json').read_text())
            return {'schema_version': 6, 'source_instance': identity['source_instance'], 'action': command['action'],
                    'prompt': 'hello' if command['action'] in ('ask', 'ask-start') else None,
                    'backend_dir': None, 'request_id': command['request_id']}

        def send(connection, value):
            protocol.validate_reply(value, connection.command['action'])
            self.replies.append(copy.deepcopy(value))
            if connection.command['drop']:
                raise OSError('fixture lost reply')

        actual_read_text = Path.read_text
        def read_text(path, *args, **kwargs):
            if path.as_posix() == '/proc/sys/kernel/random/boot_id':
                return self.owner['host_boot_id']
            return actual_read_text(path, *args, **kwargs)

        with ExitStack() as stack:
            for target, name, value in (
                    (daemon, 'supported', lambda: True), (daemon, 'prepare_directory', prepare),
                    (daemon, 'lock_directory', lambda path: os.open(path / '.lock', os.O_CREAT | os.O_RDWR)),
                    (daemon, 'read_json', lambda path: json.loads(path.read_text())),
                    (daemon, 'atomic_json', atomic), (daemon, 'regular_file', lambda *a, **k: None),
                    (daemon, 'ProcessReader', Reader), (daemon, 'ResourceManager', lambda *a: self.resources),
                    (daemon, 'peer_credentials', lambda conn: (conn.command['peer'], self.owner['uid'], self.owner['uid'])),
                    (daemon.os, 'getuid', lambda: self.owner['uid']), (daemon.os, 'O_NOFOLLOW', 0),
                    (daemon.os, 'chmod', lambda *a: None), (daemon.os, 'umask', lambda *a: None),
                    (daemon.os, 'getcwd', lambda: '/fixture-workspace'),
                    (daemon.signal, 'signal', lambda *a: None), (daemon.socket, 'socket', lambda *a: Listener()),
                    (daemon.socket, 'AF_UNIX', 1),
                    (protocol, 'socket_path', lambda path, **k: path / 'agent.sock'),
                    (protocol, 'receive', receive), (protocol, 'send', send),
                    (inference, 'load_config', lambda *a, **k: copy.deepcopy(self.fixture.config)),
                    (inference, 'infer', lambda *a, **k: {'outcome': 'OK', 'request_sha256': 'a' * 64, 'response_sha256': 'b' * 64}),
                    (backend_binding, 'ExecutionBinding', Binding),
                    (backend_client, 'control', lambda *a, **k: copy.deepcopy(self.expected)),
                    (async_inference, 'start', start), (Path, 'read_text', read_text)):
                stack.enter_context(mock.patch.object(target, name, value, create=True))
            self.exit_code = daemon.serve(self.directory, self.directory / 'input.json',
                                          fixture_backend=True, backend_dir=self.directory / 'backend')
        run = next((self.directory / 'runs').iterdir())
        self.result = json.loads((run / 'result.json').read_text())
        self.events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
        self.task_files = [json.loads(path.read_text()) for path in sorted(run.glob('tasks/*/*.json'))]
        self.run = run

    def test_control_returns_during_work_and_completion_is_saved_once(self):
        self.run_script([self.command('ask-start'), self.command('status'), self.command('task-status', before=self.finish_worker),
                         self.command('task-result'), self.command('task-result'), self.command('stop')])
        self.assertEqual(self.exit_code, 0, self.result)
        accepted = next(row for row in self.replies if row['action'] == 'ask-start')
        self.assertEqual(accepted['task']['phase'], 'ACCEPTED')
        self.assertEqual(next(row for row in self.replies if row['action'] == 'status')['state'], 'RUNNING')
        results = [row['task'] for row in self.replies if row['action'] == 'task-result']
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['model_outcome'], 'ANSWERED')
        self.assertEqual(self.result['source_record']['completed_requests'], 2)
        self.assertEqual([row['request_state']['phase'] for row in self.task_files], ['ACCEPTED', 'RUNNING', 'FINISHED'])
        self.assertEqual(sum(row['event'] == 'REQUEST_RESULT' for row in self.events), 1)
        receipt = json.loads((self.run / 'requests' / (REQUEST + '.json')).read_text())
        self.assertEqual(receipt['source_before'], accepted['task']['source_before'])
        self.assertEqual(receipt['binding_generation'], accepted['task']['management_before']['binding']['generation'])
        self.resources.finish.assert_called_once()
        self.assertTrue(all(item.closed for item in self.readers + self.bindings + self.spawned))

    def test_busy_wrong_owner_and_duplicate_request_do_not_start_more_workers(self):
        self.run_script([self.command('ask-start'), self.command('ask-start', request_id=OTHER),
                         self.command('cell-deactivate'), self.command('task-result', peer=999),
                         self.command('ask-start'), self.command('task-status', before=self.finish_worker),
                         self.command('task-result'), self.command('stop')])
        self.assertEqual(self.exit_code, 0, self.result)
        errors = [row['error'] for row in self.replies if row['outcome'] == 'ERROR']
        self.assertEqual(errors, ['request-busy', 'request-busy', 'request-owner-mismatch', 'request-id-reused'])
        self.assertEqual(len(self.spawned), 1)
        busy = next(row for row in self.replies if row['request_id'] == OTHER)
        self.assertIsNone(busy['task'])

    def test_lost_acceptance_reply_can_be_resolved_without_resending(self):
        self.run_script([self.command('ask-start', drop=True), self.command('task-status', before=self.finish_worker),
                         self.command('task-result'), self.command('stop')])
        self.assertEqual(self.exit_code, 0, self.result)
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(next(row for row in self.replies if row['action'] == 'task-result')['task']['request_id'], REQUEST)

    def test_cancel_worker_and_backend_terminal_are_separate_observations(self):
        self.run_script([self.command('ask-start'), self.command('task-cancel'), self.command('task-result'),
                         self.command('task-status', before=lambda: setattr(self, 'backend_dead', True)),
                         self.command('task-result'), self.command('task-cancel'), self.command('stop')])
        self.assertEqual(self.exit_code, 0, self.result)
        results = [row['task'] for row in self.replies if row['action'] == 'task-result']
        self.assertEqual([row['model_outcome'] for row in results], ['UNKNOWN', 'UNKNOWN'])
        self.assertIsNone(results[0]['backend_stop'])
        self.assertEqual(results[1]['backend_stop']['state'], 'STOPPED')
        cancels = [row['task_control']['cancel_outcome'] for row in self.replies if row['action'] == 'task-cancel']
        self.assertEqual(cancels, ['ACCEPTED', 'ALREADY_REQUESTED'])
        self.assertEqual(self.trace.count('worker-cancel'), 1)
        self.assertEqual(sum(row['event'] == 'REQUEST_RESULT' for row in self.events), 1)

    def test_answer_after_backend_invalidation_does_not_restore_readiness(self):
        def finish():
            self.finish_worker()
            self.backend_dead = True
        self.run_script([self.command('ask-start'), self.command('task-status', before=finish),
                         self.command('task-result'), self.command('stop')])
        self.assertEqual(self.exit_code, 0, self.result)
        answer = next(row for row in self.replies if row['action'] == 'task-result')
        self.assertEqual(answer['task']['model_outcome'], 'ANSWERED')
        self.assertFalse(answer['source_record']['model_ready'])
        self.assertEqual(answer['source_record']['completed_requests'], 2)

    def test_owner_loss_ends_producer_without_a_fabricated_result(self):
        self.run_script([self.command('ask-start'), self.command('status', before=lambda: setattr(self, 'owner_dead', True))])
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(self.result['error'], 'request-owner-lost')
        self.assertFalse(any(row['event'] == 'REQUEST_RESULT' for row in self.events))
        self.assertTrue(self.spawned[0].closed)
        self.window_stack.close.assert_called_once()

    def test_terminal_write_failure_ends_producer_without_increment(self):
        def fail():
            self.finish_worker()
            self.fail_save = '03.json'
        self.run_script([self.command('ask-start'), self.command('status', before=fail)])
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(self.result['error'], 'request-persistence-failed')
        self.assertEqual(self.result['source_record']['completed_requests'], 1)
        self.assertFalse(any(row['event'] == 'REQUEST_RESULT' for row in self.events))
        self.assertTrue(self.spawned[0].closed)

    def test_late_unknown_cancel_does_not_repeat_worker_finalization(self):
        self.run_script([self.command('ask-start'),
                         self.command('status', before=lambda: self.finish_worker(answer=False)),
                         self.command('task-result'), self.command('task-cancel'),
                         self.command('task-cancel'), self.command('stop')])
        self.assertEqual(self.exit_code, 0, self.result)
        cancels = [row for row in self.replies if row['action'] == 'task-cancel']
        self.assertEqual([row['task_control']['cancel_outcome'] for row in cancels], ['ACCEPTED', 'ALREADY_REQUESTED'])
        self.assertEqual(cancels[0]['task']['phase'], 'FINISHED')
        self.assertEqual(cancels[0]['task']['model_outcome'], 'UNKNOWN')
        self.assertNotIn('worker-cancel', self.trace)
        self.resources.finish.assert_called_once()

    def test_legacy_sync_ask_is_refused_before_inference(self):
        self.run_script([self.command('ask'), self.command('stop')])
        self.assertEqual(self.exit_code, 0, self.result)
        rejected = next(row for row in self.replies if row['action'] == 'ask')
        self.assertEqual(rejected['error'], 'request-task-required')
        self.assertFalse(self.spawned)
        self.assertEqual(self.result['source_record']['completed_requests'], 1)


if __name__ == '__main__':
    unittest.main()
