"""Execution recipient checks; generated model responses remain fixtures."""
from __future__ import annotations

import ast
import copy
import hashlib
import http.client
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
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_agent import inference
from aios_agent.backend_binding import ExecutionBinding
from aios_resources import ResourceError
from aios_resources.backend import BackendAttester
from execution_output_contract import validate_execution
from test_hosted_resources_management import proof_fixture, sample_fixture


def execution_fixture():
    descriptor = proof_fixture()["descriptor"]
    def sample(pid, start, at, parent=1):
        value = sample_fixture(pid, start, at=at)
        head, tail = value["raw_stat"].rsplit(") ", 1)
        fields = tail.split()
        fields[1] = str(parent)
        value["raw_stat"] = head + ") " + " ".join(fields) + "\n"
        return value
    def probe(at):
        return {"read_start_ns": at, "read_end_ns": at + 100,
            "backend": sample(234, 222, at + 10, 233), "launcher": sample(233, 221, at + 40),
            "listener_proof": {**proof_fixture()["listener_proof"],
                "raw_tcp_line": proof_fixture()["listener_proof"]["raw_tcp_line"].strip()}}
    def tcp(local, remote, inode):
        return f"0: 0100007F:{local:04X} 0100007F:{remote:04X} 01 00000000:00000000 00:00000000 00000000 1000 0 {inode} 1"
    send = {"read_start_ns": 2000, "read_end_ns": 2200,
        "client_address": {"address": "127.0.0.1", "port": 49152},
        "server_address": {"address": "127.0.0.1", "port": 18081},
        "raw_client_tcp_line": tcp(49152, 18081, 400), "raw_server_tcp_line": tcp(18081, 49152, 401),
        "client_fd_number": 9, "client_fd_target": "socket:[400]", "server_fd_number": 10, "server_fd_target": "socket:[401]",
        "backend": sample(234, 222, 2010, 233), "launcher": sample(233, 221, 2040), "client": sample(456, 444, 2070)}
    return {"schema_version": 1, "descriptor": descriptor, "capture_kind": "fixture",
            "before": probe(1000), "send": send, "after": probe(3000)}


class ExecutionContractTests(unittest.TestCase):
    def test_complete_fixture_replays_without_live_promotion(self):
        value = execution_fixture()
        validate_execution(value, descriptor=value["descriptor"])
        with self.assertRaises(ValueError):
            validate_execution(value, require_live=True)

    def test_checker_has_no_runtime_import(self):
        tree = ast.parse((ROOT / "tools/hosted/execution_output_contract.py").read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith("aios_") for name in imports))

    def test_connection_must_have_reverse_established_tuple_and_owned_inode(self):
        for field, changed in (("raw_server_tcp_line", execution_fixture()["send"]["raw_server_tcp_line"].replace(" 01 ", " 0A ")),
                ("server_fd_target", "socket:[999]"), ("client_fd_target", "socket:[401]"), ("server_fd_number", True)):
            value = execution_fixture()
            value["send"][field] = changed
            with self.assertRaises(ValueError, msg=field):
                validate_execution(value)

    def test_wrong_receiver_or_start_ticks_cannot_borrow_descriptor(self):
        value = execution_fixture()
        changed = copy.deepcopy(value["descriptor"])
        changed["process_start_ticks"] += 1
        with self.assertRaises(ValueError):
            validate_execution(value, descriptor=changed)
        value["send"]["backend"]["process_start_ticks"] += 1
        with self.assertRaises(ValueError):
            validate_execution(value)

    def test_probe_and_send_order_and_complete_fields_are_required(self):
        for edit in (lambda v: v.update(after=None), lambda v: v["send"].update(read_start_ns=900),
                     lambda v: v["before"].update(read_end_ns=999), lambda v: v.update(extra=True)):
            value = execution_fixture()
            edit(value)
            with self.assertRaises(ValueError):
                validate_execution(value)

    def test_launcher_relationship_and_same_boot_are_checked(self):
        value = execution_fixture()
        value["before"]["backend"]["raw_stat"] = value["before"]["backend"]["raw_stat"].replace(") S 233 ", ") S 232 ")
        with self.assertRaises(ValueError):
            validate_execution(value)

    def test_inference_fixture_schema2_does_not_invent_execution_proof(self):
        from test_hosted_inference import config
        with mock.patch.object(inference.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            value = inference.infer(config(), "Hello")
        self.assertEqual(value["schema_version"], 2)
        self.assertIsNone(value["backend_execution"])
        self.assertIsNone(value["response_body"])


SERVER = '''import http.server,json,sys,threading
from pathlib import Path
class Handler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  data=self.rfile.read(int(self.headers['Content-Length']))
  with Path(sys.argv[2]).open('ab') as out:out.write(data+b'\\n')
  raw=json.dumps({'model':'fixture-execution','content':'Fixture only.','tokens_predicted':3}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def log_message(self,*args):pass
class Server(http.server.ThreadingHTTPServer):allow_reuse_address=True
server=Server(('127.0.0.1',int(sys.argv[1])),Handler)
thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=0.01),daemon=True);thread.start()
print(server.server_port,flush=True)
for line in sys.stdin:
 if line.strip()=='close':
  server.shutdown();server.server_close();thread.join(2);print('closed',flush=True)
'''


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open") and hasattr(socket, "SO_PEERCRED"),
                     "Linux execution identity and socket ownership required")
class ExecutionLinuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aios-ex-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / "backend.py"
        self.script.write_text(SERVER)
        self.children = []
        self.addCleanup(self.cleanup_children)
        self.child, self.port, self.requests = self.start_server(0)
        self.model = self.root / "model"
        self.model.write_bytes(b"explicit execution fixture")
        self.config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{self.port}", "model_id": "fixture-execution",
            "model_path": str(self.model), "model_sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
            "backend_path": str(self.script), "backend_sha256": hashlib.sha256(self.script.read_bytes()).hexdigest(),
            "provenance_sha256": "9" * 64}
        self.control = self.root / "control"
        self.control.mkdir(mode=0o700)
        self.attester = BackendAttester(self.control, self.child, self.config, "fixture")
        self.stopping = threading.Event()
        self.errors = []
        def serve():
            try:
                while not self.stopping.is_set():
                    self.attester.poll()
                    time.sleep(0.01)
            except Exception as exc:
                self.errors.append(exc)
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_attester)

    def close_attester(self):
        self.stopping.set()
        self.thread.join(5)
        self.attester.close()

    def cleanup_children(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                child.wait(5)
            child.stdin.close()
            child.stdout.close()

    def start_server(self, port):
        log = self.root / ("requests-" + str(len(self.children)) + ".log")
        child = subprocess.Popen([sys.executable, str(self.script), str(port), str(log)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        self.children.append(child)
        self.assertTrue(select.select([child.stdout], [], [], 10)[0])
        return child, int(child.stdout.readline()), log

    def test_actual_guarded_request_and_observer_socket_failure_independence(self):
        with ExecutionBinding(self.control, self.config, "fixture") as binding:
            self.assertEqual(binding.initial_proof["descriptor"], binding.descriptor)
            self.close_attester()
            binding.check()
            value = inference.infer(self.config, "A guarded fixture request.", backend_descriptor=binding.descriptor,
                                    backend_capture_kind="fixture")
            self.assertEqual(value["outcome"], "OK", value)
            validate_execution(value["backend_execution"], self.config, descriptor=binding.descriptor)
            self.assertTrue(self.requests.read_bytes())
            with self.assertRaises(ValueError):
                validate_execution(value["backend_execution"], self.config, require_live=True)

    def test_same_port_replacement_between_check_and_connect_receives_no_prompt(self):
        with ExecutionBinding(self.control, self.config, "fixture") as binding:
            self.close_attester()
            original_connect = http.client.HTTPConnection.connect
            replacement_logs = []
            def replace_then_connect(connection):
                self.child.stdin.write("close\n")
                self.child.stdin.flush()
                self.assertTrue(select.select([self.child.stdout], [], [], 5)[0])
                self.assertEqual(self.child.stdout.readline().strip(), "closed")
                _child, port, log = self.start_server(self.port)
                self.assertEqual(port, self.port)
                replacement_logs.append(log)
                original_connect(connection)
            with mock.patch.object(http.client.HTTPConnection, "connect", replace_then_connect):
                with self.assertRaisesRegex(ResourceError, "backend-recipient"):
                    inference.worker(self.config, inference.request_body("Must not be delivered."), binding.descriptor, "fixture")
            self.assertFalse(self.requests.exists())
            self.assertEqual(len(replacement_logs), 1)
            self.assertFalse(replacement_logs[0].exists(), "replacement must receive zero prompt bytes")

    def test_dead_backend_is_not_replaced_by_endpoint_alias(self):
        with ExecutionBinding(self.control, self.config, "fixture") as binding:
            self.close_attester()
            self.child.terminate()
            self.child.wait(5)
            with self.assertRaises(ResourceError):
                binding.check()
