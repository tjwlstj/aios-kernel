from __future__ import annotations

import hashlib
import copy
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_console import shell


def put(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def fixture(root):
    proc, sysfs = root / "proc", root / "sys"
    put(proc, "cpuinfo", "processor : 0\nmodel name : Fixture CPU\n")
    put(proc, "meminfo", "MemTotal: 4096 kB\nMemAvailable: 2048 kB\n")
    for name in ("bus/pci/devices", "bus/usb/devices", "class/block", "class/net"):
        (sysfs / name).mkdir(parents=True)
    for key, value in {"ifindex": "1", "type": "772", "operstate": "unknown"}.items():
        put(sysfs, "class/net/lo/" + key, value)
    return proc, sysfs


def dns(host):
    return {"operation": "resolve", "outcome": "OK", "error": None, "elapsed_ms": 1,
            "host": host, "addresses": ["192.0.2.1"]}


def fetch(url):
    return {"operation": "fetch", "outcome": "OK", "error": None, "elapsed_ms": 1,
            "url": url, "status": 200, "protocol": "https", "tls_verified": True,
            "received_bytes": 7, "truncated": False, "content_type": "text/plain",
            "body_preview": "example", "body_sha256": hashlib.sha256(b"example").hexdigest()}


def service(state="RUNNING", *, error=None, generation=1, observations=3):
    absent = state in ("ABSENT", "UNSUPPORTED")
    return {"schema_version": 1, "outcome": "ERROR" if error else "OK", "error": error,
            "service_kind": "CONSOLE_RUNTIME", "state": state,
            "service_id": None if absent else "00000000-0000-4000-8000-000000000001",
            "instance_id": None if absent else "00000000-0000-4000-8000-000000000002",
            "boot_id": None if absent else "00000000-0000-4000-8000-000000000003",
            "generation": None if absent else generation, "pid": None if absent else 123,
            "observation_sequence": 0 if absent else observations,
            "heartbeat_monotonic_ns": None if absent else 1000,
            "source_only": True, "binding_status": "UNBOUND", "management_actions": "UNSUPPORTED"}


def agent_source(**changes):
    value = {"schema_version": 1, "source_namespace": "linux-userspace-service",
             "source_id": "00000000-0000-4000-8000-000000000011",
             "source_instance": "00000000-0000-4000-8000-000000000012",
             "host_boot_id": "00000000-0000-4000-8000-000000000013",
             "source_generation": 1, "service_start_generation": 1, "source_kind": "ai-service",
             "source_role": "main", "lifecycle_state": "active", "producer_owned": True,
             "copied_read": True, "model_ready": True, "model_sha256": "a" * 64,
             "warmup_request_sha256": "b" * 64, "warmup_response_sha256": "c" * 64,
             "completed_requests": 1, "process_id": 123, "source_only": True}
    return {**value, **changes}


def agent(action="status", *, state="RUNNING", error=None, bound=False, prompt=None, content="AIOS is ready.", protocol=5):
    # Producer fixtures are deliberately exercised through the independent
    # response verifier, including mutations with recomputed artifact hashes.
    from aios_management.binding import Authority
    from aios_agent.inference import request_body
    source = agent_source()
    authority = Authority("00000000-0000-4000-8000-000000000014")
    authority.initialize()
    if bound or action == "room-discover":
        authority.discover([source])
    if bound:
        authority.bind(source)
    inference, context = None, None
    if protocol == 5 and (action == 'space' or action == 'ask' and error is None):
        from aios_agent.space import build_observation, build_packet
        observation = build_observation(host_boot_id=source['host_boot_id'], process_id=source['process_id'],
            observed_monotonic_ns=10, working_directory='/fixture/runtime', logical_cpu_count=2, mem_total_line='MemTotal: 4096 kB')
        context = build_packet(observation, source_record=source, management_snapshot=authority.snapshot(),
                               checked_monotonic_ns=11, model_id='fixture-main')
    inference = None
    if action == "ask" and error is None:
        before = copy.deepcopy(source)
        source["completed_requests"] += 1
        authority.observe(source)
        body = request_body(prompt, space_context=context).decode("utf-8")
        response = json.dumps({"content": content, "tokens_predicted": 4, "model": "fixture-main", **({"prompt": json.loads(body)["prompt"], "truncated": False} if protocol == 5 else {})}, separators=(",", ":"))
        inference = {"schema_version": 3 if protocol == 5 else 2 if protocol >= 4 else 1, "request_id": "00000000-0000-4000-8000-000000000015",
                     **({"backend_execution": None} if protocol >= 4 else {}),
                     **({"user_prompt": prompt, "space_context": context} if protocol == 5 else {}),
                     "started_at": "2026-09-07T00:00:00+00:00", "purpose": "user", "model_id": "fixture-main",
                     "model_sha256": "a" * 64, "backend_sha256": "d" * 64, "provenance_sha256": "e" * 64,
                     "request_body": body, "request_sha256": hashlib.sha256(body.encode()).hexdigest(),
                     "response_body": response, "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
                     "content": content, "tokens_predicted": 4, "elapsed_ns": 100,
                     "outcome": "OK", "error": None, "source_before": before, "source_after": copy.deepcopy(source),
                     "authority_instance": authority.authority_instance, "binding_generation": 1}
    if state == "STOPPED":
        source.update(lifecycle_state="exited", model_ready=False, source_generation=2)
        authority.observe(source)
    absent = state in ("ABSENT", "UNSUPPORTED")
    return {"schema_version": protocol, "outcome": "ERROR" if error else "OK", "error": error, "action": action,
            "state": state, "service_kind": "AI_SERVICE", "source_record": None if absent else source,
            "management_snapshot": None if absent else authority.snapshot(),
            "management_outcome": ("rejected" if error else "accepted") if action.startswith("room-") or action == "ask" else None,
            "inference_receipt": inference, "resource_actions": "UNSUPPORTED",
            "capture_kind": "unsupported" if state == "UNSUPPORTED" else "fixture" if protocol >= 4 else "live",
            **({"resource_result": None} if protocol in (2, 3, 4, 5) else {}),
            **({"space_context": context if action == "space" and error is None else None} if protocol == 5 else {})}


def agent6(action="status", *, state="RUNNING", error=None, bound=False, prompt=None,
           content="AIOS is ready.", request_id=None):
    """Current non-Task or rejected-admission reply; keep historical agent() intact."""
    value = agent(action, state=state, error=error, bound=bound, prompt=prompt,
                  content=content, protocol=5)
    return {**value, "schema_version": 6, "request_id": request_id,
            "task": None, "task_control": None}


def cell(action="status", *, active=True, bound=True, error=None):
    from aios_management.binding import Authority
    value = agent("cell-" + action, bound=bound, error=error)
    authority = Authority("00000000-0000-4000-8000-000000000014")
    authority.initialize()
    if bound:
        authority.discover([value["source_record"]])
        authority.bind(value["source_record"])
    if not active:
        authority.set_parent(False)
    if error is None and action != "status":
        authority.set_parent(action == "activate")
    value.update(management_snapshot=authority.snapshot(), management_outcome="rejected" if error else "accepted")
    return value


def cell6(action="status", *, active=True, bound=True, error=None):
    """Current Cell reply without changing the shared historical Cell fixture."""
    return {**cell(action, active=active, bound=bound, error=error), "schema_version": 6,
            "request_id": None, "task": None, "task_control": None}


def backend(action="status", *, state="RUNNING", error=None, generation=1):
    parent = {"host_boot_id": "00000000-0000-4000-8000-000000000021",
              "process_id": 201, "process_start_ticks": 100, "uid": 1000}
    child = {**parent, "process_id": 202, "process_start_ticks": 101}
    record = {"schema_version": 1, "service_id": "00000000-0000-4000-8000-000000000022",
              "instance_id": "00000000-0000-4000-8000-000000000023", "start_generation": generation,
              "supervisor_identity": parent, "child_identity": child,
              "lifecycle_state": "exited" if state in ("STOPPED", "RECOVERED") else "starting" if state == "STARTING" else "active",
              "backend_ready": state == "RUNNING", "config_sha256": "f" * 64, "profile": "python-fixture",
              "source_only": True, "backend_source_instance": "00000000-0000-4000-8000-000000000024"}
    descriptor = {"schema_version": 1, "source_namespace": "linux-model-backend",
        "source_instance": record["backend_source_instance"], "source_generation": 1, "lifecycle_state": "active",
        "source_only": True, "producer_owned": True, "host_boot_id": child["host_boot_id"],
        "process_id": child["process_id"], "process_start_ticks": child["process_start_ticks"],
        "launcher_process_id": parent["process_id"], "launcher_start_ticks": parent["process_start_ticks"],
        "model_id": "fixture-main", "model_sha256": "a" * 64, "backend_sha256": "d" * 64,
        "endpoint": "http://127.0.0.1:8089", "listener_inode": 301}
    return {"schema_version": 1, "action": action, "outcome": "ERROR" if error else "OK", "error": error,
            "state": state, "service_kind": "MODEL_BACKEND",
            "capture_kind": "unsupported" if state == "UNSUPPORTED" else "fixture",
            "service_record": None if state in ("ABSENT", "UNSUPPORTED", "FAILED") else record,
            "descriptor": descriptor if state == "RUNNING" else None, "resource_actions": "UNSUPPORTED"}


def resource_error(action="status", error="resource-unlinked"):
    return {"schema_version": 1, "action": action, "outcome": "ERROR", "error": error,
            "relation": None, "relation_current": False, "observation": None,
            "observation_only": True, "ownership_valid": False, "resource_actions": "UNSUPPORTED",
            "capture_kind": "fixture"}


def resource_display(action="sample"):
    # Rendering-only fixture: semantic evidence fixtures belong to the resource
    # contract tests. The UI needs no process handles or live backend here.
    metric = {"state": "AVAILABLE", "error": None,
              "some": {"avg10_bp": 123}, "full": {"avg10_bp": 0}}
    return {"schema_version": 1, "action": action, "outcome": "OK", "error": None,
            "relation": {"render_fixture": True}, "relation_current": True,
            "observation": {"observation_id": "00000000-0000-4000-8000-000000000016",
                "cpu": {"main": {"cpu_time_ns": 10_000_000, "elapsed_ns": 100_000_000},
                        "backend": {"cpu_time_ns": 220_000_000, "elapsed_ns": 100_000_000}},
                "after": {"main": {"rss_bytes_estimate": 10 * 1024 * 1024},
                          "backend": {"rss_bytes_estimate": 612 * 1024 * 1024},
                          "pressure": {"metrics": {key: copy.deepcopy(metric) for key in ("cpu", "memory", "io")}}}},
            "observation_only": True, "ownership_valid": False, "resource_actions": "UNSUPPORTED",
            "capture_kind": "fixture"}


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.sysfs = fixture(self.root)
        self.destination = self.root / "session"
        self.stdout = io.StringIO()

    def run_console(self, commands="exit\n", **kwargs):
        values = {"input_stream": io.StringIO(commands), "output_stream": self.stdout,
                  "proc_root": self.proc, "sys_root": self.sysfs, "test_system": "Linux",
                  "resolver": dns, "fetcher": fetch}
        values.update(kwargs)
        return shell.run_console(self.destination, **values)

    def result(self):
        return json.loads((self.destination / "session-result.json").read_bytes())

    def events(self):
        return [json.loads(row) for row in (self.destination / "session.events.jsonl").read_bytes().splitlines()]

    def test_full_console_commands_and_exact_transcript(self):
        self.assertEqual(self.run_console("about\nstatus\nhardware\nnet status\nresolve example.com\nfetch https://example.com/\nhelp\nexit\n"), 0)
        result = self.result()
        events = self.events()
        self.assertEqual(result["capture_kind"], "fixture")
        self.assertEqual(result["state"], "CLOSED")
        self.assertEqual(events[0]["event"], "START")
        self.assertEqual(events[-1]["data"], {"state": "CLOSED", "exit_code": 0, "reason": "exit"})
        self.assertEqual(events[0]["data"]["binding_status"], "UNBOUND")
        self.assertEqual(len(result["source_hashes"]), 35)
        self.assertEqual(result["schema_version"], 10)
        self.assertTrue(all(row["schema_version"] == 10 for row in events))
        self.assertIsNone(events[0]["data"]["source_process"])
        self.assertEqual([row["sequence"] for row in events], list(range(1, len(events) + 1)))
        self.assertEqual((self.destination / "console.log").read_text(encoding="utf-8"), self.stdout.getvalue())
        for name, digest in result["files"].items():
            self.assertEqual(hashlib.sha256((self.destination / name).read_bytes()).hexdigest(), digest)
        self.assertIn("AIOS Console", self.stdout.getvalue())
        self.assertIn("HTTPS 200", self.stdout.getvalue())
        self.assertEqual(self.stdout.getvalue().count(shell.PROMPT), 8)

    def test_unknown_and_bad_arguments_do_not_close_session(self):
        self.assertEqual(self.run_console("$(whoami)\nfetch\nnet configure\nstatus\nexit\n"), 0)
        commands = [row["data"] for row in self.events() if row["event"] == "COMMAND"]
        self.assertEqual([row["outcome"] for row in commands], ["ERROR", "ERROR", "ERROR", "OK", "OK"])
        self.assertEqual(commands[0]["result"]["error"], "unknown_command")
        self.assertNotIn("$(whoami)", self.stdout.getvalue())

    def test_dns_error_returns_to_prompt(self):
        def failing(host):
            return {**dns(host), "outcome": "ERROR", "error": "dns_failed", "addresses": []}
        self.assertEqual(self.run_console("resolve invalid.example\nstatus\nexit\n", resolver=failing), 0)
        self.assertEqual(self.events()[1]["data"]["outcome"], "ERROR")
        self.assertIn("AIOS session: running", self.stdout.getvalue())

    def test_eof_is_clean_close(self):
        self.assertEqual(self.run_console(""), 0)
        self.assertEqual(self.events()[-1]["data"]["reason"], "eof")
        self.assertEqual(len(self.events()), 2)

    def test_non_linux_never_displays_product_prompt(self):
        self.assertEqual(self.run_console(test_system="Windows"), 3)
        self.assertNotIn(shell.PROMPT, self.stdout.getvalue())
        self.assertNotIn(shell.BANNER, self.stdout.getvalue())
        self.assertEqual(self.result()["state"], "FAILED")

    def test_failed_boot_never_accepts_commands(self):
        (self.proc / "cpuinfo").unlink()
        self.assertEqual(self.run_console(), 1)
        self.assertEqual([row["event"] for row in self.events()], ["START", "STOP"])
        self.assertNotIn(shell.PROMPT, self.stdout.getvalue())

    def test_existing_directory_is_refused(self):
        self.assertEqual(self.run_console(), 0)
        original = (self.destination / "session-result.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_console()
        self.assertEqual((self.destination / "session-result.json").read_bytes(), original)

    def test_remote_text_cannot_control_terminal(self):
        def malicious(url):
            return {**fetch(url), "body_preview": "hi\x1b[2J\r\x07\x9b\u202eforged"}
        self.assertEqual(self.run_console("fetch https://example.com/\nexit\n", fetcher=malicious), 0)
        output = self.stdout.getvalue()
        for character in ("\x1b", "\r", "\x07", "\x9b", "\u202e"):
            self.assertNotIn(character, output)

    def test_command_controls_are_rejected_without_reflection(self):
        self.assertEqual(self.run_console("help\x1b[2J\nexit\n"), 0)
        command = self.events()[1]["data"]
        self.assertEqual(command["result"]["error"], "invalid_input")
        self.assertEqual(command["args"], [])
        self.assertNotIn("\x1b", self.stdout.getvalue())

    def test_invalid_network_target_is_redacted_in_evidence(self):
        def invalid(url):
            return {**fetch(url), "url": "<invalid>", "outcome": "ERROR", "error": "invalid_url"}
        self.assertEqual(self.run_console("fetch https://user:secret@example.com/\nexit\n", fetcher=invalid), 0)
        command = self.events()[1]["data"]
        self.assertEqual(command["args"], ["<invalid>"])
        self.assertNotIn("secret", (self.destination / "session.events.jsonl").read_text(encoding="utf-8"))

    def test_input_limit_fails_without_running_tail(self):
        self.assertEqual(self.run_console("a" * (shell.MAX_INPUT + 1) + "\nexit\n"), 2)
        self.assertEqual(self.events()[-1]["data"]["reason"], "input_limit")
        self.assertEqual(len(self.events()), 3)

    def test_event_limit_preserves_terminal_failure_record(self):
        with mock.patch.object(shell, "MAX_EVENTS", 5):
            self.assertEqual(self.run_console("status\n" * 5), 2)
        self.assertEqual(len(self.events()), 5)
        self.assertEqual(self.events()[-1]["data"]["reason"], "command_limit")

    def test_event_byte_limit_preserves_terminal_failure_record(self):
        with mock.patch.object(shell, "MAX_SESSION_BYTES", 2048):
            self.assertEqual(self.run_console("status\n" * 12), 1)
        self.assertLessEqual((self.destination / "session.events.jsonl").stat().st_size, 2048)
        self.assertEqual(self.result()["state"], "FAILED")
        self.assertEqual(self.events()[-1]["event"], "STOP")
        self.assertEqual(self.events()[-1]["data"]["reason"], "runtime_error")

    def test_transcript_limit_preserves_failure(self):
        with mock.patch.object(shell, "MAX_TRANSCRIPT_BYTES", 2500):
            self.assertEqual(self.run_console("help\n" * 5), 1)
        self.assertLessEqual((self.destination / "console.log").stat().st_size, 2500)
        self.assertEqual(self.events()[-1]["data"]["reason"], "runtime_error")

    def test_interrupt_keeps_session_available(self):
        class InterruptedInput(io.StringIO):
            first = True
            def readline(self, size=-1):
                if self.first:
                    self.first = False
                    raise KeyboardInterrupt
                return super().readline(size)
        self.assertEqual(self.run_console(input_stream=InterruptedInput("exit\n")), 0)
        self.assertEqual(self.events()[1]["data"]["result"]["error"], "interrupted")
        self.assertEqual(self.events()[-1]["data"]["reason"], "exit")

    def test_network_runtime_exception_is_failed_not_successful(self):
        def crashed(host):
            raise RuntimeError("fixture fault")
        self.assertEqual(self.run_console("resolve example.com\nexit\n", resolver=crashed), 1)
        self.assertEqual(self.result()["state"], "FAILED")

    def test_clear_requires_tty(self):
        self.assertEqual(self.run_console("clear\nexit\n"), 0)
        self.assertEqual(self.events()[1]["data"]["result"]["error"], "not_a_terminal")
        self.assertNotIn("\x1b", self.stdout.getvalue())

    def test_visible_rows_are_bounded(self):
        for index in range(shell.MAX_VISIBLE_DEVICES + 2):
            put(self.sysfs, f"class/block/loop{index}/size", "0")
        self.assertEqual(self.run_console("hardware block\nexit\n"), 0)
        self.assertIn("Showing 16 of 18 objects", self.stdout.getvalue())
        self.assertEqual(len(self.events()[1]["data"]["result"]["sections"]["block"]["data"]), 18)

    def test_service_commands_use_explicit_state_and_exit_leaves_service_running(self):
        responses = [service("ABSENT"), service(), service("STOPPED"), service(generation=2)]
        controller = mock.Mock(side_effect=responses)
        state = self.root / "runtime"
        self.assertEqual(self.run_console("service status\nservice start\nservice stop\nservice restart\nexit\n",
                                         service_dir=state, service_control=controller), 0)
        self.assertEqual(controller.call_args_list,
                         [mock.call(state, action) for action in ("status", "start", "stop", "restart")])
        self.assertEqual(self.result()["capture_kind"], "fixture")
        self.assertIn("generation 2; instance 00000000", self.stdout.getvalue())
        self.assertIn("Observations: 3; identity: source-only / UNBOUND.", self.stdout.getvalue())
        self.assertFalse(state.exists())

    def test_service_is_never_implicitly_started_or_stopped(self):
        controller = mock.Mock()
        with mock.patch.object(shell.Path, "home", return_value=self.root / "user"):
            self.assertEqual(self.run_console("help\nabout\nexit\n", service_control=controller), 0)
        controller.assert_not_called()
        self.assertFalse((self.root / "user/.local/state/aios/console-runtime").exists())

    def test_service_defaults_to_runtime_private_state_and_errors_keep_prompt(self):
        controller = mock.Mock(return_value=service(error="ALREADY_RUNNING"))
        with mock.patch.object(shell.Path, "home", return_value=self.root / "user"):
            self.assertEqual(self.run_console("service start\nstatus\nexit\n", service_control=controller), 0)
        controller.assert_called_once_with(self.root / "user/.local/state/aios/console-runtime", "start")
        self.assertIn("Service start failed: ALREADY_RUNNING.", self.stdout.getvalue())
        self.assertIn("AIOS session: running", self.stdout.getvalue())

    def test_service_invalid_actions_never_reach_controller(self):
        controller = mock.Mock()
        self.assertEqual(self.run_console("service\nservice kill\nservice start extra\nexit\n", service_control=controller), 0)
        controller.assert_not_called()
        self.assertEqual([e["data"]["result"]["error"] for e in self.events()[1:4]], ["invalid_arguments"] * 3)

    def test_service_callback_alone_marks_capture_as_fixture(self):
        original = shell.run_boot
        def captured(destination, **kwargs):
            self.assertEqual(kwargs["test_system"], "Linux")
            return original(destination, proc_root=self.proc, sys_root=self.sysfs, test_system="Linux")
        with mock.patch.object(shell.platform, "system", return_value="Linux"), mock.patch.object(shell, "run_boot", side_effect=captured):
            self.assertEqual(shell.run_console(self.destination, input_stream=io.StringIO("exit\n"),
                                              output_stream=self.stdout, service_control=mock.Mock()), 0)
        self.assertEqual(self.result()["capture_kind"], "fixture")

    def test_service_runtime_exception_is_failed_not_successful(self):
        controller = mock.Mock(side_effect=RuntimeError("fixture fault"))
        self.assertEqual(self.run_console("service start\nexit\n", service_control=controller), 1)
        self.assertEqual(self.result()["state"], "FAILED")

    def test_agent_room_and_ask_use_explicit_controller_and_join_prompt(self):
        from test_hosted_task_console import TASK_ID, task_reply
        state, config = self.root / "main", self.root / "model.json"
        responses = [agent6("start"), agent6("room-discover"), agent6("room-bind", bound=True),
                     task_reply("ask-start", phase="ACCEPTED", prompt="What is AIOS?"),
                     task_reply("task-result", phase="FINISHED", model_outcome="ANSWERED",
                                prompt="What is AIOS?", content="AIOS is ready."),
                     agent6("restart"), agent6("stop", state="STOPPED")]
        controller = mock.Mock(side_effect=responses)
        commands = ("agent start\nroom discover\nroom bind\nask What   is\tAIOS?\ntask result "
                    + TASK_ID + "\nagent restart\nagent stop\nexit\n")
        with mock.patch.object(shell, "uuid", SimpleNamespace(UUID=uuid.UUID, uuid4=lambda: uuid.UUID(TASK_ID))):
            self.assertEqual(self.run_console(commands, agent_dir=state, agent_config=config, agent_control=controller), 0)
        self.assertEqual(controller.call_args_list,
                         [mock.call(state, action, config=config, prompt="What is AIOS?" if action == "ask-start" else None,
                                    **({"backend_dir": Path("/tmp/aios-model-backend")} if action in ("start", "restart") else
                                       {"request_id": TASK_ID} if action in ("ask-start", "task-result") else {}))
                          for action in ("start", "room-discover", "room-bind", "ask-start", "task-result", "restart", "stop")])
        self.assertIn("Task accepted: " + TASK_ID, self.stdout.getvalue())
        self.assertIn("MAIN answer:\n  AIOS is ready.", self.stdout.getvalue())
        self.assertEqual(self.events()[4]["data"]["result"], responses[3])
        self.assertEqual(self.events()[5]["data"]["result"], responses[4])
        self.assertIn("Cell 1 -> Node 101 (MAIN); binding generation 1; bound nodes 1.", self.stdout.getvalue())
        self.assertEqual(self.result()["capture_kind"], "fixture")
        self.assertFalse(state.exists())

    def test_backend_commands_use_separate_explicit_controller(self):
        directory, config = self.root / "backend", self.root / "model.json"
        actions = ("status", "start", "restart", "stop")
        controller = mock.Mock(side_effect=[backend(action, state="ABSENT" if action == "status"
            else "STOPPED" if action == "stop" else "RUNNING") for action in actions])
        main = mock.Mock()
        self.assertEqual(self.run_console("backend status\nbackend start\nbackend restart\nbackend stop\nexit\n",
            backend_control=controller, backend_dir=directory, agent_config=config, agent_control=main), 0)
        self.assertEqual(controller.call_args_list, [mock.call(directory, action, config=config) for action in actions])
        main.assert_not_called()
        self.assertFalse(directory.exists())
        self.assertEqual(self.result()["capture_kind"], "fixture")
        self.assertIn("AIOS model backend: RUNNING; backend ready", self.stdout.getvalue())
        self.assertIn("MAIN service and Cell management state are separate", self.stdout.getvalue())

    def test_backend_never_implicitly_started_or_stopped_and_bad_syntax_is_local(self):
        controller = mock.Mock()
        self.assertEqual(self.run_console("help\nabout\nbackend\nbackend kill\nbackend start extra\nexit\n",
                                         backend_control=controller), 0)
        controller.assert_not_called()

    def test_backend_default_state_path_and_stale_readiness(self):
        controller = mock.Mock(return_value=backend(state="STALE", error="process-not-running"))
        self.assertEqual(self.run_console("backend status\nstatus\nexit\n", backend_control=controller), 0)
        controller.assert_called_once_with(Path("/tmp/aios-model-backend"), "status", config=None)
        self.assertIn("Backend status failed: process-not-running.", self.stdout.getvalue())
        self.assertIn("AIOS model backend: STALE; backend not ready", self.stdout.getvalue())

    def test_backend_callback_alone_marks_fixture(self):
        original = shell.run_boot
        def captured(destination, **kwargs):
            self.assertEqual(kwargs["test_system"], "Linux")
            return original(destination, proc_root=self.proc, sys_root=self.sysfs, test_system="Linux")
        with mock.patch.object(shell.platform, "system", return_value="Linux"), mock.patch.object(shell, "run_boot", side_effect=captured):
            self.assertEqual(shell.run_console(self.destination, input_stream=io.StringIO("exit\n"),
                output_stream=self.stdout, backend_control=mock.Mock()), 0)
        self.assertEqual(self.result()["capture_kind"], "fixture")

    def test_backend_controller_exception_fails_session(self):
        self.assertEqual(self.run_console("backend start\nexit\n",
            backend_control=mock.Mock(side_effect=RuntimeError("backend fault"))), 1)
        self.assertEqual(self.result()["state"], "FAILED")

    def test_agent_never_implicitly_started_or_stopped_and_bad_syntax_is_local(self):
        controller = mock.Mock()
        self.assertEqual(self.run_console("help\nabout\nagent\nroom\nask\nagent kill\nroom bind extra\nexit\n",
                                         agent_control=controller), 0)
        controller.assert_not_called()
        self.assertIn("explicit hosted AI service bindings", self.stdout.getvalue())

    def test_agent_default_path_errors_return_to_prompt(self):
        from test_hosted_task_console import TASK_ID
        controller = mock.Mock(return_value=agent6("ask-start", error="unbound", request_id=TASK_ID))
        with mock.patch.object(shell.Path, "home", return_value=self.root / "user"), \
                mock.patch.object(shell, "uuid", SimpleNamespace(UUID=uuid.UUID, uuid4=lambda: uuid.UUID(TASK_ID))):
            self.assertEqual(self.run_console("ask hello\nstatus\nexit\n", agent_control=controller), 0)
        controller.assert_called_once_with(self.root / "user/.local/state/aios/main-agent", "ask-start",
                                           config=None, prompt="hello", request_id=TASK_ID)
        self.assertIn("MAIN ask-start failed: unbound.", self.stdout.getvalue())
        self.assertIn("AIOS session: running", self.stdout.getvalue())

    def test_agent_model_text_is_indented_sanitized_and_bounded(self):
        from test_hosted_task_console import TASK_ID, task_reply
        hostile = "\x1b[2J\naios> fetch evil\r\u202e" + "x" * 5000
        response = task_reply("task-result", phase="FINISHED", model_outcome="ANSWERED", content=hostile)
        controller = mock.Mock(return_value=response)
        self.assertEqual(self.run_console("task result " + TASK_ID + "\nexit\n", agent_control=controller), 0)
        text = self.stdout.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)
        self.assertNotIn("\u202e", text)
        self.assertNotIn("\naios> fetch", text)
        self.assertEqual(len(text.split("MAIN answer:\n  ")[1].split("\n")[0]), 4096)
        self.assertEqual(self.events()[1]["data"]["result"]["task"]["inference_receipt"]["content"], hostile)

    def test_agent_callback_alone_marks_fixture(self):
        original = shell.run_boot
        def captured(destination, **kwargs):
            self.assertEqual(kwargs["test_system"], "Linux")
            return original(destination, proc_root=self.proc, sys_root=self.sysfs, test_system="Linux")
        with mock.patch.object(shell.platform, "system", return_value="Linux"), mock.patch.object(shell, "run_boot", side_effect=captured):
            self.assertEqual(shell.run_console(self.destination, input_stream=io.StringIO("exit\n"),
                                              output_stream=self.stdout, agent_control=mock.Mock()), 0)
        self.assertEqual(self.result()["capture_kind"], "fixture")

    def test_resources_only_link_passes_backend_path_and_never_implicitly_links(self):
        controller = mock.Mock(side_effect=[{**agent6("resources-" + action, error="resource-unlinked"),
            "capture_kind": "fixture", "resource_result": resource_error(action)} for action in ("status", "link", "sample")])
        directory, backend = self.root / "main", self.root / "backend"
        self.assertEqual(self.run_console("resources status\nresources link\nresources sample\nexit\n",
            agent_control=controller, agent_dir=directory, backend_dir=backend), 0)
        self.assertEqual(controller.call_args_list, [
            mock.call(directory, "resources-status", config=None, prompt=None),
            mock.call(directory, "resources-link", config=None, prompt=None, backend_dir=backend),
            mock.call(directory, "resources-sample", config=None, prompt=None)])
        self.assertFalse(directory.exists())
        self.assertFalse(backend.exists())
        self.assertEqual(self.result()["capture_kind"], "fixture")

    def test_resources_default_backend_path_only_used_on_explicit_link(self):
        controller = mock.Mock(return_value={**agent6("resources-link", state="ABSENT", error="process-not-running"),
                                            "resource_result": None})
        self.assertEqual(self.run_console("help\nresources\nresources apply\nresources sample extra\nresources link\nexit\n",
                                         agent_control=controller), 0)
        controller.assert_called_once_with(Path.home() / ".local/state/aios/main-agent", "resources-link",
                                           config=None, prompt=None, backend_dir=Path("/tmp/aios-model-backend"))
        self.assertIn("AIOS resources: unavailable; no resource response.", self.stdout.getvalue())

    def test_resources_separate_cpu_rss_and_cached_system_pressure_display(self):
        value = resource_display("status")
        value["observation"]["after"]["pressure"]["metrics"]["memory"] = {"state": "UNAVAILABLE", "error": "pressure-missing"}
        controller = mock.Mock(return_value={**agent6("resources-status", bound=True), "resource_result": value})
        self.assertEqual(self.run_console("resources status\nexit\n", agent_control=controller), 0)
        text = self.stdout.getvalue()
        self.assertIn("Last observation (cached)", text)
        self.assertIn("MAIN control process: CPU 10.000 ms over 100.000 ms; RSS estimate 10.00 MiB.", text)
        self.assertIn("Model backend process: CPU 220.000 ms over 100.000 ms; RSS estimate 612.00 MiB.", text)
        self.assertIn("Linux system PSI (unattributed)", text)
        self.assertIn("CPU: some avg10 1.23%; full avg10 undefined for system CPU.", text)
        self.assertIn("MEMORY: unavailable (pressure-missing).", text)

    def test_explicit_resource_failure_is_separate_from_successful_task_result(self):
        from test_hosted_task_console import TASK_ID, task_reply
        # Current CLI observes resources explicitly and retrieves the Task separately.
        resource = {**agent6("resources-sample", error="process-exited", bound=True),
                    "resource_result": resource_error("sample", "process-exited")}
        value = task_reply("task-result", phase="FINISHED", model_outcome="ANSWERED", content="AIOS is ready.")
        controller = mock.Mock(side_effect=[resource, value])
        self.assertEqual(self.run_console("resources sample\ntask result " + TASK_ID + "\nexit\n",
                                         agent_control=controller), 0)
        self.assertEqual([self.events()[index]["data"]["outcome"] for index in (1, 2)], ["ERROR", "OK"])
        self.assertIn("MAIN answer:\n  AIOS is ready.", self.stdout.getvalue())
        self.assertIn("Resources sample failed: process-exited.", self.stdout.getvalue())
        self.assertNotIn("MAIN ask failed", self.stdout.getvalue())

    def test_backend_directory_cli_argument_is_forwarded(self):
        directory = self.root / "evidence"
        backend = self.root / "backend"
        with mock.patch.object(sys, "argv", ["aios-console", "--artifact-dir", str(directory), "--backend-dir", str(backend)]), \
                mock.patch.object(shell, "run_console", return_value=0) as run:
            self.assertEqual(shell.main(), 0)
        run.assert_called_once_with(directory, service_dir=None, agent_dir=None, agent_config=None, backend_dir=backend)

    def test_cell_commands_are_explicit_management_calls_and_keep_main_running(self):
        controller = mock.Mock(side_effect=[cell6(), cell6("deactivate"), cell6("activate", active=False)])
        directory = self.root / "main"
        self.assertEqual(self.run_console("cell status\ncell deactivate\ncell activate\nexit\n",
                                         agent_dir=directory, agent_control=controller), 0)
        self.assertEqual(controller.call_args_list, [mock.call(directory, "cell-" + action, config=None, prompt=None)
                                                   for action in ("status", "deactivate", "activate")])
        self.assertFalse(directory.exists())
        text = self.stdout.getvalue()
        self.assertIn("AIOS Cell 1: inactive; generation 2", text)
        self.assertIn("AIOS Cell 1: active; generation 3", text)
        self.assertIn("Bound MAIN nodes: 0; binding: STALE.", text)
        self.assertIn("MAIN process: RUNNING; Cell activity does not start or stop it.", text)
        records = [event["data"]["result"] for event in self.events()[1:4]]
        self.assertTrue(all(record["source_record"] == records[0]["source_record"] for record in records))
        self.assertEqual(self.result()["capture_kind"], "fixture")

    def test_cell_help_and_invalid_syntax_have_no_implicit_lifecycle_effect(self):
        controller = mock.Mock()
        self.assertEqual(self.run_console("help\nabout\ncell\ncell stop\ncell activate 2\ncell delete\nexit\n",
                                         agent_control=controller), 0)
        controller.assert_not_called()
        self.assertIn("cell status|activate|deactivate", self.stdout.getvalue())
        self.assertIn("Use cell to activate or deactivate Cell 1's management state.", self.stdout.getvalue())
        self.assertEqual([event["data"]["result"]["error"] for event in self.events()[3:7]], ["invalid_arguments"] * 4)

    def test_cell_transport_and_management_errors_return_to_the_prompt(self):
        controller = mock.Mock(side_effect=[agent6("cell-status", state="ABSENT", error="process-not-running"),
                                           cell6("deactivate", error="overflow")])
        self.assertEqual(self.run_console("cell status\ncell deactivate\nstatus\nexit\n", agent_control=controller), 0)
        text = self.stdout.getvalue()
        self.assertIn("Cell status failed: process-not-running.", text)
        self.assertIn("AIOS Cell 1: unavailable; no management snapshot.", text)
        self.assertIn("Cell deactivate failed: overflow.", text)
        self.assertIn("AIOS session: running", text)


if __name__ == "__main__":
    unittest.main()
