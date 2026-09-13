"""Real Linux Task IPC, worker lifetimes and fenced backend stop; fixture model.

No generated response in this suite comes from an AI model. The managed Python
backend lets tests hold a real HTTP request until an explicit release, so a
responsive control loop and stop/replacement races can be tested independently
of model speed. Historical protocol fixtures are not migrated by this file.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import stat
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'hosted/linux'))
sys.path.insert(0, str(ROOT / 'tools/hosted'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aios_agent import client as main_client
from aios_backend import client as backend_client
from aios_resources.proc import ProcessReader
from execution_output_contract import validate_execution
from verify_agent import verify_agent_runs
import test_hosted_backend_main as fixture


@unittest.skipUnless(platform.system() == 'Linux' and hasattr(os, 'pidfd_open')
                     and hasattr(socket, 'SO_PEERCRED'), 'Linux authenticated process lifetimes required')
class TaskLinuxTests(unittest.TestCase):
    def setUp(self):
        fixture.BackendMainTests.setUp(self)
        self.release = self.script.with_suffix('.release')
        self.request_id = None
        self.addCleanup(self.cleanup_owned)
        script = self.script.read_text()
        anchor = '        deadline=time.process_time()+0.03'
        self.assertEqual(script.count(anchor), 1)
        script = script.replace(anchor,
            "        if 'TASK_HOLD_FIXTURE' in request['prompt']:\n"
            "            wait_until=time.monotonic()+60\n"
            "            while not Path(__file__).with_suffix('.release').exists():\n"
            "                if time.monotonic()>wait_until: raise RuntimeError('fixture release timeout')\n"
            "                time.sleep(0.02)\n" + anchor)
        self.script.write_text(script)
        self.config['backend_sha256'] = hashlib.sha256(self.script.read_bytes()).hexdigest()
        self.config_path.write_text(json.dumps(self.config))
        self.backend_started = self.backend_okay('start', config=self.config_path)
        self.started = self.okay('start', config=self.config_path, fixture_backend=True, backend_dir=self.backend)
        self.okay('room-discover')
        self.okay('room-bind')

    okay = fixture.BackendMainTests.okay
    backend_okay = fixture.BackendMainTests.backend_okay

    def cleanup_owned(self):
        try:
            if self.request_id is not None:
                main_client.control(self.main, 'task-cancel', request_id=self.request_id, backend_dir=self.backend)
            self.release.touch()
            if self.request_id is not None:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    value = main_client.control(self.main, 'task-status', request_id=self.request_id)
                    if value.get('task') is None or value['task']['phase'] == 'FINISHED':
                        break
                    time.sleep(0.05)
        finally:
            fixture.BackendMainTests.tearDown(self)
            backend_client.close_leases()

    def wait(self, predicate, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.03)
        self.fail('bounded fixture observation timed out')

    def admit(self):
        began = time.monotonic()
        value = self.okay('ask-start', prompt='TASK_HOLD_FIXTURE: hello')
        self.assertLess(time.monotonic() - began, 5)
        self.request_id = value['request_id']
        self.assertEqual(value['task']['phase'], 'ACCEPTED')
        self.wait(lambda: self.request_log.exists() and len(self.request_log.read_bytes().splitlines()) == 2)
        return value

    def finished(self):
        value = self.okay('task-result', request_id=self.request_id)
        return value if value['task']['phase'] == 'FINISHED' else None

    def run_directory(self):
        return self.main / 'runs' / self.started['source_record']['source_instance']

    def assert_one_result(self):
        events = [json.loads(line) for line in (self.run_directory() / 'events.jsonl').read_bytes().splitlines()]
        self.assertEqual(sum(row['event'] == 'REQUEST_RESULT' for row in events), 1)

    def assert_formal_run(self):
        self.assertEqual(self.okay('stop')['state'], 'STOPPED')
        self.assertEqual(self.backend_okay('stop')['state'], 'STOPPED')
        verified = verify_agent_runs(self.main, source_root=ROOT / 'hosted/linux', require_live=False)
        # A probe may retain complete regular fixture evidence for independent
        # replay. No socket, symlink or ambient host directory is copied.
        export = os.environ.get('AIOS_TASK_EXPORT_DIR')
        if export:
            destination = Path(export) / self._testMethodName
            destination.mkdir(parents=True, exist_ok=False)
            for path in self.base.rglob('*'):
                if stat.S_ISREG(path.lstat().st_mode):
                    copied = destination / path.relative_to(self.base)
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    copied.write_bytes(path.read_bytes())
            (destination / 'fixture-verdict.json').write_text(json.dumps(verified, indent=2) + '\n')
        self.assertEqual(verified['outcome'], 'PASS', verified)
        self.assertEqual(verify_agent_runs(self.main, require_live=True)['outcome'], 'FAIL')

    def test_live_control_remains_responsive_and_repeated_result_does_not_resend(self):
        accepted = self.admit()
        status = self.okay('task-status', request_id=self.request_id)
        self.assertEqual(status['task']['phase'], 'RUNNING')
        with ProcessReader(status['task']['worker_process_id']) as worker:
            worker.sample()
            began = time.monotonic()
            self.okay('status')
            self.assertLess(time.monotonic() - began, 5)
            self.assertEqual(self.okay('task-status', request_id=self.request_id)['task'], status['task'])
            self.release.touch()
            answered = self.wait(self.finished)
        self.assertEqual(answered['task']['model_outcome'], 'ANSWERED')
        receipt = answered['task']['inference_receipt']
        validate_execution(receipt['backend_execution'], self.config, descriptor=self.backend_started['descriptor'])
        self.assertEqual(receipt['request_id'], accepted['request_id'])
        self.assertEqual(self.okay('task-result', request_id=self.request_id)['task'], answered['task'])
        self.assertEqual(len(self.request_log.read_bytes().splitlines()), 2, 'one warmup and one question only')
        cancelled = self.okay('task-cancel', request_id=self.request_id, backend_dir=self.backend)
        self.assertEqual(cancelled['task_control']['cancel_outcome'], 'ALREADY_TERMINAL')
        self.assertIsNone(cancelled['task_control']['backend_stop_attempt'])
        self.assertEqual(self.backend_okay('status')['state'], 'RUNNING')
        self.assert_one_result()
        self.assert_formal_run()

    def test_explicit_cancel_proves_worker_and_same_backend_terminal_separately(self):
        self.admit()
        status = self.okay('task-status', request_id=self.request_id)
        with ProcessReader(status['task']['worker_process_id']) as worker:
            cancelled = self.okay('task-cancel', request_id=self.request_id, backend_dir=self.backend)
            self.assertEqual(cancelled['task_control']['cancel_outcome'], 'ACCEPTED')
            self.assertEqual(cancelled['task_control']['backend_stop_attempt']['state'], 'STOPPED')
            terminal = self.wait(self.finished)
            self.wait(lambda: self.okay('task-status', request_id=self.request_id)['task']['backend_stop'])
            with self.assertRaises(ValueError):
                worker.sample()
        row = self.okay('task-result', request_id=self.request_id)['task']
        self.assertEqual(row['model_outcome'], 'UNKNOWN')
        self.assertEqual(row['backend_stop']['state'], 'STOPPED')
        self.assertIsNone(row['inference_receipt']['content'])
        self.assertEqual(terminal['source_record']['completed_requests'], 1)
        duplicate = self.okay('task-cancel', request_id=self.request_id, backend_dir=self.backend)
        self.assertEqual(duplicate['task_control']['cancel_outcome'], 'ALREADY_REQUESTED')
        self.assertIsNone(duplicate['task_control']['backend_stop_attempt'])
        self.assert_one_result()
        self.assert_formal_run()

    def test_cancel_after_replacement_cannot_stop_the_new_backend(self):
        self.admit()
        replacement = self.backend_okay('restart')
        self.assertNotEqual(replacement['descriptor'], self.backend_started['descriptor'])
        self.wait(self.finished)
        with ProcessReader(replacement['service_record']['child_identity']['process_id']) as new_backend:
            value = main_client.control(self.main, 'task-cancel', request_id=self.request_id, backend_dir=self.backend)
            self.assertEqual(value['error'], 'task-backend-stop-unconfirmed', value)
            self.assertEqual(value['task']['model_outcome'], 'UNKNOWN')
            self.assertIsNone(value['task']['backend_stop'])
            self.assertEqual(self.backend_okay('status')['descriptor'], replacement['descriptor'])
            new_backend.sample()
        self.assert_one_result()
        self.assert_formal_run()


if __name__ == '__main__':
    unittest.main()
