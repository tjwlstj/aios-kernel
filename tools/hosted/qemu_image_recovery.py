#!/usr/bin/env python3
"""Prepare and run a separately identified, expected-fault operating-image clone.

Preparation may use the pinned installer. The actual test cold-boots only the
clone disk and drives the unchanged product CLI over ttyS0. One loopback socket
backs ttyS1 solely for the installed test helper's bounded request and evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import uuid

from qemu_console import SerialGuest, export_guest, save_json
from qemu_image import (DISK_BYTES, ISO_SHA, MODEL_PROMPT_TIMEOUT, base_command, clone_verified,
                        decode_export, digest, disk_record, display_image)

SCENARIO = "image-worker-recover-exit"
COMMANDS = ["about", "backend start", "backend status", "backend recover", "backend status", "exit"]
TOOLS = ("image_recovery_guest.py", "image_recovery_prepare.py", "qemu_image_recovery.py")
CHANNEL_LIMIT = 2 * 1024 * 1024
WINDOWS_HOST = os.name == "nt"


def require(condition, reason):
    if not condition:
        raise ValueError("image_recovery_runner:" + reason)


def record(path):
    from verify_image import record as read_record
    return read_record(path)


def finish_owned_guest(guest, vm):
    """Close this tool's VM and streams even when a reader/finalizer fails."""
    process = getattr(guest, "process", None)
    if process is None:
        return
    try:
        if process.poll() is None:
            vm["host_killed"] = True
            process.kill()
        process.wait(timeout=15)
    except BaseException as exc:
        vm["runner_error"] = vm["runner_error"] or type(exc).__name__ + ":" + str(exc)
    finally:
        reader = getattr(guest, "reader", None)
        if reader is not None and reader.ident is not None:
            reader.join(timeout=15)
            if reader.is_alive():
                vm["runner_error"] = vm["runner_error"] or "serial-reader-not-drained"
        vm["runner_error"] = vm["runner_error"] or getattr(guest, "reader_error", None)
        vm["process_exit_code"] = process.poll()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def start_owned_guest(command, log, vm):
    # Retain the partially initialized instance if Popen succeeds but creating
    # the serial reader fails; a failed constructor must not orphan that VM.
    guest = SerialGuest.__new__(SerialGuest)
    try:
        SerialGuest.__init__(guest, command, log)
    except BaseException:
        finish_owned_guest(guest, vm)
        raise
    return guest


def tree_records(root):
    """Freeze the complete source artifact's bytes, sizes and modification times."""
    rows = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), "source-symlink")
        if not stat.S_ISREG(info.st_mode):
            require(stat.S_ISDIR(info.st_mode), "source-special-file")
            continue
        sha = digest(path)
        after = path.stat()
        require((info.st_size, info.st_mtime_ns, info.st_ino) ==
                (after.st_size, after.st_mtime_ns, after.st_ino), "source-changed-during-read")
        rows[path.relative_to(root).as_posix()] = {"size_bytes": info.st_size, "sha256": sha,
                                                   "mtime_ns": info.st_mtime_ns}
    require("system.raw" in rows and "verdict.json" in rows, "source-artifacts")
    return rows


def check_iso(args):
    require(all(getattr(args, name, None) is not None for name in ("iso", "kernel", "initramfs")),
            "pinned-installer-inputs-required")
    require(digest(args.iso) == ISO_SHA, "installer-iso-hash")
    for name, supplied in (("boot/vmlinuz-virt", args.kernel), ("boot/initramfs-virt", args.initramfs)):
        raw = subprocess.run(["tar", "-xOf", str(args.iso), name], capture_output=True, check=True).stdout
        require(hashlib.sha256(raw).hexdigest() == digest(supplied), "installer-boot-hash")


def prepare(args):
    source, output = args.source_image.resolve(), args.image_dir.resolve()
    require(source != output and not output.exists() and source not in output.parents
            and output not in source.parents, "new-separate-clone-required")
    check_iso(args)
    base_files = tree_records(source)
    source_image = {"schema_version": 1, "path": str(source / "system.raw"),
                    "sha256": base_files["system.raw"]["sha256"], "size_bytes": base_files["system.raw"]["size_bytes"]}
    require(record(source / "image-manifest.json")["profile"] == "local-model-cli", "model-image-required")
    clone_verified(source, output)
    cloned = disk_record(output / "system.raw")
    require(cloned["sha256"] == source_image["sha256"] and cloned["size_bytes"] == DISK_BYTES, "clone-bytes")
    stage = output / "preparation"
    stage.mkdir()
    snapshots = output / "verification-source"
    snapshots.mkdir()
    # Retain all independent replay dependencies, separate from product source35.
    for path in sorted(Path(__file__).parent.glob("*.py")):
        shutil.copyfile(path, snapshots / path.name)
    runtime = record(output / "image-manifest.json")["runtime_files"]
    require(len(runtime) == 35, "product-source35")
    job = {"schema_version": 1, "scenario": SCENARIO,
           "base_manifest_sha256": digest(output / "image-manifest.json"),
           "helper_source_sha256": digest(snapshots / "image_recovery_guest.py"), "image_id": str(uuid.uuid4())}
    save_json(stage / "prepare-input.json", job)
    sidecar = {"schema_version": 1, "scenario": SCENARIO, "test_only": True, "source_only": True,
        "source_directory": str(source), "source_verdict_sha256": base_files["verdict.json"]["sha256"],
        "source_image": source_image, "cloned_image": cloned, "prepared_image": None,
        "base_file_records": base_files, "source_files_preserved": False, "guest_receipt_sha256": None,
        "helper_source_sha256": job["helper_source_sha256"],
        "preparation_source_sha256": digest(snapshots / "image_recovery_prepare.py"),
        "runner_source_sha256": digest(snapshots / "qemu_image_recovery.py")}
    save_json(output / "fault-instrumentation.json", sidecar)
    (output / "TEST-IMAGE-DO-NOT-USE.txt").write_text(
        "Expected-fault test clone only. No user image or verified-image pointer may reference this directory.\n",
        encoding="ascii")
    vm = {"schema_version": 1, "process_exit_code": None, "host_killed": False,
          "shutdown_observed": False, "runner_error": None}
    save_json(stage / "vm-result.json", vm)
    result = {"schema_version": 1, "scenario": SCENARIO, "test_only": True,
              "outcome": "FAIL", "reasons": ["not_completed"]}
    save_json(stage / "result.json", result)
    with tempfile.TemporaryDirectory(prefix="aios-image-recovery-") as scratch:
        scratch_path = Path(scratch).resolve()
        require(scratch_path.parent == Path(tempfile.gettempdir()).resolve()
                and scratch_path.name.startswith("aios-image-recovery-"), "scratch-path")
        share = scratch_path / "share"
        share.mkdir()
        for name in ("image_recovery_guest.py", "image_recovery_prepare.py"):
            shutil.copyfile(snapshots / name, share / name)
        shutil.copyfile(stage / "prepare-input.json", share / "prepare-input.json")
        command = base_command(args.qemu, model=True) + ["-kernel", str(args.kernel), "-initrd", str(args.initramfs),
            "-append", "console=ttyS0,115200 modules=loop,squashfs,sd-mod,usb-storage", "-cdrom", str(args.iso),
            "-nic", "user,model=e1000", "-drive", "file=" + (output / "system.raw").as_posix() + ",format=raw,if=none,id=aiosdisk",
            "-device", "virtio-blk-pci,drive=aiosdisk,serial=AIOS_RECOVERY_CLONE",
            "-drive", "file=fat:ro:" + share.as_posix() + ",format=raw,if=none,id=testinput,readonly=on",
            "-device", "virtio-blk-pci,drive=testinput,serial=AIOS_RECOVERY_INPUT"]
        save_json(stage / "launch.json", {"schema_version": 1, "qemu_argv": command,
            "iso_sha256": ISO_SHA, "kernel_sha256": digest(args.kernel), "initramfs_sha256": digest(args.initramfs)})
        guest = None
        try:
            print("[AIOS recovery image] Preparing the separate test clone.", flush=True)
            guest = start_owned_guest(command, stage / "serial.log", vm)
            guest.wait(rb"login:\s*$", 120)
            guest.send("root")
            guest.wait(rb"localhost:~#")
            guest.process.stdin.write(b"\x1b[1;1R")
            guest.process.stdin.flush()
            # The separate preparation transcript has anchored export markers;
            # suppress the installer shell prompt before any export is emitted.
            guest.step("Recovery preparation console", "export TERM=dumb; stty -echo; PS1=''")
            guest.step("Recovery preparation network", "ip link set lo up && ip link set eth0 up && udhcpc -i eth0 -q -n -t 5 -T 3", 45)
            guest.step("Recovery preparation packages", "printf '%s\\n' https://dl-cdn.alpinelinux.org/alpine/v3.24/main > /etc/apk/repositories && apk add --no-cache python3", 180)
            guest.step("Read-only recovery inputs", 'test "$(cat /sys/class/block/vdb/serial)" = AIOS_RECOVERY_INPUT && '
                'test "$(cat /sys/class/block/vdb/ro)" = 1 && mkdir -p /mnt/source && mount -o ro /dev/vdb1 /mnt/source')
            guest.step("Mount only the owned clone", 'test "$(cat /sys/class/block/vda/serial)" = AIOS_RECOVERY_CLONE && '
                'test "$(cat /sys/class/block/vda/ro)" = 0 && test "$(cat /sys/class/block/vda/size)" = 8388608 && '
                '! grep -q "^/dev/vda" /proc/mounts && modprobe ext4 && mkdir -p /mnt/target && mount -t ext4 /dev/vda2 /mnt/target')
            guest.step("Install the bounded fault helper", "PYTHONDONTWRITEBYTECODE=1 python3 /mnt/source/image_recovery_prepare.py", 120)
            export_guest(guest, "/mnt/target/usr/share/aios-recovery-test", stage / "guest")
            export_guest(guest, "/mnt/target/usr/share/aios", stage / "installed-metadata")
            shutil.copyfile(stage / "guest/instrumentation.json", stage / "instrumentation.json")
            guest.step("Flush and unmount test clone", "sync && umount /mnt/target", 60)
            guest.send("poweroff -f")
            guest.wait(rb"reboot: Power down", 45)
            vm["shutdown_observed"] = True
            guest.process.wait(timeout=30)
        except BaseException as exc:
            vm["runner_error"] = type(exc).__name__ + ":" + str(exc)
        finally:
            if guest is not None:
                finish_owned_guest(guest, vm)
            save_json(stage / "vm-result.json", vm)
    # Preserve partial preparation evidence and fail closed without running it.
    preserved = tree_records(source) == base_files
    sidecar["source_files_preserved"] = preserved
    sidecar["prepared_image"] = disk_record(output / "system.raw")
    if (vm["process_exit_code"] == 0 and vm["shutdown_observed"] and not vm["host_killed"]
            and vm["runner_error"] is None and preserved):
        installed = stage / "installed-metadata"
        changed = record(installed / "image.json")
        require(changed["runtime_files"] == runtime and changed["image_id"] == job["image_id"], "prepared-source-drift")
        shutil.copyfile(installed / "image.json", output / "image-manifest.json")
        shutil.copyfile(installed / "installation-files/inittab.sha256", output / "installation-files/inittab.sha256")
        sidecar["guest_receipt_sha256"] = digest(stage / "instrumentation.json")
        result = {"schema_version": 1, "scenario": SCENARIO, "test_only": True,
                  "outcome": "PASS", "reasons": []}
    else:
        result["reasons"] = [vm["runner_error"] or "preparation-not-clean" if preserved else "source-artifact-changed"]
    save_json(output / "fault-instrumentation.json", sidecar)
    save_json(stage / "result.json", result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["outcome"] == "PASS" else 1


class FaultChannel:
    """A bounded serial transcript and fixed requests; no remote shell protocol."""
    def __init__(self, connection, output):
        self.connection = connection
        self.output = output
        self.raw = bytearray()
        self.pending = bytearray()
        self.requests = []
        self.completed_events = []
        self.log = (output / "fault-channel.log").open("xb")

    def receive(self, event, timeout):
        from verify_image import decode
        deadline = time.monotonic() + timeout
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "fault-channel-timeout")
            self.connection.settimeout(remaining)
            chunk = self.connection.recv(65536)
            require(chunk, "fault-channel-eof")
            self.raw.extend(chunk)
            self.pending.extend(chunk)
            self.log.write(chunk)
            self.log.flush()
            require(len(self.raw) <= CHANNEL_LIMIT, "fault-channel-size")
        line, _, remaining = self.pending.partition(b"\n")
        self.pending = bytearray(remaining)
        value = decode(bytes(line))
        require(type(value.get("schema_version")) is int and value["schema_version"] == 1
                and value.get("event") == event, "fault-channel-event:" + str(value.get("event")))
        self.completed_events.append(event)
        return value

    def send(self, action):
        request = {"schema_version": 1, "action": action}
        raw = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        self.connection.sendall(raw)
        self.requests.append(request)
        save_json(self.output / "fault-requests.json", {"schema_version": 1, "requests": self.requests})

    def drain(self, vm=None):
        """Retain the exact transport close after the already observed VM exit.

        Windows QEMU may close the UART socket with WSAECONNRESET instead of EOF.
        This one error is a close only after the full exchange and normal guest
        shutdown. Earlier resets, other socket errors and trailing bytes fail.
        """
        kind, error_code, failure = "eof", None, None
        try:
            self.connection.settimeout(10)
            while True:
                chunk = self.connection.recv(65536)
                if not chunk:
                    break
                self.raw.extend(chunk)
                self.pending.extend(chunk)
                self.log.write(chunk)
                self.log.flush()
                require(len(self.raw) <= CHANNEL_LIMIT, "fault-channel-size")
        except OSError as exc:
            error_code = getattr(exc, "winerror", None)
            if error_code is None:
                error_code = exc.errno
            if (WINDOWS_HOST and isinstance(exc, ConnectionResetError)
                    and getattr(exc, "winerror", None) == 10054):
                kind = "connection-reset"
            else:
                kind, failure = "error", exc
        except ValueError as exc:
            kind, failure = "error", exc
        if failure is None:
            try:
                require(not self.pending, "fault-channel-trailing-output")
                require(self.completed_events == ["READY", "FAULT", "COMPLETE"]
                        and self.requests == [{"schema_version": 1, "action": "inject"},
                                              {"schema_version": 1, "action": "acknowledge"}],
                        "fault-channel-incomplete-exchange")
                require(type(vm) is dict and type(vm.get("process_exit_code")) is int
                        and vm["process_exit_code"] == 0 and vm.get("shutdown_observed") is True
                        and vm.get("host_killed") is False and vm.get("runner_error") is None,
                        "fault-channel-not-normal-shutdown")
            except ValueError as exc:
                failure = exc
        save_json(self.output / "fault-channel-close.json", {
            "schema_version": 1, "outcome": "closed" if failure is None else "failed",
            "transport_close": kind, "socket_error_code": error_code,
            "pending_bytes": len(self.pending), "completed_frame_count": len(self.completed_events),
            "request_count": len(self.requests)})
        if failure is not None:
            raise failure

    def close(self):
        self.log.close()
        self.connection.close()


def run(args):
    output = args.image_dir.resolve()
    sidecar = record(output / "fault-instrumentation.json")
    require(sidecar["scenario"] == SCENARIO and sidecar["test_only"] is True
            and sidecar["source_files_preserved"] is True
            and record(output / "preparation/result.json")["outcome"] == "PASS", "prepared-test-clone-required")
    require(digest(Path(__file__)) == sidecar["runner_source_sha256"], "runner-source-changed")
    require(disk_record(output / "system.raw") == sidecar["prepared_image"], "prepared-disk-changed")
    require(not (output / "boots").exists(), "single-use-test-clone")
    source = Path(sidecar["source_directory"])
    require(tree_records(source) == sidecar["base_file_records"], "source-artifact-changed")
    boot = output / "boots/boot-01"
    boot.mkdir(parents=True, exist_ok=False)
    save_json(boot / "disk-before.json", disk_record(output / "system.raw"))
    vm = {"schema_version": 1, "process_exit_code": None, "host_killed": False,
          "shutdown_observed": False, "runner_error": None}
    save_json(boot / "vm-result.json", vm)
    save_json(output / "recovery-verdict.json", {"schema_version": 1, "outcome": "FAIL", "reasons": ["not_completed"]})
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(60)
    port = listener.getsockname()[1]
    command = base_command(args.qemu, model=True) + ["-boot", "c", "-nic", "none", "-device", "qemu-xhci",
        "-device", "usb-kbd", "-drive", "file=" + (output / "system.raw").as_posix() + ",format=raw,if=virtio",
        "-chardev", "socket,id=aiosfault,host=127.0.0.1,port=" + str(port), "-serial", "chardev:aiosfault"]
    launch = {"schema_version": 1, "qemu_argv": command, "stdin_commands": [],
              "network": "offline", "firmware": "bios", "boot_method": "disk"}
    save_json(boot / "launch.json", launch)
    save_json(boot / "fault-channel.json", {"schema_version": 1, "transport": "qemu-serial-socket",
        "host": "127.0.0.1", "port": port, "chardev_id": "aiosfault", "guest_device": "/dev/ttyS1"})
    guest, channel, connection, printed = None, None, None, 0
    try:
        print("[AIOS recovery image] Disk-only expected-fault boot, offline.", flush=True)
        guest = start_owned_guest(command, boot / "serial.log", vm)
        connection, peer = listener.accept()
        require(peer[0] == "127.0.0.1", "nonlocal-fault-peer")
        channel = FaultChannel(connection, boot)
        ready = channel.receive("READY", 150)
        require(ready["scenario"] == SCENARIO and ready["test_only"] is True
                and ready["injector_source_sha256"] == sidecar["helper_source_sha256"], "fault-helper-identity")
        save_json(boot / "fault-ready.json", ready)
        for index, line in enumerate(COMMANDS):
            guest.wait(rb"(?:^|\r?\n)aios> ", MODEL_PROMPT_TIMEOUT)
            display_image(bytes(guest.transcript[printed:guest.cursor]))
            printed = guest.cursor
            if index == 3:
                channel.send("inject")
                fault = channel.receive("FAULT", 60)
                save_json(boot / "fault.json", fault["proof"])
                channel.send("acknowledge")
                complete = channel.receive("COMPLETE", 30)
                require(complete["outcome"] == "PASS" and complete["boot_id"] == ready["boot_id"], "fault-completion")
                save_json(boot / "fault-complete.json", complete)
            launch["stdin_commands"].append(line)
            save_json(boot / "launch.json", launch)
            print(line, flush=True)
            guest.send(line)
        guest.wait(rb"(?:^|\r?\n)AIOS_IMAGE_RESULT=", 90)
        guest.wait(rb"reboot: Power down", 90)
        vm["shutdown_observed"] = True
        guest.process.wait(timeout=30)
    except BaseException as exc:
        vm["runner_error"] = type(exc).__name__ + ":" + str(exc)
    finally:
        if guest is not None:
            finish_owned_guest(guest, vm)
        if channel is not None:
            try:
                channel.drain(vm)
            except (OSError, ValueError) as exc:
                vm["runner_error"] = vm["runner_error"] or str(exc)
            finally:
                channel.close()
        elif connection is not None:
            connection.close()
        listener.close()
        save_json(boot / "vm-result.json", vm)
        save_json(boot / "disk-after.json", disk_record(output / "system.raw"))
    try:
        decode_export((boot / "serial.log").read_bytes(), boot / "archive")
        require(tree_records(source) == sidecar["base_file_records"], "source-artifact-changed")
        from verify_image_recovery import verify_image_recovery
        verdict = verify_image_recovery(output, boot)
    except (OSError, ValueError, ImportError) as exc:
        verdict = {"schema_version": 1, "outcome": "FAIL", "reasons": [str(exc)]}
    save_json(output / "recovery-verdict.json", verdict)
    print(json.dumps(verdict, indent=2), flush=True)
    return 0 if verdict["outcome"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--source-image", type=Path)
    for name in ("iso", "kernel", "initramfs"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.source_image is None:
            parser.error("prepare requires --source-image")
        return prepare(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
