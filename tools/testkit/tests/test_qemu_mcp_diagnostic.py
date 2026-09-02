from __future__ import annotations

import base64
import copy
import io
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib import qemu_mcp_diagnostic as diagnostic


PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthetic-diagnostic-image"
RUN_ID = "test-diagnostic-000001"


def canonical_tools() -> dict[str, dict[str, object]]:
    tools: dict[str, dict[str, object]] = {}
    for name, properties in diagnostic.REQUIRED_TOOL_PROPERTIES.items():
        tool: dict[str, object] = {
            "name": name,
            "inputSchema": {
                "type": "object",
                "properties": {
                    key: {"type": value} for key, value in properties.items()
                },
                "required": sorted(diagnostic.REQUIRED_TOOL_REQUIRED[name]),
            },
        }
        if name in diagnostic.STRING_RESULT_TOOLS:
            tool["outputSchema"] = {
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            }
        tools[name] = tool
    return tools


def text_result(text: str, *, structured: bool = True) -> dict[str, object]:
    result: dict[str, object] = {
        "isError": False,
        "content": [{"type": "text", "text": text}],
    }
    if structured:
        result["structuredContent"] = {"result": text}
    return result


def unstarted_client() -> diagnostic.McpStdioClient:
    """Exercise the real protocol methods without starting any process."""
    client = object.__new__(diagnostic.McpStdioClient)
    client.proc = SimpleNamespace(
        stdin=io.BytesIO(),
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
        poll=lambda: 0,
    )
    client.stream_usable = True
    client._request_id = 0
    client._seen_response_ids = set()
    client._messages = queue.Queue()
    client._transcript_lock = threading.Lock()
    client._transcript_sequence = 0
    client._stdout_thread = None
    client._stderr_thread = None
    client._writer_threads = []
    client.reader_errors = []
    client._record = Mock()
    return client


def enqueue_response(
    client: diagnostic.McpStdioClient,
    result: object,
    request_id: object = 1,
    **extra: object,
) -> None:
    client._messages.put(
        ("message", {"jsonrpc": "2.0", "id": request_id, "result": result, **extra})
    )


class StrictJsonTests(unittest.TestCase):
    def test_nested_json_object_preserves_types(self) -> None:
        value = diagnostic._strict_json_object(
            '{"one":1,"flag":true,"items":[null,{"value":"x"}]}', "fixture"
        )
        self.assertIs(type(value["one"]), int)
        self.assertIs(value["flag"], True)
        self.assertEqual([None, {"value": "x"}], value["items"])

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self) -> None:
        for raw in (
            '{"id":1,"id":2}',
            '{"nested":{"x":1,"x":2}}',
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":-Infinity}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(diagnostic.McpProtocolError):
                    diagnostic._strict_json_object(raw, "fixture")

    def test_object_parser_rejects_nonobjects_and_invalid_json(self) -> None:
        for raw in ("[]", "null", "1", '"text"', "{", ""):
            with self.subTest(raw=raw):
                with self.assertRaises(diagnostic.McpProtocolError):
                    diagnostic._strict_json_object(raw, "fixture")

    def test_value_parser_remains_strict_but_allows_scalar_values(self) -> None:
        self.assertEqual([1, "x"], diagnostic._strict_json_value('[1,"x"]', "value"))
        for raw in ("NaN", '{"x":1,"x":2}'):
            with self.subTest(raw=raw):
                with self.assertRaises(diagnostic.McpProtocolError):
                    diagnostic._strict_json_value(raw, "value")

    def test_recursive_equality_does_not_alias_bool_int_float(self) -> None:
        self.assertTrue(diagnostic._type_strict_equal({"x": [1, True]}, {"x": [1, True]}))
        for actual, expected in (
            (True, 1),
            (1.0, 1),
            ({"x": [True]}, {"x": [1]}),
            ({"x": 1}, {"x": 1, "y": 2}),
            ([1, 2], [1]),
        ):
            with self.subTest(actual=actual, expected=expected):
                self.assertFalse(diagnostic._type_strict_equal(actual, expected))

    def test_transcript_redacts_image_data_without_mutating_response(self) -> None:
        encoded = base64.b64encode(PNG_BYTES).decode("ascii")
        response = {"content": [{"type": "image", "mimeType": "image/png", "data": encoded}]}
        safe = diagnostic._safe_transcript_payload(response)
        self.assertEqual(encoded, response["content"][0]["data"])
        redacted = safe["content"][0]["data"]
        self.assertIs(redacted["redacted"], True)
        self.assertEqual(len(encoded), redacted["base64_characters"])
        self.assertNotIn(encoded, json.dumps(safe))


class ToolContractTests(unittest.TestCase):
    def test_required_subset_accepts_extra_tools_and_nullable_properties(self) -> None:
        tools = canonical_tools()
        tools["extra_tool"] = {"name": "extra_tool"}
        tools["qemu_boot"]["inputSchema"]["properties"]["iso"] = {
            "anyOf": [{"type": "string"}, {"type": "null"}]
        }
        diagnostic._validate_required_tool_schemas(tools)

    def test_missing_tool_wrong_type_and_required_argument_drift_are_rejected(self) -> None:
        mutations = []
        missing = canonical_tools()
        del missing["qemu_stop"]
        mutations.append(missing)
        wrong_type = canonical_tools()
        wrong_type["qemu_qmp"]["inputSchema"]["properties"]["command"] = {"type": "object"}
        mutations.append(wrong_type)
        extra_required = canonical_tools()
        extra_required["qemu_boot"]["inputSchema"]["required"] = ["name", "disk"]
        mutations.append(extra_required)
        malformed_required = canonical_tools()
        malformed_required["qemu_stop"]["inputSchema"]["required"] = [True]
        mutations.append(malformed_required)
        for index, tools in enumerate(mutations):
            with self.subTest(index=index):
                with self.assertRaises(diagnostic.McpProtocolError):
                    diagnostic._validate_required_tool_schemas(tools)

    def test_string_output_schema_must_have_required_string_result(self) -> None:
        for output in (
            None,
            {"type": "array", "properties": {}, "required": []},
            {"type": "object", "properties": {"result": {"type": "integer"}}, "required": ["result"]},
            {"type": "object", "properties": {"result": {"type": "string"}}, "required": []},
        ):
            with self.subTest(output=output):
                tools = canonical_tools()
                tools["qemu_wait_serial"]["outputSchema"] = output
                with self.assertRaises(diagnostic.McpProtocolError):
                    diagnostic._validate_required_tool_schemas(tools)

    def test_initialize_accepts_blank_version_and_sends_initialized(self) -> None:
        client = unstarted_client()
        expected = {
            "protocolVersion": diagnostic.PROTOCOL_VERSION,
            "serverInfo": {"name": "qemu", "version": ""},
            "capabilities": {},
        }
        client.request = Mock(return_value=expected)
        client.notify = Mock()
        self.assertEqual(expected, client.initialize())
        client.notify.assert_called_once_with("notifications/initialized")

    def test_initialize_rejects_protocol_server_and_capability_drift(self) -> None:
        baseline = {
            "protocolVersion": diagnostic.PROTOCOL_VERSION,
            "serverInfo": {"name": "qemu", "version": ""},
            "capabilities": {},
        }
        for changes in (
            {"protocolVersion": "2024-11-05"},
            {"serverInfo": {"name": "other", "version": "1"}},
            {"serverInfo": {"name": "qemu", "version": None}},
            {"capabilities": []},
        ):
            with self.subTest(changes=changes):
                client = unstarted_client()
                client.request = Mock(return_value={**baseline, **changes})
                client.notify = Mock()
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.initialize()
                client.notify.assert_not_called()

    def test_list_tools_paginates_and_fingerprints_only_required_tools(self) -> None:
        tools = canonical_tools()
        entries = list(tools.values())
        client = unstarted_client()
        client.request = Mock(side_effect=[
            {"tools": entries[:4], "nextCursor": "page-2"},
            {"tools": entries[4:] + [{"name": "extra_tool"}]},
        ])
        observed, fingerprint = client.list_tools()
        self.assertEqual(set(tools) | {"extra_tool"}, set(observed))
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual({"cursor": "page-2"}, client.request.call_args_list[1].args[1])
        single = unstarted_client()
        single.request = Mock(return_value={"tools": entries})
        self.assertEqual(fingerprint, single.list_tools()[1])

    def test_list_tools_rejects_duplicates_bad_cursor_and_pagination_overflow(self) -> None:
        entries = list(canonical_tools().values())
        cases = (
            [{"tools": entries + [entries[0]]}],
            [{"tools": entries, "nextCursor": ""}],
            [{"tools": "not-an-array"}],
            [{"tools": [], "nextCursor": f"page-{index}"} for index in range(8)],
        )
        for responses in cases:
            with self.subTest(responses=responses):
                client = unstarted_client()
                client.request = Mock(side_effect=responses)
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.list_tools()

    def test_text_and_structured_results_must_agree(self) -> None:
        for structured in (False, True):
            self.assertEqual("FOUND\nhello", diagnostic._extract_text_tool_result(
                "qemu_wait_serial", text_result("FOUND\nhello", structured=structured)
            ))
        result = text_result("TIMEOUT\npong")
        result["structuredContent"] = {"result": "FOUND\npong"}
        with self.assertRaisesRegex(diagnostic.McpProtocolError, "disagree"):
            diagnostic._extract_text_tool_result("qemu_wait_serial", result)

    def test_text_result_rejects_missing_multiple_or_nontext_content(self) -> None:
        for content in (
            None,
            [],
            [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}],
            [{"type": "image", "data": "x"}],
            [{"type": "text", "text": True}],
        ):
            with self.subTest(content=content):
                with self.assertRaises(diagnostic.McpProtocolError):
                    diagnostic._extract_text_tool_result("qemu_list", {"content": content})

    def test_wait_status_uses_exact_first_line_not_tail_markers(self) -> None:
        for status in ("FOUND", "TIMEOUT", "VM EXITED"):
            self.assertEqual(status, diagnostic._wait_status(f"{status}\npong\naios# "))
        for text in ("", "found\npong", " FOUND\npong", "TIMEOUT FOUND", "\nFOUND", "FOUND-ish"):
            with self.subTest(text=text):
                with self.assertRaises(diagnostic.McpProtocolError):
                    diagnostic._wait_status(text)

    def test_screenshot_requires_one_bounded_png_block(self) -> None:
        client = unstarted_client()
        image = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(PNG_BYTES).decode("ascii")}
        client.call_tool = Mock(return_value={"content": [image]})
        self.assertEqual(PNG_BYTES, client.screenshot_tool("owned", 1))
        for content in (
            [],
            [image, image],
            [{**image, "mimeType": "image/jpeg"}],
            [{**image, "data": "not base64!"}],
            [{**image, "data": base64.b64encode(b"not png").decode("ascii")}],
            [{**image, "data": True}],
        ):
            with self.subTest(content=content):
                client.call_tool = Mock(return_value={"content": content})
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.screenshot_tool("owned", 1)
        with patch.object(diagnostic, "MAX_IMAGE_BYTES", 8):
            client.call_tool = Mock(return_value={"content": [image]})
            with self.assertRaises(diagnostic.McpProtocolError):
                client.screenshot_tool("owned", 1)


class McpProtocolTests(unittest.TestCase):
    def test_constructor_interrupt_during_launch_closes_unassigned_containment(self) -> None:
        containment = SimpleNamespace(
            server_pid=None,
            popen_kwargs=Mock(return_value={}),
            terminate_all=Mock(),
            close=Mock(),
        )
        with (
            patch.object(diagnostic, "ProcessContainment", return_value=containment),
            patch.object(diagnostic.subprocess, "Popen", side_effect=KeyboardInterrupt),
            patch.object(diagnostic, "print_step"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                diagnostic.McpStdioClient(Path("unused-server"), Path("unused-transcript"), Path("unused-stderr"), {})
        containment.terminate_all.assert_not_called()
        containment.close.assert_called_once()

    def test_constructor_attach_interrupt_kills_only_owned_process_and_closes(self) -> None:
        for boundary_assigned in (False, True):
            with self.subTest(boundary_assigned=boundary_assigned):
                proc = SimpleNamespace(pid=424242, poll=Mock(return_value=None), kill=Mock(), wait=Mock())
                containment = SimpleNamespace(
                    server_pid=424242 if boundary_assigned else None,
                    popen_kwargs=Mock(return_value={}),
                    attach=Mock(side_effect=KeyboardInterrupt),
                    terminate_all=Mock(),
                    close=Mock(),
                )
                with (
                    patch.object(diagnostic, "ProcessContainment", return_value=containment),
                    patch.object(diagnostic.subprocess, "Popen", return_value=proc),
                    patch.object(diagnostic, "print_step"),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        diagnostic.McpStdioClient(Path("unused-server"), Path("unused-transcript"), Path("unused-stderr"), {})
                if boundary_assigned:
                    containment.terminate_all.assert_called_once_with(diagnostic.CONTAINMENT_DRAIN_TIMEOUT_SEC)
                else:
                    containment.terminate_all.assert_not_called()
                proc.kill.assert_called_once()
                proc.wait.assert_called_once_with(timeout=diagnostic.SERVER_EXIT_TIMEOUT_SEC)
                containment.close.assert_called_once()

    def test_constructor_reader_setup_interrupt_terminates_boundary_and_closes(self) -> None:
        for stage, failing_reader in (("construct", 0), ("construct", 1), ("start", 0), ("start", 1)):
            with self.subTest(stage=stage, failing_reader=failing_reader):
                proc = SimpleNamespace(pid=424242, wait=Mock())
                containment = SimpleNamespace(
                    server_pid=424242,
                    popen_kwargs=Mock(return_value={}),
                    attach=Mock(),
                    terminate_all=Mock(),
                    close=Mock(),
                )
                readers = [SimpleNamespace(start=Mock()), SimpleNamespace(start=Mock())]
                if stage == "construct":
                    readers[failing_reader] = KeyboardInterrupt()
                else:
                    readers[failing_reader].start.side_effect = KeyboardInterrupt
                with (
                    patch.object(diagnostic, "ProcessContainment", return_value=containment),
                    patch.object(diagnostic.subprocess, "Popen", return_value=proc),
                    patch.object(diagnostic.threading, "Thread", side_effect=readers),
                    patch.object(diagnostic, "print_step"),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        diagnostic.McpStdioClient(Path("unused-server"), Path("unused-transcript"), Path("unused-stderr"), {})
                containment.terminate_all.assert_called_once_with(diagnostic.CONTAINMENT_DRAIN_TIMEOUT_SEC)
                proc.wait.assert_called_once_with(timeout=diagnostic.SERVER_EXIT_TIMEOUT_SEC)
                containment.close.assert_called_once()

    def test_request_records_exact_id_and_ignores_valid_notifications(self) -> None:
        client = unstarted_client()
        client._messages.put(("message", {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}))
        enqueue_response(client, {"ok": True})
        self.assertEqual({"ok": True}, client.request("example", {"value": 1}, 1))
        sent = json.loads(client.proc.stdin.getvalue())
        self.assertEqual({"jsonrpc": "2.0", "id": 1, "method": "example", "params": {"value": 1}}, sent)

    def test_response_id_is_positive_integer_and_matches_request(self) -> None:
        for request_id in (True, 1.0, "1", 0, -1, 2):
            with self.subTest(request_id=request_id):
                client = unstarted_client()
                enqueue_response(client, {}, request_id=request_id)
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.request("example", {}, 1)
                self.assertFalse(client.stream_usable)

    def test_duplicate_response_cannot_satisfy_next_request(self) -> None:
        client = unstarted_client()
        enqueue_response(client, {"first": True})
        self.assertEqual({"first": True}, client.request("first", {}, 1))
        enqueue_response(client, {"late": True})
        with self.assertRaisesRegex(diagnostic.McpProtocolError, "duplicate"):
            client.request("second", {}, 1)

    def test_response_requires_jsonrpc_and_exactly_one_object_result_or_error(self) -> None:
        responses = (
            {"jsonrpc": "1.0", "id": 1, "result": {}},
            {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}},
            {"jsonrpc": "2.0", "id": 1, "result": []},
            {"jsonrpc": "2.0", "result": {}},
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "failed"}},
        )
        for response in responses:
            with self.subTest(response=response):
                client = unstarted_client()
                client._messages.put(("message", response))
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.request("example", {}, 1)

    def test_eof_reader_error_and_host_timeout_disable_stream(self) -> None:
        for queued in (("eof", None), ("error", ValueError("bad stdout")), None):
            with self.subTest(queued=queued):
                client = unstarted_client()
                if queued is not None:
                    client._messages.put(queued)
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.request("example", {}, 0.001)
                self.assertFalse(client.stream_usable)
                with self.assertRaisesRegex(diagnostic.McpProtocolError, "no longer usable"):
                    client.notify("notifications/initialized")

    def test_call_tool_rejects_iserror_and_nonboolean_flags(self) -> None:
        client = unstarted_client()
        client.request = Mock(return_value={"isError": True, "content": [{"type": "text", "text": "owned failure"}]})
        with self.assertRaisesRegex(diagnostic.McpToolError, "owned failure"):
            client.call_tool("qemu_boot", {}, 1)
        for result in (
            {"content": []},
            {"isError": 0, "content": []},
            {"isError": "false", "content": []},
            {"isError": False, "content": {}},
        ):
            with self.subTest(result=result):
                client.request = Mock(return_value=result)
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.call_tool("qemu_boot", {}, 1)

    def test_stdout_reader_rejects_invalid_utf8_truncation_and_oversize(self) -> None:
        for raw in (
            b'{"jsonrpc":"2.0","id":1,"result":{}}',
            b'not-json\n',
            b'{"x":"\xff"}\n',
            b'{"id":1,"id":2}\n',
        ):
            with self.subTest(raw=raw):
                client = unstarted_client()
                client.proc.stdout = io.BytesIO(raw)
                client._read_stdout()
                kind, error = client._messages.get_nowait()
                self.assertEqual("error", kind)
                self.assertIsInstance(error, diagnostic.McpProtocolError)
        client = unstarted_client()
        client.proc.stdout = io.BytesIO(b"x" * 33 + b"\n")
        with patch.object(diagnostic, "MAX_RPC_LINE_BYTES", 32):
            client._read_stdout()
        self.assertEqual("error", client._messages.get_nowait()[0])

    def test_stdout_reader_preserves_valid_message_and_eof(self) -> None:
        client = unstarted_client()
        expected = {"jsonrpc": "2.0", "id": 1, "result": {}}
        client.proc.stdout = io.BytesIO((json.dumps(expected) + "\r\n").encode("utf-8"))
        client._read_stdout()
        self.assertEqual(("message", expected), client._messages.get_nowait())
        self.assertEqual(("eof", None), client._messages.get_nowait())

    def test_malformed_notifications_cannot_bypass_response_validation(self) -> None:
        for notification in (
            {"jsonrpc": "2.0", "method": ""},
            {"jsonrpc": "2.0", "method": True},
            {"jsonrpc": "2.0", "method": "notifications/message", "params": None},
            {"jsonrpc": "2.0", "method": "notifications/message", "result": {}},
            {"jsonrpc": "2.0", "method": "notifications/message", "error": {}},
        ):
            with self.subTest(notification=notification):
                client = unstarted_client()
                client._messages.put(("message", notification))
                enqueue_response(client, {})
                with self.assertRaises(diagnostic.McpProtocolError):
                    client.request("example", {}, 1)
                self.assertFalse(client.stream_usable)

    def test_message_queue_overflow_fails_closed_without_blocking_reader(self) -> None:
        client = unstarted_client()
        client._messages = queue.Queue(maxsize=1)
        self.assertTrue(client._queue_server_message("eof", None))
        self.assertFalse(client._queue_server_message("eof", None))
        self.assertFalse(client.stream_usable)
        self.assertTrue(client.reader_errors)

    def test_blocked_stdin_write_has_bounded_deadline(self) -> None:
        client = unstarted_client()
        release = threading.Event()
        client.proc.stdin = SimpleNamespace(
            write=lambda raw: release.wait(timeout=2),
            flush=lambda: None,
        )
        try:
            with self.assertRaisesRegex(diagnostic.McpHostTimeout, "write"):
                client.request("blocked-write", {}, 0.01)
            self.assertFalse(client.stream_usable)
            self.assertEqual(1, len(client._writer_threads))
        finally:
            release.set()
            for thread in client._writer_threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())

    def test_fake_python_server_roundtrip_keeps_stderr_separate(self) -> None:
        script = (
            "import json,sys\n"
            "sys.stderr.write('fake server diagnostic\\n');sys.stderr.flush()\n"
            "for line in sys.stdin:\n"
            " request=json.loads(line)\n"
            " if 'id' not in request: continue\n"
            " print(json.dumps({'jsonrpc':'2.0','method':'notifications/message','params':{}}),flush=True)\n"
            " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'echo':request['method']}}),flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(diagnostic, "print_step"):
                client = diagnostic.McpStdioClient(
                    Path(sys.executable), root / "transcript.jsonl", root / "stderr.log", os.environ.copy(),
                    command=[sys.executable, "-u", "-c", script],
                )
            try:
                self.assertEqual({"echo": "first"}, client.request("first", {}, 3))
                self.assertEqual({"echo": "second"}, client.request("second", {}, 3))
                self.assertTrue(client.close_stdin_and_wait(3))
                self.assertTrue(client.drain_readers())
                self.assertTrue(diagnostic._wait_until(client.containment.drained, 5))
                self.assertEqual("fake server diagnostic\n", (root / "stderr.log").read_text(encoding="utf-8"))
                records = [json.loads(line) for line in (root / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(list(range(1, len(records) + 1)), [record["sequence"] for record in records])
                self.assertNotIn("fake server diagnostic", json.dumps(records))
            finally:
                if client.proc.poll() is None or not client.containment.drained():
                    client.force_containment_cleanup(3)
                client.drain_readers()
                client.close_containment()
                for stream in (client.proc.stdin, client.proc.stdout, client.proc.stderr):
                    if stream is not None:
                        stream.close()

    def test_fake_python_server_timeout_terminates_owned_child(self) -> None:
        script = (
            "import json,subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-u','-c','import time;time.sleep(60)'])\n"
            "print(json.dumps({'jsonrpc':'2.0','method':'notifications/ready','params':{'child_pid':child.pid}}),flush=True)\n"
            "for line in sys.stdin: time.sleep(60)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(diagnostic, "print_step"):
                client = diagnostic.McpStdioClient(
                    Path(sys.executable), root / "transcript.jsonl", root / "stderr.log", os.environ.copy(),
                    command=[sys.executable, "-u", "-c", script],
                )
            try:
                kind, notification = client._messages.get(timeout=5)
                self.assertEqual("message", kind)
                child_pid = notification["params"]["child_pid"]
                self.assertTrue(client.containment.contains_pid(child_pid))
                self.assertFalse(client.containment.pid_exited(child_pid))
                with self.assertRaises(diagnostic.McpHostTimeout):
                    client.request("never-replies", {}, 0.1)
                self.assertFalse(client.stream_usable)
                self.assertTrue(client.force_containment_cleanup(5))
                self.assertIsNotNone(client.proc.poll())
                self.assertTrue(client.drain_readers())
                self.assertTrue(diagnostic._wait_until(client.containment.drained, 5))
                # An exited Windows process object may still belong to the
                # Job; a retained signaled process handle proves its exit.
                if os.name == "nt":
                    self.assertTrue(client.containment.contains_pid(child_pid))
                self.assertTrue(client.containment.pid_exited(child_pid))
            finally:
                if client.proc.poll() is None or not client.containment.drained():
                    client.force_containment_cleanup(5)
                client.drain_readers()
                client.close_containment()
                for stream in (client.proc.stdin, client.proc.stdout, client.proc.stderr):
                    if stream is not None:
                        stream.close()


@unittest.skipUnless(os.name == "nt", "Windows suspended-launch API contract")
class WindowsContainmentTests(unittest.TestCase):
    def fake_kernel32(
        self,
        threads: list[tuple[int, int]],
        *,
        owner_pid: int = 424242,
        resume_count: int = 1,
    ) -> SimpleNamespace:
        entries = iter(threads)

        def next_entry(snapshot: object, pointer: object) -> int:
            try:
                thread_id, pid = next(entries)
            except StopIteration:
                return 0
            entry = pointer._obj
            entry.th32ThreadID = thread_id
            entry.th32OwnerProcessID = pid
            return 1

        return SimpleNamespace(
            CreateToolhelp32Snapshot=Mock(return_value=101),
            Thread32First=Mock(side_effect=next_entry),
            Thread32Next=Mock(side_effect=next_entry),
            OpenThread=Mock(return_value=102),
            GetProcessIdOfThread=Mock(return_value=owner_pid),
            ResumeThread=Mock(return_value=resume_count),
            CloseHandle=Mock(return_value=True),
        )

    def resume_with(self, kernel32: SimpleNamespace) -> None:
        containment = object.__new__(diagnostic.ProcessContainment)
        with (
            patch.object(diagnostic.ctypes, "WinDLL", return_value=kernel32),
            patch.object(diagnostic.ctypes, "get_last_error", return_value=18),
        ):
            containment._resume_windows_primary_thread(424242)

    def test_launch_flags_are_exact_suspended_and_no_window(self) -> None:
        containment = object.__new__(diagnostic.ProcessContainment)
        self.assertEqual({"creationflags": 0x00000004 | 0x08000000}, containment.popen_kwargs())

    def test_exactly_one_owned_thread_and_suspend_count_one_can_resume(self) -> None:
        kernel32 = self.fake_kernel32([(11, 999), (12, 424242)])
        self.resume_with(kernel32)
        kernel32.OpenThread.assert_called_once_with(0x0002 | 0x0800, False, 12)
        kernel32.ResumeThread.assert_called_once_with(102)
        self.assertEqual([101, 102], [call.args[0] for call in kernel32.CloseHandle.call_args_list])

    def test_zero_or_multiple_owned_threads_fail_before_resume(self) -> None:
        for threads in ([], [(1, 999)], [(1, 424242), (2, 424242)]):
            with self.subTest(threads=threads):
                kernel32 = self.fake_kernel32(threads)
                with self.assertRaisesRegex(OSError, "exactly one thread"):
                    self.resume_with(kernel32)
                kernel32.OpenThread.assert_not_called()
                kernel32.ResumeThread.assert_not_called()
                kernel32.CloseHandle.assert_called_once_with(101)

    def test_changed_thread_owner_fails_before_resume_and_closes_handles(self) -> None:
        kernel32 = self.fake_kernel32([(12, 424242)], owner_pid=999)
        with self.assertRaisesRegex(OSError, "no longer belongs"):
            self.resume_with(kernel32)
        kernel32.ResumeThread.assert_not_called()
        self.assertEqual([101, 102], [call.args[0] for call in kernel32.CloseHandle.call_args_list])

    def test_unexpected_resume_counts_fail_closed_and_close_handles(self) -> None:
        for count in (0, 2, 0xFFFFFFFF):
            with self.subTest(count=count):
                kernel32 = self.fake_kernel32([(12, 424242)], resume_count=count)
                with self.assertRaises(OSError):
                    self.resume_with(kernel32)
                kernel32.ResumeThread.assert_called_once_with(102)
                self.assertEqual([101, 102], [call.args[0] for call in kernel32.CloseHandle.call_args_list])

    def test_pid_exit_uses_owned_handle_signal_and_rejects_unowned_pid(self) -> None:
        containment = object.__new__(diagnostic.ProcessContainment)
        containment._process_handles = {424242: 102}
        kernel32 = SimpleNamespace(WaitForSingleObject=Mock(return_value=0))
        with (
            patch.object(diagnostic.ctypes, "WinDLL", return_value=kernel32),
            patch.object(diagnostic.ctypes, "get_last_error", return_value=5),
        ):
            self.assertTrue(containment.pid_exited(424242))
            kernel32.WaitForSingleObject.assert_called_once_with(102, 0)
            kernel32.WaitForSingleObject.return_value = 258
            self.assertFalse(containment.pid_exited(424242))
            kernel32.WaitForSingleObject.return_value = 0xFFFFFFFF
            with self.assertRaisesRegex(OSError, "process wait failed"):
                containment.pid_exited(424242)
            with self.assertRaisesRegex(OSError, "unowned process handle"):
                containment.pid_exited(999)


class FreshPongRecordTests(unittest.TestCase):
    def test_accepts_only_supported_terminated_uint64_records(self) -> None:
        for ending in (b"\n", b"\r\n", b"\r\r\n"):
            for ticks in (b"0", b"123", b"18446744073709551615"):
                with self.subTest(ending=ending, ticks=ticks):
                    raw = b"ping\n[STATE] pong ticks=" + ticks + ending + b"aios# "
                    self.assertTrue(diagnostic._fresh_pong_record(raw))

    def test_missing_terminator_and_invalid_record_grammar_are_rejected(self) -> None:
        for raw in (
            b"[STATE] pong ticks=123",
            b"[STATE] pong ticks=123\r",
            b"[STATE] pong ticks=123\r\r\r\n",
            b"[STATE] pong ticks=-1\n",
            b"[STATE] pong ticks=NaN\n",
            b"[STATE] pong ticks=1.5\n",
            b"[STATE] pong ticks=18446744073709551616\n",
            b"[STATE] pong ticks=100000000000000000000\n",
            b"[STATE] pong ticks=123 trailing\n",
            b" [STATE] pong ticks=123\n",
            b"prefix[STATE] pong ticks=123\n",
            b"[STATE] pongish ticks=123\n",
            "[STATE] pong ticks=１２３\n".encode("utf-8"),
            b"aios# \n",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(diagnostic._fresh_pong_record(raw))

    def test_duplicate_or_malformed_pong_family_records_are_rejected(self) -> None:
        valid = b"[STATE] pong ticks=123\n"
        for extra in (valid, b"[STATE] pong ticks=bad\n", b"[STATE] pongish\n"):
            with self.subTest(extra=extra):
                self.assertFalse(diagnostic._fresh_pong_record(valid + extra))


class OwnedPathTests(unittest.TestCase):
    def test_generated_run_id_satisfies_its_own_validator(self) -> None:
        diagnostic._validate_run_id(diagnostic._new_run_id())

    def test_unsafe_run_ids_and_existing_run_directory_are_rejected(self) -> None:
        for run_id in ("../outside", "a/b", "short", "x" * 81, "name with spaces"):
            with self.subTest(run_id=run_id):
                with self.assertRaises(ValueError):
                    diagnostic._validate_run_id(run_id)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = diagnostic._create_run_dir(RUN_ID, root)
            sentinel = run_dir / "summary.json"
            sentinel.write_text("sentinel", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                diagnostic._create_run_dir(RUN_ID, root)
            self.assertEqual("sentinel", sentinel.read_text(encoding="utf-8"))

    def _boot_tree(self, root: Path, name: str) -> tuple[Path, Path, str]:
        workdir = root / f"qemu-mcp-{name}-unique"
        workdir.mkdir(parents=True)
        serial = workdir / "serial.log"
        serial.write_bytes(b"boot serial\n")
        qemu_log = workdir / "qemu.log"
        qemu_log.write_bytes(b"qemu log\n")
        text = (
            f"VM {name!r} booted (pid 424242, x86_64, 256 MB).\n"
            f"Serial log: {serial.resolve()}\n"
            "Next: qemu_wait_serial for a boot marker, or qemu_screenshot to see the display."
        )
        return serial, qemu_log, text

    def test_boot_result_binds_pid_name_and_owned_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = f"aios-diagnostic-{RUN_ID}"
            serial, qemu_log, text = self._boot_tree(root, name)
            self.assertEqual((424242, serial.resolve(), qemu_log.resolve()), diagnostic._parse_boot_result(text, name, root))

    def test_boot_result_rejects_tuple_path_and_line_shape_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root / "owned"
            owned.mkdir()
            name = f"aios-diagnostic-{RUN_ID}"
            serial, _, text = self._boot_tree(owned, name)
            outsider, _, _ = self._boot_tree(root / "outside", name)
            wrong_name, _, _ = self._boot_tree(owned, "another-vm")
            for invalid in (
                text.replace("pid 424242", "pid 0"),
                text.replace("x86_64", "aarch64"),
                text.replace("256 MB", "512 MB"),
                text.replace(repr(name), repr("another-vm")),
                text.replace(str(serial.resolve()), str(outsider.resolve())),
                text.replace(str(serial.resolve()), str(wrong_name.resolve())),
                text.replace(str(serial.resolve()), "relative/serial.log"),
                text + "\nextra",
                text.replace("Serial log: ", " Serial log: "),
            ):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(diagnostic.McpProtocolError):
                        diagnostic._parse_boot_result(invalid, name, owned)

    def test_snapshot_preserves_bytes_hash_and_enforces_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.log"
            destination = root / "serial.log"
            source.write_bytes(b"one\r\ntwo\n")
            result = diagnostic._snapshot_source_file(source, destination, 100)
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertEqual(len(source.read_bytes()), result["captured_bytes"])
            self.assertEqual(diagnostic._sha256_bytes(source.read_bytes()), result["captured_sha256"])
            with self.assertRaises(ValueError):
                diagnostic._snapshot_source_file(source, destination, 2)

    def test_owned_temp_removal_requires_matching_parent_prefix_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            owned = base / f"aios-qemu-mcp-{RUN_ID}-unique"
            owned.mkdir()
            marker = owned / ".aios-qemu-mcp-owner.json"
            marker.write_text(json.dumps({"run_id": "other-run"}), encoding="utf-8")
            self.assertFalse(diagnostic._remove_owned_temp(owned, base, RUN_ID))
            self.assertTrue(owned.exists())
            marker.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
            self.assertFalse(diagnostic._remove_owned_temp(owned, base / "wrong", RUN_ID))
            self.assertTrue(owned.exists())
            self.assertTrue(diagnostic._remove_owned_temp(owned, base, RUN_ID))
            self.assertFalse(owned.exists())


class FakeContainment:
    def __init__(self, session: "FakeMcpSession") -> None:
        self.session = session

    def contains_pid(self, pid: int) -> bool:
        return pid == 424242 and self.session.qemu_alive

    def pid_exited(self, pid: int) -> bool:
        if pid != 424242:
            raise OSError("synthetic unowned process")
        return not self.session.qemu_alive

    def drained(self) -> bool:
        return not (
            self.session.qemu_alive
            or self.session.extra_child_alive
            or self.session.server_exit_code is None
        )


class FakeMcpSession:
    """Model MCP behavior and destructive upstream cleanup, never launch QEMU."""
    def __init__(self, server: Path, transcript: Path, stderr: Path, env: dict[str, str], scenario: dict[str, object]) -> None:
        self.scenario = scenario
        self.artifact_dir = transcript.parent
        self.initial_summary = json.loads((self.artifact_dir / "summary.json").read_text(encoding="utf-8"))
        self.owned_temp = Path(env["TEMP"])
        self.stream_usable = True
        self._request_id = 0
        self.reader_errors = []
        self._messages = queue.Queue()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.list_calls = 0
        self.wait_calls = 0
        self.qemu_alive = False
        self.extra_child_alive = False
        self.server_exit_code = None
        self.force_calls = 0
        self.containment = FakeContainment(self)
        self.proc = SimpleNamespace(poll=lambda: self.server_exit_code)
        self.workdir: Path | None = None
        self.serial_source: Path | None = None
        self.serial_at_stop = b""
        self.screenshot_at_stop = False
        self.name = ""

    def initialize(self) -> dict[str, object]:
        self._request_id += 1
        if self.scenario.get("initialize_error"):
            raise diagnostic.McpProtocolError("synthetic initialize failure")
        return {"protocolVersion": diagnostic.PROTOCOL_VERSION, "serverInfo": {"name": "qemu", "version": ""}, "capabilities": {}}

    def list_tools(self) -> tuple[dict[str, dict[str, object]], str]:
        self._request_id += 1
        return canonical_tools(), "a" * 64

    def text_tool(self, tool: str, arguments: dict[str, object], timeout_sec: float) -> str:
        self._request_id += 1
        self.calls.append((tool, copy.deepcopy(arguments)))
        if tool == "qemu_list":
            self.list_calls += 1
            return str(self.scenario.get("final_registry", "no VMs")) if self.list_calls > 1 else "no VMs"
        if tool == "qemu_boot":
            self.name = str(arguments["name"])
            self.qemu_alive = True
            if self.scenario.get("boot_timeout"):
                self.stream_usable = False
                raise diagnostic.McpHostTimeout("synthetic lost boot response")
            self.workdir = self.owned_temp / f"qemu-mcp-{self.name}-fake"
            self.workdir.mkdir()
            self.serial_source = self.workdir / "serial.log"
            self.serial_source.write_bytes(b"[SHELL] Interactive shell started\naios# ")
            (self.workdir / "qemu.log").write_bytes(b"synthetic qemu log\n")
            path = str(self.serial_source.resolve())
            if self.scenario.get("bad_serial_path"):
                path = str((self.owned_temp.parent / "outside-serial.log").resolve())
            return (
                f"VM {self.name!r} booted (pid 424242, x86_64, 256 MB).\n"
                f"Serial log: {path}\n"
                "Next: qemu_wait_serial for a boot marker, or qemu_screenshot to see the display."
            )
        if tool == "qemu_wait_serial":
            self.wait_calls += 1
            if self.scenario.get("interrupt") and self.wait_calls == 1:
                raise KeyboardInterrupt
            if self.wait_calls == 1:
                return str(self.scenario.get("prompt", "FOUND\naios# "))
            return str(self.scenario.get("pong", "FOUND\n[STATE] pong ticks=123"))
        if tool == "qemu_serial_send":
            if not self.scenario.get("missing_fresh_pong"):
                with self.serial_source.open("ab") as handle:
                    handle.write(b"\n[STATE] pong ticks=123\naios# ")
            return f"sent 5 bytes to {self.name!r}'s serial console"
        if tool == "qemu_qmp":
            return str(self.scenario.get("qmp", '{"status":"running","running":true}'))
        if tool == "qemu_serial":
            return "synthetic fallback tail"
        if tool == "qemu_stop":
            self.serial_at_stop = (self.artifact_dir / "serial.log").read_bytes()
            self.screenshot_at_stop = (self.artifact_dir / "failure.png").is_file()
            if self.scenario.get("stop_error"):
                raise diagnostic.McpToolError(tool, "synthetic stop failure")
            self.qemu_alive = False
            self.extra_child_alive = bool(self.scenario.get("lingering_child"))
            if self.workdir is not None:
                shutil.rmtree(self.workdir)
            return f"VM {self.name!r} stopped (killed)"
        raise AssertionError(f"unexpected fake MCP tool: {tool}")

    def screenshot_tool(self, name: str, timeout_sec: float) -> bytes:
        self._request_id += 1
        self.calls.append(("qemu_screenshot", {"name": name}))
        if self.scenario.get("screenshot_error"):
            raise diagnostic.McpProtocolError("synthetic screenshot failure")
        return PNG_BYTES

    def close_stdin_and_wait(self, timeout_sec: float) -> bool:
        self.calls.append(("server-close", {}))
        if self.scenario.get("server_hangs"):
            return False
        self.server_exit_code = int(self.scenario.get("server_exit_code", 0))
        return True

    def force_containment_cleanup(self, timeout_sec: float) -> bool:
        self.force_calls += 1
        self.calls.append(("containment-force", {}))
        if self.scenario.get("containment_failure"):
            return False
        self.qemu_alive = False
        self.extra_child_alive = False
        self.server_exit_code = 0
        return True

    def drain_readers(self) -> bool:
        if self.scenario.get("reader_error"):
            self.reader_errors.append("synthetic trailing malformed JSON")
        if self.scenario.get("late_response"):
            self._messages.put(("message", {"jsonrpc": "2.0", "id": 99, "result": {}}))
        return not self.scenario.get("reader_not_drained", False)

    def close_containment(self) -> None:
        self.calls.append(("containment-close", {}))


class DiagnosticWorkflowTests(unittest.TestCase):
    def run_scenario(self, *, outcome: str = "OBSERVED", timeout_sec: object = 1, skip_build: bool = True, **scenario: object) -> SimpleNamespace:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        build = base / "build"
        build.mkdir()
        (build / "aios-kernel.iso").write_bytes(b"synthetic iso")
        server = base / "fake-qemu-mcp.exe"
        server.write_bytes(b"synthetic executable, never launched")
        artifact_root = build / "qemu-mcp-diagnostic"
        owned_temp = base / f"aios-qemu-mcp-{RUN_ID}-owned"
        owned_temp.mkdir()
        (owned_temp / ".aios-qemu-mcp-owner.json").write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
        sessions: list[FakeMcpSession] = []

        def factory(*args: object) -> FakeMcpSession:
            if scenario.get("launch_error"):
                raise FileNotFoundError("synthetic launch failure")
            session = FakeMcpSession(*args, scenario=scenario)
            sessions.append(session)
            return session

        with (
            patch.object(diagnostic, "BUILD_DIR", build),
            patch.object(diagnostic, "_git_metadata", return_value={"head_sha": "a" * 40, "dirty": True}),
            patch.object(diagnostic, "_package_versions", return_value={}),
            patch.object(diagnostic, "_qemu_provenance", return_value=None),
            patch.object(diagnostic, "_create_owned_temp", return_value=(owned_temp, base)),
            patch.object(diagnostic, "McpStdioClient", side_effect=factory) as client_mock,
            patch.object(diagnostic, "build_kernel_iso") as build_mock,
            patch.object(diagnostic, "_wait_until", side_effect=lambda predicate, timeout: bool(predicate())),
            patch.object(diagnostic, "print_step"),
        ):
            if scenario.get("build_error"):
                build_mock.side_effect = diagnostic.ToolError("synthetic build failure")
            kwargs = dict(timeout_sec=timeout_sec, skip_build=skip_build, _run_id=RUN_ID, _artifact_root=artifact_root)
            if scenario.get("interrupt"):
                with self.assertRaises(KeyboardInterrupt):
                    diagnostic.run_qemu_mcp_diagnostic(str(server), **kwargs)
            elif outcome == "OBSERVED":
                returned = diagnostic.run_qemu_mcp_diagnostic(str(server), **kwargs)
                self.assertEqual(outcome, returned["outcome"])
            else:
                with self.assertRaises(diagnostic.QemuMcpDiagnosticError) as raised:
                    diagnostic.run_qemu_mcp_diagnostic(str(server), **kwargs)
                self.assertEqual(outcome, raised.exception.outcome)
        run_dir = artifact_root / RUN_ID
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(outcome, summary["outcome"])
        self.assertIs(summary["diagnostic_only"], True)
        self.assertIs(summary["authoritative"], False)
        self.assertNotIn("passed", summary)
        return SimpleNamespace(summary=summary, session=sessions[0] if sessions else None, run_dir=run_dir, build_mock=build_mock, client_mock=client_mock, owned_temp=owned_temp)

    def test_observed_sequence_has_fixed_args_complete_artifacts_and_cleanup(self) -> None:
        result = self.run_scenario()
        session = result.session
        self.assertEqual("INFRA_ERROR", session.initial_summary["outcome"])
        boot = next(arguments for name, arguments in session.calls if name == "qemu_boot")
        self.assertEqual("x86_64", boot["arch"])
        self.assertEqual(256, boot["memory_mb"])
        self.assertEqual("-nic none -no-reboot", boot["extra_args"])
        self.assertTrue(Path(boot["iso"]).is_absolute())
        self.assertEqual(f"aios-diagnostic-{RUN_ID}", boot["name"])
        for name, arguments in session.calls:
            if "name" in arguments:
                self.assertEqual(boot["name"], arguments["name"])
        self.assertIn(b"[STATE] pong ticks=123", session.serial_at_stop)
        self.assertTrue(result.summary["observations"]["serial_snapshot_complete"])
        self.assertFalse(result.summary["observations"]["serial_complete_through_termination"])
        self.assertTrue(result.summary["termination"]["cleanup_verified"])
        self.assertEqual("CLEAN", result.summary["termination"]["cleanup_status"])
        self.assertFalse(result.owned_temp.exists())
        result.build_mock.assert_not_called()
        for entry in result.summary["artifacts"].values():
            raw = (result.run_dir / entry["path"]).read_bytes()
            self.assertEqual(len(raw), entry["bytes"])
            self.assertEqual(diagnostic._sha256_bytes(raw), entry["sha256"])

    def test_timeout_tail_does_not_promote_observation_and_artifacts_precede_stop(self) -> None:
        result = self.run_scenario(outcome="TIMEOUT", prompt="TIMEOUT\naios# \n[STATE] pong ticks=123")
        self.assertIn("shell-prompt-timeout", result.summary["reasons"])
        self.assertTrue(result.session.screenshot_at_stop)
        self.assertTrue(result.session.serial_at_stop)
        tools = [name for name, _ in result.session.calls]
        self.assertLess(tools.index("qemu_screenshot"), tools.index("qemu_stop"))
        self.assertLess(tools.index("qemu_stop"), len(tools) - 1 - tools[::-1].index("qemu_list"))

    def test_vm_exit_preserves_serial_without_requesting_live_screenshot(self) -> None:
        result = self.run_scenario(outcome="VM_EXITED", prompt="VM EXITED\naios# ")
        self.assertTrue(result.session.serial_at_stop)
        self.assertNotIn("qemu_screenshot", [name for name, _ in result.session.calls])

    def test_found_without_fresh_pong_record_is_aborted(self) -> None:
        result = self.run_scenario(outcome="ABORTED", missing_fresh_pong=True)
        self.assertIn("fresh-pong-record-mismatch", result.summary["reasons"])

    def test_qmp_nonrunning_and_malformed_results_are_not_observed(self) -> None:
        for qmp, outcome in (("not-json", "INFRA_ERROR"), ('{"status":"paused","running":false}', "ABORTED"), ('{"status":true}', "INFRA_ERROR")):
            with self.subTest(qmp=qmp):
                self.run_scenario(outcome=outcome, qmp=qmp)

    def test_untrusted_boot_path_uses_tail_only_and_still_stops_owned_vm(self) -> None:
        result = self.run_scenario(outcome="INFRA_ERROR", bad_serial_path=True)
        self.assertEqual("mcp-tail-fallback", result.summary["observations"]["serial_capture_scope"])
        self.assertFalse(result.summary["observations"]["serial_snapshot_complete"])
        self.assertEqual(b"synthetic fallback tail", result.session.serial_at_stop)
        self.assertTrue(result.summary["termination"]["stop_rpc_succeeded"])

    def test_stop_failure_overrides_observed_and_forces_exact_containment(self) -> None:
        result = self.run_scenario(outcome="CLEANUP_ERROR", stop_error=True)
        self.assertEqual("OBSERVED", result.summary["termination"]["primary_outcome"])
        self.assertIn("qemu-stop-error", result.summary["reasons"])
        self.assertGreaterEqual(result.session.force_calls, 1)

    def test_nonempty_final_registry_is_cleanup_error(self) -> None:
        result = self.run_scenario(outcome="CLEANUP_ERROR", final_registry="owned-vm: running")
        self.assertFalse(result.summary["termination"]["registry_empty"])
        self.assertIn("qemu-registry-not-empty", result.summary["reasons"])

    def test_graceful_server_exit_with_lingering_child_forces_containment(self) -> None:
        result = self.run_scenario(outcome="CLEANUP_ERROR", lingering_child=True)
        self.assertGreaterEqual(result.session.force_calls, 1)
        self.assertTrue(result.summary["termination"]["containment_drained"])
        self.assertFalse(result.session.extra_child_alive)

    def test_nonzero_server_exit_and_undrained_readers_cannot_be_observed(self) -> None:
        for scenario in ({"server_exit_code": 7}, {"reader_not_drained": True}):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(outcome="CLEANUP_ERROR", **scenario)
                self.assertEqual("OBSERVED", result.summary["termination"]["primary_outcome"])

    def test_late_response_and_reader_error_are_cleanup_errors(self) -> None:
        for scenario in ({"reader_error": True}, {"late_response": True}):
            with self.subTest(scenario=scenario):
                result = self.run_scenario(outcome="CLEANUP_ERROR", **scenario)
                self.assertEqual("OBSERVED", result.summary["termination"]["primary_outcome"])
                self.assertIn("mcp-reader-error", result.summary["reasons"])
                self.assertTrue(result.summary["termination"]["reader_errors"])

    def test_server_shutdown_timeout_is_cleanup_error_even_after_recovery(self) -> None:
        result = self.run_scenario(outcome="CLEANUP_ERROR", server_hangs=True)
        self.assertGreaterEqual(result.session.force_calls, 1)
        self.assertTrue(result.summary["termination"]["cleanup_recovered_by_containment"])

    def test_lost_boot_response_cleans_containment_without_claiming_known_vm(self) -> None:
        result = self.run_scenario(outcome="CLEANUP_ERROR", boot_timeout=True)
        self.assertEqual("INFRA_ERROR", result.summary["termination"]["primary_outcome"])
        self.assertFalse(result.summary["vm"]["qemu_pid_known"])
        self.assertGreaterEqual(result.session.force_calls, 1)
        self.assertTrue(result.summary["termination"]["containment_drained"])
        self.assertNotIn("qemu_stop", [name for name, _ in result.session.calls])

    def test_invalid_timeout_initializes_failure_artifacts_without_launch(self) -> None:
        for timeout in (0, -1, True, 1.0, diagnostic.MAX_COMMAND_TIMEOUT_SEC + 1):
            with self.subTest(timeout=timeout):
                result = self.run_scenario(outcome="INFRA_ERROR", timeout_sec=timeout)
                self.assertIn("invalid-timeout", result.summary["reasons"])
                result.client_mock.assert_not_called()
                result.build_mock.assert_not_called()
                self.assertEqual(b"", (result.run_dir / "serial.log").read_bytes())

    def test_build_and_launch_failure_leave_honest_artifacts(self) -> None:
        built = self.run_scenario(outcome="INFRA_ERROR", skip_build=False, build_error=True)
        built.build_mock.assert_called_once_with(1, "default")
        built.client_mock.assert_not_called()
        launched = self.run_scenario(outcome="INFRA_ERROR", launch_error=True)
        self.assertEqual(b"", (launched.run_dir / "serial.log").read_bytes())
        self.assertFalse(launched.owned_temp.exists())

    def test_user_interrupt_is_persisted_and_cleanup_precedes_reraise(self) -> None:
        result = self.run_scenario(outcome="ABORTED", interrupt=True)
        self.assertIn("user-interrupt", result.summary["reasons"])
        self.assertTrue(result.summary["termination"]["cleanup_verified"])
        self.assertTrue(result.session.serial_at_stop)

    def test_screenshot_failure_preserves_primary_timeout_and_cleanup(self) -> None:
        result = self.run_scenario(outcome="TIMEOUT", prompt="TIMEOUT\npong", screenshot_error=True)
        self.assertIn("failure-screenshot-unavailable", result.summary["reasons"])
        self.assertFalse((result.run_dir / "failure.png").exists())
        self.assertTrue(result.summary["termination"]["cleanup_verified"])


if __name__ == "__main__":
    unittest.main()
