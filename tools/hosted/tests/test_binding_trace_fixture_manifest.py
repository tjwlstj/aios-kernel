from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
HOSTED_TOOLS = REPO_ROOT / "tools" / "hosted"
CONTRACT_PATH = REPO_ROOT / "hosted" / "contracts" / "binding-trace-v1.contract.json"
FIXTURE_ROOT = REPO_ROOT / "hosted" / "contracts" / "fixtures"

sys.path.insert(0, str(HOSTED_TOOLS))
import binding_trace_replay as replay  # noqa: E402

CONTRACT = replay.load_contract(CONTRACT_PATH)


class FixtureManifestTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixtures = self.root / "fixtures"
        shutil.copytree(FIXTURE_ROOT, self.fixtures)
        self.manifest_path = self.fixtures / "manifest.json"

    def read_manifest(self):
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write_manifest(self, manifest):
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def run_bundle(self, name="bundle", runner_os=None, platform_system=None):
        artifact_dir = self.root / name
        environment = {} if runner_os is None else {"RUNNER_OS": runner_os}
        git = replay._git_metadata()
        if runner_os is not None:
            environment["GITHUB_SHA"] = git["head_sha"]
        clean_git = {
            "head_sha": git["head_sha"],
            "dirty": False,
            "github_sha": git["head_sha"],
        }
        system = platform_system or runner_os
        system_patch = (
            mock.patch.object(replay.platform, "system", return_value=system)
            if system is not None
            else contextlib.nullcontext()
        )
        git_patch = (
            mock.patch.object(replay, "_git_metadata", return_value=clean_git)
            if runner_os is not None
            else contextlib.nullcontext()
        )
        with mock.patch.dict("os.environ", environment, clear=False), system_patch, git_patch:
            aggregate, provenance = replay.run_fixture_manifest(
                self.manifest_path,
                CONTRACT_PATH,
                CONTRACT,
                artifact_dir,
            )
        return artifact_dir, aggregate, provenance

    def call_main(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = replay.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def rewrite_bundle_json_member(self, bundle, relative, mutate):
        member = bundle / Path(*relative.split("/"))
        payload = json.loads(member.read_text(encoding="utf-8"))
        mutate(payload)
        raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        member.write_bytes(raw)

        provenance_path = bundle / "provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in [*provenance["inputs"], *provenance["outputs"]]
            if item["path"] == relative
        )
        entry["bytes"] = len(raw)
        entry["sha256"] = hashlib.sha256(raw).hexdigest()
        provenance_path.write_text(
            json.dumps(provenance, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )


class ManifestValidationTests(FixtureManifestTestCase):
    def test_checked_in_manifest_is_exact_and_complete(self):
        manifest, raw = replay.load_fixture_manifest(self.manifest_path, CONTRACT)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual("aios-binding-trace-v1-fixtures", manifest["manifest_id"])
        self.assertEqual(12, len(manifest["fixtures"]))
        self.assertEqual(
            CONTRACT["fixture_manifest_v1"]["top_level_fields_in_order"],
            list(manifest),
        )
        for fixture in manifest["fixtures"]:
            self.assertEqual(
                CONTRACT["fixture_manifest_v1"]["fixture_fields_in_order"],
                list(fixture),
            )

    def test_duplicate_manifest_key_is_rejected(self):
        raw = self.manifest_path.read_text(encoding="utf-8")
        raw = raw.replace(
            '"schema_version": 1,',
            '"schema_version": 1,\n  "schema_version": 1,',
            1,
        )
        self.manifest_path.write_text(raw, encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

    def test_missing_and_unknown_top_level_fields_are_rejected(self):
        manifest = self.read_manifest()
        del manifest["contract_id"]
        manifest["extra"] = 1
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, "exact fields violated"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

    def test_manifest_schema_version_rejects_bool_and_float_type_drift(self):
        for value in (True, 1.0):
            with self.subTest(value=value):
                manifest = self.read_manifest()
                manifest["schema_version"] = value
                self.write_manifest(manifest)
                with self.assertRaisesRegex(ValueError, "schema_version must equal 1"):
                    replay.load_fixture_manifest(self.manifest_path, CONTRACT)
                shutil.copyfile(FIXTURE_ROOT / "manifest.json", self.manifest_path)

    def test_duplicate_fixture_id_and_path_are_rejected(self):
        manifest = self.read_manifest()
        manifest["fixtures"].append(copy.deepcopy(manifest["fixtures"][0]))
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, "duplicate fixture id"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

        manifest = self.read_manifest()
        manifest["fixtures"][-1]["id"] = "different-id"
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, "duplicate fixture trace path"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

    def test_path_escape_absolute_backslash_and_bad_suffix_are_rejected(self):
        cases = (
            "../outside.jsonl",
            "/absolute.jsonl",
            "C:/absolute.jsonl",
            "valid\\full-lifecycle.jsonl",
            "valid/full-lifecycle.txt",
        )
        for trace in cases:
            with self.subTest(trace=trace):
                manifest = self.read_manifest()
                manifest["fixtures"][0]["trace"] = trace
                self.write_manifest(manifest)
                with self.assertRaises(ValueError):
                    replay.load_fixture_manifest(self.manifest_path, CONTRACT)
                shutil.copyfile(FIXTURE_ROOT / "manifest.json", self.manifest_path)

    def test_missing_fixture_file_is_infrastructure_error(self):
        manifest = self.read_manifest()
        manifest["fixtures"][0]["trace"] = "valid/missing.jsonl"
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, "missing or not a file"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

    def test_pass_and_fail_expectation_shapes_are_exact(self):
        manifest = self.read_manifest()
        manifest["fixtures"][0]["expected_first_reason"] = "stale"
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, "PASS fixture"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

        manifest = self.read_manifest()
        manifest["fixtures"][0]["expected_outcome"] = "FAIL"
        manifest["fixtures"][0]["expected_first_reason"] = "unregistered"
        self.write_manifest(manifest)
        with self.assertRaisesRegex(ValueError, "registered expected_first_reason"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

    def test_native_projection_label_requires_the_exact_boot_source_tuple(self):
        native = self.fixtures / "valid/native-k2a-observation.jsonl"
        raw = native.read_text(encoding="utf-8").replace(
            '"source_instance":"1"',
            '"source_instance":"11"',
        )
        native.write_text(raw, encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(ValueError, "native-k2a-projection.*instance"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)

    def test_native_projection_label_requires_timestamp_unsupported_tuple(self):
        native = self.fixtures / "valid/native-k2a-observation.jsonl"
        raw = native.read_text(encoding="utf-8")
        raw = raw.replace('"observed_at_ns":"0"', '"observed_at_ns":"1"')
        raw = raw.replace('"observed_at_valid":0', '"observed_at_valid":1')
        native.write_text(raw, encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(ValueError, "observed_at_ns must equal '0'"):
            replay.load_fixture_manifest(self.manifest_path, CONTRACT)


class FixtureRunnerTests(FixtureManifestTestCase):
    def test_deep_manifest_emits_infrastructure_failure_artifact(self):
        self.manifest_path.write_bytes(b"[" * 1200 + b"]" * 1200 + b"\n")
        artifact_dir = self.root / "deep-manifest"
        code, _, stderr = self.call_main(
            ["--fixture-manifest", str(self.manifest_path),
             "--artifact-dir", str(artifact_dir), "--json"]
        )
        self.assertEqual(2, code, stderr)
        self.assertIn("trace.io", stderr)
        self.assertNotIn("Traceback", stderr)
        aggregate = json.loads((artifact_dir / "aggregate-verdict.json").read_text("utf-8"))
        self.assertFalse(aggregate["passed"])
        self.assertEqual("trace.io", aggregate["first_failure"]["reason"])
        self.assertIn("JSON nesting exceeds decoder limit", aggregate["first_failure"]["detail"])

    def test_surrogate_negative_fixture_preserves_utf8_verdict_artifacts(self):
        manifest = self.read_manifest()
        fixture = next(case for case in manifest["fixtures"] if case["id"] == "invalid-orphan-parent")
        fixture["expected_first_reason"] = "trace.type"
        self.write_manifest(manifest)
        trace_path = self.fixtures / fixture["trace"]
        original = [json.loads(line) for line in trace_path.read_text("utf-8").splitlines()]
        for index, field in enumerate(("trace_id", "accepted_count")):
            with self.subTest(field=field):
                records = copy.deepcopy(original)
                record = records[0] if field == "trace_id" else records[-1]
                record[field] = "\ud800"
                trace_path.write_bytes(
                    ("\n".join(json.dumps(record) for record in records) + "\n").encode("utf-8")
                )
                bundle, aggregate, provenance = self.run_bundle(f"surrogate-{index}")
                self.assertTrue(aggregate["passed"], aggregate)
                verdict_path = bundle / "verdicts" / f"{fixture['id']}.json"
                verdict = json.loads(verdict_path.read_text("utf-8", errors="strict"))
                self.assertFalse(verdict["passed"])
                self.assertEqual("trace.type", verdict["first_failure"]["reason"])
                if field == "trace_id":
                    self.assertIsNone(verdict["trace_id"])
                for entry in provenance["outputs"]:
                    raw = (bundle / Path(*entry["path"].split("/"))).read_bytes()
                    raw.decode("utf-8", errors="strict")
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])

    def test_expected_negative_traces_match_without_becoming_trace_passes(self):
        artifact_dir, aggregate, _ = self.run_bundle()
        self.assertTrue(aggregate["passed"], aggregate)
        self.assertEqual(12, aggregate["fixture_count"])
        negative = next(
            item for item in aggregate["fixtures"] if item["id"] == "invalid-orphan-parent"
        )
        self.assertEqual("FAIL", negative["actual_outcome"])
        self.assertEqual("orphan", negative["actual_first_reason"])
        self.assertTrue(negative["matched"])
        verdict = json.loads(
            (artifact_dir / negative["verdict_artifact"]).read_text(encoding="utf-8")
        )
        self.assertFalse(verdict["passed"])
        self.assertEqual("orphan", verdict["first_failure"]["reason"])

    def test_wrong_expected_reason_produces_fixture_mismatch_and_exit_one(self):
        manifest = self.read_manifest()
        target = next(
            item for item in manifest["fixtures"] if item["id"] == "invalid-orphan-parent"
        )
        target["expected_first_reason"] = "kind"
        self.write_manifest(manifest)

        artifact_dir = self.root / "wrong-expectation"
        code, stdout, stderr = self.call_main(
            [
                "--fixture-manifest",
                str(self.manifest_path),
                "--artifact-dir",
                str(artifact_dir),
                "--json",
            ]
        )
        self.assertEqual(1, code, stderr)
        aggregate = json.loads(stdout)
        self.assertFalse(aggregate["passed"])
        self.assertEqual("trace.fixture-mismatch", aggregate["first_failure"]["reason"])
        self.assertEqual("invalid-orphan-parent", aggregate["first_failure"]["fixture_id"])

    def test_bundle_is_self_contained_and_hashes_every_declared_member(self):
        artifact_dir, aggregate, provenance = self.run_bundle()
        self.assertTrue((artifact_dir / "inputs/binding-trace-v1.contract.json").is_file())
        self.assertTrue((artifact_dir / "inputs/fixtures/manifest.json").is_file())
        self.assertTrue((artifact_dir / "aggregate-verdict.json").is_file())
        self.assertTrue((artifact_dir / "provenance.json").is_file())
        self.assertEqual(14, len(provenance["inputs"]))
        self.assertEqual(13, len(provenance["outputs"]))

        for entry in [*provenance["inputs"], *provenance["outputs"]]:
            member = artifact_dir / Path(*entry["path"].split("/"))
            raw = member.read_bytes()
            self.assertEqual(len(raw), entry["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])

        persisted = json.loads((artifact_dir / "aggregate-verdict.json").read_text("utf-8"))
        self.assertEqual(aggregate, persisted)
        self.assertNotIn(str(self.root), json.dumps(aggregate))

    def test_existing_artifact_directory_is_refused_without_touching_sentinel(self):
        artifact_dir = self.root / "existing"
        artifact_dir.mkdir()
        sentinel = artifact_dir / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "already exists"):
            replay.run_fixture_manifest(
                self.manifest_path, CONTRACT_PATH, CONTRACT, artifact_dir
            )
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([sentinel], list(artifact_dir.iterdir()))

    def test_manifest_cli_pass_and_usage_exit_contract(self):
        artifact_dir = self.root / "cli-pass"
        code, stdout, stderr = self.call_main(
            [
                "--fixture-manifest",
                str(self.manifest_path),
                "--artifact-dir",
                str(artifact_dir),
                "--json",
            ]
        )
        self.assertEqual(0, code, stderr)
        self.assertTrue(json.loads(stdout)["passed"])

        code, _, _ = self.call_main([])
        self.assertEqual(2, code)
        code, _, _ = self.call_main(
            [str(self.fixtures / "valid/full-lifecycle.jsonl"), "--fixture-manifest", str(self.manifest_path)]
        )
        self.assertEqual(2, code)
        code, _, _ = self.call_main(["--fixture-manifest", str(self.manifest_path)])
        self.assertEqual(2, code)

    def test_manifest_infrastructure_failure_publishes_failed_current_run_artifact(self):
        manifest = self.read_manifest()
        manifest["fixtures"][0]["trace"] = "../escape.jsonl"
        self.write_manifest(manifest)
        artifact_dir = self.root / "infra-failure"
        code, _, stderr = self.call_main(
            [
                "--fixture-manifest",
                str(self.manifest_path),
                "--artifact-dir",
                str(artifact_dir),
                "--json",
            ]
        )
        self.assertEqual(2, code, stderr)
        aggregate = json.loads((artifact_dir / "aggregate-verdict.json").read_text("utf-8"))
        self.assertFalse(aggregate["passed"])
        self.assertEqual("trace.io", aggregate["first_failure"]["reason"])


class BundleParityTests(FixtureManifestTestCase):
    def test_surrogate_key_in_bundle_emits_utf8_human_failure(self):
        bundle = self.root / "surrogate-bundle"
        bundle.mkdir()
        (bundle / "aggregate-verdict.json").write_bytes(
            b'{"bad\\ud800key":1,"bad\\ud800key":2}\n'
        )
        artifact_dir = self.root / "surrogate-parity"
        code, stdout, stderr = self.call_main(
            ["--compare-fixture-bundles", str(bundle), str(bundle),
             "--artifact-dir", str(artifact_dir)]
        )
        self.assertEqual(2, code, stderr)
        stdout.encode("utf-8", errors="strict")
        self.assertIn("[BINDING-TRACE-PARITY] FAIL first_reason=trace.io", stdout)
        self.assertIn(r"bad\ud800key", stdout)
        persisted = json.loads((artifact_dir / "parity-verdict.json").read_text("utf-8"))
        self.assertFalse(persisted["passed"])
        self.assertEqual("trace.io", persisted["first_failure"]["reason"])

    def test_deep_bundle_json_emits_infrastructure_failure_artifact(self):
        for index, member in enumerate(("aggregate-verdict.json", "provenance.json")):
            with self.subTest(member=member):
                bundle = self.root / f"deep-bundle-{index}"
                bundle.mkdir()
                (bundle / "aggregate-verdict.json").write_text("{}\n", encoding="utf-8")
                (bundle / member).write_bytes(b"[" * 1200 + b"]" * 1200 + b"\n")
                artifact_dir = self.root / f"deep-parity-{index}"
                code, stdout, stderr = self.call_main(
                    ["--compare-fixture-bundles", str(bundle), str(bundle),
                     "--artifact-dir", str(artifact_dir), "--json"]
                )
                self.assertEqual(2, code, stderr)
                verdict = json.loads(stdout)
                self.assertFalse(verdict["passed"])
                self.assertEqual("trace.io", verdict["first_failure"]["reason"])
                self.assertIn("JSON nesting exceeds decoder limit", verdict["first_failure"]["detail"])
                self.assertEqual(
                    verdict,
                    json.loads((artifact_dir / "parity-verdict.json").read_text("utf-8")),
                )
                self.assertNotIn("Traceback", stderr)

    def test_equivalent_bundles_pass_and_python_patch_is_advisory(self):
        left, left_aggregate, _ = self.run_bundle("left", "Linux")
        right, right_aggregate, _ = self.run_bundle("right", "Windows")
        self.assertEqual(left_aggregate, right_aggregate)

        provenance_path = right / "provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["python"]["version"][2] += 1
        provenance["python"]["full_version"] = "3.11.advisory-difference"
        provenance_path.write_text(json.dumps(provenance) + "\n", encoding="utf-8")

        parity = replay.compare_fixture_bundles(left, right)
        self.assertTrue(parity["passed"], parity)
        self.assertEqual(12, parity["compared_fixture_count"])

    def test_input_or_expected_verdict_difference_fails_parity(self):
        left, _, _ = self.run_bundle("left", "Linux")
        manifest = self.read_manifest()
        target = next(
            item for item in manifest["fixtures"] if item["id"] == "invalid-orphan-parent"
        )
        target["expected_first_reason"] = "kind"
        self.write_manifest(manifest)
        right, aggregate, _ = self.run_bundle("right", "Windows")
        self.assertFalse(aggregate["passed"])

        with self.assertRaisesRegex(ValueError, "canonical input"):
            replay.compare_fixture_bundles(left, right)

    def test_tampered_bundle_member_fails_integrity_check(self):
        left, _, _ = self.run_bundle("left", "Linux")
        right, _, _ = self.run_bundle("right", "Windows")
        target = right / "verdicts/valid-full-lifecycle.json"
        target.write_bytes(target.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "byte count mismatch"):
            replay.compare_fixture_bundles(left, right)

    def test_rehashed_output_type_drift_cannot_pass_independent_replay(self):
        cases = (
            (
                "verdicts/valid-full-lifecycle.json",
                lambda payload: payload.__setitem__("schema_version", True),
                "fixture verdict was not reproduced",
            ),
            (
                "aggregate-verdict.json",
                lambda payload: payload.__setitem__("fixture_count", 12.0),
                "aggregate verdict does not match",
            ),
        )
        for index, (relative, mutate, expected) in enumerate(cases):
            with self.subTest(relative=relative):
                left, _, _ = self.run_bundle(f"left-type-{index}", "Linux")
                right, _, _ = self.run_bundle(f"right-type-{index}", "Windows")
                self.rewrite_bundle_json_member(right, relative, mutate)
                with self.assertRaisesRegex(ValueError, expected):
                    replay.compare_fixture_bundles(left, right)

    def test_provenance_schema_version_rejects_bool_and_float_type_drift(self):
        for index, value in enumerate((True, 1.0)):
            with self.subTest(value=value):
                left, _, _ = self.run_bundle(f"left-provenance-{index}", "Linux")
                right, _, _ = self.run_bundle(f"right-provenance-{index}", "Windows")
                provenance_path = right / "provenance.json"
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                provenance["bundle_schema_version"] = value
                provenance_path.write_text(
                    json.dumps(provenance) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                with self.assertRaisesRegex(ValueError, "schema or kind is invalid"):
                    replay.compare_fixture_bundles(left, right)

    def test_different_git_head_fails_parity(self):
        left, _, _ = self.run_bundle("left", "Linux")
        right, _, _ = self.run_bundle("right", "Windows")
        provenance_path = right / "provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["git"]["head_sha"] = "0" * 40
        provenance_path.write_text(json.dumps(provenance) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "github_sha must equal"):
            replay.compare_fixture_bundles(left, right)

    def test_same_or_reversed_runner_os_bundles_fail_parity(self):
        cases = (
            ("Linux", "Linux", "right runner_os is not Windows"),
            ("Windows", "Windows", "left runner_os is not Linux"),
            ("Windows", "Linux", "left runner_os is not Linux"),
        )
        for index, (left_os, right_os, expected_detail) in enumerate(cases):
            with self.subTest(left_os=left_os, right_os=right_os):
                left, _, _ = self.run_bundle(f"left-{index}", left_os)
                right, _, _ = self.run_bundle(f"right-{index}", right_os)
                parity = replay.compare_fixture_bundles(left, right)
                self.assertFalse(parity["passed"])
                self.assertEqual(
                    expected_detail,
                    parity["first_failure"]["detail"],
                )

    def test_runner_os_spoof_against_platform_system_is_rejected(self):
        left, _, _ = self.run_bundle("left-spoof", "Linux", "Windows")
        right, _, _ = self.run_bundle("right-genuine", "Windows", "Windows")
        with self.assertRaisesRegex(ValueError, "runner_os does not match platform.system"):
            replay.compare_fixture_bundles(left, right)

    def test_empty_forged_aggregate_and_member_lists_are_rejected(self):
        bundle = self.root / "forged-empty"
        bundle.mkdir()
        head = replay._git_metadata()["head_sha"]
        verifier_raw = (REPO_ROOT / replay.VERIFIER_RELATIVE_PATH).read_bytes()
        aggregate = {
            "kind": "fixture-manifest-verdict",
            "manifest_id": "aios-binding-trace-v1-fixtures",
            "contract_id": "aios-binding-trace-v1",
            "fixture_count": 12,
            "passed": True,
            "fixtures": [],
        }
        provenance = {
            "bundle_schema_version": 1,
            "kind": "fixture-manifest-provenance",
            "manifest_id": "aios-binding-trace-v1-fixtures",
            "contract_id": "aios-binding-trace-v1",
            "git": {"head_sha": head, "dirty": False, "github_sha": head},
            "python": {
                "implementation": "cpython",
                "version": [3, 11, 0],
                "full_version": "3.11.0",
            },
            "platform": {"system": "Linux", "release": "x", "machine": "x86_64"},
            "runner_os": "Linux",
            "generated_at_utc": "2026-08-31T00:00:00Z",
            "verifier": replay._artifact_entry(
                replay.VERIFIER_RELATIVE_PATH,
                verifier_raw,
            ),
            "inputs": [],
            "outputs": [],
        }
        (bundle / "aggregate-verdict.json").write_text(
            json.dumps(aggregate) + "\n", encoding="utf-8"
        )
        (bundle / "provenance.json").write_text(
            json.dumps(provenance) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "file set mismatch|unreadable contract|provenance inputs"):
            replay.compare_fixture_bundles(bundle, bundle)

    def test_missing_bundle_returns_exit_two_and_preserves_parity_failure(self):
        artifact_dir = self.root / "parity-failure"
        code, stdout, stderr = self.call_main(
            [
                "--compare-fixture-bundles",
                str(self.root / "missing-left"),
                str(self.root / "missing-right"),
                "--artifact-dir",
                str(artifact_dir),
                "--json",
            ]
        )
        self.assertEqual(2, code, stderr)
        verdict = json.loads(stdout)
        self.assertEqual("trace.io", verdict["first_failure"]["reason"])
        persisted = json.loads((artifact_dir / "parity-verdict.json").read_text("utf-8"))
        self.assertEqual(verdict, persisted)


class ContractInputTests(FixtureManifestTestCase):
    def test_deep_contract_exits_two_without_traceback(self):
        bad_contract = self.root / "deep-contract.json"
        bad_contract.write_bytes(b"[" * 1200 + b"]" * 1200 + b"\n")
        code, _, stderr = self.call_main(
            [str(self.fixtures / "valid/full-lifecycle.jsonl"),
             "--contract", str(bad_contract), "--json"]
        )
        self.assertEqual(2, code)
        self.assertIn("trace.io", stderr)
        self.assertIn("JSON nesting exceeds decoder limit", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_semantically_mutated_v1_contract_copy_is_rejected(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        contract["trusted_target_v1"]["canonical_id"] = "999"
        path = self.root / "mutated-v1-contract.json"
        path.write_text(json.dumps(contract) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "semantically identical"):
            replay.load_contract(path)

    def test_contract_numeric_fields_reject_bool_and_float_type_drift(self):
        mutations = (
            lambda contract: contract.__setitem__("schema_version", True),
            lambda contract: contract["constants"].__setitem__("observation_only", True),
            lambda contract: contract["scalar_types"]["u32"].__setitem__("min_value", 0.0),
            lambda contract: contract["string_mappings_v1"]["canonical_namespace"].__setitem__(
                "node", 2.0
            ),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
                mutate(contract)
                path = self.root / f"type-drift-{index}.json"
                path.write_text(json.dumps(contract) + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    replay.load_contract(path)

    def test_malformed_alternate_contract_exits_two_without_traceback(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        del contract["trusted_target_v1"]
        bad_contract = self.root / "bad-contract.json"
        bad_contract.write_text(json.dumps(contract) + "\n", encoding="utf-8")
        code, _, stderr = self.call_main(
            [
                str(self.fixtures / "valid/full-lifecycle.jsonl"),
                "--contract",
                str(bad_contract),
                "--json",
            ]
        )
        self.assertEqual(2, code)
        self.assertIn("trace.io", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_invalid_contract_regex_and_reason_registry_are_rejected(self):
        for mutation in ("regex", "reason", "mapping"):
            with self.subTest(mutation=mutation):
                contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
                if mutation == "regex":
                    contract["scalar_types"]["trace-id"]["pattern"] = "["
                else:
                    if mutation == "reason":
                        contract["trace_reasons_v1"].remove("trace.trace-id")
                    else:
                        del contract["string_mappings_v1"]["source_role"]
                path = self.root / f"bad-{mutation}.json"
                path.write_text(json.dumps(contract) + "\n", encoding="utf-8")
                with self.assertRaises((ValueError, re.error)) as caught:
                    replay.load_contract(path)
                self.assertIsNotNone(caught.exception)


class WorkflowContractTests(unittest.TestCase):
    """Guard the checked-in H1 job text, not general YAML or runner semantics."""

    def setUp(self):
        workflow = (REPO_ROOT / ".github/workflows/linux-boot-check.yml").read_text(
            encoding="utf-8"
        )
        jobs = list(
            re.finditer(
                r"^  hosted-binding-trace-parity:\n.*?(?=^  [a-z][a-z0-9-]*:|\Z)",
                workflow,
                re.MULTILINE | re.DOTALL,
            )
        )
        self.assertEqual(1, len(jobs), "require exactly one H1 parity job")
        self.job = jobs[0].group()

    def step(self, name):
        steps = list(
            re.finditer(
                rf"^      - name: {re.escape(name)}\n.*?(?=^      - name:|\Z)",
                self.job,
                re.MULTILINE | re.DOTALL,
            )
        )
        self.assertEqual(1, len(steps), f"require exactly one step: {name}")
        return steps[0].group()

    def test_artifact_download_failures_cannot_be_downgraded_to_success(self):
        # A failed digest check may leave extracted files that replay correctly.
        # Neither a step nor the containing job may erase that transport failure.
        self.assertNotRegex(self.job, r"(?m)^\s*continue-on-error:")
        for system in ("Linux", "Windows"):
            with self.subTest(system=system):
                step = self.step(f"Download {system} binding-trace bundle")
                self.assertIn("        uses: actions/download-artifact@v8\n", step)
                self.assertIn(f"          name: hosted-binding-trace-{system}\n", step)
                self.assertIn("          digest-mismatch: error\n", step)

    def test_parity_diagnostics_continue_after_download_failure(self):
        for name in (
            "Download Windows binding-trace bundle",
            "Compare Linux and Windows binding-trace bundles",
        ):
            with self.subTest(step=name):
                self.assertIn(
                    "        if: ${{ always() && !cancelled() }}\n", self.step(name)
                )
        upload = self.step("Upload binding-trace parity verdict")
        self.assertIn("        if: always()\n", upload)
        self.assertIn("          if-no-files-found: error\n", upload)


if __name__ == "__main__":
    unittest.main()
