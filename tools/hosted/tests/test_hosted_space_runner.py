"""SpaceSmoke orchestration boundaries; no VM or model is launched."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qemu_console as runner
from space_output_contract import SPACE_COMMANDS, SPACE_STALE_COMMAND_INDEX, SPACE_STALE_WAIT_SECONDS


class Match:
    def __init__(self, code=None):
        self.code = code

    def group(self, index):
        if index != 1:
            raise AssertionError(index)
        return self.code

    def start(self):
        return 0


class PromptGuest:
    """Expose one prompt per sent command, then acknowledge console exit."""
    def __init__(self, command_count, trace):
        self.command_count = command_count
        self.trace = trace
        self.commands = []
        self.transcript = bytearray()
        self.cursor = 0
        self.launch = None
        self.wait_count = 0
        self.wait_seconds = []

    def send(self, line):
        if self.launch is None:
            self.launch = line
        else:
            self.commands.append(line)
            self.trace.append(('send', line))

    def wait(self, pattern, seconds=60):
        self.wait_count += 1
        self.wait_seconds.append(seconds)
        if self.wait_count == 1 or len(self.commands) < self.command_count:
            return Match(), 0
        return Match(b'0'), 0


class SpaceSmokeRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='aios-space-runner-test-')
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / 'new-run'
        self.retained = Path(temporary.name) / 'runtime-source'
        self.trace = []

    @staticmethod
    def export_fixture(guest, directory, output, names=None):
        output.mkdir(parents=True, exist_ok=True)
        for name in names or []:
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'')

    def run_console(self, commands, *, space_smoke=False, exit_after=None, agent=True):
        guest = PromptGuest(len(commands) if exit_after is None else exit_after, self.trace)
        with patch.object(runner, 'export_guest', side_effect=self.export_fixture), \
             patch.object(runner, 'verify_execution', return_value={'outcome': 'PASS', 'reasons': []}) as verify, \
             patch.object(runner.time, 'sleep', side_effect=lambda seconds: self.trace.append(('sleep', seconds))) as pause, \
             contextlib.redirect_stdout(io.StringIO()):
            result = runner.run_session(guest, self.output, self.retained, commands, 'aios-session',
                                        agent=agent, space_smoke=space_smoke)
        return guest, result, verify, pause

    def test_full_space_plan_pauses_once_before_stale_ask_and_requires_internet(self):
        guest, result, verify, pause = self.run_console(list(SPACE_COMMANDS), space_smoke=True)
        expected = [('send', command) for command in SPACE_COMMANDS]
        expected.insert(SPACE_STALE_COMMAND_INDEX, ('sleep', 31))
        self.assertEqual(self.trace, expected)
        self.assertEqual(guest.commands, SPACE_COMMANDS)
        self.assertEqual(guest.wait_seconds[1:], [2450] * (len(SPACE_COMMANDS) + 1))
        self.assertEqual(result['outcome'], 'PASS')
        pause.assert_called_once_with(SPACE_STALE_WAIT_SECONDS)
        verify.assert_called_once_with(self.output, source_root=self.retained, require_live=True,
                                       require_internet=True)
        execution = json.loads((self.output / 'execution.json').read_bytes())
        self.assertEqual(execution['mode'], 'smoke')
        self.assertEqual(execution['requested_commands'], SPACE_COMMANDS)

    def test_existing_console_smoke_has_no_space_pause(self):
        guest, _, verify, pause = self.run_console(runner.SMOKE_COMMANDS, agent=False)
        self.assertEqual(guest.commands, runner.SMOKE_COMMANDS)
        self.assertEqual(guest.wait_seconds[1:], [60] * (len(runner.SMOKE_COMMANDS) + 1))
        pause.assert_not_called()
        verify.assert_called_once_with(self.output, source_root=self.retained, require_live=True,
                                       require_internet=True)

    def test_model_console_outside_space_smoke_uses_context_timeout_without_sleep(self):
        guest, _, _, pause = self.run_console(['about', 'exit'], agent=True)
        self.assertEqual(guest.wait_seconds[1:], [2450, 2450, 2450])
        pause.assert_not_called()

    def test_truncated_or_reordered_plan_is_refused_before_console_launch(self):
        altered = list(SPACE_COMMANDS)
        altered[0] = 'help' if altered[0] != 'help' else 'about'
        for commands in (SPACE_COMMANDS[:-1], altered, None):
            with self.subTest(commands=commands):
                guest = PromptGuest(0, self.trace)
                with self.assertRaisesRegex(ValueError, 'exact command plan'):
                    runner.run_session(guest, self.output, self.retained, commands, 'aios-session',
                                       agent=True, space_smoke=True)
                self.assertIsNone(guest.launch)
                self.assertFalse(self.output.exists())

    def test_space_lane_requires_model_before_console_launch(self):
        guest = PromptGuest(0, self.trace)
        with self.assertRaisesRegex(ValueError, 'model-enabled console'):
            runner.run_session(guest, self.output, self.retained, SPACE_COMMANDS, 'aios-session',
                               agent=False, space_smoke=True)
        self.assertIsNone(guest.launch)
        self.assertFalse(self.output.exists())

    def test_early_console_exit_cannot_produce_completed_execution_or_verdict(self):
        with self.assertRaisesRegex(RuntimeError, 'before the SpaceSmoke command plan completed'):
            self.run_console(list(SPACE_COMMANDS), space_smoke=True, exit_after=1)
        self.assertFalse((self.output / 'execution.json').exists())
        self.assertFalse((self.output / 'verdict.json').exists())
        self.assertFalse(any(kind == 'sleep' for kind, _ in self.trace))

    def test_changed_or_repeated_command_is_refused_without_an_extra_pause(self):
        script = runner.SpaceSmokeScript(SPACE_COMMANDS)
        with patch.object(runner.time, 'sleep') as pause, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'command order changed'):
                script.before_send('not-a-space-command')
            for command in SPACE_COMMANDS:
                script.before_send(command)
            script.complete()
            with self.assertRaisesRegex(ValueError, 'command order changed'):
                script.before_send(SPACE_COMMANDS[SPACE_STALE_COMMAND_INDEX])
        pause.assert_called_once_with(31)

    def test_conflicting_smokes_fail_before_artifacts_cache_access_or_vm_launch(self):
        base = ['qemu_console.py', '--qemu', 'unused-qemu', '--iso', 'unused-iso',
                '--kernel', 'unused-kernel', '--initramfs', 'unused-initramfs',
                '--sha256', '0' * 64, '--artifact-dir', str(self.output), '--space-smoke']
        for other in ('--smoke', '--service-smoke', '--agent-smoke', '--resource-smoke',
                      '--cell-smoke', '--backend-smoke', '--recovery-smoke'):
            with self.subTest(other=other), patch.object(sys, 'argv', base + [other]), \
                 patch.object(runner, 'SerialGuest') as guest, \
                 patch.object(Path, 'read_bytes') as read, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    runner.main()
                self.assertEqual(caught.exception.code, 2)
                guest.assert_not_called()
                read.assert_not_called()
                self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
