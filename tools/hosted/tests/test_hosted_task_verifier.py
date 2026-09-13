"""Task replay fixtures; no worker, backend, model, or VM is executed."""
from __future__ import annotations

import ast
import copy
import json
import unittest
import uuid
from pathlib import Path

import test_hosted_space_verifier as space_fixture
from test_hosted_agent_verifier import encoded
from test_hosted_backend_execution import execution_fixture
from test_hosted_console import backend
from test_hosted_resources_management import proof_fixture, sample_fixture
from aios_agent import inference
from aios_agent.request_state import RequestState
from newagent_output_contract import (REQUEST_CONTEXT, SPACE_RECEIPT_KEYS, agent_result, render_agent)
from task_output_contract import (validate_task_envelope, validate_task_state, validate_task_transition)
from verify_agent import (TASK_SOURCES, digest, verify_run, verify_resource_delivery,
                          verify_task_backends, verify_task_delivery)
from verify_console import SCHEMA_SOURCES, VERSIONS, source_version


class TaskVerifierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = space_fixture.SpaceVerifierTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = f = self.fixture.f
        f.config['endpoint'] = 'http://127.0.0.1:18081'
        f.start['config_sha256'] = digest(encoded(f.config))
        proof = proof_fixture()
        descriptor = proof['descriptor']
        descriptor.update({key: f.config[key] for key in ('model_id', 'model_sha256', 'backend_sha256', 'endpoint')})
        self.expected = backend()
        record = self.expected['service_record']
        record.update(supervisor_identity={'host_boot_id': descriptor['host_boot_id'], 'process_id': 233,
                      'process_start_ticks': 221, 'uid': 1000},
                      child_identity={'host_boot_id': descriptor['host_boot_id'], 'process_id': 234,
                      'process_start_ticks': 222, 'uid': 1000},
                      backend_source_instance=descriptor['source_instance'],
                      config_sha256=digest(encoded(f.config)))
        self.expected['descriptor'] = copy.deepcopy(descriptor)
        self.fixture.fixture.binding = {'schema_version': 1, 'capture_kind': 'fixture',
                                       'initial_proof': proof, 'descriptor': copy.deepcopy(descriptor)}

        def execution(offset, worker):
            value = execution_fixture()
            value['descriptor'] = copy.deepcopy(descriptor)
            def shift(item):
                if type(item) is dict:
                    for key, child in item.items():
                        if key in ('read_start_ns', 'read_end_ns'):
                            item[key] += offset
                        else:
                            shift(child)
            shift(value)
            client = value['send']['client']
            client['process_id'] = worker
            client['raw_stat'] = client['raw_stat'].replace('456 (', str(worker) + '(', 1)
            # Keep the Linux stat delimiter and exact copied PID/parent bytes.
            client['raw_stat'] = client['raw_stat'].replace(str(worker) + '(', str(worker) + ' (', 1)
            head, tail = client['raw_stat'].rsplit(') ', 1)
            fields = tail.split()
            fields[1] = str(f.source['process_id'])
            client['raw_stat'] = head + ') ' + ' '.join(fields) + '\n'
            client['raw_status'] = client['raw_status'].replace('456', str(worker))
            return value
        f.warmup.update(backend_execution=execution(0, 456), elapsed_ns=10000)
        f.user.update(backend_execution=execution(5_000_000_000, 1009), elapsed_ns=10000)
        self.owner = {'host_boot_id': descriptor['host_boot_id'], 'process_id': 303,
                      'process_start_ticks': 102, 'uid': 1000}
        self.state = RequestState(request_id=f.user['request_id'], owner=self.owner,
            config=f.config, source=f.user['source_before'], management=f.events[3]['management_snapshot'],
            space_context=f.user['space_context'], prompt=f.user['user_prompt'], backend_expected=self.expected,
            accepted_ns=4_000_000_005)
        self.template = inference._new_receipt(f.config, f.user['user_prompt'],
            f.user['request_body'].encode(), space_context=f.user['space_context'], request_id=f.user['request_id'])
        self.template['started_at'] = f.user['started_at']
        self.envelopes = [self.envelope()]
        self.state.mark_running(1009, now_ns=4_000_000_007)
        self.envelopes.append(self.envelope())
        self.base_receipt = {key: copy.deepcopy(f.user[key]) for key in SPACE_RECEIPT_KEYS}
        self.state.record_worker_exit(1009, 0, receipt=self.base_receipt, now_ns=6_000_000_000)
        self.envelopes.append(self.envelope())
        old = copy.deepcopy(f.events)
        f.events = old[:4]
        for index, envelope in enumerate(self.envelopes):
            row = copy.deepcopy(old[3])
            row.update(event='REQUEST', action=None, receipt_file=None, space_file=None,
                       monotonic_ns=envelope['request_state']['updated_ns'] + 1,
                       task_file=self.path(index))
            f.events.append(row)
        result_event = next(row for row in old if row['action'] == 'ask')
        result_event.update(event='REQUEST_RESULT', action=None, monotonic_ns=6_000_000_002,
                            space_file=None, task_file=self.path(2))
        f.events.append(result_event)
        for index, row in enumerate(old[-2:], 7):
            row['monotonic_ns'] = index * 1_000_000_000
            f.events.append(row)
        for index, row in enumerate(f.events, 1):
            row.update(schema_version=6, sequence=index)
            row.setdefault('task_file', None)
        f.start['schema_version'] = f.result['schema_version'] = 6
        for name in TASK_SOURCES:
            path = f.sources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(b'# Explicit Task fixture source\n')
        f.start['source_hashes'] = {name: digest((f.sources / name).read_bytes()) for name in TASK_SOURCES}
        (f.run / self.fixture.space_path).unlink()
        self.seal()

    def path(self, index):
        return 'tasks/' + self.f.user['request_id'] + '/' + format(index + 1, '02d') + '.json'

    def envelope(self):
        row = self.state.snapshot()
        worker = None
        if row['worker_process_id'] is not None:
            done = row['phase'] == 'FINISHED'
            worker = {'request_id': row['request_id'], 'done': done, 'worker_pid': row['worker_process_id'],
                'worker_exit_code': row['worker_exit_code'],
                'worker_exit_observed_monotonic_ns': row['finished_ns'] - 1 if done else None,
                'cancel_requested': False, 'timed_out': False, 'stop_reason': None, 'terminate_requested': False,
                'kill_requested': False, 'signal_error': None, 'io_error': None,
                'output_bytes': {'stdout': 100 if done else 0, 'stderr': 0},
                'receipt': row['inference_receipt']}
        return {'schema_version': 1, 'request_state': row, 'receipt_template': copy.deepcopy(self.template),
                'worker_progress': worker, 'control_failure': None, 'backend_observation': None}

    def seal(self):
        """Only serialize the chosen fixture; never repair hostile semantics."""
        f = self.f
        self.fixture.fixture.save()
        for index, value in enumerate(self.envelopes):
            path = f.run / self.path(index)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded(value))
            f.result['files'][self.path(index)] = digest(path.read_bytes())
        (f.run / 'result.json').write_bytes(encoded(f.result))

    def check(self, *, outcome='PASS', reason=None):
        value = verify_run(self.f.run, self.f.sources)
        self.assertEqual(value['outcome'], outcome, value)
        if reason:
            self.assertIn(reason, value['reasons'][0])
        return value

    def test_task_run_exact_revision_result_and_receipt_join(self):
        value = self.check()
        self.assertEqual(len(value['tasks']), 1)
        self.assertEqual(len(value['requests']), 1)
        self.assertFalse(value['process_exit_verified'])
        self.assertFalse(value['model_bytes_verified'])
        self.assertEqual(verify_run(self.f.run, self.f.sources, require_live=True)['outcome'], 'FAIL')

    def test_public_task6_requires_cli10_and_exact_command_uuid(self):
        row = self.envelopes[0]['request_state']
        value = {'schema_version': 6, 'outcome': 'OK', 'error': None, 'action': 'ask-start', 'state': 'RUNNING',
                 'service_kind': 'AI_SERVICE', 'source_record': row['source_before'],
                 'management_snapshot': row['management_before'], 'management_outcome': None,
                 'inference_receipt': None, 'resource_actions': 'UNSUPPORTED', 'capture_kind': 'fixture',
                 'resource_result': None, 'space_context': None, 'request_id': row['request_id'],
                 'task': row, 'task_control': None}
        command = {'name': 'ask', 'args': [row['user_prompt']], 'outcome': 'OK', 'result': value}
        agent_result(command, 10)
        self.assertIn('Task accepted:', render_agent(command, console_version='0.10.0'))
        for schema in (7, 8, 9):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                agent_result(command, schema)
        value['request_id'] = '00000000-0000-4000-8000-000000000099'
        with self.assertRaisesRegex(ValueError, 'task_reply_id'):
            agent_result(command, 10)

    def test_non_task_reply_cannot_smuggle_task_payload(self):
        row = self.envelopes[0]['request_state']
        value = {'schema_version': 6, 'outcome': 'OK', 'error': None, 'action': 'status', 'state': 'RUNNING',
                 'service_kind': 'AI_SERVICE', 'source_record': row['source_before'],
                 'management_snapshot': row['management_before'], 'management_outcome': None,
                 'inference_receipt': None, 'resource_actions': 'UNSUPPORTED', 'capture_kind': 'fixture',
                 'resource_result': None, 'space_context': None, 'request_id': None, 'task': row, 'task_control': None}
        with self.assertRaisesRegex(ValueError, 'unexpected_task_payload'):
            agent_result({'name': 'agent', 'args': ['status'], 'outcome': 'OK', 'result': value}, 10)

    def test_task_revision_or_admission_rewrite_rejected_after_rehash(self):
        original = copy.deepcopy(self.envelopes)
        for field, value in (('revision', 4), ('owner', {**self.owner, 'process_start_ticks': 999})):
            self.envelopes = copy.deepcopy(original)
            self.envelopes[1]['request_state'][field] = value
            self.seal()
            with self.subTest(field=field):
                self.check(outcome='FAIL')

    def test_query_does_not_justify_extra_record_or_duplicate_finalization(self):
        self.f.events.insert(-2, copy.deepcopy(self.f.events[-3]))
        for index, row in enumerate(self.f.events, 1):
            row['sequence'] = index
        self.seal()
        self.check(outcome='FAIL', reason='task_result_once')

    def test_missing_or_extra_task_file_rejected(self):
        (self.f.run / self.path(1)).unlink()
        self.check(outcome='FAIL', reason='artifact_file:')
        self.seal()
        (self.f.run / 'tasks' / self.f.user['request_id'] / '04.json').write_bytes(b'{}\n')
        self.check(outcome='FAIL', reason='unaccounted_task_revisions')

    def test_plain_worker_exit_or_backend_claim_cannot_be_an_answer(self):
        row = copy.deepcopy(self.envelopes[-1]['request_state'])
        row['inference_receipt'] = None
        with self.assertRaisesRegex(ValueError, 'task_model_outcome'):
            validate_task_state(row, config=self.f.config)
        row = copy.deepcopy(self.envelopes[-1]['request_state'])
        row.update(model_outcome='NOT_STARTED', inference_receipt=None)
        with self.assertRaisesRegex(ValueError, 'task_not_started'):
            validate_task_state(row, config=self.f.config)

    def test_answer_rejects_contrary_worker_evidence_after_rehash(self):
        original = copy.deepcopy(self.envelopes)
        mutations = [
            ('timed_out', True), ('terminate_requested', True), ('kill_requested', True),
            ('stop_reason', 'cancel'), ('stop_reason', 'timeout'),
            ('stop_reason', 'io-failed'), ('stop_reason', 'close'),
            ('signal_error', 'worker-terminate'), ('io_error', 'stdout-limit'),
            ('output_bytes', {'stdout': 0, 'stderr': 0}),
            ('output_bytes', {'stdout': 128 * 1024 + 1, 'stderr': 0}),
            ('output_bytes', {'stdout': 100, 'stderr': 1}),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                self.envelopes = copy.deepcopy(original)
                self.envelopes[-1]['worker_progress'][field] = value
                self.seal()
                self.check(outcome='FAIL', reason='task_worker_answer_evidence')

    def test_durable_failure_and_worker_receipt_disagreement_reject(self):
        value = copy.deepcopy(self.envelopes[-1])
        value['control_failure'] = {'code': 'request-persistence-failed'}
        with self.assertRaisesRegex(ValueError, 'task_envelope_failure'):
            validate_task_envelope(value, config=self.f.config)
        value = copy.deepcopy(self.envelopes[-1])
        value['worker_progress']['receipt']['request_id'] = '00000000-0000-4000-8000-000000000099'
        with self.assertRaises(ValueError):
            validate_task_envelope(value, config=self.f.config)

    def test_terminal_answer_cannot_be_rewritten_by_late_cancel(self):
        previous = self.envelopes[-1]['request_state']
        changed = {**previous, 'revision': 4, 'updated_ns': previous['updated_ns'] + 1,
                   'cancel_requested_ns': previous['updated_ns'] + 1}
        with self.assertRaises(ValueError):
            validate_task_state(changed, config=self.f.config)
        with self.assertRaisesRegex(ValueError, 'task_transition'):
            validate_task_transition(previous, changed)

    def test_main6_source_set_cannot_drop_async_code_or_relabel_main5(self):
        self.f.start['source_hashes'].pop('aios_agent/async_inference.py')
        self.seal()
        self.check(outcome='FAIL', reason='agent_sources')

    def test_versions_keep_old_tuples_and_exact_runtime_literal(self):
        self.assertEqual((len(TASK_SOURCES), len(SCHEMA_SOURCES[10])), (28, 35))
        self.assertEqual((len(SCHEMA_SOURCES[8]), len(SCHEMA_SOURCES[9])), (32, 32))
        self.assertEqual(VERSIONS[10], '0.10.0')
        self.assertEqual(source_version(b'"""Task runtime."""\nVERSION = "0.10.0"\n'), VERSIONS[10])
        with self.assertRaises(ValueError):
            source_version(b'VERSION = "0.10.0"\nexec("pass")\n')
        contract = Path(__file__).resolve().parents[1] / 'task_output_contract.py'
        modules = [node.module for node in ast.walk(ast.parse(contract.read_text()))
                   if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith('aios_') for name in modules))

    def test_fast_worker_terminal_progress_precedes_state_finalization(self):
        final_progress = copy.deepcopy(self.envelopes[-1]['worker_progress'])
        running = self.envelopes[1]
        running['request_state'].update(started_ns=6_000_000_001, updated_ns=6_000_000_001)
        running['worker_progress'] = final_progress
        final = self.envelopes[-1]['request_state']
        final.update(started_ns=6_000_000_001, finished_ns=6_000_000_003, updated_ns=6_000_000_003)
        self.f.events[5]['monotonic_ns'] = 6_000_000_002
        self.f.events[6]['monotonic_ns'] = 6_000_000_004
        self.f.events[7]['monotonic_ns'] = 6_000_000_005
        self.seal()
        self.check()
        self.envelopes[-1]['worker_progress']['output_bytes']['stdout'] += 1
        self.seal()
        self.check(outcome='FAIL', reason='task_terminal_progress_rewrite')

    def make_unknown(self):
        error_receipt = {**copy.deepcopy(self.template), 'error': 'backend-failed', 'elapsed_ns': 10000}
        row = copy.deepcopy(self.envelopes[-1]['request_state'])
        row.update(model_outcome='UNKNOWN', worker_exit_code=-15, inference_receipt=error_receipt)
        value = copy.deepcopy(self.envelopes[-1])
        value['request_state'] = row
        value['worker_progress'].update(worker_exit_code=-15, receipt=error_receipt)
        return value

    def test_unknown_allows_one_late_cancel_without_erasing_worker_result(self):
        previous = self.make_unknown()
        current = copy.deepcopy(previous)
        row = current['request_state']
        row.update(revision=4, updated_ns=row['finished_ns'] + 1, cancel_requested_ns=row['finished_ns'] + 1)
        validate_task_envelope(previous, config=self.f.config)
        validate_task_envelope(current, config=self.f.config)
        validate_task_transition(previous['request_state'], row)
        self.assertEqual((row['phase'], row['model_outcome'], row['worker_exit_code']), ('FINISHED', 'UNKNOWN', -15))
        changed = copy.deepcopy(row)
        changed.update(phase='CANCEL_REQUESTED')
        with self.assertRaises(ValueError):
            validate_task_state(changed, config=self.f.config)

    def test_cancel_before_dispatch_has_no_receipt_or_worker(self):
        previous = copy.deepcopy(self.envelopes[0])
        cancel = copy.deepcopy(previous)
        cancel['request_state'].update(revision=2, phase='CANCEL_REQUESTED',
            updated_ns=4_000_000_006, cancel_requested_ns=4_000_000_006)
        finished = copy.deepcopy(cancel)
        finished['request_state'].update(revision=3, phase='FINISHED', model_outcome='NOT_STARTED',
            updated_ns=4_000_000_007, finished_ns=4_000_000_007, not_started_reason='cancel-before-dispatch')
        for value in (previous, cancel, finished):
            validate_task_envelope(value, config=self.f.config)
        validate_task_transition(previous['request_state'], cancel['request_state'])
        validate_task_transition(cancel['request_state'], finished['request_state'])
        finished['request_state']['worker_process_id'] = 1009
        with self.assertRaises(ValueError):
            validate_task_envelope(finished, config=self.f.config)

    def test_fully_rehashed_result_cannot_change_admission_source(self):
        self.f.user['source_before']['completed_requests'] += 1
        self.seal()
        self.check(outcome='FAIL', reason='task_result_admission')

    def test_backend_stop_typed_wrapper_joins_raw_terminal_files(self):
        """The backend lifetime is an explicit prerequisite fixture here."""
        from verify_backend import FILES
        from task_output_contract import validate_task_backend_observation
        value = self.make_unknown()
        row = value['request_state']
        row.update(revision=5, updated_ns=6_000_000_010, cancel_requested_ns=6_000_000_001,
                   backend_stopped_ns=6_000_000_010)
        stopped = {**copy.deepcopy(self.expected), 'action': 'stop', 'state': 'STOPPED', 'descriptor': None}
        stopped['service_record'].update(lifecycle_state='exited', backend_ready=False)
        row['backend_stop'] = stopped
        directory = self.f.root / 'backend-join'
        run = directory / 'model-backend/runs' / self.expected['service_record']['instance_id']
        run.mkdir(parents=True)
        files = {}
        for name in FILES:
            raw = ('explicit terminal fixture: ' + name).encode()
            (run / name).write_bytes(raw)
            files[name] = {'sha256': digest(raw), 'bytes': len(raw)}
        result = {'files': {name: item['sha256'] for name, item in files.items()}}
        (run / 'result.json').write_bytes(encoded(result))
        registry = {'schema_version': 1, **{name: self.expected['service_record'][name]
                    for name in ('service_id', 'instance_id', 'start_generation')}}
        latest = {**stopped, 'action': 'status'}
        terminal = {name: {'sha256': digest(raw), 'bytes': len(raw)} for name, raw in
                    [('registry.json', encoded(registry)), ('latest.json', encoded(latest)),
                     ('result.json', (run / 'result.json').read_bytes())]}
        evidence = {'schema_version': 1, 'expected': self.expected, 'read_start_ns': 6_000_000_002,
            'read_end_ns': 6_000_000_009, 'state_directory': {'st_dev': 1, 'st_ino': 2, 'st_uid': 1000, 'st_mode': 0o40700},
            'registry': registry, 'terminal_files': terminal, 'artifacts': files, 'lifetimes': {
                name: {'identity': self.expected['service_record'][key], 'exited': True,
                       'observed_monotonic_ns': 6_000_000_003}
                for name, key in [('backend', 'child_identity'), ('launcher', 'supervisor_identity')]}}
        observation = {'backend_stop': stopped, 'evidence': evidence}
        value['backend_observation'] = observation
        validate_task_envelope(value, config=self.f.config)
        model = {'state': 'STOPPED', 'descriptor': self.expected['descriptor'],
                 'service_record': stopped['service_record'], 'result': result, 'events': [
                     {}, {}, {'monotonic_ns': 1, 'service_record': self.expected['service_record']},
                     {'monotonic_ns': 6_000_000_000}, {'monotonic_ns': 6_000_000_001}]}
        runs = [{'tasks': {row['request_id']: {'revisions': [self.envelopes[0], value]}}}]
        verify_task_backends(directory, runs, {'managed_runs': [model]})
        early_stop = copy.deepcopy(model)
        early_stop['events'][3]['monotonic_ns'] = self.envelopes[0]['request_state']['accepted_ns'] - 1
        with self.assertRaisesRegex(ValueError, 'task_backend_admission_time'):
            verify_task_backends(directory, runs, {'managed_runs': [early_stop]})
        for path, change in [('lifetimes', lambda e: e['lifetimes']['backend'].update(exited=False)),
                             ('expected', lambda e: e['expected']['service_record'].update(start_generation=2))]:
            changed = copy.deepcopy(observation)
            change(changed['evidence'])
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_task_backend_observation(changed, row)
        (run / 'stderr.log').write_bytes(b'replaced even if other records stay valid')
        with self.assertRaisesRegex(ValueError, 'task_backend_artifact'):
            verify_task_backends(directory, runs, {'managed_runs': [model]})

    def test_backend_invalidation_before_answer_keeps_model_not_ready(self):
        from aios_management.binding import Authority
        from newagent_output_contract import STATE_KEYS
        f = self.f
        authority = Authority.from_state({key: f.events[3]['management_snapshot'][key] for key in STATE_KEYS})
        changed = {**f.user['source_before'], 'source_generation': 2, 'model_ready': False}
        authority.observe(changed)
        invalidated = copy.deepcopy(f.events[5])
        invalidated.update(event='BACKEND_INVALIDATED', task_file=None, outcome='ERROR', error='backend-changed',
                           source_record=copy.deepcopy(changed), management_snapshot=authority.snapshot(),
                           monotonic_ns=5_000_000_000)
        f.events.insert(6, invalidated)
        f.events[7].update(source_record=copy.deepcopy(changed), management_snapshot=authority.snapshot())
        changed['completed_requests'] += 1
        authority.observe(changed)
        f.events[8].update(source_record=copy.deepcopy(changed), management_snapshot=authority.snapshot())
        f.user['source_after'] = copy.deepcopy(changed)
        changed.update(source_generation=3, lifecycle_state='exited')
        authority.observe(changed)
        f.source = copy.deepcopy(changed)
        f.authority = authority
        f.result.update(source_record=f.source, management_snapshot=authority.snapshot())
        for event in f.events[-2:]:
            event.update(source_record=copy.deepcopy(changed), management_snapshot=authority.snapshot())
        for index, event in enumerate(f.events, 1):
            event['sequence'] = index
        self.seal()
        value = self.check()
        self.assertFalse(value['source_record']['model_ready'])
        self.assertEqual(value['source_record']['completed_requests'], 2)

    def test_repeated_queries_join_same_durable_revision_and_cli_owner(self):
        run = self.check()
        def command(name, row, args):
            return {'name': name, 'args': args, 'outcome': 'OK', 'result': {
                'schema_version': 6, 'outcome': 'OK', 'task': row, 'request_id': row['request_id']}}
        accepted, finished = (copy.deepcopy(self.envelopes[index]['request_state']) for index in (0, -1))
        commands = [command('ask', accepted, [accepted['user_prompt']]),
                    command('task', finished, ['status', finished['request_id']]),
                    command('task', finished, ['result', finished['request_id']]),
                    command('task', finished, ['result', finished['request_id']])]
        owner = sample_fixture(303, 102, at=1000)
        verify_task_delivery([run], commands, owner)
        invalid = {'name': 'task', 'args': ['status', 'bad-uuid'], 'outcome': 'ERROR', 'result': {
            'error': 'invalid_arguments', 'message': 'Task requires a canonical nonzero UUID.'}}
        verify_task_delivery([run], [invalid, *commands], owner)
        invalid['result']['error'] = 'fabricated-protocol-error'
        with self.assertRaisesRegex(ValueError, 'task_delivery_protocol'):
            verify_task_delivery([run], [invalid, *commands], owner)
        wrong = sample_fixture(303, 103, at=1000)
        with self.assertRaisesRegex(ValueError, 'task_console_owner'):
            verify_task_delivery([run], commands, wrong)
        altered = copy.deepcopy(commands)
        altered[-1]['result']['task']['updated_ns'] += 1
        with self.assertRaisesRegex(ValueError, 'task_delivery_revision'):
            verify_task_delivery([run], altered, owner)
        with self.assertRaisesRegex(ValueError, 'task_admission_accounting'):
            verify_task_delivery([run], [commands[0], commands[0], *commands[1:]], owner)
        with self.assertRaisesRegex(ValueError, 'task_delivery_orphan'):
            verify_task_delivery([run], commands[1:], owner)
        rolled_back = [*commands, command('task', self.envelopes[1]['request_state'],
                                         ['status', finished['request_id']])]
        with self.assertRaisesRegex(ValueError, 'task_delivery_rollback'):
            verify_task_delivery([run], rolled_back, owner)
        lost = copy.deepcopy(commands)
        lost[0].update(outcome='ERROR')
        lost[0]['result'].update(outcome='ERROR', error='rpc-timeout', task=None)
        prior = {'name': 'agent', 'args': ['status'], 'outcome': 'OK', 'result': {
            'schema_version': 6, 'state': 'RUNNING', 'outcome': 'OK',
            'source_record': copy.deepcopy(accepted['source_before'])}}
        verify_task_delivery([run], [prior, *lost], owner)
        with self.assertRaisesRegex(ValueError, 'task_uncertain_main_instance'):
            verify_task_delivery([run], lost, owner)
        for key in ('source_instance', 'service_start_generation', 'process_id'):
            replaced = copy.deepcopy(prior)
            replaced['result']['source_record'][key] = (str(uuid.uuid4()) if key == 'source_instance'
                                                       else accepted['source_before'][key] + 1)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'task_uncertain_main_instance'):
                verify_task_delivery([run], [replaced, *lost], owner)
        failed_restart = {'name': 'agent', 'args': ['restart'], 'outcome': 'ERROR', 'result': {
            'schema_version': 6, 'state': 'FAILED', 'outcome': 'ERROR', 'source_record': None}}
        with self.assertRaisesRegex(ValueError, 'task_uncertain_main_instance'):
            verify_task_delivery([run], [prior, failed_restart, *lost], owner)
        for error in ('request-busy', 'prompt-invalid', 'request-not-found', 'request-task-required',
                      'request-owner-mismatch'):
            lost[0]['result']['error'] = error
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, 'task_refusal_admitted'):
                verify_task_delivery([run], [prior, *lost], owner)

    def test_active_task_rejects_otherwise_valid_management_command(self):
        event = copy.deepcopy(self.f.events[3])
        event.update(monotonic_ns=4_000_000_008)
        self.f.events.insert(6, event)
        for index, row in enumerate(self.f.events, 1):
            row['sequence'] = index
        self.seal()
        self.check(outcome='FAIL', reason='task_busy_mutation')

    def cancel_delivery_fixture(self):
        # The delivery seam consumes independently validated revisions. Keep
        # late UNKNOWN cancellation separate from worker/receipt finalization.
        unknown = self.make_unknown()
        cancelled = copy.deepcopy(unknown)
        row = cancelled['request_state']
        row.update(revision=4, updated_ns=row['finished_ns'] + 1,
                   cancel_requested_ns=row['finished_ns'] + 1)
        revisions = [*copy.deepcopy(self.envelopes[:2]), unknown, cancelled]
        for envelope in revisions:
            validate_task_envelope(envelope, config=self.f.config)
        for before, after in zip(revisions, revisions[1:]):
            validate_task_transition(before['request_state'], after['request_state'])
        task_id = row['request_id']
        run = {'tasks': {task_id: {'revisions': revisions}}}

        def command(name, state, args, cancel_outcome=None):
            return {'name': name, 'args': args, 'outcome': 'OK', 'result': {
                'schema_version': 6, 'state': 'RUNNING', 'outcome': 'OK',
                'source_record': copy.deepcopy(state['source_before']),
                'task': copy.deepcopy(state), 'request_id': task_id,
                'task_control': None if cancel_outcome is None else {
                    'cancel_outcome': cancel_outcome, 'backend_stop_attempt': None}}}

        accepted = revisions[0]['request_state']
        prefix = [command('ask', accepted, [accepted['user_prompt']]),
                  command('task', unknown['request_state'], ['result', task_id])]
        cancel = command('task', row, ['cancel', task_id], 'ACCEPTED')
        repeated = command('task', row, ['cancel', task_id], 'ALREADY_REQUESTED')
        query = command('task', row, ['status', task_id])
        return [run], prefix, cancel, repeated, query, sample_fixture(303, 102, at=1000)

    def test_cancel_delivery_requires_one_accounted_admission(self):
        runs, prefix, cancel, repeated, query, owner = self.cancel_delivery_fixture()
        verify_task_delivery(runs, [*prefix, cancel, repeated, query], owner)
        for tail in ([], [query], [repeated]):
            with self.subTest(tail=tail), self.assertRaisesRegex(ValueError, 'task_cancel_missing_admission'):
                verify_task_delivery(runs, [*prefix, *tail], owner)
        with self.assertRaisesRegex(ValueError, 'task_cancel_duplicate_acceptance'):
            verify_task_delivery(runs, [*prefix, cancel, copy.deepcopy(cancel), query], owner)
        terminal = copy.deepcopy(repeated)
        terminal['result']['task_control']['cancel_outcome'] = 'ALREADY_TERMINAL'
        with self.assertRaisesRegex(ValueError, 'task_cancel_terminal_outcome'):
            verify_task_delivery(runs, [*prefix, cancel, terminal], owner)

    def test_lost_cancel_reply_preserves_uncertainty_and_allows_repeated_queries(self):
        runs, prefix, cancel, repeated, query, owner = self.cancel_delivery_fixture()
        for error in ('rpc-timeout', 'protocol-error', 'state-io'):
            lost = copy.deepcopy(cancel)
            lost.update(outcome='ERROR')
            lost['result'].update(state='FAILED', outcome='ERROR', error=error,
                                  source_record=None, task=None, task_control=None)
            with self.subTest(error=error):
                verify_task_delivery(runs, [*prefix, lost, repeated, query], owner)
                # The first uncertain RPC may have failed before sending.
                verify_task_delivery(runs, [*prefix, lost, cancel, query], owner)
                with self.assertRaisesRegex(ValueError, 'task_cancel_duplicate_acceptance'):
                    verify_task_delivery(runs, [*prefix, lost, query, cancel], owner)
                replaced = {'name': 'agent', 'args': ['status'], 'outcome': 'OK', 'result': {
                    'schema_version': 6, 'state': 'RUNNING', 'outcome': 'OK', 'source_record': {
                        **prefix[-1]['result']['source_record'], 'source_instance': str(uuid.uuid4())}}}
                with self.assertRaisesRegex(ValueError, 'task_uncertain_cancel_instance'):
                    verify_task_delivery(runs, [*prefix, replaced, lost, query], owner)
        lost['result']['error'] = 'request-owner-mismatch'
        with self.assertRaisesRegex(ValueError, 'task_cancel_missing_admission'):
            verify_task_delivery(runs, [*prefix, lost, query], owner)

    def test_interrupt_during_cancel_keeps_exact_uuid_but_invalid_input_does_not(self):
        runs, prefix, cancel, repeated, query, owner = self.cancel_delivery_fixture()
        interrupted = {'name': 'task', 'args': cancel['args'], 'outcome': 'ERROR',
                       'result': {'error': 'interrupted', 'message': 'Command interrupted.'}}
        verify_task_delivery(runs, [*prefix, interrupted, repeated, query], owner)
        interrupted['result']['error'] = 'invalid_arguments'
        with self.assertRaisesRegex(ValueError, 'task_cancel_missing_admission'):
            verify_task_delivery(runs, [*prefix, interrupted, query], owner)

    def test_task_resources_remain_evidence_without_top_level_task_payload(self):
        # Validated run records are the prerequisite; this seam checks only
        # the direct CLI delivery accounting, not raw resource/process proof.
        direct, request = {'action': 'link'}, {'action': 'request'}
        runs = [{'resource_results': [direct, request]}]
        commands = [{'result': {'resource_result': direct}}, {'result': {'resource_result': None}}]
        self.assertEqual(verify_resource_delivery(commands, runs, task_protocol=True), [direct, request])
        with self.assertRaisesRegex(ValueError, 'resource_delivery_accounting'):
            verify_resource_delivery(commands[1:], runs, task_protocol=True)
        with self.assertRaisesRegex(ValueError, 'resource_delivery_accounting'):
            verify_resource_delivery(commands, runs, task_protocol=False)
        commands[-1]['result']['resource_result'] = request
        self.assertEqual(verify_resource_delivery(commands, runs, task_protocol=False), [direct, request])
        with self.assertRaisesRegex(ValueError, 'resource_delivery_accounting'):
            verify_resource_delivery(commands, runs, task_protocol=True)


if __name__ == '__main__':
    unittest.main()
