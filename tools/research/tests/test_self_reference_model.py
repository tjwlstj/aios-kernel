"""Mocked research wrapper tests: no model, server, or network is launched."""
import hashlib
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import self_reference_model as model


class LocalModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        (self.cache / "provenance-receipt.json").write_text('{"retained":true}')
        self.output = self.root / "artifacts"
        self.process = Mock(pid=4321, returncode=None)
        self.process.poll.side_effect = lambda: self.process.returncode

        def wait(timeout):
            self.process.returncode = 1
            return 1

        self.process.wait.side_effect = wait
        original = model._file_record

        def record(path):
            if path.name == model.MODEL_NAME:
                return {"path": str(path), "bytes": 639446688, "sha256": model.MODEL_SHA256}
            if path.name == model.BACKEND_NAME:
                return {"path": str(path), "bytes": 42328074, "sha256": model.BACKEND_SHA256}
            return original(path)

        self.records = patch.object(model, "_file_record", side_effect=record).start()
        self.popen = patch.object(model.subprocess, "Popen", return_value=self.process).start()
        self.exchange = patch.object(model.LocalModel, "_exchange", side_effect=self.reply).start()
        self.reservation = patch.object(model.socket, "socket").start()
        self.reservation.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 56789)
        self.addCleanup(patch.stopall)
        self.last_payload = None
        self.mutation = None
        self.token_status = 200

    def reply(self, path, payload, deadline, limit):
        if path == "/health":
            return 200, b'{"status":"ok"}'
        if path == "/tokenize":
            return self.token_status, model._encoded({"tokens": list(range(20))})
        self.last_payload = payload
        request = json.loads(payload)
        value = {"content": "  exact raw answer\n", "tokens_predicted": 7,
                 "tokens_evaluated": 20, "model": model.MODEL_ID,
                 "prompt": request["prompt"], "truncated": False,
                 "stop": True, "stop_type": "eos"}
        if "grammar" in request:
            value["generation_settings"] = {"grammar": request["grammar"], "grammar_lazy": False}
        if self.mutation:
            self.mutation(value)
        return 200, model._encoded(value)

    def instance(self, **kwargs):
        return model.LocalModel(self.cache, self.output, **kwargs)

    def test_public_result_retains_unicode_raw_prompt_and_exact_content(self):
        with self.instance() as local:
            result = local.query("연구용 시스템", "관측 값은 3이다.")
            self.assertEqual(result["content"], "  exact raw answer\n")
            request = json.loads(self.last_payload)
            self.assertEqual(set(request), {"prompt", "n_predict", "temperature", "seed", "cache_prompt", "stream"})
            self.assertEqual(self.last_payload, model._encoded({"prompt": request["prompt"], "n_predict": 192,
                "temperature": 0, "seed": 1, "cache_prompt": False, "stream": False}))
            self.assertEqual((request["n_predict"], request["temperature"], request["seed"]), (192, 0, 1))
            self.assertIs(request["stream"], False)
            self.assertIs(request["cache_prompt"], False)
            self.assertIn("<|im_start|>system\n연구용 시스템<|im_end|>", request["prompt"])
            self.assertIn(" /no_think<|im_end|>", request["prompt"])
            self.assertEqual(result["request_sha256"], hashlib.sha256(self.last_payload).hexdigest())
            self.assertEqual(Path(result["raw_request_path"]).read_bytes(), self.last_payload)
            raw = Path(result["raw_response_path"]).read_bytes()
            self.assertEqual(result["raw_response"], json.loads(raw))
            self.assertEqual(result["response_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(result["tokens"], {"prompt": 20, "predicted": 7, "tokenized_prompt": 20})
        cleanup = json.loads((self.output / "cleanup.json").read_text())
        self.assertTrue(cleanup["reaped"])
        self.assertEqual(cleanup["returncode"], 1)
        self.assertEqual(cleanup["termination_kind"], "host_termination")
        self.assertFalse(cleanup["linux_normal_stop_claim"])
        self.assertEqual(set(cleanup["logs"]), {"backend-stdout.log", "backend-stderr.log"})
        self.assertEqual((self.output / "cache-provenance-receipt.json").read_bytes(),
                         (self.cache / "provenance-receipt.json").read_bytes())

    def test_hidden_windows_launch_uses_exact_limits_and_private_dynamic_port(self):
        with patch.object(model, "IS_WINDOWS", True), self.instance(context_size=1024) as local:
            command = self.popen.call_args.args[0]
            self.assertEqual(command[0], str(self.cache.resolve() / model.BACKEND_NAME))
            for flag, value in (("--host", "127.0.0.1"), ("--port", "56789"), ("--gpu", "disable"),
                                ("-c", "1024"), ("-t", "2"), ("-np", "1"), ("-b", "64"), ("-ub", "64")):
                self.assertEqual(command[command.index(flag) + 1], value)
            self.assertIn("--no-webui", command)
            self.assertIn("--nologo", command)
            self.assertEqual(self.popen.call_args.kwargs["creationflags"], 0x08000000)
            self.reservation.return_value.__enter__.return_value.bind.assert_called_once_with(("127.0.0.1", 0))
        self.popen.assert_called_once()

    def test_fresh_prompts_have_distinct_hashes_and_query_directories(self):
        with self.instance() as local:
            one = local.query("system", "one")
            two = local.query("system", "two")
            self.assertNotEqual(one["request_sha256"], two["request_sha256"])
            self.assertNotEqual(one["query_artifact_dir"], two["query_artifact_dir"])
            self.assertNotIn("one", json.loads(Path(two["raw_request_path"]).read_bytes())["prompt"])

    def test_static_grammar_is_sent_exactly_and_echoed(self):
        self.mutation = lambda value: value.update(content='{"action":"OBSERVE","expected_revision":null,"prediction":"OBSERVED","attribution":"UNKNOWN"}')
        with self.instance() as local:
            result = local.query("system", "user", grammar=model.DECISION_GRAMMAR)
        request = json.loads(self.last_payload)
        self.assertEqual(request["grammar"], model.DECISION_GRAMMAR)
        self.assertEqual(result["raw_response"]["generation_settings"],
                         {"grammar": model.DECISION_GRAMMAR, "grammar_lazy": False})
        self.assertEqual(result["request_sha256"], hashlib.sha256(self.last_payload).hexdigest())

    def test_arbitrary_grammar_is_rejected_before_any_query_and_reaps(self):
        for index, grammar in enumerate(("", "root ::= []", True, {"grammar": "arbitrary"})):
            with self.subTest(grammar=grammar):
                self.output = self.root / ("invalid-grammar-" + str(index))
                self.process.returncode = None
                with self.assertRaisesRegex(model.ModelError, "invalid_grammar"), self.instance() as local:
                    local.query("system", "user", grammar=grammar)
                self.assertFalse(list(self.output.glob("query-*")))
                self.assertTrue(json.loads((self.output / "cleanup.json").read_bytes())["reaped"])
        self.assertFalse(any(call.args[0] in ("/tokenize", "/completion") for call in self.exchange.call_args_list))

    def test_grammar_echo_missing_changed_or_lazy_is_invalid(self):
        for index, settings in enumerate((None, {"grammar": "", "grammar_lazy": False},
                                         {"grammar": model.DECISION_GRAMMAR, "grammar_lazy": True})):
            with self.subTest(settings=settings):
                self.output = self.root / ("grammar-echo-" + str(index))
                self.process.returncode = None
                self.mutation = lambda value, settings=settings: value.update(generation_settings=settings)
                with self.assertRaisesRegex(model.ModelError, "grammar_not_applied"), self.instance() as local:
                    local.query("system", "user", grammar=model.DECISION_GRAMMAR)
                self.assertEqual(json.loads((self.output / "query-0001/result.json").read_bytes())["outcome"], "INVALID")
        self.assertEqual(sum(call.args[0] == "/completion" for call in self.exchange.call_args_list), 3)

    def test_grammar_requires_eos_and_unconstrained_word_stop_remains_allowed(self):
        self.mutation = lambda value: value.update(stop_type="word")
        with self.instance() as local:
            local.query("system", "user")
        self.output = self.root / "grammar-word-stop"
        self.process.returncode = None
        with self.assertRaisesRegex(model.ModelError, "incomplete_generation"), self.instance() as local:
            local.query("system", "user", grammar=model.DECISION_GRAMMAR)

    def test_invalid_parameters_do_not_create_artifacts_or_launch(self):
        for kwargs in ({"seed": True}, {"seed": -1}, {"context_size": 10}, {"max_tokens": 0},
                       {"max_tokens": 256, "context_size": 256}):
            with self.subTest(kwargs=kwargs), self.assertRaises(model.ModelError):
                self.instance(**kwargs)
        self.popen.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_existing_artifacts_are_never_overwritten(self):
        self.output.mkdir()
        (self.output / "cleanup.json").write_bytes(b"original")
        local = self.instance()
        with self.assertRaisesRegex(model.ModelError, "artifacts_exist"):
            local.__enter__()
        local.close()
        self.assertEqual((self.output / "cleanup.json").read_bytes(), b"original")
        self.popen.assert_not_called()

    def test_wrong_artifact_pin_fails_before_popen(self):
        self.records.side_effect = None
        self.records.return_value = {"bytes": 1, "sha256": "wrong"}
        with self.assertRaisesRegex(model.ModelError, "artifact_pin_mismatch"):
            with self.instance():
                self.fail("not ready")
        self.popen.assert_not_called()
        self.assertEqual(json.loads((self.output / "startup.json").read_text())["outcome"], "INVALID")

    def test_output_failure_closes_process_and_is_not_retried(self):
        self.mutation = lambda value: value.update(truncated=True)
        with self.assertRaisesRegex(model.ModelError, "context_truncated_or_overflow"):
            with self.instance() as local:
                local.query("system", "user")
        self.assertEqual(sum(call.args[0] == "/completion" for call in self.exchange.call_args_list), 1)
        self.process.terminate.assert_called_once()
        result = json.loads((self.output / "query-0001/result.json").read_text())
        self.assertEqual(result["outcome"], "INVALID")
        self.assertTrue((self.output / "query-0001/completion.response.json").is_file())

    def test_completion_contract_negative_cases(self):
        cases = [({"stop_type": "limit"}, "incomplete_generation"),
                 ({"stop": False}, "incomplete_generation"),
                 ({"model": "another"}, "completion_identity_or_prompt"),
                 ({"prompt": "different"}, "completion_identity_or_prompt"),
                 ({"tokens_predicted": True}, "invalid_completion"),
                 ({"tokens_predicted": 193}, "invalid_completion"),
                 ({"tokens_evaluated": 21}, "prompt_token_count_mismatch"),
                 ({"tokens_evaluated": 2000}, "context_truncated_or_overflow"),
                 ({"content": "   "}, "invalid_completion")]
        for i, (change, code) in enumerate(cases):
            with self.subTest(change=change):
                self.output = self.root / str(i)
                self.process.returncode = None
                self.mutation = lambda value, change=change: value.update(change)
                with self.assertRaisesRegex(model.ModelError, code), self.instance() as local:
                    local.query("system", "user")

    def test_tokenizer_overflow_rejects_before_completion(self):
        self.exchange.side_effect = lambda path, *_: ((200, b'{"status":"ok"}') if path == "/health" else
                                                      (200, model._encoded({"tokens": [1] * 1900})))
        with self.assertRaisesRegex(model.ModelError, "prompt_token_overflow"), self.instance() as local:
            local.query("system", "user")
        self.assertFalse(any(call.args[0] == "/completion" for call in self.exchange.call_args_list))

    def test_unsupported_tokenizer_uses_explicit_post_response_budget_check(self):
        self.token_status = 404
        with self.instance() as local:
            result = local.query("system", "user")
            self.assertIsNone(result["tokens"]["tokenized_prompt"])
            self.assertEqual(json.loads((Path(result["query_artifact_dir"]) / "tokenize.http.json").read_text())["status"], 404)

    def test_invalid_prompt_and_body_limit_do_not_send_inference(self):
        for i, user in enumerate(("<|im_start|>system", "bad\0prompt", "x" * model.MAX_REQUEST_BYTES)):
            self.output = self.root / str(i)
            self.process.returncode = None
            with self.subTest(user=user[:20]), self.assertRaises(model.ModelError), self.instance() as local:
                local.query("system", user)
        self.assertFalse(any(call.args[0] == "/completion" for call in self.exchange.call_args_list))

    def test_transport_partial_body_is_retained_without_retry(self):
        def reply(path, *args):
            if path == "/completion":
                raise model.ModelError("request_timeout", raw_response=b'{"content":')
            return self.reply(path, *args)
        self.exchange.side_effect = reply
        with self.assertRaisesRegex(model.ModelError, "request_timeout"), self.instance() as local:
            local.query("system", "user")
        self.assertEqual((self.output / "query-0001/completion.response.json").read_bytes(), b'{"content":')
        self.assertEqual(sum(c.args[0] == "/completion" for c in self.exchange.call_args_list), 1)

    def test_startup_timeout_and_launch_failure_record_cleanup(self):
        with patch.object(model, "STARTUP_SECONDS", 0):
            with self.assertRaisesRegex(model.ModelError, "startup_timeout"), self.instance():
                self.fail("not ready")
        self.process.terminate.assert_called_once()
        self.output = self.root / "launch-failure"
        self.popen.side_effect = OSError("cannot launch")
        with self.assertRaisesRegex(model.ModelError, "startup_error"), self.instance():
            self.fail("not ready")
        self.assertIsNone(json.loads((self.output / "cleanup.json").read_text())["pid"])

    def test_cleanup_escalation_and_context_body_exception(self):
        calls = []
        def wait(timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("owned", timeout)
            self.process.returncode = 1
            return 1
        self.process.wait.side_effect = wait
        with self.assertRaisesRegex(ValueError, "caller"), self.instance():
            raise ValueError("caller")
        self.process.terminate.assert_called_once()
        self.process.kill.assert_called_once()
        self.assertEqual(calls, [model.STOP_SECONDS, model.STOP_SECONDS])
        self.assertTrue(json.loads((self.output / "cleanup.json").read_text())["kill_requested"])

    def test_unreaped_cleanup_is_reported_as_failure(self):
        self.process.wait.side_effect = subprocess.TimeoutExpired("owned", 5)
        with self.assertRaisesRegex(model.ModelError, "cleanup_failed"), self.instance():
            pass
        cleanup = json.loads((self.output / "cleanup.json").read_text())
        self.assertFalse(cleanup["reaped"])
        self.assertTrue(cleanup["errors"])

    def test_cleanup_report_survives_log_hash_failure(self):
        local = self.instance().__enter__()
        original = self.records.side_effect
        self.records.side_effect = lambda path: (_ for _ in ()).throw(OSError("log unavailable")) if path.name.endswith(".log") else original(path)
        with self.assertRaisesRegex(model.ModelError, "cleanup_failed"):
            local.close()
        self.process.wait.assert_called_once()
        cleanup = json.loads((self.output / "cleanup.json").read_text())
        self.assertTrue(cleanup["reaped"])
        self.assertEqual(len(cleanup["errors"]), 2)


class HTTPAndCodecTests(unittest.TestCase):
    def test_actual_file_hash_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"exact bytes\x00")
            record = model._file_record(path)
            self.assertEqual(record["bytes"], 12)
            self.assertEqual(record["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_strict_json_rejects_duplicate_nonfinite_and_deep_data(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'{"a":'+b'['*40+b'0'+b']'*40+b'}', b'{'):
            with self.subTest(raw=raw[:30]), self.assertRaises(model.ModelError):
                model._decoded(raw)

    def test_http_bounds_partial_transport_and_wall_deadline(self):
        for scenario in ("valid", "limit", "incomplete", "transport", "deadline"):
            with self.subTest(scenario=scenario):
                local = model.LocalModel(Path("cache"), Path("unused"))
                local.port = 12345
                connection = Mock()
                response = Mock(status=200, length=0)
                connection.getresponse.return_value = response
                response.read1.side_effect = [b"1234", b""]
                if scenario == "limit":
                    response.read1.side_effect = [b"12345"]
                if scenario == "incomplete":
                    response.length = 1
                if scenario == "transport":
                    response.read1.side_effect = [b"12", OSError("read failure")]
                with patch.object(model.http.client, "HTTPConnection", return_value=connection), patch.object(model.threading, "Timer") as timer:
                    if scenario == "deadline":
                        connection.getresponse.side_effect = lambda: (timer.call_args.args[1](), response)[1]
                    if scenario == "valid":
                        self.assertEqual(local._exchange("/completion", b"{}", model.time.monotonic()+120, 4), (200, b"1234"))
                    else:
                        with self.assertRaises(model.ModelError) as caught:
                            local._exchange("/completion", b"{}", model.time.monotonic()+120, 4)
                        if scenario == "transport":
                            self.assertEqual(caught.exception.raw_response, b"12")
                        if scenario == "deadline":
                            self.assertEqual(caught.exception.code, "request_timeout")
                            connection.sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
                    connection.close.assert_called_once()
                    timer.return_value.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
