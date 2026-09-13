"""Real console loops with controlled task replies; no live model or cancel proof."""
from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_hosted_console import ROOT, agent, backend, dns, fetch, fixture
from aios_console import shell


TASK_ID = "12345678-9abc-4def-8abc-123456789abc"
OTHER_ID = "12345678-9abc-4def-8abc-123456789abd"


def task_reply(action, *, phase="RUNNING", model_outcome=None, request_id=TASK_ID,
               prompt="hello", content="The task answer.", cancelled=False,
               stopped=False, error=None, task_control=None):
    """Build full MAIN6 rendering records from an I/O-free admitted state.

    The execution dictionaries are fixture placeholders, not Linux evidence.
    This suite exercises the console boundary; independent attestation belongs
    to the task/verifier tests.
    """
    from aios_agent.request_state import RECEIPT_KEYS, RequestState

    completed = agent("ask", bound=True, prompt=prompt, content=content, protocol=5)
    admitted = agent("status", bound=True, protocol=5)
    receipt = {key: copy.deepcopy(completed["inference_receipt"][key]) for key in RECEIPT_KEYS}
    receipt["request_id"] = request_id
    expected = backend()
    boot = admitted["source_record"]["host_boot_id"]
    for name in ("supervisor_identity", "child_identity"):
        expected["service_record"][name]["host_boot_id"] = boot
    expected["descriptor"]["host_boot_id"] = boot
    config = {"schema_version": 1, "endpoint": "http://127.0.0.1:8089", "model_id": "fixture-main",
              "model_sha256": "a" * 64, "backend_sha256": "d" * 64, "provenance_sha256": "e" * 64,
              "model_path": "fixture.gguf", "backend_path": "fixture-backend"}
    owner = {"host_boot_id": boot, "process_id": 303, "process_start_ticks": 102, "uid": 1000}
    state = RequestState(request_id=request_id, owner=owner, config=config,
        source=admitted["source_record"], management=admitted["management_snapshot"],
        space_context=receipt["space_context"], prompt=prompt,
        backend_expected=expected, accepted_ns=100)
    if model_outcome != "NOT_STARTED" and phase != "ACCEPTED":
        state.mark_running(1009, now_ns=101)
    if cancelled or model_outcome == "NOT_STARTED":
        state.request_cancel(owner, now_ns=102)
    if phase == "FINISHED":
        if model_outcome == "NOT_STARTED":
            state.finish_without_dispatch("cancel-before-dispatch", now_ns=103)
        elif model_outcome == "ANSWERED":
            receipt["backend_execution"] = {"schema_version": 1,
                "descriptor": copy.deepcopy(expected["descriptor"]), "capture_kind": "fixture",
                **{name: {"console_fixture": True} for name in ("before", "send", "after")}}
            state.record_worker_exit(1009, 0, receipt=receipt, now_ns=103)
        else:
            state.record_worker_exit(1009, -15, receipt=None, now_ns=103)
    if stopped:
        terminal = copy.deepcopy(expected)
        terminal.update(action="stop", state="STOPPED", descriptor=None)
        terminal["service_record"].update(lifecycle_state="exited", backend_ready=False)
        state.record_backend_stop(terminal, now_ns=104)
    current = completed if model_outcome == "ANSWERED" else admitted
    if action == "task-cancel" and task_control is None:
        task_control = {"cancel_outcome": "ACCEPTED", "backend_stop_attempt": None}
    return {"schema_version": 6, "outcome": "ERROR" if error else "OK", "error": error,
        "action": action, "state": "RUNNING", "service_kind": "AI_SERVICE",
        "source_record": copy.deepcopy(current["source_record"]),
        "management_snapshot": copy.deepcopy(current["management_snapshot"]),
        "management_outcome": None, "inference_receipt": None,
        "resource_actions": "UNSUPPORTED", "capture_kind": "fixture", "resource_result": None,
        "space_context": None, "request_id": request_id, "task": state.snapshot(),
        "task_control": copy.deepcopy(task_control)}


class TaskConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.sysfs = fixture(self.root)
        self.agent_dir = self.root / "main"
        self.backend_dir = self.root / "backend"
        self.config_path = self.root / "model.json"
        self.serial = 0

    def run_console(self, commands="exit\n", *, controller=None, input_stream=None):
        self.destination = self.root / ("session-" + str(self.serial))
        self.serial += 1
        self.stdout = io.StringIO()
        if controller is None:
            controller = mock.Mock(side_effect=AssertionError("unexpected MAIN control"))
        self.controller = controller
        backend_control = mock.Mock(side_effect=AssertionError("unexpected backend control"))
        service_control = mock.Mock(side_effect=AssertionError("unexpected service control"))
        code = shell.run_console(self.destination,
            input_stream=io.StringIO(commands) if input_stream is None else input_stream,
            output_stream=self.stdout, proc_root=self.proc, sys_root=self.sysfs,
            test_system="Linux", resolver=dns, fetcher=fetch,
            agent_dir=self.agent_dir, agent_config=self.config_path,
            backend_dir=self.backend_dir, agent_control=controller,
            backend_control=backend_control, service_control=service_control)
        backend_control.assert_not_called()
        service_control.assert_not_called()
        self.assertEqual(code, 0, self.stdout.getvalue())
        self.assertEqual(self.result()["state"], "CLOSED")
        self.assertEqual((self.destination / "console.log").read_text(encoding="utf-8"),
                         self.stdout.getvalue())
        return self.stdout.getvalue()

    def result(self):
        return json.loads((self.destination / "session-result.json").read_bytes())

    def events(self):
        return [json.loads(line) for line in
                (self.destination / "session.events.jsonl").read_bytes().splitlines()]

    def commands(self):
        return [event["data"] for event in self.events() if event["event"] == "COMMAND"]

    def generated_ids(self, *ids):
        values = [uuid.UUID("12345678-9abc-4def-8abc-123456789abe"),
                  *(uuid.UUID(value) for value in ids)]
        return mock.patch.object(shell, "uuid",
            SimpleNamespace(UUID=uuid.UUID, uuid4=mock.Mock(side_effect=values)))

    def test_cli10_session10_and_all_35_source_bytes_are_recorded(self):
        text = self.run_console("about\nhelp\nexit\n")
        result = self.result()
        self.assertEqual(shell.VERSION, "0.10.0")
        self.assertEqual(result["schema_version"], 10)
        self.assertTrue(all(row["schema_version"] == 10 for row in self.events()))
        self.assertEqual(self.events()[0]["data"]["runtime_version"], "0.10.0")
        self.assertEqual(len(result["source_hashes"]), 35)
        for name in ("async_inference.py", "request_state.py", "request_runtime.py"):
            self.assertIn("aios_agent/" + name, result["source_hashes"])
        for name, digest in result["source_hashes"].items():
            self.assertEqual(hashlib.sha256((ROOT / "hosted/linux" / name).read_bytes()).hexdigest(), digest)
        for name, digest in result["files"].items():
            self.assertEqual(hashlib.sha256((self.destination / name).read_bytes()).hexdigest(), digest)
        self.assertIn("0.10", text)
        self.controller.assert_not_called()

    def test_invalid_task_syntax_and_noncanonical_ids_never_call_controller(self):
        invalid_ids = ("not-a-uuid", TASK_ID.upper(), TASK_ID.replace("-", ""),
                       "{" + TASK_ID + "}", "00000000-0000-0000-0000-000000000000", "../another")
        commands = ["task", "task status", "task result", "task cancel", "task kill " + TASK_ID,
                    "task status " + TASK_ID + " extra"]
        commands.extend("task " + action + " " + value
                        for action in ("status", "result", "cancel") for value in invalid_ids)
        text = self.run_console("\n".join([*commands, "about", "exit", ""]))
        self.controller.assert_not_called()
        recorded = self.commands()
        self.assertEqual(len(recorded), len(commands) + 2)
        self.assertTrue(all(row["outcome"] == "ERROR" for row in recorded[:-2]))
        self.assertEqual([row["outcome"] for row in recorded[-2:]], ["OK", "OK"])
        self.assertEqual(text.count(shell.PROMPT), len(commands) + 2)

    def test_help_about_and_explicit_network_commands_keep_working_without_tasks(self):
        text = self.run_console("help\nabout\nnet status\nresolve example.com\nfetch https://example.com/\nexit\n")
        self.controller.assert_not_called()
        self.assertIn("task status|result|cancel UUID", text)
        self.assertIn("HTTPS 200", text)
        self.assertTrue(all(row["outcome"] == "OK" for row in self.commands()))

    def test_ask_admission_returns_to_input_without_polling_or_showing_an_answer(self):
        response = task_reply("ask-start", prompt="What is AIOS?")
        controller = mock.Mock(return_value=response)
        test = self

        class AdmissionInput(io.StringIO):
            reads = 0

            def readline(self, *args):
                self.reads += 1
                if self.reads == 2:
                    test.assertEqual(controller.call_count, 1)
                    test.assertIn(TASK_ID, test.stdout.getvalue())
                    test.assertEqual(test.stdout.getvalue().count(shell.PROMPT), 2)
                    test.assertNotIn("MAIN answer:", test.stdout.getvalue())
                return super().readline(*args)

        with self.generated_ids(TASK_ID):
            text = self.run_console(controller=controller,
                input_stream=AdmissionInput("ask What   is\tAIOS?\nabout\nexit\n"))
        controller.assert_called_once()
        call = controller.call_args
        self.assertEqual(call.args, (self.agent_dir, "ask-start"))
        self.assertEqual(call.kwargs["prompt"], "What is AIOS?")
        self.assertEqual(call.kwargs["request_id"], TASK_ID)
        self.assertEqual(call.kwargs["config"], self.config_path)
        self.assertNotIn("MAIN answer:", text)
        self.assertEqual(self.commands()[0]["result"], response)

    def test_separate_asks_forward_distinct_externally_generated_ids(self):
        responses = [task_reply("ask-start", request_id=TASK_ID, prompt="first"),
                     task_reply("ask-start", request_id=OTHER_ID, prompt="second")]
        controller = mock.Mock(side_effect=responses)
        with self.generated_ids(TASK_ID, OTHER_ID):
            self.run_console("ask first\nask second\nexit\n", controller=controller)
        self.assertEqual([call.args for call in controller.call_args_list],
                         [(self.agent_dir, "ask-start"), (self.agent_dir, "ask-start")])
        self.assertEqual([call.kwargs["request_id"] for call in controller.call_args_list], [TASK_ID, OTHER_ID])
        self.assertEqual([row["result"] for row in self.commands()[:2]], responses)

    def test_status_and_cancel_forward_the_same_id_and_preserve_complete_control_evidence(self):
        stop_attempt = backend("stop", state="STOPPED")
        control = {"cancel_outcome": "ACCEPTED", "backend_stop_attempt": stop_attempt}
        responses = [task_reply("task-status"),
                     task_reply("task-cancel", phase="CANCEL_REQUESTED", cancelled=True, task_control=control),
                     task_reply("task-result", phase="FINISHED", model_outcome="UNKNOWN", cancelled=True)]
        original = copy.deepcopy(responses)
        controller = mock.Mock(side_effect=responses)
        self.run_console("\n".join(["task " + action + " " + TASK_ID
                         for action in ("status", "cancel", "result")]) + "\nexit\n", controller=controller)
        self.assertEqual([call.args for call in controller.call_args_list],
                         [(self.agent_dir, "task-" + action) for action in ("status", "cancel", "result")])
        self.assertTrue(all(call.kwargs["request_id"] == TASK_ID for call in controller.call_args_list))
        self.assertTrue(all(call.kwargs["prompt"] is None for call in controller.call_args_list))
        self.assertEqual(responses, original, "rendering must not rewrite the controller evidence")
        self.assertEqual([row["result"] for row in self.commands()[:3]], original)
        self.assertEqual(self.commands()[1]["result"]["task_control"], control)

    def test_only_task_result_renders_an_answer_even_if_admission_or_status_is_finished(self):
        secret = "Only the explicit result command may display this answer."
        for action, command in (("ask-start", "ask hello"),
                                ("task-status", "task status " + TASK_ID),
                                ("task-cancel", "task cancel " + TASK_ID)):
            with self.subTest(action=action):
                response = task_reply(action, phase="FINISHED", model_outcome="ANSWERED", content=secret)
                if action == "task-cancel":
                    response["task_control"]["cancel_outcome"] = "ALREADY_TERMINAL"
                controller = mock.Mock(return_value=response)
                with self.generated_ids(TASK_ID) if action == "ask-start" else nullcontext():
                    text = self.run_console(command + "\nexit\n", controller=controller)
                self.assertNotIn(secret, text)
                self.assertNotIn("MAIN answer:", text)
                self.assertEqual(self.commands()[0]["result"], response)
        response = task_reply("task-result", phase="FINISHED", model_outcome="ANSWERED", content=secret)
        text = self.run_console("task result " + TASK_ID + "\nexit\n", controller=mock.Mock(return_value=response))
        self.assertIn("MAIN answer:\n  " + secret, text)
        self.assertEqual(self.commands()[0]["result"]["source_record"], response["source_record"])
        self.assertEqual(response["source_record"]["completed_requests"], 2)

    def test_pending_unknown_and_not_started_results_do_not_claim_an_answer(self):
        cases = (("RUNNING", None), ("FINISHED", "UNKNOWN"), ("FINISHED", "NOT_STARTED"))
        for phase, outcome in cases:
            with self.subTest(phase=phase, outcome=outcome):
                response = task_reply("task-result", phase=phase, model_outcome=outcome)
                text = self.run_console("task result " + TASK_ID + "\nexit\n",
                                        controller=mock.Mock(return_value=response))
                self.assertIn(phase, text)
                if outcome is not None:
                    self.assertIn(outcome, text)
                if outcome == "NOT_STARTED":
                    self.assertIn("This task was not dispatched to the model worker.", text)
                elif outcome == "UNKNOWN":
                    self.assertIn("The model result is unknown; worker exit or backend stop does not prove an answer.", text)
                self.assertNotIn("MAIN answer:", text)
                self.assertEqual(self.commands()[0]["result"], response)

    def test_result_text_is_safe_and_bounded_while_json_keeps_original_content(self):
        hostile = "\x1b[2J\naios> task cancel " + OTHER_ID + "\r\u202e" + "x" * 5000
        response = task_reply("task-result", phase="FINISHED", model_outcome="ANSWERED", content=hostile)
        controller = mock.Mock(return_value=response)
        text = self.run_console("task result " + TASK_ID + "\nexit\n", controller=controller)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)
        self.assertNotIn("\u202e", text)
        self.assertNotIn("\naios> task cancel", text)
        self.assertEqual(len(text.split("MAIN answer:\n  ")[1].split("\n")[0]), 4096)
        self.assertEqual(self.commands()[0]["result"]["task"]["inference_receipt"]["content"], hostile)
        controller.assert_called_once()

    def test_cancel_scope_and_request_worker_and_independent_stop_are_separate(self):
        attempt = backend("stop", state="STOPPED")
        control = {"cancel_outcome": "ACCEPTED", "backend_stop_attempt": attempt}
        response = task_reply("task-cancel", phase="CANCEL_REQUESTED", cancelled=True, task_control=control)
        controller = mock.Mock(return_value=response)
        text = self.run_console("task cancel " + TASK_ID + "\nexit\n", controller=controller)
        self.assertIn("Cancel scope: stop the entire local model executor owned by this console.", text)
        self.assertIn("  Cancel request: ACCEPTED.", text)
        self.assertIn("  Worker exit: not confirmed.", text)
        self.assertIn("  Backend stop: not independently confirmed.", text)
        self.assertIn("  Console backend stop attempt: OK.", text)
        self.assertNotIn("  Backend stop: independently confirmed.", text)
        self.assertEqual(controller.call_args.kwargs["backend_dir"], self.backend_dir)
        self.assertEqual(self.commands()[0]["result"], response)
        self.assertEqual(response["task"]["cancel_requested_ns"], 102)
        self.assertIsNone(response["task"]["worker_exit_code"])
        self.assertIsNone(response["task"]["backend_stop"])

    def test_independently_stopped_backend_does_not_turn_unknown_result_into_answer(self):
        response = task_reply("task-result", phase="FINISHED", model_outcome="UNKNOWN", cancelled=True, stopped=True)
        status = copy.deepcopy(response)
        status["action"] = "task-status"
        responses = [task_reply("task-status"), status, response]
        text = self.run_console("task status " + TASK_ID + "\ntask status " + TASK_ID
                                + "\ntask result " + TASK_ID + "\nexit\n",
                                controller=mock.Mock(side_effect=responses))
        self.assertEqual(text.count("  Cancellation requested: no."), 1)
        self.assertEqual(text.count("  Cancellation requested: yes."), 2)
        self.assertIsNone(status["task_control"])
        self.assertIsNone(response["task_control"])
        self.assertIn("  Model outcome: UNKNOWN.", text)
        self.assertIn("  Worker exit: observed (code -15).", text)
        self.assertIn("  Backend stop: independently confirmed.", text)
        self.assertIn("The model result is unknown; worker exit or backend stop does not prove an answer.", text)
        self.assertNotIn("MAIN answer:", text)
        self.assertEqual([row["result"] for row in self.commands()[:3]], responses)

    def test_answer_requires_finished_answered_ok_receipt_and_successful_reply(self):
        secret = "A result with a broken success condition must stay hidden."
        mutations = (
            lambda value: value["task"].update(phase="RUNNING"),
            lambda value: value["task"].update(model_outcome="UNKNOWN"),
            lambda value: value["task"]["inference_receipt"].update(outcome="ERROR"),
            lambda value: value.update(outcome="ERROR", error="state-io"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(condition=index):
                response = task_reply("task-result", phase="FINISHED", model_outcome="ANSWERED", content=secret)
                # Deliberately inconsistent rendering input, independent of the
                # protocol's separate validation: the UI must not print it.
                mutate(response)
                text = self.run_console("task result " + TASK_ID + "\nexit\n",
                                        controller=mock.Mock(return_value=response))
                self.assertNotIn(secret, text)
                self.assertNotIn("MAIN answer:", text)
                self.assertEqual(self.commands()[0]["result"], response)

    def test_missing_authenticated_task_retains_id_and_never_retries(self):
        response = task_reply("task-result", error="task-not-found")
        response["task"] = None
        controller = mock.Mock(return_value=response)
        text = self.run_console("task result " + TASK_ID + "\nabout\nexit\n", controller=controller)
        self.assertIn("Task " + TASK_ID + ": no authenticated task state is available.", text)
        self.assertIn("Query this UUID before deciding whether to submit another question.", text)
        self.assertNotIn("MAIN answer:", text)
        controller.assert_called_once()
        self.assertEqual(controller.call_args.kwargs["request_id"], TASK_ID)
        self.assertEqual(self.commands()[0]["result"], response)

    def test_uncertain_admission_keeps_generated_id_without_claiming_no_execution(self):
        response = task_reply("ask-start", error="rpc-timeout")
        response["task"] = None
        controller = mock.Mock(return_value=response)
        with self.generated_ids(TASK_ID):
            text = self.run_console("ask hello\nabout\nexit\n", controller=controller)
        controller.assert_called_once()
        self.assertEqual(controller.call_args.kwargs["request_id"], TASK_ID)
        self.assertIn("No inference receipt was returned; this does not prove that the model request was never sent.", text)
        self.assertIn("Use task status " + TASK_ID + ", task result " + TASK_ID + ", or task cancel " + TASK_ID + ".", text)
        self.assertNotIn("Task accepted:", text)
        self.assertNotIn("before model execution", text)
        self.assertNotIn("MAIN answer:", text)
        self.assertEqual(self.commands()[0]["result"], response)


if __name__ == "__main__":
    unittest.main()
