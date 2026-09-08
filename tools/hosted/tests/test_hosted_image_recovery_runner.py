"""Bounded host-side failures in the separate image recovery test runner."""
from __future__ import annotations

from pathlib import Path
import json
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qemu_image_recovery as runner


class RecoveryRunnerTests(unittest.TestCase):
    def completed_channel(self, directory, events=("READY", "FAULT", "COMPLETE"), actions=("inject", "acknowledge")):
        producer, consumer = socket.socketpair()
        channel = runner.FaultChannel(consumer, Path(directory))
        self.addCleanup(producer.close)
        self.addCleanup(channel.close)
        producer.sendall(b"".join((json.dumps({"schema_version": 1, "event": event}) + "\n").encode("ascii")
                                  for event in events))
        for event in events:
            channel.receive(event, 1)
        for action in actions:
            channel.send(action)
        return channel, producer

    @staticmethod
    def reset_error(code=10054, kind=ConnectionResetError):
        # OSError(code, ...) selects a specialized subclass on Windows; create
        # an actually generic error when testing that the exact class matters.
        error = kind("injected socket close") if kind is OSError else kind(code, "injected socket close")
        error.errno = code
        error.winerror = code
        return error

    @staticmethod
    def clean_vm():
        return {"schema_version": 1, "process_exit_code": 0, "host_killed": False,
                "shutdown_observed": True, "runner_error": None}

    def test_constructor_failure_reaps_owned_process_and_closes_streams(self):
        children = []

        def cleanup():
            for process in children:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()

        self.addCleanup(cleanup)

        class BrokenGuest:
            def __init__(self, command, log):
                self.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                children.append(self.process)
                raise RuntimeError("reader setup failed")

        vm = dict(host_killed=False, process_exit_code=None, runner_error=None)
        with patch.object(runner, "SerialGuest", BrokenGuest):
            with self.assertRaisesRegex(RuntimeError, "reader setup failed"):
                runner.start_owned_guest([], Path("unused"), vm)
        self.assertTrue(vm["host_killed"])
        self.assertIsNotNone(vm["process_exit_code"])
        self.assertNotEqual(vm["process_exit_code"], 0)
        self.assertTrue(children[0].stdin.closed)
        self.assertTrue(children[0].stdout.closed)
        self.assertIsNotNone(children[0].poll())

    def test_same_directory_preparation_rejects_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sentinel").write_text("retained", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "new-separate-clone-required"):
                runner.prepare(SimpleNamespace(source_image=root, image_dir=root))
            self.assertEqual([path.name for path in root.iterdir()], ["sentinel"])
            self.assertEqual((root / "sentinel").read_text(encoding="ascii"), "retained")

    def test_fault_channel_records_exact_frame_and_rejects_extra_output(self):
        with tempfile.TemporaryDirectory() as directory:
            producer, consumer = socket.socketpair()
            channel = runner.FaultChannel(consumer, Path(directory))
            try:
                raw = b'{"schema_version":1,"event":"READY"}\ntrailing\n'
                producer.sendall(raw)
                self.assertEqual(channel.receive("READY", 1), {"schema_version": 1, "event": "READY"})
                producer.shutdown(socket.SHUT_WR)
                with self.assertRaisesRegex(ValueError, "fault-channel-trailing-output"):
                    channel.drain()
                self.assertEqual((Path(directory) / "fault-channel.log").read_bytes(), raw)
            finally:
                channel.close()
                producer.close()

    def test_fault_channel_rejects_boolean_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            producer, consumer = socket.socketpair()
            channel = runner.FaultChannel(consumer, Path(directory))
            try:
                producer.sendall(b'{"schema_version":true,"event":"READY"}\n')
                with self.assertRaisesRegex(ValueError, "fault-channel-event"):
                    channel.receive("READY", 1)
            finally:
                channel.close()
                producer.close()

    def test_complete_eof_is_recorded_as_clean_transport_close(self):
        with tempfile.TemporaryDirectory() as directory:
            channel, producer = self.completed_channel(directory)
            producer.shutdown(socket.SHUT_WR)
            try:
                channel.drain(self.clean_vm())
                self.assertEqual(json.loads((Path(directory) / "fault-channel-close.json").read_bytes()), {
                    "schema_version": 1, "outcome": "closed", "transport_close": "eof", "socket_error_code": None,
                    "pending_bytes": 0, "completed_frame_count": 3, "request_count": 2})
            finally:
                channel.close()
                producer.close()

    def test_windows_reset_after_complete_exchange_and_normal_shutdown_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            channel, producer = self.completed_channel(directory)
            try:
                with patch.object(runner, "WINDOWS_HOST", True), patch.object(channel, "connection") as transport:
                    transport.recv.side_effect = self.reset_error()
                    channel.drain(self.clean_vm())
                self.assertEqual(json.loads((Path(directory) / "fault-channel-close.json").read_bytes()), {
                    "schema_version": 1, "outcome": "closed", "transport_close": "connection-reset", "socket_error_code": 10054,
                    "pending_bytes": 0, "completed_frame_count": 3, "request_count": 2})
            finally:
                channel.close()
                producer.close()

    def test_reset_cannot_hide_incomplete_exchange_or_failed_vm(self):
        cases = [("missing-complete", ("READY", "FAULT"), {}),
                 ("nonzero-exit", ("READY", "FAULT", "COMPLETE"), {"process_exit_code": 1}),
                 ("boolean-exit", ("READY", "FAULT", "COMPLETE"), {"process_exit_code": False}),
                 ("host-killed", ("READY", "FAULT", "COMPLETE"), {"host_killed": True}),
                 ("missing-shutdown", ("READY", "FAULT", "COMPLETE"), {"shutdown_observed": False}),
                 ("prior-runner-failure", ("READY", "FAULT", "COMPLETE"), {"runner_error": "earlier failure"})]
        for name, events, changes in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                channel, producer = self.completed_channel(directory, events)
                try:
                    with patch.object(runner, "WINDOWS_HOST", True), patch.object(channel, "connection") as transport:
                        transport.recv.side_effect = self.reset_error()
                        with self.assertRaisesRegex(ValueError, "incomplete-exchange|not-normal-shutdown"):
                            channel.drain({**self.clean_vm(), **changes})
                    receipt = json.loads((Path(directory) / "fault-channel-close.json").read_bytes())
                    self.assertEqual(receipt["outcome"], "failed")
                    self.assertEqual(receipt["socket_error_code"], 10054)
                finally:
                    channel.close()
                    producer.close()

    def test_trailing_output_before_reset_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            channel, producer = self.completed_channel(directory)
            try:
                with patch.object(runner, "WINDOWS_HOST", True), patch.object(channel, "connection") as transport:
                    transport.recv.side_effect = [b"unexpected\n", self.reset_error()]
                    with self.assertRaisesRegex(ValueError, "trailing-output"):
                        channel.drain(self.clean_vm())
                receipt = json.loads((Path(directory) / "fault-channel-close.json").read_bytes())
                self.assertEqual(receipt["outcome"], "failed")
                self.assertEqual(receipt["pending_bytes"], len(b"unexpected\n"))
                self.assertTrue((Path(directory) / "fault-channel.log").read_bytes().endswith(b"unexpected\n"))
            finally:
                channel.close()
                producer.close()

    def test_reset_without_the_acknowledgement_request_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            channel, producer = self.completed_channel(directory, actions=("inject",))
            try:
                with patch.object(runner, "WINDOWS_HOST", True), patch.object(channel, "connection") as transport:
                    transport.recv.side_effect = self.reset_error()
                    with self.assertRaisesRegex(ValueError, "incomplete-exchange"):
                        channel.drain(self.clean_vm())
                receipt = json.loads((Path(directory) / "fault-channel-close.json").read_bytes())
                self.assertEqual(receipt["outcome"], "failed")
                self.assertEqual(receipt["request_count"], 1)
            finally:
                channel.close()
                producer.close()

    def test_other_socket_errors_and_nonwindows_resets_are_not_success(self):
        cases = [("aborted", True, self.reset_error(10053)),
                 ("untyped-error", True, self.reset_error(10054, OSError)),
                 ("nonwindows", False, self.reset_error()),
                 ("timeout", True, TimeoutError("socket timeout"))]
        for name, windows, error in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                channel, producer = self.completed_channel(directory)
                try:
                    with patch.object(runner, "WINDOWS_HOST", windows), patch.object(channel, "connection") as transport:
                        transport.recv.side_effect = error
                        with self.assertRaises(OSError):
                            channel.drain(self.clean_vm())
                    receipt = json.loads((Path(directory) / "fault-channel-close.json").read_bytes())
                    self.assertEqual(receipt["outcome"], "failed")
                    self.assertEqual(receipt["transport_close"], "error")
                finally:
                    channel.close()
                    producer.close()


if __name__ == "__main__":
    unittest.main()
