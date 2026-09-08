"""Resource semantics and real Linux process/listener identity negative cases."""
from __future__ import annotations

import copy
import json
import os
import platform
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted" / "linux"))
from aios_resources import ResourceError, backend, proc


def stat_record(*, pid=123, comm="worker", user=50, system=10, started=1234, rss=8, virtual=65536):
    fields = ["S"] + ["0"] * 49
    for index, value in ((1, 1), (11, user), (12, system), (13, 999999), (14, 888888),
                         (19, started), (20, virtual), (21, rss), (40, 777777)):
        fields[index] = str(value)
    return f"{pid} ({comm}) " + " ".join(fields) + "\n"


def status_record(pid=123, uid=1000):
    return f"Name:\tworker\nTgid:\t{pid}\nPid:\t{pid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"


def psi_record():
    return ("some avg10=12.34 avg60=5.67 avg300=0.01 total=123456\n"
            "full avg10=1.23 avg60=0.45 avg300=0.00 total=12345\n")


def config_for(port):
    return {"schema_version": 1, "endpoint": f"http://127.0.0.1:{port}", "model_id": "fixture-resource-model",
            "model_path": "/fixture/model", "model_sha256": "1" * 64,
            "backend_path": "/fixture/backend", "backend_sha256": "2" * 64, "provenance_sha256": "3" * 64}


class ResourceParserTests(unittest.TestCase):
    def test_comm_parentheses_spaces_and_newline_do_not_shift_stat_fields(self):
        for comm in ("name with spaces", "a)b(c", "x) S 123 (y", "line\nname"):
            parsed = proc.parse_stat(stat_record(comm=comm), 123)
            self.assertEqual((parsed["user_ticks"], parsed["system_ticks"], parsed["process_start_ticks"]), (50, 10, 1234))
            values = proc.accounting(parsed, 100, 4096)
            self.assertEqual(values["cpu_total_ns"], 600_000_000)
            self.assertEqual(values["rss_bytes_estimate"], 32768)

    def test_only_process_user_and_system_ticks_are_counted(self):
        parsed = proc.parse_stat(stat_record())
        self.assertEqual(proc.accounting(parsed, 100, 4096)["cpu_total_ns"], 600_000_000)
        self.assertNotIn("child_ticks", parsed)
        self.assertNotIn("guest_ticks", parsed)

    def test_bad_stat_and_counter_overflow_fail_closed(self):
        bad = ["bad", stat_record(user=-1), stat_record(system=proc.U64_MAX + 1),
               stat_record(user=proc.U64_MAX, system=1), stat_record(started=0), stat_record(rss=-1),
               stat_record()[:20], b"x" * (proc.STAT_LIMIT + 1)]
        for raw in bad:
            with self.subTest(raw=repr(raw)[:50]), self.assertRaises(ResourceError):
                proc.parse_stat(raw)
        with self.assertRaises(ResourceError):
            proc.parse_stat(stat_record(), True)
        parsed = proc.parse_stat(stat_record(user=proc.U64_MAX, system=0))
        with self.assertRaises(ResourceError):
            proc.accounting(parsed, 1, 4096)
        parsed = proc.parse_stat(stat_record(rss=proc.U64_MAX))
        with self.assertRaises(ResourceError):
            proc.accounting(parsed, 100, 4096)
        with self.assertRaises(ResourceError):
            proc.accounting(proc.parse_stat(stat_record()), True, 4096)

    def test_status_requires_unique_consistent_process_and_uid(self):
        self.assertEqual(proc.parse_status(status_record(), 123), {"process_id": 123, "uid": 1000})
        for raw in (status_record() + "Uid:\t1000 1000 1000 1000\n",
                    status_record().replace("Tgid:\t123", "Tgid:\t124"),
                    status_record().replace("1000\t1000\t1000\t1000", "1000\t1001\t1000\t1000"),
                    status_record().replace("Uid:", "Other:")):
            with self.assertRaises(ResourceError):
                proc.parse_status(raw, 123)

    def test_pressure_uses_integer_basis_points_and_cpu_full_is_invalid(self):
        memory = proc.parse_pressure(psi_record(), "memory")
        self.assertEqual(memory["some"]["avg10_bp"], 1234)
        self.assertEqual(memory["full"]["total_us"], 12345)
        self.assertTrue(memory["full_valid"])
        cpu = proc.parse_pressure(psi_record(), "cpu")
        self.assertFalse(cpu["full_valid"])
        self.assertEqual(cpu["full"]["avg10_bp"], 123)
        cpu_old = proc.parse_pressure(psi_record().splitlines()[0] + "\n", "cpu")
        self.assertIsNone(cpu_old["full"])
        self.assertFalse(cpu_old["full_valid"])

    def test_pressure_missing_and_malformed_never_fall_back_to_zero(self):
        with patch.object(proc.platform, "system", return_value="Linux"), \
             patch.object(proc, "read_bounded", side_effect=FileNotFoundError(2, "missing")):
            missing = proc.pressure_sample()
        self.assertEqual(missing["attribution"], "unattributed")
        for value in missing["metrics"].values():
            self.assertEqual((value["state"], value["error"]), ("UNAVAILABLE", "pressure-missing"))
            self.assertIsNone(value["some"])
            self.assertIsNone(value["raw"])
        with patch.object(proc.platform, "system", return_value="Linux"), \
             patch.object(proc, "read_bounded", return_value=b"malformed\n"):
            bad = proc.pressure_sample()
        self.assertEqual(bad["metrics"]["cpu"]["raw"], "malformed\n")
        self.assertEqual(bad["metrics"]["cpu"]["error"], "pressure-format")
        for value in (psi_record().replace("12.34", "nan"), psi_record().replace("12.34", "100.01"),
                      psi_record().replace("total=123456", "total=-1"), psi_record() + psi_record(),
                      psi_record().replace("avg60=5.67", "avg10=5.67")):
            with self.assertRaises(ResourceError):
                proc.parse_pressure(value, "memory")

    def test_tcp_requires_exact_loopback_listen_uid_and_unique_inode(self):
        row = "  0: 0100007F:46A1 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 4321 1\n"
        prefix = b"  sl  local_address rem_address st tx_queue rx_queue\n"
        result = backend.tcp_listener(prefix + row.encode(), 18081, 1000)
        self.assertEqual(result["listener_inode"], 4321)
        for modified in (row.replace("0100007F", "00000000"), row.replace(" 0A ", " 01 "),
                         row.replace(" 1000 ", " 1001 "), row + row):
            with self.assertRaises(ResourceError):
                backend.tcp_listener(prefix + modified.encode(), 18081, 1000)

    def test_invalid_identity_and_unsupported_platform_do_not_open_processes(self):
        for pid in (True, 0, -1, "123"):
            with self.assertRaises(ResourceError):
                proc.ProcessReader(pid)
        with patch.object(proc.platform, "system", return_value="Windows"), self.assertRaisesRegex(ResourceError, "unsupported-platform"):
            proc.ProcessReader(123)


LINUX = platform.system() == "Linux" and hasattr(os, "pidfd_open") and hasattr(socket, "SO_PEERCRED")


@unittest.skipUnless(LINUX, "Linux process and Unix peer credentials required")
class ResourceProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aios-res-")
        self.base = Path(self.temporary.name)
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            if child.stdout is not None:
                child.stdout.close()
            if child.stdin is not None:
                child.stdin.close()
        self.temporary.cleanup()

    def spawn(self, code):
        child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.children.append(child)
        return child

    def line(self, child):
        self.assertTrue(select.select([child.stdout], [], [], 10)[0], "fixture process did not respond")
        line = child.stdout.readline()
        self.assertTrue(line, "fixture process exited early")
        return line.strip()

    def server(self):
        child = self.spawn("import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(2); print(s.getsockname()[1],flush=True); time.sleep(60)")
        return child, config_for(int(self.line(child)))

    def test_live_process_identity_cpu_and_declining_rss(self):
        child = self.spawn("import mmap,sys,time; m=mmap.mmap(-1,32*1024*1024); m[:]=b'x'*(32*1024*1024); print('allocated',flush=True); sys.stdin.readline(); m.close(); print('released',flush=True); sys.stdin.readline()")
        self.assertEqual(self.line(child), "allocated")
        with proc.ProcessReader(child.pid) as reader:
            first = reader.sample()
            self.assertEqual(first["process_id"], child.pid)
            self.assertEqual(first["uid"], os.getuid())
            self.assertEqual(first["scope"], "single-linux-process")
            self.assertEqual(first["memory_accuracy"], "kernel-approximate")
            self.assertEqual(first["cpu_total_ns"], (first["user_ticks"] + first["system_ticks"]) * 1_000_000_000 // first["clock_ticks_per_second"])
            child.stdin.write("release\n")
            child.stdin.flush()
            self.assertEqual(self.line(child), "released")
            deadline = time.monotonic() + 5
            while True:
                second = reader.sample()
                if second["rss_pages"] < first["rss_pages"] or time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
            self.assertLess(second["rss_pages"], first["rss_pages"])
            self.assertGreaterEqual(second["cpu_total_ns"], first["cpu_total_ns"])
            self.assertEqual(second["process_start_ticks"], first["process_start_ticks"])

    def test_exit_and_stale_start_ticks_cannot_be_sampled_as_live(self):
        child = self.spawn("import time; print('ready',flush=True); time.sleep(60)")
        self.line(child)
        with proc.ProcessReader(child.pid) as reader:
            identity = reader.identity
            with self.assertRaisesRegex(ResourceError, "process-identity"):
                proc.ProcessReader(child.pid, identity["process_start_ticks"] + 1)
            with self.assertRaisesRegex(ResourceError, "boot-identity"):
                proc.ProcessReader(child.pid, expected_boot_id="00000000-0000-0000-0000-000000000001")
            identity["process_start_ticks"] += 100
            self.assertNotEqual(identity, reader.identity)
            child.terminate()
            child.wait(timeout=5)
            with self.assertRaisesRegex(ResourceError, "process-exited"):
                reader.sample()
        with self.assertRaisesRegex(ResourceError, "process-reader-closed"):
            reader.sample()

    def test_same_pid_with_changed_stat_start_identity_is_rejected(self):
        with proc.ProcessReader(os.getpid()) as reader:
            original = reader._read_pair
            def changed():
                parsed, status, raw_stat, raw_status = original()
                parsed["process_start_ticks"] += 1
                return parsed, status, raw_stat, raw_status
            with patch.object(reader, "_read_pair", side_effect=changed), self.assertRaisesRegex(ResourceError, "process-identity"):
                reader.sample()

    def test_listener_belongs_to_owned_child_and_not_another_process(self):
        child, config = self.server()
        other = self.spawn("import time; print('ready',flush=True); time.sleep(60)")
        self.line(other)
        with proc.ProcessReader(child.pid) as reader:
            proof = backend.listener_proof(reader, config)
            self.assertEqual(proof["fd_target"], "socket:[" + str(proof["listener_inode"]) + "]")
        with proc.ProcessReader(other.pid) as reader, self.assertRaisesRegex(ResourceError, "listener-owner"):
            backend.listener_proof(reader, config)

    def test_authenticated_attestation_fixture_nonce_peer_and_bounds(self):
        child, config = self.server()
        output = self.base / "attester"
        output.mkdir(mode=0o700)
        attester = backend.BackendAttester(output, child, config, capture_kind="fixture")
        stopping = threading.Event()
        errors = []
        def poller():
            try:
                while not stopping.is_set():
                    attester.poll()
                    time.sleep(0.005)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=poller, daemon=True)
        thread.start()
        try:
            value = backend.attest(output, config)
            self.assertEqual(value["capture_kind"], "fixture")
            self.assertEqual(value["peer_pid"], os.getpid())
            self.assertEqual(value["peer_uid"], os.getuid())
            self.assertEqual(value["descriptor"]["process_id"], child.pid)
            self.assertEqual(value["descriptor"], json.loads((output / "backend-source.json").read_bytes()))
            with patch.object(backend, "peer_credentials", return_value=(os.getpid(), os.getuid() + 1, os.getgid())), \
                 self.assertRaisesRegex(ResourceError, "backend-peer"):
                backend.attest(output, config)
            next_value = backend.attest(output, config)
            self.assertNotEqual(next_value["nonce"], value["nonce"])
            self.assertEqual(next_value["descriptor"], value["descriptor"])
            attester._count = backend.MAX_ATTESTATIONS
            size = (output / "backend-attestations.jsonl").stat().st_size
            with self.assertRaisesRegex(ResourceError, "attestation-limit"):
                backend.attest(output, config)
            self.assertEqual((output / "backend-attestations.jsonl").stat().st_size, size)
            self.assertEqual((output / "backend.sock").stat().st_mode & 0o777, 0o600)
        finally:
            stopping.set()
            thread.join(timeout=5)
            attester.close()
        self.assertFalse(errors, errors)
        self.assertFalse((output / "backend.sock").exists())
        self.assertIsNone(child.poll(), "attester must never own backend termination")
        with self.assertRaises(ResourceError):
            backend.attest(output, config)

    def test_malformed_client_has_bounded_poll_and_creates_no_proof(self):
        child, config = self.server()
        output = self.base / "bounded"
        output.mkdir(mode=0o700)
        attester = backend.BackendAttester(output, child, config, capture_kind="fixture")
        try:
            self.assertTrue(attester.poll())
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(str(output / "backend.sock"))
                connection.sendall(b'{"schema_version":1')
                began = time.monotonic()
                self.assertTrue(attester.poll())
                elapsed = time.monotonic() - began
                self.assertLess(elapsed, 2.0)
                response = json.loads(connection.recv(4096))
                self.assertEqual(response["outcome"], "ERROR")
                self.assertIsNone(response["proof"])
            self.assertEqual((output / "backend-attestations.jsonl").read_bytes(), b"")
        finally:
            attester.close()

    def test_backend_exit_stops_proofs_and_missing_owned_socket_closes_cleanly(self):
        child, config = self.server()
        output = self.base / "exited"
        output.mkdir(mode=0o700)
        attester = backend.BackendAttester(output, child, config, capture_kind="fixture")
        self.assertTrue(attester.poll())
        child.terminate()
        child.wait(timeout=5)
        with self.assertRaisesRegex(ResourceError, "backend-exited"):
            attester.poll()
        self.assertEqual((output / "backend-attestations.jsonl").read_bytes(), b"")
        (output / "backend.sock").unlink()
        attester.close()
        attester.close()

    def test_close_refuses_replaced_socket_path_without_deleting_replacement(self):
        child, config = self.server()
        output = self.base / "replaced"
        output.mkdir(mode=0o700)
        attester = backend.BackendAttester(output, child, config, capture_kind="fixture")
        path = output / "backend.sock"
        path.unlink()
        path.write_text("replacement owned by this test")
        with self.assertRaisesRegex(ResourceError, "backend-socket-changed"):
            attester.close()
        self.assertEqual(path.read_text(), "replacement owned by this test")
        self.assertIsNone(child.poll())


if __name__ == "__main__":
    unittest.main()
