from __future__ import annotations

import unittest

from lib.boot_matrix_lane import _matrix_result_passed


def passing_result() -> dict[str, object]:
    return {
        "outcome": "PASS",
        "passed": True,
        "skipped": False,
        "unsupported": False,
        "ready": True,
        "shell_started": True,
        "missing_patterns": [],
    }


class BootMatrixVerdictTests(unittest.TestCase):
    def test_canonical_pass_is_required(self) -> None:
        self.assertTrue(_matrix_result_passed(passing_result()))

        for mutation in (
            {"outcome": "SKIP", "skipped": True},
            {"outcome": "UNSUPPORTED", "unsupported": True},
            {"outcome": "FAIL", "passed": False},
            {"ready": False},
            {"shell_started": False},
            {"missing_patterns": ["required"]},
        ):
            with self.subTest(mutation=mutation):
                result = passing_result()
                result.update(mutation)
                self.assertFalse(_matrix_result_passed(result))


if __name__ == "__main__":
    unittest.main()
