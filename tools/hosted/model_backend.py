#!/usr/bin/env python3
"""Own one pinned external model backend in the disposable Linux guest.

This development helper manages its own Popen handle and logs. It does not
install an init service or adopt a PID from a file.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

MODEL_NAME = "Qwen3-0.6B-Q8_0.gguf"
MODEL_BYTES = 639446688
MODEL_SHA = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
BACKEND_NAME = "llamafile-0.10.5-thin.exe"
BACKEND_SHA = "55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611"
MODEL_ID = "aios-qwen3-0.6b-q8_0"


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def prepare_model(device: Path, destination: Path) -> None:
    """Copy only the pinned model bytes from a read-only development disk."""
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    sha = hashlib.sha256()
    with device.open("rb") as source, destination.open("xb") as output:
        remaining = MODEL_BYTES
        while remaining:
            data = source.read(min(1024 * 1024, remaining))
            if not data:
                raise ValueError("model disk truncated")
            output.write(data)
            sha.update(data)
            remaining -= len(data)
    if sha.hexdigest() != MODEL_SHA:
        raise ValueError("model disk checksum")
    destination.chmod(0o444)
    save(destination.parent / "integrity.json", {"schema_version": 1, "model_bytes": MODEL_BYTES,
         "model_sha256": sha.hexdigest(), "verification": "guest-read-complete", "read_only_source": True})


def serve(backend: Path, model: Path, output: Path, *, port: int = 18081,
          config_path: Path = Path('/tmp/aios-agent-config.json')) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hosted/linux'))
    from aios_agent.inference import load_config
    from aios_resources.backend import BackendAttester
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    process = None
    attester = None
    stop_requested = False
    killed = False
    started = time.monotonic()
    result = {"schema_version": 1, "outcome": "FAIL", "uid": os.getuid(), "model_id": MODEL_ID,
              "model_sha256": MODEL_SHA, "backend_sha256": BACKEND_SHA, "ready": False,
              "error": None, "host_killed": False, "process_exit_code": None}
    def stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    command = ["/bin/sh", str(backend), "--server", "-m", str(model), "--gpu", "disable", "--host", "127.0.0.1",
               "--port", str(port), "--no-webui", "-c", "1024", "-b", "64", "-ub", "64", "-t", "2", "-np", "1",
               "--alias", MODEL_ID, "--nologo"]
    save(output / "command.json", {"command": command, "source_only": True})
    try:
        config = load_config(config_path, verify_artifacts=False)
        if (config['model_id'] != MODEL_ID or config['model_sha256'] != MODEL_SHA
                or config['backend_sha256'] != BACKEND_SHA or config['model_path'] != str(model)
                or config['backend_path'] != str(backend) or config['endpoint'] != 'http://127.0.0.1:' + str(port)):
            raise ValueError('backend config mismatch')
        if hashlib.sha256(backend.read_bytes()).hexdigest() != BACKEND_SHA:
            raise ValueError("backend checksum")
        with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=True, close_fds=True)
            attester = BackendAttester(output, process, config)
            while not stop_requested and not (output / "stop.request").exists():
                if process.poll() is not None:
                    raise ValueError("backend exited before stop")
                attestation_ready = attester.poll()
                if not result["ready"]:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    try:
                        connection.request("GET", "/health")
                        response = connection.getresponse()
                        body = response.read(65537)
                        if attestation_ready and len(body) <= 65536 and response.status == 200 and json.loads(body).get("status") == "ok":
                            result.update(ready=True, startup_seconds=time.monotonic() - started, pid=process.pid)
                            (output / "health.json").write_bytes(body)
                            save(output / "ready.json", result)
                    except (OSError, ValueError, http.client.HTTPException):
                        pass
                    finally:
                        connection.close()
                    if time.monotonic() - started > 180:
                        raise TimeoutError("model readiness timeout")
                time.sleep(0.2)
            process.terminate()
            process.wait(timeout=20)
            if process.returncode not in (0, -signal.SIGTERM):
                raise ValueError("backend abnormal shutdown")
            result["outcome"] = "PASS"
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
    finally:
        if attester is not None:
            try:
                attester.close()
            except (OSError, ValueError) as exc:
                result.update(outcome='FAIL', error='attester-close:' + str(exc))
        if process is not None:
            if process.poll() is None:
                killed = True
                process.kill()
                process.wait(timeout=10)
            result["process_exit_code"] = process.returncode
        result.update(host_killed=killed, elapsed_seconds=time.monotonic() - started)
        save(output / "result.json", result)
    return 0 if result["outcome"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve"))
    parser.add_argument("--device", type=Path, default=Path("/dev/vdb"))
    parser.add_argument("--model", type=Path, default=Path("/tmp/aios-model/" + MODEL_NAME))
    parser.add_argument("--backend", type=Path, default=Path("/mnt/aios/inference/" + BACKEND_NAME))
    parser.add_argument("--output", type=Path, default=Path("/tmp/aios-model-backend"))
    parser.add_argument("--config", type=Path, default=Path('/tmp/aios-agent-config.json'))
    args = parser.parse_args()
    if args.action == "prepare":
        prepare_model(args.device, args.model)
        return 0
    return serve(args.backend, args.model, args.output, config_path=args.config)


if __name__ == "__main__":
    raise SystemExit(main())
