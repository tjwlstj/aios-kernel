from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = REPO_ROOT / "tools/platform/linux_resource_guard.py"
MANIFEST_PATH = REPO_ROOT / "tools/platform/resources/linux_substrate_resources.json"
CANONICAL_DOC_PATH = REPO_ROOT / "docs/os/linux_hosted_substrate_and_resource_policy_ko.md"
PLATFORM_README_PATH = REPO_ROOT / "tools/platform/README.md"
SPEC = importlib.util.spec_from_file_location("linux_resource_guard", GUARD_PATH)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class LinuxResourceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = guard.load_manifest(MANIFEST_PATH)

    def validate(self, manifest=None, repo_root=REPO_ROOT):
        candidate = copy.deepcopy(self.manifest) if manifest is None else manifest
        return guard.validate_manifest(candidate, repo_root)

    def resource(self, manifest, resource_id):
        return next(item for item in manifest["resources"] if item["id"] == resource_id)

    def test_canonical_manifest_passes_with_expected_summary(self):
        errors, summary = self.validate()
        self.assertEqual([], errors)
        self.assertTrue(summary["passed"])
        self.assertEqual(13, summary["resource_count"])
        self.assertEqual(
            {
                "blocked_import": 1,
                "host_only": 3,
                "import_candidate": 0,
                "interface_only": 5,
                "reference_only": 4,
            },
            summary["dispositions"],
        )

    def test_duplicate_resource_id_fails(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["resources"].append(copy.deepcopy(manifest["resources"][0]))
        errors, _ = self.validate(manifest)
        self.assertTrue(any("duplicate resource ids" in error for error in errors))

    def test_missing_required_resource_fails(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["resources"] = [
            item for item in manifest["resources"] if item["id"] != "linux-kvm-uapi"
        ]
        errors, _ = self.validate(manifest)
        self.assertIn("required resource missing: linux-kvm-uapi", errors)

    def test_unknown_top_level_field_fails(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["approval"] = True
        errors, _ = self.validate(manifest)
        self.assertIn("manifest has unknown fields: approval", errors)

    def test_schema_version_type_confusion_fails(self):
        for value in (True, 1.0, "1"):
            with self.subTest(value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest["schema_version"] = value
                errors, _ = self.validate(manifest)
                self.assertIn("schema_version must be the integer 1", errors)

    def test_empty_manifest_does_not_fall_back_to_canonical(self):
        errors, summary = self.validate({})
        self.assertTrue(errors)
        self.assertFalse(summary["passed"])

    def test_unknown_resource_field_fails(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["resources"][0]["downloaded"] = False
        errors, _ = self.validate(manifest)
        self.assertTrue(any("has unknown fields: downloaded" in error for error in errors))

    def test_floating_and_release_candidate_versions_fail(self):
        for value in ("latest", "main", "11.1.0-rc3"):
            with self.subTest(value=value):
                manifest = copy.deepcopy(self.manifest)
                resource = self.resource(manifest, "qemu-system-linux-host")
                resource["version"] = value
                resource["reference_pin"] = value
                errors, _ = self.validate(manifest)
                self.assertTrue(any("floating or a release candidate" in error for error in errors))

    def test_valid_but_unapproved_primary_baseline_fails(self):
        manifest = copy.deepcopy(self.manifest)
        resource = self.resource(manifest, "linux-host-primary")
        resource["version"] = "6.13.y"
        resource["reference_pin"] = "6.13.1"
        errors, _ = self.validate(manifest)
        self.assertTrue(any("linux-host-primary.version must equal 6.12.y" in error for error in errors))

    def test_required_resource_official_url_drift_fails(self):
        manifest = copy.deepcopy(self.manifest)
        self.resource(manifest, "qemu-system-linux-host")["source_url"] = (
            "https://www.qemu.org/docs/master/about/license.html"
        )
        errors, _ = self.validate(manifest)
        self.assertTrue(any("qemu-system-linux-host.source_url must equal" in error for error in errors))

    def test_required_resource_license_drift_fails(self):
        manifest = copy.deepcopy(self.manifest)
        self.resource(manifest, "qemu-system-linux-host")["license"] = "MIT"
        errors, _ = self.validate(manifest)
        self.assertTrue(any("qemu-system-linux-host.license must equal" in error for error in errors))

    def test_descriptive_role_and_update_policy_drift_fail(self):
        manifest = copy.deepcopy(self.manifest)
        resource = self.resource(manifest, "linux-host-primary")
        resource["aios_role"] = "Production Linux scheduler actuator with canonical PID identity."
        resource["update_policy"] = "Never update."
        errors, _ = self.validate(manifest)
        self.assertTrue(any("linux-host-primary full-row contract digest mismatch" in error for error in errors))

    def test_block_reason_drift_fails(self):
        manifest = copy.deepcopy(self.manifest)
        self.resource(manifest, "linux-kernel-internal-source")["block_reason"] = "Still blocked."
        errors, _ = self.validate(manifest)
        self.assertTrue(any("linux-kernel-internal-source full-row contract digest mismatch" in error for error in errors))

    def test_review_date_drift_and_noncanonical_iso_forms_fail(self):
        for reviewed_on in ("1970-01-01", "20260811", "2026-W33-2"):
            with self.subTest(reviewed_on=reviewed_on):
                manifest = copy.deepcopy(self.manifest)
                manifest["reviewed_on"] = reviewed_on
                errors, _ = self.validate(manifest)
                self.assertTrue(any("reviewed_on" in error for error in errors))

    def test_non_https_and_unapproved_source_hosts_fail(self):
        for url, expected in (
            ("http://www.kernel.org/category/releases.html", "unauthenticated HTTPS"),
            ("https://example.com/linux", "not in the official allowlist"),
        ):
            with self.subTest(url=url):
                manifest = copy.deepcopy(self.manifest)
                manifest["resources"][0]["source_url"] = url
                errors, _ = self.validate(manifest)
                self.assertTrue(any(expected in error for error in errors))

    def test_code_import_is_always_false_in_schema_v1(self):
        manifest = copy.deepcopy(self.manifest)
        self.resource(manifest, "linux-kernel-internal-source")["code_import"] = True
        errors, _ = self.validate(manifest)
        self.assertTrue(any("code_import must be false" in error for error in errors))

    def test_blocked_import_requires_reason(self):
        manifest = copy.deepcopy(self.manifest)
        self.resource(manifest, "linux-kernel-internal-source")["block_reason"] = ""
        errors, _ = self.validate(manifest)
        self.assertTrue(any("block_reason is required" in error for error in errors))

    def test_host_observation_identity_must_remain_source_only(self):
        manifest = copy.deepcopy(self.manifest)
        self.resource(manifest, "linux-cgroup-v2-source")["identity_semantics"] = "none"
        errors, _ = self.validate(manifest)
        self.assertTrue(any("host observation identity must remain source_only" in error for error in errors))

    def test_extra_import_candidate_is_forbidden_even_with_root_license(self):
        manifest = copy.deepcopy(self.manifest)
        candidate = copy.deepcopy(self.resource(manifest, "unikraft-design-reference"))
        candidate.update(
            id="permissive-library-candidate",
            disposition="import_candidate",
            maturity="PLANNED",
        )
        manifest["resources"].append(candidate)
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            canonical = repo_root / manifest["canonical_document"]
            canonical.parent.mkdir(parents=True)
            canonical.write_text("policy\n", encoding="utf-8")
            (repo_root / "LICENSE").write_text("test license\n", encoding="utf-8")
            errors, _ = self.validate(manifest, repo_root)
        self.assertTrue(any("import_candidate is forbidden" in error for error in errors))
        self.assertTrue(any("unexpected resource ids" in error for error in errors))

    def test_valid_extra_reference_row_fails(self):
        manifest = copy.deepcopy(self.manifest)
        extra = copy.deepcopy(self.resource(manifest, "unikraft-design-reference"))
        extra["id"] = "another-reference"
        manifest["resources"].append(extra)
        errors, _ = self.validate(manifest)
        self.assertIn("unexpected resource ids: another-reference", errors)

    def test_canonical_document_path_drift_fails(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["canonical_document"] = "README.md"
        errors, _ = self.validate(manifest)
        self.assertTrue(any("canonical_document must equal" in error for error in errors))

    def test_future_review_snapshot_fails(self):
        manifest = copy.deepcopy(self.manifest)
        resource = self.resource(manifest, "unikraft-design-reference")
        resource["version"] = "9999-12-31"
        resource["reference_pin"] = "9999-12-31"
        errors, _ = self.validate(manifest)
        self.assertTrue(any("review_snapshot must not be in the future" in error for error in errors))

    def test_malformed_json_fails_to_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text('{"schema_version":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                guard.load_manifest(path)

    def test_duplicate_json_key_fails_to_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key: schema_version"):
                guard.load_manifest(path)

    def test_json_round_trip_keeps_canonical_contract(self):
        round_trip = json.loads(json.dumps(self.manifest))
        errors, summary = self.validate(round_trip)
        self.assertEqual([], errors)
        self.assertTrue(summary["passed"])

    def test_document_and_tool_readme_mirror_terminal_contract(self):
        errors, summary = self.validate()
        self.assertEqual([], errors)
        verdict = guard.format_pass(summary)
        canonical_doc = CANONICAL_DOC_PATH.read_text(encoding="utf-8")
        platform_readme = PLATFORM_README_PATH.read_text(encoding="utf-8")
        self.assertIn(verdict, canonical_doc)
        self.assertIn(verdict, platform_readme)
        for stale_text in (
            "resources=12",
            "reference_only=3",
            "VirtIO 1.3",
            "1.3 CSD",
        ):
            self.assertNotIn(stale_text, canonical_doc)
            self.assertNotIn(stale_text, platform_readme)
        for resource_id in guard.REQUIRED_RESOURCES:
            self.assertIn(f"`{resource_id}`", canonical_doc)


if __name__ == "__main__":
    unittest.main()
