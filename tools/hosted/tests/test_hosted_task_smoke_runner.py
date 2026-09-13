"""Task smoke routing and failed-driver preservation without launching a VM."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qemu_console as runner
import verify_agent


class TaskSmokeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / 'attempt'

    def test_conflicting_workflow_fails_before_vm_or_source_access(self):
        base = ['qemu_console.py', '--qemu', 'unused', '--iso', 'unused',
                '--kernel', 'unused', '--initramfs', 'unused', '--sha256', '0' * 64,
                '--artifact-dir', str(self.output), '--task-smoke']
        for other in ('--smoke', '--service-smoke', '--agent-smoke', '--resource-smoke',
                      '--cell-smoke', '--backend-smoke', '--recovery-smoke', '--space-smoke'):
            with self.subTest(other=other), patch.object(sys, 'argv', base + [other]), \
                 patch.object(runner, 'SerialGuest') as guest, \
                 patch.object(Path, 'read_bytes') as read, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    runner.main()
                self.assertEqual(caught.exception.code, 2)
                guest.assert_not_called()
                read.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_completed_failed_driver_is_exported_and_never_verified_as_success(self):
        self.output.mkdir()
        guest = Mock()
        guest.step.side_effect = RuntimeError('driver failed (exit 1)')
        with patch.object(runner, 'export_guest') as export, \
             patch.object(runner, 'verify_execution') as verify:
            result = runner.run_task_smoke_guest(guest, self.output, Path('retained'))
        export.assert_called_once_with(guest, '/tmp/aios-task-smoke', self.output)
        verify.assert_not_called()
        self.assertEqual(result['outcome'], 'FAIL')
        self.assertEqual(json.loads((self.output / 'verdict.json').read_text()), result)

    def test_timeout_is_not_treated_as_a_completed_driver(self):
        guest = Mock()
        guest.step.side_effect = TimeoutError('guest checkpoint timeout')
        with patch.object(runner, 'export_guest') as export:
            with self.assertRaises(TimeoutError):
                runner.run_task_smoke_guest(guest, self.output, Path('retained'))
        export.assert_not_called()

    def test_failed_main_stop_still_exports_both_stores_and_stops_backend(self):
        self.output.mkdir()
        guest = Mock()
        with patch.object(runner, 'task_stop_guest', side_effect=[1, 0]) as stop, \
             patch.object(runner, 'export_guest') as export:
            issues = runner.cleanup_task_model(guest, self.output)
        self.assertEqual(issues, ['agent_stop_exit_1'])
        self.assertEqual([call.args[1] for call in stop.call_args_list], ['agent', 'backend'])
        self.assertEqual([call.args[1] for call in export.call_args_list],
                         [runner.AGENT_DIR, '/tmp/aios-model-backend'])
        self.assertEqual(json.loads((self.output / 'task-cleanup.json').read_text())['outcome'], 'FAIL')

    def test_stop_transport_error_does_not_skip_remaining_evidence(self):
        self.output.mkdir()
        with patch.object(runner, 'task_stop_guest', side_effect=[TimeoutError('stopped replying'), 0]), \
             patch.object(runner, 'export_guest') as export:
            issues = runner.cleanup_task_model(Mock(), self.output)
        self.assertEqual(issues, ['agent_stop:stopped replying'])
        self.assertEqual(export.call_count, 2)

    def test_nonzero_stop_exit_is_recorded_as_returned(self):
        guest = Mock()
        match = Mock()
        match.group.return_value = b'1'
        guest.wait.return_value = (match, 0)

        def export(guest, directory, output, names):
            output.mkdir()
            for name in names:
                (output / name).write_bytes(b'error' if name.endswith('stdout') else b'')

        with patch.object(runner, 'export_guest', side_effect=export):
            code = runner.task_stop_guest(guest, 'agent', runner.AGENT_DIR, self.output)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((self.output / 'execution.json').read_text())['process_exit_code'], 1)

    def test_task_verifier_refuses_fixture_and_conflicting_modes(self):
        for other in ('--allow-fixture', '--run', '--workflow', '--interactive', '--resources',
                      '--cells', '--backends', '--recovery-smoke', '--space'):
            with self.subTest(other=other), \
                 patch.object(sys, 'argv', ['verify_agent.py', str(self.output), '--tasks', other]), \
                 patch.object(verify_agent, 'verify_interactive') as verify:
                with self.assertRaisesRegex(ValueError, 'tasks_live_only'):
                    verify_agent.main()
                verify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
