"""Bounded MAIN6 transport/client fixture checks, not live Linux owner evidence."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'hosted/linux'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aios_agent import client, protocol
from aios_backend import client as backend_client, protocol as backend_protocol
import test_hosted_request_state as fixture_module


class TaskTransportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.RequestStateTests()
        self.fixture.setUp()
        self.row = self.fixture.state.snapshot()
        self.identity = self.row['request_id']
        self.source = self.row['source_before']
        self.management = self.row['management_before']
        self.directory = Path('/fixture/main')
        self.backend_dir = Path('/fixture/backend')

    def reply(self, action='task-status', *, task=True, error=None, control=None):
        return protocol.reply(action, 'RUNNING', source=copy.deepcopy(self.source),
            management=copy.deepcopy(self.management), request_id=self.identity,
            task=copy.deepcopy(self.row) if task else None, error=error,
            task_control=copy.deepcopy(control), capture_kind='fixture')

    def ack(self, outcome='ACCEPTED'):
        return self.reply('task-cancel', control={'cancel_outcome': outcome, 'backend_stop_attempt': None})

    def invoke(self, replies, *, action='task-cancel', attempt=None, **kwargs):
        with mock.patch.object(client, 'supported', return_value=True), \
             mock.patch.object(client, 'prepare_directory', return_value=self.directory), \
             mock.patch.object(client, '_status', side_effect=AssertionError('long status path used')), \
             mock.patch.object(client, '_rpc', side_effect=replies) as rpc, \
             mock.patch.object(backend_client, 'stop_bound', return_value=attempt or self.fixture.stopped()) as stop:
            result = client.control(self.directory, action, request_id=self.identity,
                                    backend_dir=self.backend_dir, **kwargs)
        return result, rpc, stop

    def test_main6_task_full_row_and_rejected_request_uuid_are_exact(self):
        value = self.reply()
        self.assertEqual(protocol.validate_reply(value, 'task-status'), value)
        denied = self.reply('ask-start', task=False, error='request-busy')
        self.assertEqual(protocol.validate_reply(denied, 'ask-start')['request_id'], self.identity)
        for edit in (lambda v: v.update(schema_version=5), lambda v: v.update(request_id=None),
                     lambda v: v['task'].update(request_id='00000000-0000-4000-8000-000000000099'),
                     lambda v: v['task'].update(schema_version=True), lambda v: v['task'].pop('owner'),
                     lambda v: v.update(inference_receipt={}),
                     lambda v: v.update(management_outcome='accepted'),
                     lambda v: v.update(resource_result={}), lambda v: v.update(space_context={}),
                     lambda v: v.update(task_control={'cancel_outcome':'ACCEPTED','backend_stop_attempt':None})):
            changed = copy.deepcopy(value)
            edit(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                protocol.validate_reply(changed, 'task-status')

    def test_cancel_control_must_be_exact_and_only_on_cancel(self):
        value = self.ack()
        self.assertEqual(protocol.validate_reply(value, 'task-cancel'), value)
        for changed in (None, {}, {'cancel_outcome':'DONE','backend_stop_attempt':None},
                        {'cancel_outcome':'ACCEPTED','backend_stop_attempt':{}},
                        {'cancel_outcome':'ALREADY_REQUESTED','backend_stop_attempt':self.fixture.stopped()},
                        {'cancel_outcome':'ALREADY_TERMINAL','backend_stop_attempt':self.fixture.stopped()},
                        {'cancel_outcome':'ACCEPTED','backend_stop_attempt':None,'extra':True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                protocol.validate_reply({**value,'task_control':changed}, 'task-cancel')

    def test_non_task_reply_cannot_smuggle_task_or_uuid(self):
        value = protocol.reply('status', 'ABSENT')
        for key, changed in (('request_id', self.identity), ('task',self.row), ('task_control',{})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                protocol.validate_reply({**value,key:changed}, 'status')

    def test_uuid_is_generated_once_before_first_rpc_and_survives_uncertainty(self):
        with mock.patch.object(client, 'supported', return_value=True), \
             mock.patch.object(client, 'prepare_directory', return_value=self.directory), \
             mock.patch.object(client.uuid, 'uuid4', return_value=self.identity) as generate, \
             mock.patch.object(client, '_rpc', side_effect=ValueError('rpc-timeout')) as rpc:
            result = client.control(self.directory, 'ask-start', prompt='hello')
        self.assertEqual(result['request_id'], self.identity)
        self.assertEqual(result['error'], 'rpc-timeout')
        self.assertEqual(result['action'], 'ask-start')
        self.assertIsNone(result['task'])
        generate.assert_called_once()
        rpc.assert_called_once_with(self.directory,'ask-start','hello',request_id=self.identity)
        protocol.validate_reply(result, 'ask-start')

    def test_invalid_or_missing_query_uuid_has_no_rpc_or_backend_stop(self):
        with mock.patch.object(client, '_rpc') as rpc, mock.patch.object(backend_client, 'stop_bound') as stop:
            for identity in (None, '', True, 'bad', '00000000-0000-0000-0000-000000000000'):
                for action in ('task-status', 'task-result', 'task-cancel'):
                    result = client.control(self.directory, action, request_id=identity)
                    self.assertEqual((result['action'],result['error']),('status','task-request-id'))
                    protocol.validate_reply(result,'status')
            rpc.assert_not_called()
            stop.assert_not_called()

    def test_unsupported_task_preserves_valid_uuid(self):
        with mock.patch.object(client, 'supported', return_value=False):
            result = client.control(self.directory, 'task-status', request_id=self.identity)
        self.assertEqual(result['request_id'], self.identity)
        protocol.validate_reply(result,'task-status')

    def test_legacy_direct_ask_error_is_preserved_without_async_retry(self):
        denied=protocol.reply('ask','RUNNING',source=self.source,management=self.management,
                              error='request-task-required',capture_kind='fixture')
        protocol.validate_reply(denied,'ask')
        with mock.patch.object(client,'supported',return_value=True), \
             mock.patch.object(client,'prepare_directory',return_value=self.directory), \
             mock.patch.object(client,'_status',return_value=protocol.reply('status','RUNNING')), \
             mock.patch.object(client,'_rpc',return_value=denied) as rpc, \
             mock.patch.object(client.uuid,'uuid4') as generate:
            result=client.control(self.directory,'ask',prompt='hello')
        self.assertEqual(result,denied)
        self.assertIsNone(result['task'])
        self.assertIsNone(result['request_id'])
        rpc.assert_called_once_with(self.directory,'ask','hello',None)
        generate.assert_not_called()

    def test_cancel_stops_exact_saved_backend_and_refreshes_same_uuid_once(self):
        self.fixture.state.mark_running(self.fixture.worker, now_ns=101)
        self.fixture.state.request_cancel(self.fixture.owner, now_ns=102)
        self.row = self.fixture.state.snapshot()
        acknowledged = self.ack()
        self.fixture.state.record_worker_exit(self.fixture.worker,-15,receipt=None,now_ns=103)
        self.fixture.state.record_backend_stop(self.fixture.stopped(),now_ns=104)
        self.row = self.fixture.state.snapshot()
        refreshed = self.reply()
        result, rpc, stop = self.invoke([acknowledged,refreshed])
        stop.assert_called_once_with(self.backend_dir,acknowledged['task']['backend_expected'])
        self.assertEqual(rpc.call_args_list,[mock.call(self.directory,'task-cancel',None,request_id=self.identity),
                                          mock.call(self.directory,'task-status',request_id=self.identity)])
        self.assertEqual(result['task'],refreshed['task'])
        self.assertEqual(result['action'],'task-cancel')
        self.assertEqual(result['task_control']['backend_stop_attempt'],self.fixture.stopped())
        protocol.validate_reply(result,'task-cancel')

    def test_already_requested_or_terminal_never_retries_stop_or_refresh(self):
        for outcome in ('ALREADY_REQUESTED','ALREADY_TERMINAL'):
            result,rpc,stop=self.invoke([self.ack(outcome)])
            self.assertEqual(result['task_control']['cancel_outcome'],outcome)
            self.assertEqual(rpc.call_count,1)
            stop.assert_not_called()

    def test_answered_and_not_started_terminal_are_never_stopped_even_on_bad_ack(self):
        for outcome in ('ANSWERED','NOT_STARTED'):
            self.row.update(phase='FINISHED',model_outcome=outcome)
            _result,rpc,stop=self.invoke([self.ack()])
            self.assertEqual(rpc.call_count,1)
            stop.assert_not_called()

    def test_refused_cancel_does_not_touch_backend(self):
        result,rpc,stop=self.invoke([self.reply('task-cancel',task=False,error='request-owner-mismatch')])
        self.assertEqual(result['error'],'request-owner-mismatch')
        self.assertEqual(result['request_id'],self.identity)
        self.assertEqual(rpc.call_count,1)
        stop.assert_not_called()

    def test_failed_backend_attempt_is_preserved_without_claiming_stop(self):
        attempt=backend_protocol.reply('stop','FAILED',error='stop-owner-required',capture_kind='fixture')
        result,rpc,stop=self.invoke([self.ack(),self.reply()],attempt=attempt)
        self.assertEqual(result['error'],'task-backend-stop-unconfirmed')
        self.assertEqual(result['task_control']['backend_stop_attempt'],attempt)
        self.assertIsNone(result['task']['backend_stop'])
        self.assertEqual(rpc.call_count,2)
        stop.assert_called_once()
        protocol.validate_reply(result,'task-cancel')

    def test_refresh_failure_keeps_ack_uuid_and_attempt_without_retry(self):
        original=self.ack()
        for refresh in (ValueError('rpc-timeout'),self.reply(task=False,error='request-owner-mismatch')):
            result,rpc,stop=self.invoke([original,refresh])
            self.assertEqual(result['error'],'task-refresh-failed')
            self.assertEqual(result['request_id'],self.identity)
            self.assertEqual(result['task'],original['task'])
            self.assertEqual(result['task_control']['backend_stop_attempt'],self.fixture.stopped())
            self.assertEqual(rpc.call_count,2)
            stop.assert_called_once()

    def test_refresh_cannot_adopt_replacement_main_or_backend(self):
        for side in ('source','backend'):
            changed=self.reply()
            if side=='source':changed['source_record']['source_instance']='00000000-0000-4000-8000-000000000099'
            else:changed['task']['backend_expected']['descriptor']['listener_inode']+=1
            result,_rpc,_stop=self.invoke([self.ack(),changed])
            self.assertEqual(result['error'],'task-refresh-failed')

    def test_task_rpc_has_shared_five_second_budget_and_no_backend_dir_on_wire(self):
        now=[100.0]
        incoming=mock.Mock()
        handshake=mock.Mock()
        registry={key:self.source[key] for key in client.IDENTITY_KEYS}
        def connected(*_args,**kwargs):
            self.assertEqual(kwargs['seconds'],5.0)
            now[0]=102.0
            return handshake,protocol.reply('status','RUNNING',source=self.source),self.source['process_id']
        def connection_established(_path):now[0]=103.0
        incoming.connect.side_effect=connection_established
        with mock.patch.object(client,'registry_at',return_value=registry), \
             mock.patch.object(client.time,'monotonic',side_effect=lambda:now[0]), \
             mock.patch.object(protocol,'connect',side_effect=connected), \
             mock.patch.object(protocol,'socket_path',return_value=Path('/fixture/agent.sock')), \
             mock.patch.object(client.socket,'AF_UNIX',getattr(client.socket,'AF_UNIX',1),create=True), \
             mock.patch.object(client.socket,'socket',return_value=incoming), \
             mock.patch.object(client,'peer_credentials',return_value=(self.source['process_id'],1000,1000)), \
             mock.patch.object(client.os,'getuid',return_value=1000,create=True), \
             mock.patch.object(protocol,'send') as send, \
             mock.patch.object(protocol,'receive',return_value=self.reply('ask-start')) as receive:
            value=client._rpc(self.directory,'ask-start','hello',self.backend_dir,self.identity)
        self.assertEqual(value['request_id'],self.identity)
        self.assertEqual(receive.call_args.args[1],2.0)
        self.assertEqual(send.call_args.args[1],{'schema_version':6,'action':'ask-start',
            'source_instance':self.source['source_instance'],'prompt':'hello','backend_dir':None,'request_id':self.identity})
        self.assertTrue(all(0<call.args[0]<=5 for call in incoming.settimeout.call_args_list))
        incoming.close.assert_called_once()

    @contextmanager
    def rpc_boundary(self, value, *, received_at=101.0):
        now=[100.0]
        incoming=mock.Mock()
        handshake=mock.Mock()
        registry={key:self.source[key] for key in client.IDENTITY_KEYS}
        def receive(*_args):
            now[0]=received_at
            return value
        with mock.patch.object(client,'registry_at',return_value=registry), \
             mock.patch.object(client.time,'monotonic',side_effect=lambda:now[0]), \
             mock.patch.object(protocol,'connect',return_value=(handshake,{},self.source['process_id'])), \
             mock.patch.object(protocol,'socket_path',return_value=Path('/fixture/agent.sock')), \
             mock.patch.object(client.socket,'AF_UNIX',getattr(client.socket,'AF_UNIX',1),create=True), \
             mock.patch.object(client.socket,'socket',return_value=incoming), \
             mock.patch.object(client,'peer_credentials',return_value=(self.source['process_id'],1000,1000)), \
             mock.patch.object(client.os,'getuid',return_value=1000,create=True), \
             mock.patch.object(protocol,'send') as send, \
             mock.patch.object(protocol,'receive',side_effect=receive):
            yield incoming,send

    def test_task_reply_for_another_uuid_is_rejected_and_connection_closed(self):
        changed=self.reply()
        changed['request_id']=changed['task']['request_id']='00000000-0000-4000-8000-000000000099'
        with self.rpc_boundary(changed) as (incoming,send):
            with self.assertRaisesRegex(ValueError,'protocol-error'):
                client._rpc(self.directory,'task-status',request_id=self.identity)
        send.assert_called_once()
        incoming.close.assert_called_once()

    def test_daemon_cannot_forge_client_backend_stop_witness(self):
        changed=self.ack()
        changed['task_control']['backend_stop_attempt']=self.fixture.stopped()
        with self.rpc_boundary(changed) as (incoming,_send), \
             mock.patch.object(backend_client,'stop_bound') as stop:
            with self.assertRaisesRegex(ValueError,'protocol-error'):
                client._rpc(self.directory,'task-cancel',request_id=self.identity)
        incoming.close.assert_called_once()
        stop.assert_not_called()

    def test_reply_completed_after_shared_deadline_is_not_returned_as_success(self):
        with self.rpc_boundary(self.reply('ask-start'),received_at=105.1) as (incoming,send):
            with self.assertRaisesRegex(ValueError,'rpc-timeout'):
                client._rpc(self.directory,'ask-start','hello',request_id=self.identity)
        send.assert_called_once()
        incoming.close.assert_called_once()

    def test_handshake_identity_read_must_finish_inside_its_deadline(self):
        now=[100.0]
        incoming=mock.Mock()
        current=protocol.reply('status','RUNNING',source=self.source)
        def boot_read(*_args,**_kwargs):
            now[0]=105.1
            return self.source['host_boot_id']
        with mock.patch.object(protocol.time,'monotonic',side_effect=lambda:now[0]), \
             mock.patch.object(protocol.socket,'AF_UNIX',getattr(protocol.socket,'AF_UNIX',1),create=True), \
             mock.patch.object(protocol.socket,'socket',return_value=incoming), \
             mock.patch.object(protocol,'socket_path',return_value=Path('/fixture/agent.sock')), \
             mock.patch.object(protocol,'peer_credentials',return_value=(self.source['process_id'],1000,1000)), \
             mock.patch.object(protocol.os,'getuid',return_value=1000,create=True), \
             mock.patch.object(protocol,'send'), mock.patch.object(protocol,'receive',return_value=current), \
             mock.patch.object(Path,'read_text',side_effect=boot_read):
            with self.assertRaisesRegex(ValueError,'rpc-timeout'):
                protocol.connect(self.directory,self.source['source_instance'],seconds=5)
        incoming.close.assert_called_once()

    def terminal_status(self, root, extra_names, *, tamper=False):
        directory=Path(root)
        registry={key:self.source[key] for key in client.IDENTITY_KEYS}
        run=directory/'runs'/self.source['source_instance']
        run.mkdir(parents=True)
        source={**self.source,'lifecycle_state':'exited','model_ready':False}
        latest=protocol.reply('status','STOPPED',source=source,management=self.management,capture_kind='fixture')
        names=['config.json','start.json','source.json','management.json','events.jsonl',*extra_names]
        files={}
        for name in names:
            path=run/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'{}\n')
            files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        result={'schema_version':6,**registry,'state':'STOPPED','exit_code':0,'error':None,
                'completed_at':'2026-09-09T00:00:00+00:00','capture_kind':'fixture',
                'source_record':source,'management_snapshot':self.management,'files':files}
        (run/'result.json').write_text(json.dumps(result),encoding='utf-8')
        if tamper:(run/extra_names[0]).write_bytes(b'{"changed":true}\n')
        # Linux ownership/liveness are mocked; the manifest/path/hash boundary
        # below reads actual temporary files and is not live pidfd evidence.
        with mock.patch.object(client,'registry_at',return_value=registry), \
             mock.patch.object(client,'_latest',return_value=latest), \
             mock.patch.object(protocol,'connect',side_effect=OSError('gone')), \
             mock.patch.object(client,'_busy',return_value=False), \
             mock.patch.object(client,'prepare_directory',side_effect=lambda path:path), \
             mock.patch.object(client,'read_json',side_effect=lambda path:json.loads(path.read_text(encoding='utf-8'))), \
             mock.patch.object(client,'regular_file',side_effect=lambda path:self.assertTrue(path.is_file())):
            return client._status(directory)

    def test_main6_terminal_manifest_accepts_task_revision_files_and_rejects_tamper(self):
        names=[f'tasks/{self.identity}/{revision:02d}.json' for revision in range(1,9)]
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.terminal_status(root,names)['state'],'STOPPED')
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(ValueError,'state-corrupt'):
            self.terminal_status(root,names,tamper=True)

    def test_terminal_task_manifest_rejects_invalid_revision_or_uuid(self):
        for name in (f'tasks/{self.identity}/00.json',f'tasks/{self.identity}/09.json',
                     f'tasks/{self.identity}/1.json','tasks/not-a-uuid/01.json',
                     'tasks/00000000-0000-0000-0000-000000000000/01.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as root, \
                 self.assertRaisesRegex(ValueError,'state-corrupt'):
                self.terminal_status(root,[name])

    def test_main6_terminal_manifest_bound_is_256_files(self):
        names=[f'requests/00000000-0000-4000-8000-{number:012d}.json' for number in range(251)]
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.terminal_status(root,names)['state'],'STOPPED')
        names.append(f'tasks/{self.identity}/01.json')
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(ValueError,'state-corrupt'):
            self.terminal_status(root,names)

    def test_agent_entry_forwards_request_id_but_not_to_serve(self):
        spec=importlib.util.spec_from_file_location('task_entry_fixture',ROOT/'hosted/linux/aios-agent.py')
        entry=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        output=mock.Mock(buffer=io.BytesIO())
        argv=['aios-agent.py','task-status','--state-dir','/fixture/main','--request-id',self.identity]
        with mock.patch.object(sys,'argv',argv), mock.patch.object(sys,'stdout',output), \
             mock.patch.object(entry,'control',return_value=self.reply()) as control:
            self.assertEqual(entry.main(),0)
        self.assertEqual(control.call_args.kwargs['request_id'],self.identity)
        with mock.patch.object(sys,'argv',['aios-agent.py','--serve','--state-dir','/fixture/main','--config','/fixture/config']), \
             mock.patch.object(entry,'serve',return_value=0) as serve:
            self.assertEqual(entry.main(),0)
        self.assertNotIn('request_id',serve.call_args.kwargs)


if __name__=='__main__':unittest.main()
