#!/usr/bin/env python3
"""Fail-closed validator for the AIOS Linux substrate resource manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "tools/platform/resources/linux_substrate_resources.json"

TOP_LEVEL_FIELDS = {
    "schema_version",
    "policy_id",
    "reviewed_on",
    "canonical_document",
    "resource_policy_maturity",
    "hosted_backend_maturity",
    "resources",
}
RESOURCE_FIELDS = {
    "id",
    "kind",
    "disposition",
    "version_policy",
    "version",
    "reference_pin",
    "source_url",
    "license",
    "code_import",
    "boundary",
    "identity_semantics",
    "aios_role",
    "maturity",
    "update_policy",
    "provenance_required",
    "artifact_pin_required",
    "block_reason",
}

ALLOWED_KINDS = {
    "host_kernel",
    "hypervisor_uapi",
    "emulator",
    "virtual_device_spec",
    "host_observation_api",
    "reference_kernel",
    "reference_runtime",
    "kernel_internal_api",
}
ALLOWED_DISPOSITIONS = {
    "host_only",
    "interface_only",
    "reference_only",
    "import_candidate",
    "blocked_import",
}
ALLOWED_VERSION_POLICIES = {
    "lts_series",
    "exact_release",
    "api_contract",
    "spec_release",
    "review_snapshot",
}
ALLOWED_BOUNDARIES = {
    "linux_host",
    "guest_host_interface",
    "linux_userspace_source",
    "design_reference",
    "forbidden_internal",
}
ALLOWED_IDENTITIES = {"none", "source_only"}
ALLOWED_MATURITY = {"PLANNED", "RESEARCH"}
OFFICIAL_HOSTS = {
    "kernel.org",
    "www.kernel.org",
    "docs.kernel.org",
    "git.kernel.org",
    "qemu.org",
    "www.qemu.org",
    "qemu.readthedocs.io",
    "docs.oasis-open.org",
    "www.oasis-open.org",
    "docs.sel4.systems",
    "sel4.systems",
    "fuchsia.dev",
    "fuchsia.googlesource.com",
    "unikraft.org",
    "www.unikraft.org",
    "man7.org",
    "www.man7.org",
}

REQUIRED_RESOURCES = {
    "linux-host-primary": {
        "kind": "host_kernel",
        "disposition": "host_only",
        "version_policy": "lts_series",
        "version": "6.12.y",
        "reference_pin": "6.12.103",
        "source_url": "https://www.kernel.org/category/releases.html",
        "boundary": "linux_host",
        "identity_semantics": "none",
        "maturity": "PLANNED",
    },
    "linux-host-forward": {
        "kind": "host_kernel",
        "disposition": "host_only",
        "version_policy": "lts_series",
        "version": "6.18.y",
        "reference_pin": "6.18.44",
        "source_url": "https://www.kernel.org/category/releases.html",
        "boundary": "linux_host",
        "identity_semantics": "none",
        "maturity": "PLANNED",
    },
    "qemu-system-linux-host": {
        "kind": "emulator",
        "disposition": "host_only",
        "version_policy": "exact_release",
        "version": "11.1.0",
        "reference_pin": "11.1.0",
        "source_url": "https://www.qemu.org/download/",
        "boundary": "linux_host",
        "identity_semantics": "none",
        "maturity": "PLANNED",
    },
    "linux-kvm-uapi": {
        "kind": "hypervisor_uapi",
        "disposition": "interface_only",
        "version_policy": "api_contract",
        "version": "KVM_API_VERSION=12",
        "reference_pin": "KVM_API_VERSION=12",
        "source_url": "https://docs.kernel.org/6.12/virt/kvm/api.html",
        "boundary": "guest_host_interface",
        "identity_semantics": "none",
        "maturity": "PLANNED",
    },
    "virtio-1-2-contract": {
        "kind": "virtual_device_spec",
        "disposition": "interface_only",
        "version_policy": "spec_release",
        "version": "1.2-CS01",
        "reference_pin": "1.2-CS01",
        "source_url": "https://docs.oasis-open.org/virtio/virtio/v1.2/cs01/virtio-v1.2-cs01.html",
        "boundary": "guest_host_interface",
        "identity_semantics": "none",
        "maturity": "PLANNED",
    },
    "virtio-1-4-reference": {
        "kind": "virtual_device_spec",
        "disposition": "reference_only",
        "version_policy": "spec_release",
        "version": "1.4-CS01",
        "reference_pin": "1.4-CS01",
        "source_url": "https://docs.oasis-open.org/virtio/virtio/v1.4/cs01/virtio-v1.4-cs01.html",
        "boundary": "guest_host_interface",
        "identity_semantics": "none",
        "maturity": "RESEARCH",
    },
    "linux-cgroup-v2-source": {
        "kind": "host_observation_api",
        "disposition": "interface_only",
        "version_policy": "lts_series",
        "version": "6.12.y",
        "reference_pin": "6.12.103",
        "source_url": "https://docs.kernel.org/6.12/admin-guide/cgroup-v2.html",
        "boundary": "linux_userspace_source",
        "identity_semantics": "source_only",
        "maturity": "PLANNED",
    },
    "linux-pidfd-source": {
        "kind": "host_observation_api",
        "disposition": "interface_only",
        "version_policy": "review_snapshot",
        "version": "2026-08-11",
        "reference_pin": "2026-08-11",
        "source_url": "https://man7.org/linux/man-pages/man2/pidfd_open.2.html",
        "boundary": "linux_userspace_source",
        "identity_semantics": "source_only",
        "maturity": "PLANNED",
    },
    "linux-psi-source": {
        "kind": "host_observation_api",
        "disposition": "interface_only",
        "version_policy": "lts_series",
        "version": "6.12.y",
        "reference_pin": "6.12.103",
        "source_url": "https://docs.kernel.org/6.12/accounting/psi.html",
        "boundary": "linux_userspace_source",
        "identity_semantics": "source_only",
        "maturity": "PLANNED",
    },
    "linux-kernel-internal-source": {
        "kind": "kernel_internal_api",
        "disposition": "blocked_import",
        "version_policy": "lts_series",
        "version": "6.12.y",
        "reference_pin": "6.12.103",
        "source_url": "https://docs.kernel.org/6.12/process/stable-api-nonsense.html",
        "boundary": "forbidden_internal",
        "identity_semantics": "none",
        "maturity": "RESEARCH",
    },
    "sel4-design-reference": {
        "kind": "reference_kernel",
        "disposition": "reference_only",
        "version_policy": "review_snapshot",
        "version": "2026-08-11",
        "reference_pin": "2026-08-11",
        "source_url": "https://docs.sel4.systems/projects/sel4/index.html",
        "boundary": "design_reference",
        "identity_semantics": "none",
        "maturity": "RESEARCH",
    },
    "zircon-design-reference": {
        "kind": "reference_kernel",
        "disposition": "reference_only",
        "version_policy": "review_snapshot",
        "version": "2026-08-11",
        "reference_pin": "2026-08-11",
        "source_url": "https://fuchsia.dev/fuchsia-src/concepts/kernel/concepts",
        "boundary": "design_reference",
        "identity_semantics": "none",
        "maturity": "RESEARCH",
    },
    "unikraft-design-reference": {
        "kind": "reference_runtime",
        "disposition": "reference_only",
        "version_policy": "review_snapshot",
        "version": "2026-08-11",
        "reference_pin": "2026-08-11",
        "source_url": "https://unikraft.org/docs/internals/architecture",
        "boundary": "design_reference",
        "identity_semantics": "none",
        "maturity": "RESEARCH",
    },
}

REQUIRED_LICENSES = {
    "linux-host-primary": "GPL-2.0-only",
    "linux-host-forward": "GPL-2.0-only",
    "qemu-system-linux-host": "QEMU emulator GPL-2.0-only overall; compatible per-file licenses; bundled firmware separately licensed",
    "linux-kvm-uapi": "GPL-2.0-only WITH Linux-syscall-note for UAPI headers",
    "virtio-1-2-contract": "OASIS document notices and terms; VIRTIO TC Non-Assertion Mode for patent IPR",
    "virtio-1-4-reference": "OASIS document notices and terms; VIRTIO TC Non-Assertion Mode for patent IPR",
    "linux-cgroup-v2-source": "GPL-2.0-only documentation; userspace interface use only",
    "linux-pidfd-source": "Linux man-pages project documentation; userspace interface use only",
    "linux-psi-source": "GPL-2.0-only documentation; userspace interface use only",
    "linux-kernel-internal-source": "GPL-2.0-only kernel implementation",
    "sel4-design-reference": "GPL-2.0-only kernel; reference-only review",
    "zircon-design-reference": "Zircon kernel MIT-style; broader Fuchsia tree file licenses reviewed separately",
    "unikraft-design-reference": "BSD-3-Clause core with separately licensed libraries; reference-only review",
}

EXPECTED_RESOURCE_DIGESTS = {
    "linux-host-primary": "f94f036a5d0d63a5970d7ca8ec5fdc6c39f8c68150f436d3a3095afcf8ef3383",
    "linux-host-forward": "0865ee0fba547ce53361b0636060bac78d54589d07345ca48096f14b1c59a459",
    "qemu-system-linux-host": "c53705176c47b94d5e037edee5d261193aa46f20d3919c67816154347842448d",
    "linux-kvm-uapi": "af296aac0a0f9ebe27bc3050b50fa3f12b352f061ef3e2371954698f4ce79165",
    "virtio-1-2-contract": "aaaee261b4f58ba76210a033a5f9b66c5f6c426d5079b5d6466c784d683474a4",
    "linux-cgroup-v2-source": "6b8ce3ee1e9a15d45243f8ef86e8d36fe88009a77390a5455882bcf5d317b266",
    "virtio-1-4-reference": "67aaf73e091d736af35c6267dd955551b1f3914f612acdc971999c75a3dd07c3",
    "linux-pidfd-source": "12acbb3060261231244a087ba0e2769dba613e6a2292a2942d22f5b95d78d9b3",
    "linux-psi-source": "3f12f6b1431876622fa2a9f0bda63a335dd20c9b87a1feb2624953c1b434a9c7",
    "linux-kernel-internal-source": "e3964ce55e75734bc242c104db62392a1c609e8909620a4740a0403e0eefb149",
    "sel4-design-reference": "38eb2cb7551ea1da0d551985d66c6db86184f2b361db173883c37cebd9ee55e4",
    "zircon-design-reference": "d3af1b7bf73119de6fa95de4b65a5ecde816b22c333be44a6a8a58233aa59950",
    "unikraft-design-reference": "c2a21e577f07d1f7b7999da3261b672f9d2f2f95591ac69d2c786ad08360564e",
}

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXACT_RELEASE_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
LTS_SERIES_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.y$")
API_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*=[0-9]+$")
SPEC_PATTERN = re.compile(r"^[0-9]+\.[0-9]+-[A-Z]{2}[0-9]{2}$")
FLOATING_PATTERN = re.compile(
    r"(?:^|[-_.])(latest|main|master|head|rolling|trunk)(?:$|[-_.])",
    re.IGNORECASE,
)
RC_PATTERN = re.compile(r"(?:^|[-_.])rc[0-9]*(?:$|[-_.])", re.IGNORECASE)
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _resource_digest(resource: dict[str, Any]) -> str:
    canonical = json.dumps(resource, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read manifest {path}: {exc}") from exc
    try:
        data = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid JSON in {path}: line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a JSON object")
    return data


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_version(resource: dict[str, Any], prefix: str, errors: list[str]) -> None:
    policy = resource.get("version_policy")
    version = resource.get("version")
    pin = resource.get("reference_pin")
    if policy not in ALLOWED_VERSION_POLICIES:
        errors.append(f"{prefix}.version_policy is unknown: {policy!r}")
        return
    if not _nonempty_string(version):
        errors.append(f"{prefix}.version must be a trimmed non-empty string")
        return
    if not _nonempty_string(pin):
        errors.append(f"{prefix}.reference_pin must be a trimmed non-empty string")
        return
    for field_name, value in (("version", version), ("reference_pin", pin)):
        if FLOATING_PATTERN.search(value) or RC_PATTERN.search(value):
            errors.append(f"{prefix}.{field_name} must not be floating or a release candidate: {value!r}")

    if policy == "lts_series":
        if not LTS_SERIES_PATTERN.fullmatch(version):
            errors.append(f"{prefix}.version must be an N.N.y LTS family")
        if not EXACT_RELEASE_PATTERN.fullmatch(pin):
            errors.append(f"{prefix}.reference_pin must be an exact N.N.N release")
    elif policy == "exact_release":
        if not EXACT_RELEASE_PATTERN.fullmatch(version) or version != pin:
            errors.append(f"{prefix} exact_release requires matching N.N.N version and reference_pin")
    elif policy == "api_contract":
        if not API_PATTERN.fullmatch(version) or version != pin:
            errors.append(f"{prefix} api_contract requires matching NAME=N version and reference_pin")
    elif policy == "spec_release":
        if not SPEC_PATTERN.fullmatch(version) or version != pin:
            errors.append(f"{prefix} spec_release requires matching N.N-CSNN version and reference_pin")
    elif policy == "review_snapshot":
        if not DATE_PATTERN.fullmatch(version) or not DATE_PATTERN.fullmatch(pin):
            errors.append(f"{prefix} review_snapshot requires ISO YYYY-MM-DD version and reference_pin")
        else:
            try:
                parsed_version = date.fromisoformat(version)
                parsed_pin = date.fromisoformat(pin)
            except ValueError:
                errors.append(f"{prefix} review_snapshot requires valid calendar dates")
            else:
                if parsed_version != parsed_pin:
                    errors.append(f"{prefix} review_snapshot version and reference_pin must match")
                if parsed_version > date.today():
                    errors.append(f"{prefix} review_snapshot must not be in the future")


def _validate_url(value: Any, prefix: str, errors: list[str]) -> None:
    if not _nonempty_string(value):
        errors.append(f"{prefix}.source_url must be a trimmed non-empty string")
        return
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        errors.append(f"{prefix}.source_url must be an unauthenticated HTTPS URL")
    elif host not in OFFICIAL_HOSTS:
        errors.append(f"{prefix}.source_url host is not in the official allowlist: {host}")
    if not parsed.path or parsed.path == "/":
        errors.append(f"{prefix}.source_url must identify a specific official resource")
    if parsed.fragment:
        errors.append(f"{prefix}.source_url must not contain a fragment")


def validate_manifest(data: dict[str, Any], repo_root: Path = REPO_ROOT) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    keys = set(data)
    missing = TOP_LEVEL_FIELDS - keys
    unknown = keys - TOP_LEVEL_FIELDS
    if missing:
        errors.append(f"manifest missing fields: {', '.join(sorted(missing))}")
    if unknown:
        errors.append(f"manifest has unknown fields: {', '.join(sorted(unknown))}")

    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        errors.append("schema_version must be the integer 1")
    if data.get("policy_id") != "aios-linux-substrate-resources-v0":
        errors.append("policy_id must equal aios-linux-substrate-resources-v0")
    reviewed_text = data.get("reviewed_on", "")
    if not isinstance(reviewed_text, str) or not DATE_PATTERN.fullmatch(reviewed_text):
        errors.append("reviewed_on must be an ISO YYYY-MM-DD date")
    else:
        try:
            reviewed_on = date.fromisoformat(reviewed_text)
        except ValueError:
            errors.append("reviewed_on must be a valid calendar date")
        else:
            if reviewed_on != date(2026, 8, 15):
                errors.append("reviewed_on must equal the schema v1 review date 2026-08-15")
    if data.get("resource_policy_maturity") != "CURRENT":
        errors.append("resource_policy_maturity must equal CURRENT for schema v1")
    if data.get("hosted_backend_maturity") != "PLANNED":
        errors.append("hosted_backend_maturity must remain PLANNED for schema v1")

    canonical_document = data.get("canonical_document")
    if canonical_document != "docs/os/linux_hosted_substrate_and_resource_policy_ko.md":
        errors.append(
            "canonical_document must equal docs/os/linux_hosted_substrate_and_resource_policy_ko.md"
        )
    if not _nonempty_string(canonical_document):
        errors.append("canonical_document must be a trimmed non-empty repository-relative path")
    else:
        canonical_path = Path(canonical_document)
        if canonical_path.is_absolute() or ".." in canonical_path.parts:
            errors.append("canonical_document must stay inside the repository")
        else:
            resolved_root = repo_root.resolve()
            resolved_document = (resolved_root / canonical_path).resolve()
            try:
                resolved_document.relative_to(resolved_root)
            except ValueError:
                errors.append("canonical_document resolves outside the repository")
            else:
                if not resolved_document.is_file():
                    errors.append(f"canonical_document does not exist: {canonical_document}")

    resources = data.get("resources")
    if not isinstance(resources, list) or not resources:
        errors.append("resources must be a non-empty array")
        resources = []
    elif len(resources) > 64:
        errors.append("resources must not exceed 64 rows")

    ids: list[str] = []
    dispositions: Counter[str] = Counter()
    for index, resource in enumerate(resources):
        prefix = f"resources[{index}]"
        if not isinstance(resource, dict):
            errors.append(f"{prefix} must be an object")
            continue
        resource_keys = set(resource)
        missing_resource = RESOURCE_FIELDS - resource_keys
        unknown_resource = resource_keys - RESOURCE_FIELDS
        if missing_resource:
            errors.append(f"{prefix} missing fields: {', '.join(sorted(missing_resource))}")
        if unknown_resource:
            errors.append(f"{prefix} has unknown fields: {', '.join(sorted(unknown_resource))}")

        resource_id = resource.get("id")
        if not isinstance(resource_id, str) or not ID_PATTERN.fullmatch(resource_id):
            errors.append(f"{prefix}.id must use lowercase kebab-case")
        else:
            ids.append(resource_id)
            prefix = f"resource[{resource_id}]"

        kind = resource.get("kind")
        disposition = resource.get("disposition")
        boundary = resource.get("boundary")
        identity = resource.get("identity_semantics")
        maturity = resource.get("maturity")
        if kind not in ALLOWED_KINDS:
            errors.append(f"{prefix}.kind is unknown: {kind!r}")
        if disposition not in ALLOWED_DISPOSITIONS:
            errors.append(f"{prefix}.disposition is unknown: {disposition!r}")
        else:
            dispositions[disposition] += 1
        if boundary not in ALLOWED_BOUNDARIES:
            errors.append(f"{prefix}.boundary is unknown: {boundary!r}")
        if identity not in ALLOWED_IDENTITIES:
            errors.append(f"{prefix}.identity_semantics is unknown: {identity!r}")
        if maturity not in ALLOWED_MATURITY:
            errors.append(f"{prefix}.maturity is unknown: {maturity!r}")

        _validate_version(resource, prefix, errors)
        _validate_url(resource.get("source_url"), prefix, errors)
        for field_name in ("license", "aios_role", "update_policy"):
            if not _nonempty_string(resource.get(field_name)):
                errors.append(f"{prefix}.{field_name} must be a trimmed non-empty string")
        if resource.get("code_import") is not False:
            errors.append(f"{prefix}.code_import must be false in source-curation schema v1")
        if resource.get("provenance_required") is not True:
            errors.append(f"{prefix}.provenance_required must be true")
        if resource.get("artifact_pin_required") is not True:
            errors.append(f"{prefix}.artifact_pin_required must be true")

        block_reason = resource.get("block_reason")
        if not isinstance(block_reason, str) or block_reason != block_reason.strip():
            errors.append(f"{prefix}.block_reason must be a trimmed string")
        elif disposition == "blocked_import" and not block_reason:
            errors.append(f"{prefix}.block_reason is required for blocked_import")
        elif disposition != "blocked_import" and block_reason:
            errors.append(f"{prefix}.block_reason must be empty unless disposition is blocked_import")

        expected_maturity = {
            "host_only": "PLANNED",
            "interface_only": "PLANNED",
            "reference_only": "RESEARCH",
            "import_candidate": "PLANNED",
            "blocked_import": "RESEARCH",
        }.get(disposition)
        if expected_maturity and maturity != expected_maturity:
            errors.append(f"{prefix}.maturity must equal {expected_maturity} for {disposition}")
        if kind == "host_observation_api" and identity != "source_only":
            errors.append(f"{prefix} host observation identity must remain source_only")
        if boundary == "linux_userspace_source" and identity != "source_only":
            errors.append(f"{prefix} Linux userspace identities must remain source_only")
        if disposition == "blocked_import" and boundary != "forbidden_internal":
            errors.append(f"{prefix} blocked_import must use forbidden_internal boundary")
        if disposition == "import_candidate":
            errors.append(f"{prefix} import_candidate is forbidden in source-curation schema v1")

    duplicate_ids = sorted(resource_id for resource_id, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"duplicate resource ids: {', '.join(duplicate_ids)}")
    actual_ids = set(ids)
    required_ids = set(REQUIRED_RESOURCES)
    unexpected_ids = sorted(actual_ids - required_ids)
    if unexpected_ids:
        errors.append(f"unexpected resource ids: {', '.join(unexpected_ids)}")

    by_id = {
        resource.get("id"): resource
        for resource in resources
        if isinstance(resource, dict) and isinstance(resource.get("id"), str)
    }
    for required_id, expected_fields in REQUIRED_RESOURCES.items():
        resource = by_id.get(required_id)
        if resource is None:
            errors.append(f"required resource missing: {required_id}")
            continue
        for field_name, expected_value in expected_fields.items():
            if resource.get(field_name) != expected_value:
                errors.append(
                    f"required resource {required_id}.{field_name} must equal {expected_value}"
                )
        expected_license = REQUIRED_LICENSES[required_id]
        if resource.get("license") != expected_license:
            errors.append(
                f"required resource {required_id}.license must equal {expected_license}"
            )
        expected_digest = EXPECTED_RESOURCE_DIGESTS[required_id]
        if _resource_digest(resource) != expected_digest:
            errors.append(f"required resource {required_id} full-row contract digest mismatch")

    summary = {
        "schema_version": data.get("schema_version"),
        "policy_id": data.get("policy_id"),
        "resource_count": len(resources),
        "dispositions": {key: dispositions.get(key, 0) for key in sorted(ALLOWED_DISPOSITIONS)},
        "code_import_count": sum(
            1 for resource in resources if isinstance(resource, dict) and resource.get("code_import") is True
        ),
        "hosted_backend_maturity": data.get("hosted_backend_maturity"),
        "passed": not errors,
    }
    return errors, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def format_pass(summary: dict[str, Any]) -> str:
    counts = summary["dispositions"]
    return (
        "[LINUX-RESOURCE] PASS "
        f"schema={summary['schema_version']} resources={summary['resource_count']} "
        f"host_only={counts['host_only']} interface_only={counts['interface_only']} "
        f"reference_only={counts['reference_only']} blocked_import={counts['blocked_import']} "
        f"import_candidate={counts['import_candidate']} code_import={summary['code_import_count']} "
        f"hosted_backend={summary['hosted_backend_maturity']}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_manifest(DEFAULT_MANIFEST)
        errors, summary = validate_manifest(data, REPO_ROOT)
    except ValueError as exc:
        errors = [str(exc)]
        summary = {"passed": False}

    if args.json_output:
        print(json.dumps({"summary": summary, "errors": errors}, ensure_ascii=False, sort_keys=True))
    elif errors:
        print(f"[LINUX-RESOURCE] FAIL errors={len(errors)}")
        for error in errors:
            print(f"[ERROR] {error}")
    else:
        print(format_pass(summary))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
