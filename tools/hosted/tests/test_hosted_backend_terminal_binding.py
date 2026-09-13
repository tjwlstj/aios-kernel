"""Mocked Linux lifetimes with real bounded files, never live pidfd evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/hosted'))
sys.path.insert(0, str(ROOT / 'hosted/linux'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aios_agent import backend_binding as binding_module
from aios_backend import client as backend_client, daemon as backend_daemon, protocol as backend_protocol
from aios_resources import ResourceError
from aios_service import lifecycle
from test_hosted_backend_verifier import fixture, read, save


class Reader:
    def __init__(self, identity, pidfd):
        self.identity = copy.deepcopy(identity)
        self._pidfd, self._dirfd = pidfd, pidfd + 100
        self.closed = 0

    def close(self):
        self.closed += 1
        self._pidfd = self._dirfd = None


class BackendTerminalBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state, _source, runs = fixture(self.root)
        self.run = runs[0]
        self.terminal = read(self.state / 'latest.json')
        self.result_bytes = (self.run / 'result.json').read_bytes()
        (self.run / 'result.json').unlink()
        events = [json.loads(line) for line in (self.run / 'events.jsonl').read_bytes().splitlines()]
        running = next(row for row in events if row['event'] == 'RUNNING')
        self.expected = {**self.terminal, 'state': 'RUNNING',
                         'service_record': running['service_record'], 'descriptor': running['descriptor']}
        save(self.state / 'latest.json', self.expected)
        self.binding = binding_module.ExecutionBinding.__new__(binding_module.ExecutionBinding)
        self.binding._backend_dir = self.state
        self.binding._terminal_dirfd = self.binding._terminal_target = None
        self.binding.config = read(self.state / 'config.json')
        self.binding.capture_kind = 'fixture'
        self.binding.initial_proof = {'descriptor': copy.deepcopy(self.expected['descriptor'])}
        self.binding._descriptor = copy.deepcopy(self.expected['descriptor'])
        self.binding._backend = Reader(self.expected['service_record']['child_identity'], 8000)
        self.binding._launcher = Reader(self.expected['service_record']['supervisor_identity'], 8001)
        self.binding.check = mock.Mock(return_value={})
        self.dead = set()
        self.held = set()
        self.opened = []
        self.marker = self.root / 'directory-fd-fixture'
        self.marker.write_bytes(b'fixture only')
        self.connection = mock.Mock()
        self.authenticated = copy.deepcopy(self.expected)
        self.original_open, self.original_fstat = os.open, os.fstat
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.addCleanup(self.binding.close)

        def regular(path, **_kwargs):
            if not Path(path).is_file() or Path(path).is_symlink():
                raise ValueError('fixture nonregular file')

        def directory(path, **_kwargs):
            if not Path(path).is_dir() or Path(path).is_symlink():
                raise ValueError('fixture nondirectory')
            return Path(path)

        def bounded(path, limit, **_kwargs):
            regular(path)
            with Path(path).open('rb') as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError('fixture oversize')
            return data

        def opened(path, flags, *args, **kwargs):
            if Path(path) == self.state:
                fd = self.original_open(self.marker, os.O_RDONLY)
                self.held.add(fd)
                self.opened.append(fd)
                return fd
            return self.original_open(path, flags, *args, **kwargs)

        def fstat(fd):
            if fd in self.held:
                self.original_fstat(fd)  # The mock still requires a genuinely open owned handle.
                return self.state.stat()
            return self.original_fstat(fd)

        self.patches.enter_context(mock.patch.object(binding_module.os, 'O_DIRECTORY', getattr(os, 'O_DIRECTORY', 0), create=True))
        self.patches.enter_context(mock.patch.object(binding_module.os, 'O_NOFOLLOW', getattr(os, 'O_NOFOLLOW', 0), create=True))
        self.patches.enter_context(mock.patch.object(binding_module.os, 'open', side_effect=opened))
        self.patches.enter_context(mock.patch.object(binding_module.os, 'fstat', side_effect=fstat))
        self.patches.enter_context(mock.patch.object(binding_module, 'read_bounded', side_effect=bounded))
        self.patches.enter_context(mock.patch.object(lifecycle, 'prepare_directory', side_effect=directory))
        self.patches.enter_context(mock.patch.object(lifecycle, 'regular_file', side_effect=regular))
        self.patches.enter_context(mock.patch.object(backend_client, 'prepare_directory', side_effect=directory))
        self.patches.enter_context(mock.patch.object(backend_client, 'regular_file', side_effect=regular))
        self.patches.enter_context(mock.patch.object(backend_client, 'read_json', side_effect=read))
        self.patches.enter_context(mock.patch.object(backend_daemon, 'read_json', side_effect=read))
        self.connect = self.patches.enter_context(mock.patch.object(backend_protocol, 'connect',
            side_effect=lambda *_args: (self.connection, copy.deepcopy(self.authenticated),
                                        self.expected['service_record']['supervisor_identity']['process_id'])))
        self.select = self.patches.enter_context(mock.patch.object(binding_module.select, 'select',
            side_effect=lambda readable, _w, _e, _timeout: ([fd for fd in readable if fd in self.dead], [], [])))

    def pin(self):
        self.binding.bind_terminal(self.expected)

    def stopped(self):
        self.dead = {8000, 8001}
        save(self.state / 'latest.json', self.terminal)
        (self.run / 'result.json').write_bytes(self.result_bytes)

    def test_coherent_target_joins_authenticated_admission_and_terminal_hashes(self):
        self.pin()
        pinned = copy.deepcopy(self.expected)
        self.expected['descriptor']['listener_inode'] += 1
        self.stopped()
        output = self.binding.verify_stopped()
        self.assertEqual(output['backend_stop'], {**self.terminal, 'action': 'stop'})
        self.assertEqual(output['evidence']['expected'], pinned)
        self.assertEqual(output['evidence']['terminal_files']['result.json']['sha256'],
                         hashlib.sha256(self.result_bytes).hexdigest())
        for name, record in output['evidence']['artifacts'].items():
            self.assertEqual(record['sha256'], hashlib.sha256((self.run / name).read_bytes()).hexdigest())
        self.assertTrue(all(row['exited'] for row in output['evidence']['lifetimes'].values()))
        self.assertGreaterEqual(output['evidence']['read_end_ns'], output['evidence']['read_start_ns'])
        self.assertEqual(self.connect.call_count, 1, 'terminal verification must not use replacement IPC')
        self.connection.close.assert_called_once()
        output['evidence']['expected']['state'] = 'changed'
        self.assertEqual(self.binding.verify_stopped()['evidence']['expected'], pinned)
        self.assertEqual(self.binding._backend.closed, 0)
        self.assertEqual(self.binding._launcher.closed, 0)

    def test_authenticated_reply_different_from_supplied_expected_is_rejected(self):
        self.authenticated['descriptor']['listener_inode'] += 1
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-target'):
            self.pin()
        self.connection.close.assert_called_once()
        self.assertIsNone(self.binding._terminal_target)
        self.assertIsNone(self.binding._terminal_dirfd)
        for fd in self.opened:
            with self.assertRaises(OSError):
                self.original_fstat(fd)

    def test_descriptor_or_held_reader_mismatch_rejects_before_authentication(self):
        for which in ('descriptor', 'child', 'parent'):
            descriptor = copy.deepcopy(self.binding._descriptor)
            child = copy.deepcopy(self.binding._backend.identity)
            parent = copy.deepcopy(self.binding._launcher.identity)
            if which == 'descriptor':
                self.binding._descriptor['listener_inode'] += 1
            elif which == 'child':
                self.binding._backend.identity['process_start_ticks'] += 1
            else:
                self.binding._launcher.identity['process_start_ticks'] += 1
            with self.subTest(which=which), self.assertRaises(ResourceError):
                self.pin()
            self.binding._descriptor = descriptor
            self.binding._backend.identity = child
            self.binding._launcher.identity = parent
        self.connect.assert_not_called()

    def test_descriptor_only_worker_is_unsupported_and_no_readers_are_adopted(self):
        self.binding._backend_dir = None
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-unsupported'):
            self.pin()
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-unsupported'):
            self.binding.verify_stopped()
        self.connect.assert_not_called()

    def test_unbound_closed_or_replaced_reader_fails(self):
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-unbound'):
            self.binding.verify_stopped()
        self.pin()
        self.stopped()
        original = self.binding._backend
        self.binding._backend = Reader(original.identity, 8000)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-lifetime'):
            self.binding.verify_stopped()
        self.binding._backend = original
        original._pidfd = 9000
        self.dead.add(9000)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-lifetime'):
            self.binding.verify_stopped()
        original._pidfd = 8000
        original.close()
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-lifetime'):
            self.binding.verify_stopped()

    def test_one_live_process_cannot_borrow_terminal_file_claims(self):
        self.pin()
        self.stopped()
        for dead in ({8000}, {8001}, set()):
            self.dead = dead
            with self.subTest(dead=dead), self.assertRaisesRegex(ResourceError, 'backend-terminal-live'):
                self.binding.verify_stopped()
        self.assertTrue(all(call.args[-1] == 0 for call in self.select.call_args_list))

    def test_replaced_registry_bytes_and_same_bytes_new_inode_reject(self):
        self.pin()
        self.stopped()
        registry = read(self.state / 'registry.json')
        save(self.state / 'registry.json', {**registry, 'start_generation': registry['start_generation'] + 1})
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-registry'):
            self.binding.verify_stopped()
        save(self.state / 'registry.json', registry)
        replacement = self.state / 'replacement'
        replacement.write_bytes((self.state / 'registry.json').read_bytes())
        os.replace(replacement, self.state / 'registry.json')
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-registry'):
            self.binding.verify_stopped()

    def test_same_files_in_replaced_state_directory_do_not_restore_target(self):
        self.pin()
        self.stopped()
        prior_directory = self.root / 'prior-state'
        self.state.rename(prior_directory)
        shutil.copytree(prior_directory, self.state)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-directory'):
            self.binding.verify_stopped()

    def test_failed_forced_unverified_or_killed_child_rejects(self):
        self.pin()
        self.stopped()
        original = read(self.run / 'result.json')
        for changed in ({'forced': True}, {'child_exit_verified': False}, {'child_exit_code': -9},
                        {'child_exit_code': True}, {'exit_code': True}, {'state': 'FAILED', 'exit_code': 1}):
            save(self.run / 'result.json', {**original, **changed})
            with self.subTest(changed=changed), self.assertRaises(ResourceError):
                self.binding.verify_stopped()
        save(self.run / 'result.json', original)
        save(self.state / 'latest.json', {**self.terminal, 'state': 'FAILED', 'outcome': 'ERROR', 'error': 'backend-failed'})
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-result'):
            self.binding.verify_stopped()

    def test_fully_resealed_other_service_record_is_not_this_target(self):
        self.pin()
        self.stopped()
        changed = copy.deepcopy(self.terminal)
        changed['service_record']['child_identity']['process_start_ticks'] += 1
        save(self.state / 'latest.json', changed)
        save(self.run / 'service.json', changed['service_record'])
        result = read(self.run / 'result.json')
        result['service_record'] = changed['service_record']
        result['files']['service.json'] = hashlib.sha256((self.run / 'service.json').read_bytes()).hexdigest()
        save(self.run / 'result.json', result)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-result'):
            self.binding.verify_stopped()

    def test_result_descriptor_mismatch_rejected_after_other_terminal_gates(self):
        self.pin()
        self.stopped()
        result = read(self.run / 'result.json')
        result['descriptor']['listener_inode'] += 1
        save(self.run / 'result.json', result)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-result'):
            self.binding.verify_stopped()

    def test_rehashed_original_start_file_cannot_replace_admission_pin(self):
        self.pin()
        self.stopped()
        start = read(self.run / 'start.json')
        start['started_at'] = '2026-09-09T01:02:03+00:00'
        save(self.run / 'start.json', start)
        result = read(self.run / 'result.json')
        result['files']['start.json'] = hashlib.sha256((self.run / 'start.json').read_bytes()).hexdigest()
        save(self.run / 'result.json', result)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-artifact'):
            self.binding.verify_stopped()

    def test_artifact_hash_corruption_and_mid_check_result_swap_reject(self):
        self.pin()
        self.stopped()
        stdout = (self.run / 'stdout.log').read_bytes()
        (self.run / 'stdout.log').write_bytes(b'changed after result')
        with self.assertRaises(ResourceError):
            self.binding.verify_stopped()
        (self.run / 'stdout.log').write_bytes(stdout)
        original = backend_client._terminal

        def swap(*args):
            result = original(*args)
            changed = {**result, 'completed_at': '2026-09-09T00:00:00+00:00'}
            save(self.run / 'result.json', changed)
            return result

        with mock.patch.object(backend_client, '_terminal', side_effect=swap), \
             self.assertRaisesRegex(ResourceError, 'backend-terminal-artifact'):
            self.binding.verify_stopped()

    def test_latest_parsed_bytes_cannot_borrow_a_different_reported_hash(self):
        self.pin()
        self.stopped()
        original = backend_protocol.validate_reply
        swapped = False

        def replace_after_parse(value, action):
            nonlocal swapped
            parsed = original(value, action)
            if action == 'status' and value['state'] == 'STOPPED' and not swapped:
                swapped = True
                (self.state / 'latest.json').write_bytes(b'not the parsed terminal reply')
            return parsed

        with mock.patch.object(backend_protocol, 'validate_reply', side_effect=replace_after_parse), \
             self.assertRaisesRegex(ResourceError, 'backend-terminal-artifact'):
            self.binding.verify_stopped()
        self.assertTrue(swapped)

    def test_existing_terminal_at_admission_and_rebinding_are_refused(self):
        (self.run / 'result.json').write_bytes(self.result_bytes)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-target'):
            self.pin()
        (self.run / 'result.json').unlink()
        self.pin()
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-already-bound'):
            self.pin()

    def test_close_releases_both_original_readers_and_held_directory(self):
        self.pin()
        backend, launcher, descriptor = self.binding._backend, self.binding._launcher, self.binding._terminal_dirfd
        self.binding.close()
        self.binding.close()
        self.assertEqual((backend.closed, launcher.closed), (1, 1))
        with self.assertRaises(OSError):
            self.original_fstat(descriptor)
        with self.assertRaisesRegex(ResourceError, 'backend-terminal-unbound'):
            self.binding.verify_stopped()


if __name__ == '__main__':
    unittest.main()
