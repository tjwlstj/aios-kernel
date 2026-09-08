#!/usr/bin/env python3
"""Bounded, read-only ext4 audit of a stopped installed-image v0 raw disk.

The caller must first confirm that every writer, including QEMU, has exited.
This samples the MBR and primary superblocks; it neither mounts nor repairs a
filesystem and does not replace a complete consistency check or native proof.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

SOURCES = ["https://docs.kernel.org/filesystems/ext4/super.html",
           "https://docs.kernel.org/filesystems/ext4/orphan.html"]


def crc32c(raw: bytes) -> int:
    """ext4 superblock CRC32C: initial ~0, no final XOR, exclude checksum."""
    crc = 0xFFFFFFFF
    for byte in raw:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc


def partitions(mbr: bytes, disk_bytes: int) -> list[dict]:
    if len(mbr) != 512 or mbr[510:512] != b"\x55\xaa":
        raise ValueError("mbr-signature")
    found = []
    for slot in range(4):
        raw = mbr[446 + 16 * slot:462 + 16 * slot]
        start, sectors = struct.unpack_from("<II", raw, 8)
        if raw == bytes(16):
            continue
        if (raw[0] not in (0, 0x80) or raw[4] != 0x83 or start < 1 or sectors < 4
                or (start + sectors) * 512 > disk_bytes):
            raise ValueError("partition-bounds-or-type")
        found.append({"slot": slot + 1, "mbr_entry_hex": raw.hex(), "start_lba": start,
                      "sector_count": sectors, "partition_offset_bytes": start * 512,
                      "superblock_offset_bytes": start * 512 + 1024})
    if len(found) != 2:
        raise ValueError("image-v0-partition-count")
    ordered = sorted(found, key=lambda item: item["start_lba"])
    if ordered[0]["start_lba"] + ordered[0]["sector_count"] > ordered[1]["start_lba"]:
        raise ValueError("partition-overlap")
    return found


def superblock(raw: bytes) -> dict:
    if len(raw) != 1024:
        raise ValueError("superblock-size")
    def u16(offset):
        return struct.unpack_from("<H", raw, offset)[0]
    def u32(offset):
        return struct.unpack_from("<I", raw, offset)[0]
    if u16(0x38) != 0xEF53:
        raise ValueError("ext4-magic")
    state, incompat, rocompat = u16(0x3A), u32(0x60), u32(0x64)
    computed = crc32c(raw[:0x3FC])
    reasons = []
    if state != 1:
        reasons.append("not-clean-unmounted")
    if incompat & 4:
        reasons.append("journal-recovery-needed")
    if rocompat & 0x10000:
        reasons.append("orphan-file-pending")
    if u32(0xE8):
        reasons.append("orphan-list-pending")
    if u32(0x194):
        reasons.append("recorded-filesystem-errors")
    if not rocompat & 0x400 or raw[0x175] != 1:
        reasons.append("metadata-crc32c-required")
    if u32(0x3FC) != computed:
        reasons.append("superblock-checksum")
    return {"superblock_bytes_read": len(raw), "superblock_base64": base64.b64encode(raw).decode("ascii"),
            "superblock_sha256": hashlib.sha256(raw).hexdigest(), "uuid": str(uuid.UUID(bytes=raw[0x68:0x78])),
            "s_magic": u16(0x38), "s_state": state, "clean_unmounted": bool(state & 1),
            "errors_detected": bool(state & 2), "orphan_recovery_state": bool(state & 4),
            "s_feature_compat": u32(0x5C), "s_feature_incompat": incompat,
            "needs_recovery": bool(incompat & 4), "s_feature_ro_compat": rocompat,
            "orphan_present": bool(rocompat & 0x10000), "s_last_orphan": u32(0xE8),
            "s_error_count": u32(0x194), "s_orphan_file_inum": u32(0x280), "s_mnt_count": u16(0x34),
            "s_last_mounted": raw[0x88:0xC8].split(b"\0", 1)[0].decode("ascii", errors="strict"),
            "s_checksum": u32(0x3FC), "calculated_crc32c": computed,
            "checksum_matches": u32(0x3FC) == computed,
            "raw_fields": {hex(offset): raw[offset:offset + size].hex() for offset, size in
                ((0x38, 2), (0x3A, 2), (0x5C, 4), (0x60, 4), (0x64, 4), (0xE8, 4),
                 (0x194, 4), (0x280, 4), (0x3FC, 4))},
            "outcome": "PASS" if not reasons else "FAIL", "reasons": reasons}


def audit_disk(path: Path) -> dict:
    """Read exactly 2560 bytes from a regular, stopped two-partition raw disk."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("regular-disk-required")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ValueError("disk-changed")
        mbr = stream.read(512)
        parsed = partitions(mbr, before.st_size)
        for item in parsed:
            stream.seek(item["superblock_offset_bytes"])
            item.update(superblock(stream.read(1024)))
        final_handle = os.fstat(stream.fileno())
    after = path.lstat()
    if any((item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) != (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
           for item in (final_handle, after)):
        raise ValueError("disk-changed")
    return {"schema_version": 1, "path": str(path.absolute()), "read_only": True,
            "assurance": "supporting-offline-postshutdown-ext4-superblock-audit",
            "source_only": True, "native_filesystem_proof": False, "filesystem_repair": False,
            "quiescence_requirement": "caller-confirms-all-disk-writers-exited",
            "observed_at_utc": datetime.now(timezone.utc).isoformat(), "bytes_read": 2560,
            "disk_before": {"size_bytes": before.st_size, "mtime_ns": before.st_mtime_ns},
            "disk_after": {"size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns},
            "disk_size_mtime_unchanged": True, "mbr_base64": base64.b64encode(mbr).decode("ascii"),
            "mbr_sha256": hashlib.sha256(mbr).hexdigest(), "partitions": parsed,
            "source_urls": SOURCES, "outcome": "PASS" if all(p["outcome"] == "PASS" for p in parsed) else "FAIL"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disk", type=Path)
    args = parser.parse_args()
    try:
        result = audit_disk(args.disk)
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema_version": 1, "outcome": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
