#!/usr/bin/env python3
"""Instrument only a freshly cloned, labelled disk in an installer VM.

This tool is never copied into the product runtime. It installs one fixed test
helper and one inittab entry; product source, model, config and boot hook remain
byte-for-byte unchanged. Every changed small file retains before/after evidence.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

TARGET = Path("/mnt/target")
INPUT = Path("/mnt/source")
HELPER = "/usr/local/libexec/aios-image-recovery-test.py"
INITTAB_LINE = "ttyS1::once:/usr/bin/python3 -B " + HELPER + "\n"
SCENARIO = "image-worker-recover-exit"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("ascii")


def hashed(raw):
    return hashlib.sha256(raw).hexdigest()


def trusted_parent(path):
    relative = path.relative_to(TARGET)
    current = TARGET
    for part in relative.parts[:-1]:
        current /= part
        info = current.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                and not info.st_mode & 0o022, "unsafe-target-parent")


def snapshot(name):
    path = TARGET / name.lstrip("/")
    trusted_parent(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and info.st_nlink == 1 and not info.st_mode & 0o022
                and 0 <= info.st_size <= 256 * 1024, "unsafe-target-file")
        raw = bytearray()
        while len(raw) <= 256 * 1024:
            block = os.read(descriptor, 65536)
            if not block:
                break
            raw.extend(block)
        after = os.fstat(descriptor)
        require(len(raw) == info.st_size and (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "target-file-changed")
        return {"path": name, "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode),
            "nlink": info.st_nlink, "device": info.st_dev, "inode": info.st_ino,
            "size_bytes": len(raw), "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "sha256": hashed(raw), "data_base64": base64.b64encode(raw).decode("ascii")}
    finally:
        os.close(descriptor)


def content(value):
    return base64.b64decode(value["data_base64"], validate=True)


def write(name, raw, *, new=False, mode=0o444):
    path = TARGET / name.lstrip("/")
    trusted_parent(path)
    flags = os.O_WRONLY | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if new else os.O_TRUNC)
    descriptor = os.open(path, flags, mode)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == info.st_gid == 0, "unsafe-write-file")
        pending = memoryview(raw)
        while pending:
            count = os.write(descriptor, pending)
            require(count > 0, "short-write")
            pending = pending[count:]
        if new:
            os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    require(os.getuid() == os.geteuid() == 0, "root-required")
    require(Path("/sys/class/block/vda/serial").read_text().strip() == "AIOS_RECOVERY_CLONE"
            and Path("/sys/class/block/vda/ro").read_text().strip() == "0"
            and Path("/sys/class/block/vda/size").read_text().strip() == "8388608", "owned-clone-disk")
    mount = [line.split() for line in Path("/proc/mounts").read_text().splitlines()
             if line.split()[1] == str(TARGET)]
    require(len(mount) == 1 and mount[0][0] == "/dev/vda2" and mount[0][2] == "ext4"
            and "rw" in mount[0][3].split(","), "owned-clone-mount")
    expected = json.loads((INPUT / "prepare-input.json").read_bytes())
    require(set(expected) == {"schema_version", "scenario", "base_manifest_sha256", "helper_source_sha256", "image_id"}
            and type(expected["schema_version"]) is int and expected["schema_version"] == 1
            and expected["scenario"] == SCENARIO and str(uuid.UUID(expected["image_id"])) == expected["image_id"], "prepare-input")
    names = ("/etc/inittab", "/usr/share/aios/installation-files/inittab.sha256", "/usr/share/aios/image.json")
    before = {name: snapshot(name) for name in names}
    manifest = json.loads(content(before[names[2]]))
    require(before[names[2]]["sha256"] == expected["base_manifest_sha256"]
            and manifest["profile"] == "local-model-cli" and manifest["schema_version"] == 2
            and len(manifest["runtime_files"]) == 35, "base-manifest")
    require(content(before[names[1]]) == (before[names[0]]["sha256"] + "\n").encode("ascii")
            and hashed(content(before[names[1]])) == manifest["installation_files"]["inittab.sha256"], "base-inittab")
    runtime = {name: snapshot("/opt/aios/linux/" + name)["sha256"] for name in manifest["runtime_files"]}
    require(runtime == manifest["runtime_files"], "base-runtime")
    preserved = {name: snapshot(name) for name in ("/usr/local/sbin/aios-start-system", "/etc/aios/boot.json")}
    helper_raw = (INPUT / "image_recovery_guest.py").read_bytes()
    require(0 < len(helper_raw) <= 128 * 1024 and hashed(helper_raw) == expected["helper_source_sha256"], "helper-source")
    helper_path = TARGET / HELPER.lstrip("/")
    require(not helper_path.exists() and not helper_path.is_symlink(), "test-helper-exists")
    helper_path.parent.mkdir(mode=0o755, exist_ok=True)
    trusted_parent(helper_path)
    inittab = content(before[names[0]])
    require(inittab.endswith(b"\n") and b"ttyS1" not in inittab and b"aios-image-recovery" not in inittab,
            "base-inittab-test-free")
    write(HELPER, helper_raw, new=True)
    write(names[0], inittab + INITTAB_LINE.encode("ascii"))
    write(names[1], (hashed(inittab + INITTAB_LINE.encode("ascii")) + "\n").encode("ascii"))
    base_id = manifest["image_id"]
    manifest["image_id"] = expected["image_id"]
    manifest["installation_files"]["inittab.sha256"] = snapshot(names[1])["sha256"]
    write(names[2], encoded(manifest))
    require({name: snapshot("/opt/aios/linux/" + name)["sha256"] for name in runtime} == runtime,
            "runtime-changed")
    require({name: snapshot(name) for name in preserved} == preserved, "preserved-file-changed")
    receipt = {"schema_version": 1, "scenario": SCENARIO, "test_only": True, "source_only": True,
        "base_image_id": base_id, "image_id": manifest["image_id"],
        "base_manifest_sha256": before[names[2]]["sha256"], "manifest_sha256": snapshot(names[2])["sha256"],
        "runtime_files": runtime,
        "changes": [{"path": name, "before": before[name], "after": snapshot(name)} for name in names]
                   + [{"path": HELPER, "before": None, "after": snapshot(HELPER)}],
        "preserved": preserved}
    destination = TARGET / "usr/share/aios-recovery-test"
    destination.mkdir(mode=0o755, exist_ok=False)
    write("/usr/share/aios-recovery-test/instrumentation.json", encoded(receipt), new=True)
    os.sync()
    print("AIOS_IMAGE_RECOVERY_PREPARED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
