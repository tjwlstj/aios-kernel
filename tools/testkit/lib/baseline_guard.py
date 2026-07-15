from __future__ import annotations

from collections.abc import Sequence

from lib.boot_verdict import SCHEMA_VERSION
from lib.common import ToolError


def require_strict_baseline_write(strict: bool) -> None:
    if not strict:
        raise ToolError("`--write-baseline` requires `--strict`.")


def require_exact_profile_request(
    requested_profiles: Sequence[str],
    normalized_profiles: Sequence[str],
) -> None:
    if list(requested_profiles) != list(normalized_profiles):
        raise ToolError(
            "Baseline write requires an exact, unique ordered profile request."
        )


def require_trusted_matrix_source(
    matrix_summary: dict[str, object],
    expected_profiles: Sequence[str],
) -> None:
    """Reject incomplete, skipped, unsupported, or failed baseline sources."""

    expected = list(expected_profiles)
    errors: list[str] = []

    if matrix_summary.get("passed") is not True:
        errors.append("matrix aggregate did not pass")
    if matrix_summary.get("profiles_requested") != expected:
        errors.append("matrix requested profiles do not match")
    profile_count = matrix_summary.get("profile_count")
    if type(profile_count) is not int or profile_count != len(expected):
        errors.append("matrix profile count does not match")

    results = matrix_summary.get("results")
    if not isinstance(results, list):
        errors.append("matrix results are missing")
        results = []
    elif len(results) != len(expected):
        errors.append("matrix result count does not match")

    result_profiles: list[object] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            errors.append(f"matrix result {index} is malformed")
            result_profiles.append(None)
            continue

        result_profiles.append(result.get("profile"))
        if result.get("summary_present") is not True:
            errors.append(f"matrix result {index} has no source summary")
        if result.get("skipped") is True or result.get("outcome") == "SKIP":
            errors.append(f"matrix result {index} was skipped")
        if result.get("unsupported") is True or result.get("outcome") == "UNSUPPORTED":
            errors.append(f"matrix result {index} is unsupported")
        verdict = result.get("verdict")
        if not isinstance(verdict, dict):
            errors.append(f"matrix result {index} has no canonical verdict")
        else:
            if verdict.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"matrix result {index} verdict schema is invalid")
            if verdict.get("outcome") != "PASS" or verdict.get("passed") is not True:
                errors.append(f"matrix result {index} canonical verdict did not pass")
            if verdict.get("reasons") != [] or verdict.get("missing_patterns") != []:
                errors.append(f"matrix result {index} canonical verdict is incomplete")
            health = verdict.get("health")
            checkpoints = verdict.get("checkpoints")
            if not isinstance(health, dict) or health.get("passed") is not True:
                errors.append(f"matrix result {index} health verdict did not pass")
            if not isinstance(checkpoints, dict) or checkpoints.get("passed") is not True:
                errors.append(f"matrix result {index} checkpoint verdict did not pass")
        if (
            result.get("outcome") != "PASS"
            or result.get("passed") is not True
            or not isinstance(verdict, dict)
            or result.get("outcome") != verdict.get("outcome")
            or result.get("passed") is not verdict.get("passed")
        ):
            errors.append(f"matrix result {index} did not pass")
        if result.get("missing_patterns"):
            errors.append(f"matrix result {index} is missing required patterns")

    if result_profiles != expected:
        errors.append("matrix result profile order does not match")

    if errors:
        raise ToolError("Baseline write refused: " + "; ".join(errors))
