#!/usr/bin/env python3
"""Expected-fault driver; never installed in the operating image or CLI."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_backend.client import control
from aios_resources.proc import ProcessReader
from backend_recovery_contract import RECOVERY_COMMANDS

LIMIT = 4 * 1024 * 1024
PROMPT = re.compile(rb"(?:^|\n)aios> $")


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def inject(cli, directory, destination):
    before = control(directory, "status")
    if before["outcome"] != "OK" or before["state"] != "RUNNING":
        raise RuntimeError("fault requires an authenticated RUNNING backend")
    record = before["service_record"]
    parent, child = record["supervisor_identity"], record["child_identity"]
    with ProcessReader(cli.pid) as owner, ProcessReader(parent["process_id"], parent["process_start_ticks"], parent["host_boot_id"]) as supervisor, ProcessReader(child["process_id"], child["process_start_ticks"], child["host_boot_id"]) as model:
        held = os.pidfd_open(parent["process_id"])
        try:
            acquired = time.monotonic_ns()
            owner_before, supervisor_before, child_before = owner.sample(), supervisor.sample(), model.sample()
            if supervisor.identity != parent or model.identity != child or cli.poll() is not None:
                raise RuntimeError("fault identity changed")
            # Reauthenticate after opening the target lifetime handle.
            if control(directory, "status") != before or select.select([held], [], [], 0)[0]:
                raise RuntimeError("fault target unavailable")
            sent = time.monotonic_ns()
            signal.pidfd_send_signal(held, signal.SIGKILL)
            if not select.select([held], [], [], 10)[0]:
                raise RuntimeError("supervisor did not exit")
            observed = time.monotonic_ns()
            child_after = model.sample()
            fresh = subprocess.run([sys.executable, str(ROOT / "hosted/linux/aios-backend.py"),
                "recover", "--state-dir", str(directory)], capture_output=True, timeout=30)
            response = json.loads(fresh.stdout)
            if fresh.returncode != 1 or fresh.stderr or response["error"] != "recovery-owner-required":
                raise RuntimeError("fresh controller unexpectedly recovered")
            child_after_fresh = model.sample()
            proof = {"schema_version": 1, "scenario": "running-supervisor-loss", "capture_kind": "live",
                "source_only": True, "injector_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "backend_status": before, "cli": owner_before, "supervisor_before": supervisor_before,
                "child_before": child_before, "child_after": child_after, "child_after_fresh": child_after_fresh,
                "signal": {"name": "SIGKILL", "via": "authenticated-pidfd", "pidfd_acquired_monotonic_ns": acquired,
                    "sent_monotonic_ns": sent, "supervisor_exit_observed": True,
                    "supervisor_exit_monotonic_ns": observed},
                "fresh_recover": {"process_exit_code": fresh.returncode, "stdout": fresh.stdout.decode("utf-8"),
                    "stderr": fresh.stderr.decode("utf-8"), "response": response}}
            save(destination / "fault.json", proof)
        finally:
            os.close(held)


def main():
    destination = Path(sys.argv[1])
    destination.mkdir(mode=0o700, exist_ok=False)
    state = Path("/tmp/aios-model-backend")
    with (destination / "stderr.log").open("wb") as errors:
        cli = subprocess.Popen([sys.executable, str(ROOT / "hosted/linux/aios-console.py"),
            "--artifact-dir", str(destination / "session"), "--service-dir", "/tmp/aios-runtime",
            "--agent-dir", "/tmp/aios-agent", "--agent-config", "/tmp/aios-agent-config.json",
            "--backend-dir", str(state)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        output = bytearray()

        def prompt():
            deadline = time.monotonic() + 650
            start = len(output)
            while not PROMPT.search(output[start:]):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([cli.stdout], [], [], max(0, remaining))[0]:
                    raise RuntimeError("recovery CLI prompt timeout")
                chunk = os.read(cli.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("recovery CLI exited before prompt")
                output.extend(chunk)
                if len(output) > LIMIT:
                    raise RuntimeError("recovery CLI output limit")

        try:
            prompt()
            for index, command in enumerate(RECOVERY_COMMANDS):
                print("[recovery] " + command, flush=True)
                cli.stdin.write((command + "\n").encode("utf-8"))
                cli.stdin.flush()
                if command == "exit":
                    tail, _ = cli.communicate(timeout=30)
                    output.extend(tail)
                else:
                    prompt()
                if index == 4:
                    inject(cli, state, destination)
            if cli.returncode != 0:
                raise RuntimeError("recovery CLI failed")
        finally:
            (destination / "stdout.log").write_bytes(output)
            if cli.poll() is None:
                cli.kill()
                cli.wait(timeout=10)
            for stream in (cli.stdin, cli.stdout):
                if not stream.closed:
                    stream.close()
    save(destination / "execution.json", {"schema_version": 1, "mode": "smoke",
        "process_exit_code": cli.returncode, "stdout_sha256": hashlib.sha256(output).hexdigest(),
        "stderr_sha256": hashlib.sha256((destination / "stderr.log").read_bytes()).hexdigest(),
        "requested_commands": RECOVERY_COMMANDS})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
