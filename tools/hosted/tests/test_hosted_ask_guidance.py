"""Current Task admission guidance and the preserved receipt-aware failure helper."""
from __future__ import annotations

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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aios_agent.inference import request_body
from aios_console import shell
from test_hosted_console import agent, agent6, fixture


class AskGuidanceTests(unittest.TestCase):
    def render(self, error=None, *, prompt="Explain this environment."):
        from test_hosted_task_console import TASK_ID, task_reply
        action = "ask-start" if error is not None else "task-result"
        command_text = f"ask {prompt}" if error is not None else "task result " + TASK_ID
        reply = (agent6(action, error=error, bound=True, request_id=TASK_ID) if error is not None
                 else task_reply(action, phase="FINISHED", model_outcome="ANSWERED",
                                 prompt=prompt, content="AIOS is ready."))
        before = copy.deepcopy(reply)
        controller = mock.Mock(return_value=reply)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proc, sysfs = fixture(root)
            output = io.StringIO()
            with mock.patch("aios_console.shell.resolve_host", side_effect=AssertionError("unexpected DNS")), \
                 mock.patch("aios_console.shell.fetch_url", side_effect=AssertionError("unexpected HTTP")), \
                 mock.patch.object(shell, "uuid", SimpleNamespace(UUID=uuid.UUID, uuid4=lambda: uuid.UUID(TASK_ID))):
                code = shell.run_console(root / "session", input_stream=io.StringIO(command_text + "\nexit\n"),
                    output_stream=output, proc_root=proc, sys_root=sysfs, test_system="Linux",
                    agent_control=controller, service_control=mock.Mock(side_effect=AssertionError("service action")),
                    backend_control=mock.Mock(side_effect=AssertionError("backend action")))
            self.assertEqual(code, 0)
            events = [json.loads(row) for row in (root / "session/session.events.jsonl").read_bytes().splitlines()]
            command = next(row for row in events if row["event"] == "COMMAND"
                           and row["data"]["name"] == ("ask" if error is not None else "task"))
            self.assertEqual(command["data"]["result"], before)
            self.assertEqual(reply, before, "guidance must not change the service response or its evidence")
            controller.assert_called_once()
            self.assertEqual(controller.call_args.args[1], action)
            self.assertEqual(controller.call_args.kwargs["prompt"], prompt if error is not None else None)
            self.assertEqual(controller.call_args.kwargs["request_id"], TASK_ID)
            if error is not None:
                self.assertIsNone(reply["task"])
                self.assertIn("Use task status " + TASK_ID, output.getvalue())
            self.assertEqual((root / "session/console.log").read_text(encoding="utf-8"), output.getvalue())
            return output.getvalue()

    def test_real_combined_input_budget_refusal_guides_question_revision(self):
        prompt = "\u6f22" * 1024
        request_body(prompt)  # The question alone is within the model request limit.
        context = agent("ask", bound=True, prompt="hello")["inference_receipt"]["space_context"]
        with self.assertRaisesRegex(ValueError, "^space-budget$"):
            request_body(prompt, space_context=context)
        text = self.render("space-budget", prompt=prompt)
        self.assertIn("Shorten the question", text)
        self.assertIn("model execution was not started", text)
        self.assertNotIn("check agent status and room status before retrying", text)
        self.assertNotIn("explicit recovery", text)

    def test_real_prompt_byte_limit_refusal_guides_input_revision(self):
        prompt = "\U0001f30e" * 1100
        with self.assertRaisesRegex(ValueError, "^prompt-invalid$"):
            request_body(prompt)
        text = self.render("prompt-invalid", prompt=prompt)
        self.assertIn("Revise the question", text)
        self.assertIn("before model execution", text)
        self.assertNotIn("room status", text)

    def test_environment_errors_request_new_observation(self):
        for error in ("space-invalid", "space-overflow", "space-future"):
            with self.subTest(error=error):
                text = self.render(error)
                self.assertIn("Run space to refresh observations", text)
                self.assertIn("before model execution", text)
                self.assertNotIn("Shorten the question", text)

    def test_environment_source_mismatch_requires_target_check_before_refresh(self):
        text = self.render("space-source-mismatch")
        self.assertIn("current MAIN source or binding", text)
        self.assertIn("after resolving the mismatch, run space", text)

    def test_binding_and_parent_refusals_request_explicit_target_inspection(self):
        for error in ("unbound", "stale", "model-not-ready", "source-exited", "retired-instance"):
            with self.subTest(error=error):
                text = self.render(error)
                self.assertIn("before model execution", text)
                self.assertIn("explicit recovery action", text)
        text = self.render("orphan")
        self.assertIn("parent Cell is inactive", text)
        self.assertIn("Check cell status and room status", text)

    def test_absent_main_limits_and_platform_have_actionable_distinct_guidance(self):
        text = self.render("process-not-running")
        self.assertIn("MAIN service is not running", text)
        self.assertIn("explicit start action", text)
        text = self.render("request-limit")
        self.assertIn("this MAIN session reached its request limit", text)
        text = self.render("unsupported-platform")
        self.assertIn("use the Linux-hosted runtime", text)

    def test_rpc_and_unclassified_failures_without_receipt_do_not_claim_unsent(self):
        for error in ("rpc-timeout", "protocol-error", "peer-mismatch", "state-io", "state-corrupt",
                      "backend-timeout", "backend-failed", "future-error"):
            with self.subTest(error=error):
                text = self.render(error)
                self.assertIn("request outcome is unknown", text)
                self.assertIn("does not prove that the model request was never sent", text)
                self.assertNotIn("before model execution", text)

    def test_receipt_presence_never_promotes_an_error_to_pre_execution_refusal(self):
        receipt = agent("ask", bound=True, prompt="hello")["inference_receipt"]
        for error in ("backend-timeout", "backend-failed", "space-budget", "prompt-invalid", "orphan"):
            with self.subTest(error=error):
                failed = {**receipt, "outcome": "ERROR", "error": error, "content": None,
                          "response_body": None, "response_sha256": None, "tokens_predicted": 0}
                # MAIN6 admission has no top-level receipt. Preserve the older
                # receipt-aware helper contract without inventing a MAIN6 reply.
                reply = agent("ask", bound=True, error=error)
                reply["inference_receipt"] = failed
                before = copy.deepcopy(reply)
                text = "\n".join(shell._ask_failure_lines(reply))
                self.assertEqual(reply, before)
                self.assertIn("request outcome is unknown", text)
                self.assertIn("does not confirm that backend work finished or stopped", text)
                self.assertNotIn("before model execution", text)
                self.assertNotIn("model execution was not started", text)

    def test_successful_task_result_displays_the_existing_answer(self):
        text = self.render()
        self.assertIn("MAIN answer:\n  AIOS is ready.\n", text)
        self.assertNotIn("request outcome is unknown", text)
        self.assertNotIn("MAIN ask failed", text)


if __name__ == "__main__":
    unittest.main()
