"""Local fake-server tests for real transport boundaries, never model evidence."""
from __future__ import annotations

import hashlib
import http.server
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_agent import inference


def config(port: int = 18081) -> dict:
    return {"schema_version": 1, "endpoint": "http://127.0.0.1:" + str(port), "model_id": "fixture-model",
            "model_path": "fixture.gguf", "backend_path": "fixture-backend", "model_sha256": "a" * 64,
            "backend_sha256": "b" * 64, "provenance_sha256": "c" * 64}


class InferenceTests(unittest.TestCase):
    def test_invalid_config_rejects_before_any_request(self):
        for field, value in (("schema_version", True), ("endpoint", "https://example.com:443"),
                             ("endpoint", "http://127.0.0.1:18081/other"), ("endpoint", "http://name:18081"),
                             ("endpoint", "http://user:pass@127.0.0.1:18081"), ("endpoint", "http://127.0.0.1:80"),
                             ("model_sha256", ""), ("backend_path", "bad\x1bpath")):
            with self.subTest(field=field, value=value), mock.patch.object(inference.subprocess, "run") as execute:
                with self.assertRaises(ValueError):
                    inference.infer({**config(), field: value}, "hello")
                execute.assert_not_called()

    def test_actual_file_integrity_and_missing_model_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model, backend = root / "model", root / "backend"
            model.write_bytes(b"real fixture file")
            backend.write_bytes(b"fixture executable")
            record = {**config(), "model_path": str(model), "backend_path": str(backend),
                      "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                      "backend_sha256": hashlib.sha256(backend.read_bytes()).hexdigest()}
            path = root / "config.json"
            path.write_text(json.dumps(record))
            self.assertEqual(inference.load_config(path), record)
            model.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "model-hash-mismatch"):
                inference.load_config(path)
            model.unlink()
            with self.assertRaises(OSError):
                inference.load_config(path)

    def test_prompt_limits_and_exact_no_thinking_format(self):
        for prompt in ("", "   ", "x\x00y", "한" * 1366, None):
            with self.subTest(prompt=str(prompt)[:20]), self.assertRaises(ValueError):
                inference.request_body(prompt)
        body = json.loads(inference.request_body("Explain memory", warmup=True))
        self.assertEqual(body["n_predict"], 8)
        self.assertTrue(body["prompt"].endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n"))
        self.assertFalse(body["stream"])

    def test_parser_rejects_duplicate_nonfinite_complex_and_oversize(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{"x":' + b'[' * 20 + b'0' + b']' * 20 + b'}',
                    b" " * (inference.MAX_BODY + 1)):
            with self.subTest(raw=raw[:25]), self.assertRaises(ValueError):
                inference.parse(raw)

    def test_outer_deadline_and_bad_worker_cannot_create_success(self):
        with mock.patch.object(inference.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            receipt = inference.infer(config(), "hello")
        self.assertEqual((receipt["outcome"], receipt["error"]), ("ERROR", "backend-timeout"))
        self.assertIsNone(receipt["response_sha256"])
        self.assertGreaterEqual(receipt["elapsed_ns"], 0)
        with mock.patch.object(inference.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"{}", b"")):
            self.assertEqual(inference.infer(config(), "hello")["outcome"], "ERROR")

    def test_local_transport_receipt_and_wrong_model_empty_bool_reject(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            payload = {"content": "fixture response", "tokens_predicted": 3, "model": "fixture-model"}
            requests = []
            def do_POST(self):
                Handler.requests.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps(Handler.payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = config(server.server_port)
            receipt = inference.infer(settings, "A fixture input")
            self.assertEqual(receipt["outcome"], "OK", receipt)
            self.assertEqual(receipt["request_sha256"], hashlib.sha256(Handler.requests[0][1]).hexdigest())
            self.assertEqual(receipt["response_sha256"], hashlib.sha256(receipt["response_body"].encode()).hexdigest())
            self.assertEqual(Handler.requests[0][0], "/completion")
            for field, value in (("model", "other-model"), ("content", ""), ("tokens_predicted", True), ("tokens_predicted", 65)):
                old = Handler.payload
                Handler.payload = {**old, field: value}
                with self.subTest(field=field):
                    self.assertEqual(inference.infer(settings, "A fixture input")["outcome"], "ERROR")
                Handler.payload = old
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == "__main__":
    unittest.main()
