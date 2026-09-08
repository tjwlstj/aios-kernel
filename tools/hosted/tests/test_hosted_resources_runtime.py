"""Actual Linux MAIN/resource integration with explicitly fixture inference."""
from __future__ import annotations

import hashlib
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

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted" / "linux"))
from aios_agent import client
from aios_resources.backend import BackendAttester


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open") and hasattr(socket, "SO_PEERCRED"),
                     "Linux MAIN and backend resource IPC required")
class ResourceRuntimeTests(unittest.TestCase):
    def test_actual_link_sample_request_restart_and_failed_observation_preserve_main(self):
        with tempfile.TemporaryDirectory(prefix="aios-rm-") as temporary:
            base = Path(temporary)
            state, output = base / "main", base / "backend"
            output.mkdir(mode=0o700)
            script = base / "fixture_backend.py"
            script.write_text('''import http.server,json,time
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        deadline=time.process_time()+0.03
        while time.process_time()<deadline:
            pass
        value={'content':'Explicit fixture completion.','tokens_predicted':4,'model':'fixture-resource-main'}
        raw=json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self,*args):
        pass
server=http.server.HTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()
''', encoding="utf-8")
            model = base / "fixture.model"
            model.write_bytes(b"explicit resource fixture model")
            child = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
            attester = thread = None
            stopping = threading.Event()
            errors = []
            try:
                self.assertTrue(select.select([child.stdout], [], [], 10)[0])
                port = int(child.stdout.readline())
                config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{port}",
                    "model_id": "fixture-resource-main", "model_path": str(model),
                    "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "backend_path": str(script),
                    "backend_sha256": hashlib.sha256(script.read_bytes()).hexdigest(), "provenance_sha256": "9" * 64}
                config_path = base / "config.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                attester = BackendAttester(output, child, config, capture_kind="fixture")
                def poll():
                    try:
                        while not stopping.is_set():
                            attester.poll()
                            time.sleep(0.01)
                    except Exception as exc:
                        errors.append(exc)
                thread = threading.Thread(target=poll, daemon=True)
                thread.start()

                def command(action, **kwargs):
                    return client.control(state, action, **kwargs)
                def okay(action, **kwargs):
                    value = command(action, **kwargs)
                    self.assertEqual(value["outcome"], "OK", value)
                    return value

                first = okay("start", config=config_path, fixture_backend=True)
                okay("room-discover")
                okay("room-bind")
                linked = okay("resources-link", backend_dir=output)
                self.assertTrue(linked["resource_result"]["relation_current"])
                self.assertFalse(linked["resource_result"]["ownership_valid"])
                self.assertEqual(linked["resource_result"]["resource_actions"], "UNSUPPORTED")
                repeated = okay("resources-link", backend_dir=output)
                self.assertEqual(repeated["resource_result"]["relation"], linked["resource_result"]["relation"])
                self.assertEqual(repeated["resource_result"]["relation"]["relation_generation"], 1)
                sampled = okay("resources-sample")
                self.assertIsNotNone(sampled["resource_result"]["observation"])
                self.assertEqual(sampled["resource_result"]["observation"]["kind"], "sample")
                self.assertEqual(sampled["resource_result"]["observation"]["source_before"], sampled["source_record"])
                answer = okay("ask", prompt="Describe AIOS.")
                self.assertEqual(answer["inference_receipt"]["outcome"], "OK")
                measured = answer["resource_result"]
                self.assertEqual(measured["outcome"], "OK", measured)
                self.assertIsNotNone(measured["observation"])
                self.assertGreater(measured["observation"]["cpu"]["backend"]["cpu_time_ns"], 0)
                self.assertEqual(measured["observation"]["request_id"], answer["inference_receipt"]["request_id"])
                cached = okay("resources-status")
                self.assertEqual(cached["resource_result"]["action"], "status")
                self.assertEqual(cached["resource_result"]["observation"], measured["observation"])
                self.assertEqual(cached["source_record"], answer["source_record"])
                self.assertEqual(answer["source_record"]["source_generation"], 1)

                restarted = okay("restart", fixture_backend=True)
                self.assertNotEqual(restarted["source_record"]["source_instance"], first["source_record"]["source_instance"])
                stale = command("resources-status")
                self.assertEqual(stale["outcome"], "ERROR")
                self.assertFalse(stale["resource_result"]["relation_current"])
                okay("room-discover")
                okay("room-reconcile")
                relinked = okay("resources-link", backend_dir=output)
                relation = relinked["resource_result"]["relation"]
                self.assertEqual(relation["relation_generation"], 2)
                okay("ask", prompt="After explicit relink.")

                # Only the observer is closed. Actual backend inference remains
                # available and must not lose MAIN readiness or binding validity.
                stopping.set()
                thread.join(timeout=5)
                attester.close()
                before = okay("status")
                degraded = okay("ask", prompt="Inference still works without observations.")
                self.assertEqual(degraded["inference_receipt"]["outcome"], "OK")
                self.assertEqual(degraded["resource_result"]["outcome"], "ERROR")
                self.assertIsNone(degraded["resource_result"]["observation"])
                self.assertTrue(degraded["source_record"]["model_ready"])
                self.assertEqual(degraded["source_record"]["source_generation"], before["source_record"]["source_generation"])
                self.assertTrue(degraded["management_snapshot"]["binding_current"])
                self.assertEqual(okay("stop")["state"], "STOPPED")
                sys.path.insert(0, str(ROOT / "tools" / "hosted"))
                from verify_agent import verify_agent_runs
                verified = verify_agent_runs(state, require_live=False)
                self.assertEqual(verified["outcome"], "PASS", verified)
                self.assertEqual(verify_agent_runs(state, require_live=True)["outcome"], "FAIL")
                self.assertFalse(errors, errors)
            finally:
                stopping.set()
                if thread is not None:
                    thread.join(timeout=5)
                client.control(state, "stop")
                if attester is not None:
                    attester.close()
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)
                if child.stdout is not None:
                    child.stdout.close()
                for instance, owned in list(client._CHILDREN.items()):
                    if owned.poll() is None:
                        client._finish_failed_start(owned)
                    client._CHILDREN.pop(instance, None)


if __name__ == "__main__":
    unittest.main()
