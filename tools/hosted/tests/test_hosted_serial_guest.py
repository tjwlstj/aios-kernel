"""Owned SerialGuest constructor cleanup using real Python children, never QEMU."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qemu_console as runner


class SerialGuestOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log = Path(temporary.name) / 'serial.log'
        self.children = []
        self.addCleanup(self.cleanup_children)

    def cleanup_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    stream.close()

    def sleeping_child(self):
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, bufsize=0)
        self.children.append(child)
        return child

    def assert_reaped_and_closed(self, child):
        self.assertIsNotNone(child.returncode)
        self.assertIsNotNone(child.poll())
        self.assertNotEqual(child.returncode, 0)
        self.assertTrue(child.stdin.closed)
        self.assertTrue(child.stdout.closed)

    def test_thread_start_failure_reaps_created_child_and_preserves_original_error(self):
        child = self.sleeping_child()
        failure = RuntimeError('injected reader thread start failure')
        with patch.object(runner.subprocess, 'Popen', return_value=child), \
             patch.object(runner.threading.Thread, 'start', side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                runner.SerialGuest(['unused-fixture-command'], self.log)
        self.assertIs(caught.exception, failure)
        self.assert_reaped_and_closed(child)
        self.assertFalse(self.log.exists())

    def test_terminate_error_falls_back_to_owned_kill_without_replacing_constructor_error(self):
        child = self.sleeping_child()
        failure = RuntimeError('injected reader thread construction failure')
        with patch.object(runner.subprocess, 'Popen', return_value=child), \
             patch.object(runner.threading, 'Thread', side_effect=failure), \
             patch.object(child, 'terminate', side_effect=OSError('injected terminate failure')), \
             patch.object(child, 'kill', wraps=child.kill) as kill:
            with self.assertRaises(RuntimeError) as caught:
                runner.SerialGuest(['unused-fixture-command'], self.log)
        self.assertIs(caught.exception, failure)
        kill.assert_called_once()
        self.assert_reaped_and_closed(child)

    def test_normal_reader_still_captures_output_and_clean_process_exit(self):
        guest = runner.SerialGuest([sys.executable, '-B', '-u', '-c',
                                    "print('SERIAL_READY', flush=True)"], self.log)
        self.children.append(guest.process)
        guest.wait(rb'SERIAL_READY\r?\n', seconds=5)
        self.assertEqual(guest.process.wait(timeout=5), 0)
        guest.reader.join(timeout=5)
        self.assertFalse(guest.reader.is_alive())
        self.assertIsNone(guest.reader_error)
        self.assertEqual(self.log.read_bytes().replace(b'\r\n', b'\n'), b'SERIAL_READY\n')


if __name__ == '__main__':
    unittest.main()
