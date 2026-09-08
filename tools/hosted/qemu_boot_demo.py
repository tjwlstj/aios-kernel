#!/usr/bin/env python3
"""Run AIOS in an isolated, disposable Linux development VM on Windows/Linux.

The supplied Linux ISO is a test host, not an AIOS distribution. Nothing is
installed on the host. Its SHA-256 and the QEMU version are retained with the run.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from verify_boot import verify_execution


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--kernel", type=Path, required=True, help="boot/vmlinuz-virt extracted from the supplied ISO")
    parser.add_argument("--initramfs", type=Path, required=True, help="boot/initramfs-virt extracted from that ISO")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--repository-url", default="https://dl-cdn.alpinelinux.org/alpine/v3.24/main")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.artifact_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Only the documented official package source is accepted in this diagnostic helper.
    if not re.fullmatch(r"https://dl-cdn\.alpinelinux\.org/alpine/v[0-9]+\.[0-9]+/main", args.repository_url):
        raise ValueError("unsupported package repository")
    image_hash = hashlib.sha256(args.iso.read_bytes()).hexdigest()
    if image_hash != args.sha256.lower():
        raise ValueError("Linux ISO checksum mismatch")
    version = subprocess.run([str(args.qemu), "--version"], capture_output=True, text=True, check=True).stdout.strip()
    head = subprocess.run(["git", "--no-optional-locks", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    status = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True).stdout
    # FAT directory sharing needs a local path on Windows, so use a fresh temp tree.
    with tempfile.TemporaryDirectory(prefix="aios-linux-demo-") as scratch:
        resolved_scratch = Path(scratch).resolve()
        if resolved_scratch.parent != Path(tempfile.gettempdir()).resolve() or not resolved_scratch.name.startswith("aios-linux-demo-"):
            raise ValueError("unexpected temporary workspace")
        share = Path(scratch) / "share"
        share.mkdir()
        for relative in ("hosted/linux", "hosted/contracts"):
            shutil.copytree(root / relative, share / relative, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for relative in ("tools/hosted/verify_boot.py", "tools/hosted/boot_smoke.py", "tools/hosted/tests/test_hosted_boot.py"):
            target = share / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        retained_source = output / "runtime-source"
        shutil.copytree(share / "hosted/linux", retained_source)
        command = [str(args.qemu), "-machine", "q35", "-accel", "tcg", "-m", "768", "-smp", "2",
                   "-kernel", str(args.kernel), "-initrd", str(args.initramfs),
                   "-append", "console=ttyS0,115200 modules=loop,squashfs,sd-mod,usb-storage",
                   "-cdrom", str(args.iso), "-display", "none", "-monitor", "none", "-serial", "stdio",
                   "-no-reboot", "-nic", "user,model=e1000", "-device", "qemu-xhci", "-device", "usb-kbd",
                   "-drive", "file=fat:ro:" + share.as_posix() + ",format=raw,if=virtio,readonly=on"]
        provenance = {"schema_version": 1, "purpose": "isolated-linux-development-host", "physical_host_inventory": False,
                      "git_head": head, "git_dirty": bool(status), "git_status": status.splitlines(),
                      "qemu_version": version, "iso_sha256": image_hash,
                      "kernel_sha256": hashlib.sha256(args.kernel.read_bytes()).hexdigest(),
                      "initramfs_sha256": hashlib.sha256(args.initramfs.read_bytes()).hexdigest(),
                      "repository_url": args.repository_url, "command": command}
        (output / "environment.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        chunks: queue.Queue[bytes | None] = queue.Queue()

        def read_output():
            with (output / "linux-serial.log").open("wb") as log:
                while chunk := process.stdout.read(65536):
                    log.write(chunk)
                    log.flush()
                    chunks.put(chunk)
            chunks.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        transcript = bytearray()
        cursor = 0

        def wait(pattern: bytes, seconds: int = 90) -> re.Match:
            nonlocal cursor
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                match = re.search(pattern, transcript[cursor:])
                if match:
                    cursor += match.end()
                    return match
                try:
                    chunk = chunks.get(timeout=0.5)
                except queue.Empty:
                    continue
                if chunk is None:
                    raise RuntimeError("VM exited before expected serial checkpoint")
                transcript.extend(chunk)
            raise TimeoutError("serial checkpoint timeout: " + pattern.decode(errors="replace"))

        def send(value: str):
            # Pace serial input for the emulated UART/BusyBox line editor under TCG.
            for byte in (value + "\n").encode():
                process.stdin.write(bytes([byte]))
                process.stdin.flush()
                time.sleep(0.005)

        def step(name: str, command_text: str, seconds: int = 90):
            print("[DEMO] " + name, flush=True)
            send(command_text + "; printf '\\n__AIOS_STEP_" + name + "_%s__\\n' \"$?\"")
            match = wait(rb"\r?\n__AIOS_STEP_" + name.encode() + rb"_([0-9]+)__\r?\n", seconds)
            if match.group(1) != b"0":
                raise RuntimeError("guest step failed: " + name + " exit=" + match.group(1).decode())

        verdict = {"outcome": "FAIL", "reasons": ["not_completed"]}
        killed = False
        shutdown_observed = False
        try:
            print("[DEMO] booting isolated Linux development VM", flush=True)
            wait(rb"login:\s*$", 120)
            send("root")
            # BusyBox may append an ANSI cursor-position request after the prompt.
            wait(rb"localhost:~#")
            process.stdin.write(b"\x1b[1;1R")
            process.stdin.flush()
            step("console", "export TERM=dumb; stty -echo")
            step("network", "ip link set eth0 up && udhcpc -i eth0 -q -n -t 5 -T 3", 45)
            step("python", "printf '%s\\n' '" + args.repository_url + "' > /etc/apk/repositories && apk add --no-cache python3", 180)
            step("share", "mkdir -p /mnt/aios && mount -t vfat -o ro /dev/vda1 /mnt/aios")
            step("tests", "cd /mnt/aios && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/hosted/tests -p test_hosted_boot.py -v")
            step("startup", "cd /mnt/aios && PYTHONDONTWRITEBYTECODE=1 python3 tools/hosted/boot_smoke.py --artifact-dir /tmp/aios-smoke")
            export = "import base64,json,pathlib; p=pathlib.Path('/tmp/aios-smoke'); d={str(f.relative_to(p)):base64.b64encode(f.read_bytes()).decode() for f in p.rglob('*') if f.is_file()}; print('__AIOS_BUNDLE_BEGIN__'+base64.b64encode(json.dumps(d).encode()).decode()+'__AIOS_BUNDLE_END__')"
            send('python3 -c "' + export + '"')
            match = wait(rb"__AIOS_BUNDLE_BEGIN__([A-Za-z0-9+/=]+)__AIOS_BUNDLE_END__")
            bundle = json.loads(base64.b64decode(match.group(1), validate=True))
            allowed = {"run/" + name for name in ("boot.log", "events.jsonl", "inventory.json", "result.json")}
            allowed.update(("process.json", "verdict.json", "stdout.log", "stderr.log"))
            if set(bundle) != allowed:
                raise ValueError("unexpected guest bundle file set")
            for name, value in bundle.items():
                destination = output / name
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes(base64.b64decode(value, validate=True))
            verdict = verify_execution(output, require_live=True, source_root=retained_source)
            guest_verdict = json.loads((output / "verdict.json").read_bytes())
            if guest_verdict.get("outcome") != "PASS" or verdict["outcome"] != "PASS":
                raise ValueError("host or guest startup verdict failed")
            inventory = json.loads((output / "run/inventory.json").read_bytes())["inventory"]
            print("[AIOS] userspace startup READY (Linux guest-visible hardware)", flush=True)
            print("[AIOS] CPU " + json.dumps(inventory["cpu"]["data"], ensure_ascii=True), flush=True)
            print("[AIOS] MEMORY " + json.dumps(inventory["memory"]["data"], ensure_ascii=True), flush=True)
            for category in ("pci", "usb", "block", "net"):
                devices = inventory[category]["data"]
                print("[AIOS] " + category.upper() + " observed=" + str(len(devices)), flush=True)
                for device in devices:
                    print("  " + json.dumps(device, ensure_ascii=True, sort_keys=True), flush=True)
            print("[DEMO] AIOS startup and hardware inventory verified; powering off guest", flush=True)
            send("poweroff -f")
            wait(rb"reboot: Power down", 30)
            shutdown_observed = True
            process.wait(timeout=30)
            if process.returncode != 0:
                raise RuntimeError("VM did not exit cleanly")
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            verdict = {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}
        finally:
            if process.poll() is None:
                killed = True
                process.kill()
                process.wait(timeout=15)
            reader.join(timeout=10)
            if reader.is_alive():
                verdict = {"outcome": "FAIL", "reasons": ["serial_reader_not_drained"]}
            if killed or process.returncode != 0 or not shutdown_observed:
                verdict = {"outcome": "FAIL", "reasons": ["vm_not_clean", verdict]}
            for stream in (process.stdin, process.stdout):
                stream.close()
            verdict.update(vm_exit_code=process.returncode, host_killed=killed, shutdown_observed=shutdown_observed)
            (output / "demo-verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(verdict, sort_keys=True), flush=True)
    return 0 if verdict["outcome"] == "PASS" and not killed else 1


if __name__ == "__main__":
    raise SystemExit(main())
