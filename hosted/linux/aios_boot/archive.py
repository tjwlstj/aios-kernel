"""Bounded copied evidence. Root never follows a session-controlled pathname."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from . import BootError, MAX_FILES, MAX_HISTORIES, MAX_FILE_BYTES, MAX_TOTAL_BYTES


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("ascii")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def is_uuid(value) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def relative_path(value: str) -> str:
    if (not isinstance(value, str) or len(value) > 240 or len(value.split("/")) > 12
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", part) or part in (".", "..")
                   for part in value.split("/"))):
        raise BootError("unsafe-path")
    return value


def json_object(raw: bytes) -> dict:
    if len(raw) > MAX_FILE_BYTES:
        raise BootError("file-capacity")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BootError("invalid-json")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(BootError("invalid-json")))
        if not isinstance(value, dict):
            raise BootError("invalid-json")
        pending, count = [(value, 1)], 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > 16384 or depth > 20:
                raise BootError("json-complexity")
            children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
            pending.extend((child, depth + 1) for child in children)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, BootError):
            raise
        raise BootError("invalid-json") from exc


def directory_fd(path: Path, *, trusted: bool = False) -> int:
    """Pin ancestors without requiring their read permission; read the leaf.

    The root-owned live parent is searchable (0711) by the worker, but its
    directory listing is private. O_PATH preserves that boundary while dirfd
    traversal plus NOFOLLOW still rejects symlink ancestors.
    """
    path = Path(path)
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts[1:]):
        raise BootError("unsafe-path")
    handle = os.open("/", (os.O_PATH if len(path.parts) > 1 else os.O_RDONLY)
                     | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(path.parts[1:]):
            access = os.O_RDONLY if index == len(path.parts) - 2 else os.O_PATH
            next_handle = os.open(part, access | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=handle)
            os.close(handle)
            handle = next_handle
            observed = os.fstat(handle)
            if trusted and (observed.st_uid != 0 or observed.st_mode & 0o022):
                raise BootError("unsafe-owner")
        return handle
    except BaseException:
        os.close(handle)
        raise


def _stamp(item):
    return (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
            item.st_nlink, item.st_size, item.st_mtime_ns, item.st_ctime_ns)


def read_at(parent: int, name: str, *, owner: int | None = None,
            immutable: bool = False) -> bytes:
    relative_path(name)
    if "/" in name:
        raise BootError("unsafe-path")
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise BootError("unsafe-file")
    if owner is not None and before.st_uid != owner:
        raise BootError("unsafe-owner")
    if immutable and before.st_mode & 0o022:
        raise BootError("unsafe-owner")
    if before.st_size > MAX_FILE_BYTES:
        raise BootError("file-capacity")
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        opened = os.fstat(handle)
        if _stamp(before) != _stamp(opened):
            raise BootError("file-changed")
        chunks, count = [], 0
        while True:
            raw = os.read(handle, min(65536, MAX_FILE_BYTES + 1 - count))
            if not raw:
                break
            count += len(raw)
            if count > MAX_FILE_BYTES:
                raise BootError("file-capacity")
            chunks.append(raw)
        if _stamp(os.fstat(handle)) != _stamp(opened) or count != opened.st_size:
            raise BootError("file-changed")
        if _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _stamp(opened):
            raise BootError("file-changed")
        return b"".join(chunks)
    finally:
        os.close(handle)


def read_file(path: Path, *, owner: int | None = None, trusted: bool = False) -> bytes:
    handle = directory_fd(Path(path).parent, trusted=trusted)
    try:
        return read_at(handle, Path(path).name, owner=owner, immutable=trusted)
    finally:
        os.close(handle)


def snapshot_tree(path: Path, *, owner: int, immutable: bool = False,
                  skip_sockets: bool = True) -> dict[str, bytes]:
    """Copy handles, not links. Reject races, aliases, devices, FIFOs and limits."""
    root = directory_fd(path)
    result, total, nodes = {}, 0, 0
    def visit(handle, prefix):
        nonlocal total, nodes
        before = os.fstat(handle)
        if before.st_uid != owner or (immutable and before.st_mode & 0o022):
            raise BootError("unsafe-owner")
        with os.scandir(handle) as entries:
            for entry in entries:
                nodes += 1
                if nodes > MAX_FILES * 2:
                    raise BootError("file-capacity")
                name = relative_path(prefix + entry.name)
                observed = os.stat(entry.name, dir_fd=handle, follow_symlinks=False)
                if stat.S_ISSOCK(observed.st_mode) and skip_sockets:
                    continue
                if stat.S_ISDIR(observed.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=handle)
                    try:
                        if _stamp(os.fstat(child)) != _stamp(observed):
                            raise BootError("file-changed")
                        visit(child, name + "/")
                        if _stamp(os.stat(entry.name, dir_fd=handle, follow_symlinks=False)) != _stamp(observed):
                            raise BootError("file-changed")
                    finally:
                        os.close(child)
                else:
                    raw = read_at(handle, entry.name, owner=owner, immutable=immutable)
                    total += len(raw)
                    if len(result) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                        raise BootError("evidence-capacity")
                    result[name] = raw
        if _stamp(os.fstat(handle)) != _stamp(before):
            raise BootError("file-changed")
    try:
        visit(root, "")
        return dict(sorted(result.items()))
    finally:
        os.close(root)


def files_manifest(files: dict[str, bytes]) -> dict:
    if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise BootError("evidence-capacity")
    result = {}
    for name, raw in sorted(files.items()):
        relative_path(name)
        if not isinstance(raw, bytes) or len(raw) > MAX_FILE_BYTES:
            raise BootError("file-capacity")
        result[name] = {"bytes": len(raw), "sha256": digest(raw)}
    return result


def archive_files(boot_id: str, files: dict[str, bytes]) -> dict[str, bytes]:
    if not is_uuid(boot_id) or "archive-manifest.json" in files:
        raise BootError("invalid-archive")
    manifest = files_manifest(files)
    raw = encoded({"schema_version": 1, "boot_id": boot_id, "file_count": len(files),
                   "total_bytes": sum(map(len, files.values())), "files": manifest})
    complete = {**files, "archive-manifest.json": raw}
    files_manifest(complete)
    return complete


def write_at(parent: int, name: str, raw: bytes) -> None:
    relative_path(name)
    if "/" in name or len(raw) > MAX_FILE_BYTES:
        raise BootError("file-capacity")
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
    try:
        remaining = memoryview(raw)
        while remaining:
            count = os.write(handle, remaining)
            if count < 1:
                raise BootError("archive-io")
            remaining = remaining[count:]
        os.fsync(handle)
    finally:
        os.close(handle)


def write_tree(parent: Path, boot_id: str, files: dict[str, bytes]) -> Path:
    """Create once; no replacement, deletion, or user-controlled destination."""
    if not is_uuid(boot_id):
        raise BootError("invalid-boot-id")
    files_manifest(files)
    root = directory_fd(parent, trusted=True)
    archive = None
    try:
        os.mkdir(boot_id, 0o700, dir_fd=root)
        archive = os.open(boot_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        for name, raw in sorted(files.items()):
            parts, handle = name.split("/"), os.dup(archive)
            try:
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, 0o700, dir_fd=handle)
                    except FileExistsError:
                        pass
                    next_handle = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                          dir_fd=handle)
                    os.fsync(handle)
                    os.close(handle)
                    handle = next_handle
                write_at(handle, parts[-1], raw)
                os.fsync(handle)
            finally:
                os.close(handle)
        os.fsync(archive)
        os.fsync(root)
        return Path(parent) / boot_id
    finally:
        if archive is not None:
            os.close(archive)
        os.close(root)


def previous_histories(parent: Path, boot_id: str) -> dict:
    handle = directory_fd(parent, trusted=True)
    result = {}
    try:
        with os.scandir(handle) as entries:
            for entry in entries:
                if len(result) >= MAX_HISTORIES - 1:
                    raise BootError("history-capacity")
                if not is_uuid(entry.name) or entry.name == boot_id:
                    raise BootError("history-conflict")
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=handle)
                try:
                    observed = os.fstat(child)
                    if observed.st_uid != 0 or observed.st_mode & 0o077:
                        raise BootError("unsafe-owner")
                    manifest = read_at(child, "archive-manifest.json", owner=0, immutable=True)
                    final = read_at(child, "root-result.json", owner=0, immutable=True)
                    parsed = json_object(manifest)
                    if (set(parsed) != {"schema_version", "boot_id", "file_count", "total_bytes", "files"}
                            or type(parsed["schema_version"]) is not int or parsed["schema_version"] != 1
                            or parsed["boot_id"] != entry.name):
                        raise BootError("history-corrupt")
                    if json_object(final).get("boot_id") != entry.name:
                        raise BootError("history-corrupt")
                    prior_files = snapshot_tree(Path(parent) / entry.name, owner=0,
                                                immutable=True, skip_sockets=False)
                    if prior_files.pop("archive-manifest.json", None) != manifest:
                        raise BootError("history-corrupt")
                    if (type(parsed["file_count"]) is not int or parsed["file_count"] != len(prior_files)
                            or type(parsed["total_bytes"]) is not int
                            or parsed["total_bytes"] != sum(map(len, prior_files.values()))
                            or parsed["files"] != files_manifest(prior_files)
                            or prior_files.get("root-result.json") != final):
                        raise BootError("history-corrupt")
                    result[entry.name] = {"archive_manifest_sha256": digest(manifest),
                                          "root_result_sha256": digest(final)}
                finally:
                    os.close(child)
        return dict(sorted(result.items()))
    finally:
        os.close(handle)


def export_lines(boot_id: str, files: dict[str, bytes]):
    manifest = files_manifest(files)
    if "archive-manifest.json" not in files:
        raise BootError("invalid-archive")
    info = {"schema_version": 1, "boot_id": boot_id, "encoding": "json-files-base64",
            "file_count": len(files), "total_bytes": sum(map(len, files.values())),
            "manifest_sha256": digest(files["archive-manifest.json"])}
    yield "AIOS_IMAGE_EXPORT_BEGIN=" + encoded(info).decode("ascii").rstrip("\n")
    for name, raw in sorted(files.items()):
        yield "AIOS_IMAGE_EXPORT_FILE=" + encoded({"path": name, **manifest[name],
            "data_base64": base64.b64encode(raw).decode("ascii")}).decode("ascii").rstrip("\n")
    yield "AIOS_IMAGE_EXPORT_END=" + encoded(info).decode("ascii").rstrip("\n")
