from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_console import network


class _LocalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((self.command, self.path, dict(self.headers)))
        if self.path == "/slow":
            self.server.release.wait(4)
        body = {"/ok": b"AIOS local response\n",
                "/large": b"0123456789" * 8000,
                "/controls": ("\x1b[31mAIOS\x00\n\u202e" + "\ud55c" * 1000).encode("utf-8"),
                "/missing": b"not found", "/redirect": b"moved"}.get(self.path, b"ok")
        status = {"/missing": 404, "/redirect": 302}.get(self.path, 200)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(100 if self.path == "/short" else len(body)))
            if self.path == "/redirect":
                self.send_header("Location", "/ok")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        self.close_connection = True


class NetworkInputTests(unittest.TestCase):
    def test_invalid_urls_never_launch_or_echo_credentials(self):
        invalid = [None, "", "http://", "http://u:p@example.com/", "http://@example.com/",
                   "http://example.com/\r\nInjected:yes", "http://example.com/%0aattack",
                   "http://example.com/\x1b[31m", "http://example.com/\u202e",
                   "http://example.com:0/", "http://example.com:65536/",
                   "http://example.com:/", "http://example.com:bad/",
                   "http://example.com\\evil/", "http://exa mple.com/",
                   "http://example.com/%zz", "http://example.com/#fragment",
                   "http://[broken/", "http://example.com/" + "x" * 2048]
        with mock.patch.object(network.subprocess, "run") as run:
            for url in invalid:
                with self.subTest(url=repr(url)):
                    result = network.fetch_url(url)
                    self.assertEqual(result["outcome"], "ERROR")
                    self.assertEqual(result["error"], "invalid_url")
                    self.assertEqual(result["url"], "<invalid>")
            run.assert_not_called()

    def test_only_http_schemes_are_supported(self):
        for url in ["file:///etc/passwd", "ftp://example.com/a", "javascript:alert(1)",
                    "data:text/plain,hello"]:
            with self.subTest(url=url):
                result = network.fetch_url(url)
                self.assertEqual(result["error"], "unsupported_protocol")
                self.assertIsNone(result["protocol"])

    def test_invalid_hosts_never_launch(self):
        with mock.patch.object(network.subprocess, "run") as run:
            for host in [None, "", "a" * 254, "a" * 64 + ".com", "-host", "host-",
                         "host..test", "host...", "user@host", "host/a", "host\n",
                         "\u202ehost", "[::1]", "fe80::1%eth0", "\ud800"]:
                with self.subTest(host=repr(host)):
                    self.assertEqual(network.resolve_host(host)["error"], "invalid_host")
            run.assert_not_called()

    def test_timeout_and_byte_limits_reject_bool_nonfinite_and_huge_int(self):
        with mock.patch.object(network.subprocess, "run") as run:
            for timeout in [None, True, False, -1, 0, 0.01, 30.01, float("inf"),
                            float("nan"), "5", 10 ** 1000]:
                with self.subTest(timeout=repr(timeout)):
                    self.assertEqual(network.resolve_host("localhost", timeout=timeout)["error"],
                                     "invalid_timeout")
                    self.assertEqual(network.fetch_url("http://localhost", timeout=timeout)["error"],
                                     "invalid_timeout")
            for maximum in [None, True, False, 0, -1, 65537, 3.5, "10"]:
                with self.subTest(maximum=maximum):
                    self.assertEqual(network.fetch_url("http://localhost", max_bytes=maximum)["error"],
                                     "invalid_max_bytes")
            run.assert_not_called()

    def test_dns_failure_is_code_only(self):
        with mock.patch.object(network.socket, "getaddrinfo",
                               side_effect=socket.gaierror(-2, "private diagnostic")):
            result = network._resolve_worker("missing.invalid", 1)
        self.assertEqual(result["error"], "dns_failed")
        self.assertEqual(result["addresses"], [])
        self.assertNotIn("private", json.dumps(result))

    def test_dns_unique_and_bounded_addresses(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"127.0.0.{i}", 0))
                     for i in range(1, 40)]
        with mock.patch.object(network.socket, "getaddrinfo", return_value=addresses * 2) as call:
            result = network._resolve_worker("localhost", 1)
        self.assertEqual(result["outcome"], "OK")
        self.assertEqual(len(result["addresses"]), 32)
        self.assertEqual(len(set(result["addresses"])), 32)
        self.assertEqual(call.call_args.kwargs["type"], socket.SOCK_STREAM)
        self.assertEqual(call.call_args.kwargs["proto"], socket.IPPROTO_TCP)

    def test_live_loopback_dns_worker(self):
        result = network.resolve_host("localhost")
        self.assertEqual(result["outcome"], "OK", result)
        self.assertTrue(result["addresses"])
        self.assertIs(type(result["elapsed_ms"]), int)
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_hung_worker_is_terminated_under_outer_deadline(self):
        # A deliberately blocked worker models a resolver that ignores socket timeout.
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / "blocked_worker.py"
            worker.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
            start = time.monotonic()
            with mock.patch.object(network, "__file__", str(worker)):
                result = network.resolve_host("localhost", timeout=0.2)
            elapsed = time.monotonic() - start
        self.assertEqual(result["error"], "timeout")
        self.assertLess(elapsed, 3)

    def test_worker_boundary_has_no_shell_or_implicit_key_log(self):
        response = network._base("resolve", "localhost")
        response.update(outcome="OK", addresses=["127.0.0.1"])
        process = subprocess.CompletedProcess([], 0, json.dumps(response).encode(), b"")
        with mock.patch.dict(os.environ, {"SSLKEYLOGFILE": "unwanted-key-file"}):
            with mock.patch.object(network.subprocess, "run", return_value=process) as run:
                result = network.resolve_host("localhost", timeout=0.75)
        self.assertEqual(result["outcome"], "OK")
        self.assertEqual(run.call_args.kwargs["timeout"], 0.75)
        self.assertNotIn("SSLKEYLOGFILE", run.call_args.kwargs["env"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(run.call_args.args[0][0], sys.executable)
        self.assertIn("--network-worker", run.call_args.args[0])

    def test_failed_malformed_and_duplicate_worker_output_is_not_success(self):
        normal = {"operation": "resolve", "host": "localhost", "addresses": ["127.0.0.1"],
                  "outcome": "OK", "error": None, "elapsed_ms": 0}
        payload = json.dumps(normal).encode()
        cases = [subprocess.CompletedProcess([], 1, payload, b""),
                 subprocess.CompletedProcess([], 0, payload, b"warning"),
                 subprocess.CompletedProcess([], 0, b"not json", b""),
                 subprocess.CompletedProcess([], 0, b"x" * 32769, b""),
                 subprocess.CompletedProcess([], 0, payload[:-1] + b',"outcome":"OK"}', b"")]
        for changed in [{"host": "different"}, {"addresses": []}, {"elapsed_ms": False},
                        {"addresses": ["127.0.0.1", "127.0.0.1"]}, {"extra": 1}]:
            cases.append(subprocess.CompletedProcess([], 0, json.dumps(normal | changed).encode(), b""))
        for process in cases:
            with self.subTest(process=process):
                with mock.patch.object(network.subprocess, "run", return_value=process):
                    self.assertEqual(network.resolve_host("localhost")["error"], "worker_failed")
        with mock.patch.object(network.subprocess, "run", side_effect=OSError("private path")):
            self.assertEqual(network.resolve_host("localhost")["error"], "worker_failed")

    def test_tls_certificate_failure_never_falls_back_to_plaintext(self):
        fake = mock.Mock()
        fake.connect.side_effect = ssl.SSLCertVerificationError(1, "private diagnostic")
        with mock.patch.object(network.http.client, "HTTPSConnection", return_value=fake) as secure:
            with mock.patch.object(network.http.client, "HTTPConnection") as plaintext:
                result = network._fetch_worker("https://localhost/", 1, 1024)
        context = secure.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(result["error"], "tls_certificate")
        self.assertFalse(result["tls_verified"])
        self.assertNotIn("private", json.dumps(result))
        fake.close.assert_called_once()
        plaintext.assert_not_called()

    def test_unverified_context_is_rejected_before_connect(self):
        context = mock.Mock(check_hostname=False, verify_mode=ssl.CERT_NONE)
        with mock.patch.object(network.ssl, "create_default_context", return_value=context):
            with mock.patch.object(network.http.client, "HTTPSConnection") as secure:
                result = network._fetch_worker("https://localhost/", 1, 1024)
        self.assertEqual(result["error"], "tls_failed")
        secure.assert_not_called()


class NetworkLocalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalHandler)
        cls.server.requests = []
        cls.server.release = threading.Event()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.release.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_real_get_ignores_proxy_environment_and_hashes_body(self):
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1",
                                         "HTTP_PROXY": "http://127.0.0.1:1",
                                         "ALL_PROXY": "http://127.0.0.1:1"}):
            result = network.fetch_url(self.base_url + "/ok")
        body = b"AIOS local response\n"
        self.assertEqual(result["outcome"], "OK", result)
        self.assertEqual(result["status"], 200)
        self.assertFalse(result["tls_verified"])
        self.assertEqual(result["received_bytes"], len(body))
        self.assertEqual(result["body_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["body_preview"], "AIOS local response ")
        self.assertFalse(result["truncated"])
        self.assertEqual(self.server.requests[-1][0], "GET")

    def test_large_response_is_bounded_and_explicitly_truncated(self):
        result = network.fetch_url(self.base_url + "/large", max_bytes=31)
        expected = (b"0123456789" * 4)[:31]
        self.assertEqual(result["outcome"], "OK", result)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["received_bytes"], 31)
        self.assertEqual(result["body_sha256"], hashlib.sha256(expected).hexdigest())

    def test_preview_removes_terminal_controls_and_bounds_utf8_bytes(self):
        result = network.fetch_url(self.base_url + "/controls")
        self.assertEqual(result["outcome"], "OK", result)
        preview = result["body_preview"]
        self.assertLessEqual(len(preview.encode("utf-8")), 2048)
        self.assertTrue(all(c.isprintable() for c in preview))
        self.assertNotIn("\x1b", preview)
        self.assertNotIn("\u202e", preview)

    def test_http_error_keeps_status_and_bounded_diagnostic_body(self):
        result = network.fetch_url(self.base_url + "/missing")
        self.assertEqual(result["error"], "http_status")
        self.assertEqual(result["status"], 404)
        self.assertEqual(result["body_preview"], "not found")

    def test_redirect_is_not_followed(self):
        before = len(self.server.requests)
        result = network.fetch_url(self.base_url + "/redirect")
        self.assertEqual(result["error"], "http_status")
        self.assertEqual(result["status"], 302)
        self.assertEqual([request[1] for request in self.server.requests[before:]], ["/redirect"])

    def test_truncated_transport_is_error_even_with_200_status(self):
        result = network.fetch_url(self.base_url + "/short")
        self.assertEqual(result["error"], "http_protocol")
        self.assertEqual(result["status"], 200)
        self.assertIsNone(result["body_sha256"])

    def test_server_that_never_sends_headers_hits_total_timeout(self):
        start = time.monotonic()
        result = network.fetch_url(self.base_url + "/slow", timeout=0.5)
        self.assertEqual(result["error"], "timeout")
        self.assertLess(time.monotonic() - start, 3)


if __name__ == "__main__":
    unittest.main()
