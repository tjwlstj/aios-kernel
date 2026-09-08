"""Process-boundary regressions for the hosted startup smoke runner.

No child processes or real Linux sources are used. The collector/verifier have
their own tests; these cases keep execution failures from inheriting a PASS.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools/hosted"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("hosted_boot_smoke_runner", TOOLS / "boot_smoke.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class HostedBootRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aios-runner-test-")
        self.addCleanup(self.temporary.cleanup)
        self.artifacts = Path(self.temporary.name) / "smoke"
        self.stdout = b"[AIOS-BOOT] process-boundary fixture\n"

    def run_main(self, *, process=None, exception=None, verifier_outcome="PASS",
                 produce_boot_log=True):
        def fake_process(command, **kwargs):
            self.assertTrue(kwargs["capture_output"])
            self.assertGreater(kwargs["timeout"], 0)
            self.assertEqual(Path(command[1]), ROOT / "hosted/linux/aios-boot.py")
            self.assertEqual(Path(command[-1]), self.artifacts / "run")
            if produce_boot_log:
                (self.artifacts / "run").mkdir()
                (self.artifacts / "run/boot.log").write_bytes(self.stdout)
            if exception is not None:
                raise exception
            return process or subprocess.CompletedProcess(command, 0, self.stdout, b"")

        with mock.patch.object(sys, "argv", ["boot_smoke.py", "--artifact-dir", str(self.artifacts)]), \
                mock.patch.object(runner.subprocess, "run", side_effect=fake_process) as launch, \
                mock.patch.object(runner, "verify_bundle", return_value={"outcome": verifier_outcome, "reasons": []}) as verify, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = runner.main()
        return code, launch, verify

    def evidence(self):
        return (
            json.loads((self.artifacts / "process.json").read_bytes()),
            json.loads((self.artifacts / "verdict.json").read_bytes()),
        )

    def test_clean_exit_requires_matching_stdout_and_live_verifier(self):
        code, launch, verify = self.run_main()
        self.assertEqual(code, 0)
        launch.assert_called_once()
        verify.assert_called_once_with(self.artifacts / "run", require_live=True, process_exit=0)
        process, verdict = self.evidence()
        self.assertEqual(process["outcome"], "exited")
        self.assertEqual(process["exit_code"], 0)
        self.assertEqual(process["stdout_sha256"], hashlib.sha256(self.stdout).hexdigest())
        self.assertEqual(process["stderr_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(verdict["outcome"], "PASS")

    def test_nonzero_process_cannot_inherit_a_bundle_pass(self):
        for exit_code in (1, 2, 3, 4, -9):
            with self.subTest(exit_code=exit_code):
                self.artifacts = Path(self.temporary.name) / ("exit-" + str(exit_code))
                process = subprocess.CompletedProcess([], exit_code, self.stdout, b"")
                code, _, _ = self.run_main(process=process)
                recorded, verdict = self.evidence()
                self.assertEqual(code, 1)
                self.assertEqual(recorded["exit_code"], exit_code)
                self.assertEqual(verdict["outcome"], "FAIL")

    def test_timeout_after_pass_output_remains_a_process_failure(self):
        timeout = subprocess.TimeoutExpired([], 30, output=self.stdout, stderr=b"timeout diagnostic\n")
        code, _, verify = self.run_main(exception=timeout)
        process, verdict = self.evidence()
        self.assertEqual(code, 1)
        self.assertEqual(process["outcome"], "timeout")
        self.assertIsNone(process["exit_code"])
        self.assertEqual((self.artifacts / "stdout.log").read_bytes(), self.stdout)
        self.assertEqual((self.artifacts / "stderr.log").read_bytes(), b"timeout diagnostic\n")
        self.assertEqual(verdict["outcome"], "FAIL")
        verify.assert_called_once_with(self.artifacts / "run", require_live=True, process_exit=None)

    def test_stderr_after_pass_output_is_not_clean_execution(self):
        process = subprocess.CompletedProcess([], 0, self.stdout, b"unexpected failure\n")
        code, _, _ = self.run_main(process=process)
        self.assertEqual(code, 1)
        self.assertEqual(self.evidence()[1]["outcome"], "FAIL")

    def test_stdout_mismatch_cannot_inherit_a_bundle_pass(self):
        process = subprocess.CompletedProcess([], 0, self.stdout + b"late contradiction\n", b"")
        code, _, _ = self.run_main(process=process)
        self.assertEqual(code, 1)
        self.assertEqual(self.evidence()[1]["outcome"], "FAIL")

    def test_clean_process_cannot_override_independent_verifier_failure(self):
        code, _, _ = self.run_main(verifier_outcome="FAIL")
        self.assertEqual(code, 1)
        self.assertEqual(self.evidence()[1]["outcome"], "FAIL")

    def test_existing_artifacts_are_refused_without_launch_or_overwrite(self):
        self.artifacts.mkdir()
        old_verdict = self.artifacts / "verdict.json"
        old_verdict.write_bytes(b"previous-run-content")
        code, launch, verify = self.run_main()
        self.assertEqual(code, 1)
        launch.assert_not_called()
        verify.assert_not_called()
        self.assertEqual(old_verdict.read_bytes(), b"previous-run-content")

    def test_launch_error_leaves_current_run_failure_evidence(self):
        code, _, _ = self.run_main(exception=OSError("fixture launch failure"), produce_boot_log=False)
        process, verdict = self.evidence()
        self.assertEqual(code, 1)
        self.assertIsNone(process["exit_code"])
        self.assertNotEqual(process["outcome"], "exited")
        self.assertEqual(verdict["outcome"], "FAIL")

    def test_clean_exit_without_bundle_leaves_current_run_failure_evidence(self):
        code, _, _ = self.run_main(verifier_outcome="FAIL", produce_boot_log=False)
        process, verdict = self.evidence()
        self.assertEqual(code, 1)
        self.assertEqual(process["exit_code"], 0)
        self.assertEqual(verdict["outcome"], "FAIL")


if __name__ == "__main__":
    unittest.main()
