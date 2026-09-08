#!/usr/bin/env python3
"""Run and independently verify one real Linux startup, retaining process evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from verify_boot import verify_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        args.artifact_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print("fresh artifact directory required: " + str(exc), file=sys.stderr)
        return 1
    command = [sys.executable, str(root / "hosted/linux/aios-boot.py"), "--artifact-dir", str(args.artifact_dir / "run")]
    try:
        process = subprocess.run(command, capture_output=True, timeout=30)
        stdout, stderr, exit_code = process.stdout, process.stderr, process.returncode
        outcome = "exited"
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, exit_code = exc.stdout or b"", exc.stderr or b"", None
        outcome = "timeout"
    except OSError as exc:
        stdout, stderr, exit_code = b"", ("launch_error:" + type(exc).__name__).encode(), None
        outcome = "launch_error"
    (args.artifact_dir / "stdout.log").write_bytes(stdout)
    (args.artifact_dir / "stderr.log").write_bytes(stderr)
    verdict = verify_bundle(args.artifact_dir / "run", require_live=True, process_exit=exit_code)
    process_evidence = {"outcome": outcome, "exit_code": exit_code, "command": command,
                        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                        "stderr_sha256": hashlib.sha256(stderr).hexdigest()}
    if outcome != "exited" or exit_code != 0 or stderr:
        verdict = {"outcome": "FAIL", "reasons": ["process_not_clean", verdict]}
    elif verdict["outcome"] == "PASS":
        try:
            if stdout != (args.artifact_dir / "run/boot.log").read_bytes():
                verdict = {"outcome": "FAIL", "reasons": ["stdout_log_mismatch"]}
        except OSError:
            verdict = {"outcome": "FAIL", "reasons": ["boot_log_unavailable"]}
    (args.artifact_dir / "process.json").write_text(json.dumps(process_evidence, indent=2) + "\n", encoding="utf-8")
    (args.artifact_dir / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
