#!/usr/bin/env python3
"""Prepare pinned external MAIN dependencies in an explicit cache outside Git.

Only dependency bytes and provenance are prepared. No executable is launched,
system package installed, or runtime/probe success claimed. Complete existing
caches are read and verified without network requests or receipt rewrites.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

BACKEND_REV = "486e6c5f9356eae50b851b07517bfae1f2420193"
LLAMA_REV = "c588c4f47683e73ad2d69f50480bec6cc85fd0f7"
MODEL_REV = "23749fefcc72300e3a2ad315e1317431b06b590a"
BACKEND_SHA = "55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611"
MODEL_SHA = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
ARTIFACTS = (
    {"name": "llamafile-0.10.5-thin.exe", "upstream_name": "llamafile-0.10.5-thin",
     "project": "mozilla-ai/llamafile", "version": "0.10.5", "revision": BACKEND_REV,
     "llama_cpp_revision": LLAMA_REV,
     "url": "https://github.com/mozilla-ai/llamafile/releases/download/0.10.5/llamafile-0.10.5-thin",
     "license": "Apache-2.0 project; MIT llama.cpp; bundled third-party terms apply",
     "size": 42328074, "sha256": BACKEND_SHA, "hash_source": "GitHub release asset digest",
     "local_integrity_verified": True},
    {"name": "Qwen3-0.6B-Q8_0.gguf", "upstream_name": "Qwen3-0.6B-Q8_0.gguf",
     "project": "Qwen/Qwen3-0.6B-GGUF", "version": "Qwen3-0.6B-Q8_0", "revision": MODEL_REV,
     "url": f"https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/{MODEL_REV}/Qwen3-0.6B-Q8_0.gguf",
     "license": "Apache-2.0", "size": 639446688, "sha256": MODEL_SHA,
     "hash_source": "Hugging Face exact-revision tree LFS oid", "local_integrity_verified": True},
)
# API metadata contains changing download/like counters. It is pinned by its
# release/revision/asset semantics, then its exact captured bytes are hashed in
# the receipt. Immutable documents have independent fixed byte hashes as well.
SOURCES = (
    ("llamafile-release.json", "https://api.github.com/repos/mozilla-ai/llamafile/releases/tags/0.10.5", None, None),
    ("llamafile-tag.json", "https://api.github.com/repos/mozilla-ai/llamafile/git/ref/tags/0.10.5", None, None),
    ("llamafile-tree.json", f"https://api.github.com/repos/mozilla-ai/llamafile/git/trees/{BACKEND_REV}", None, None),
    ("llamafile-LICENSE.txt", f"https://raw.githubusercontent.com/mozilla-ai/llamafile/{BACKEND_REV}/LICENSE", 583,
     "df7b68d51e439946dd34ec5c9466f51659fba68940ce7c804d6079ef4fd4b13f"),
    ("llama.cpp-LICENSE.txt", f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{LLAMA_REV}/LICENSE", 1078,
     "94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d"),
    ("llamafile-quickstart.md", f"https://raw.githubusercontent.com/mozilla-ai/llamafile/{BACKEND_REV}/docs/quickstart.md", 12537,
     "f04ccc51180e96a1c01dcd827b59f17c6be43b85426e66d4dc8ce6c96ea07983"),
    ("llama.cpp-server-README.md", f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{LLAMA_REV}/tools/server/README.md", 98895,
     "bdf35ef84d5d3f61effa071d56b35ded3413de1319f719f96691ea87c5aa5054"),
    ("qwen-gguf-model.json", f"https://huggingface.co/api/models/Qwen/Qwen3-0.6B-GGUF/revision/{MODEL_REV}", None, None),
    ("qwen-gguf-tree.json", f"https://huggingface.co/api/models/Qwen/Qwen3-0.6B-GGUF/tree/{MODEL_REV}", None, None),
    ("qwen-LICENSE.txt", f"https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/{MODEL_REV}/LICENSE", 11544,
     "5de36594c10839788a8c589443a8ef9d8b8d17c65a1b5807206ae037fc36c6bd"),
    ("qwen-README.md", f"https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/{MODEL_REV}/README.md", 6236,
     "44adabae2153c3a7bb741b86fbffd48736bc95118831935fdfc4d3cc1854f2f5"),
)
RECEIPT_KEYS = {"schema_version", "prepared_at", "purpose", "repository_import", "repository_manifest_changed",
    "host_global_install", "redistribution_approved", "dependency_license_review", "artifacts",
    "source_receipts", "windows_probe", "linux_probe"}
MAX_METADATA = 2 * 1024 * 1024
CHUNK = 1024 * 1024


class PreparationError(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PreparationError(reason)


def same(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(same(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    return left == right


def decode(raw: bytes):
    require(len(raw) <= MAX_METADATA, "metadata-size")
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "metadata-duplicate-key")
            result[key] = value
        return result
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(PreparationError("metadata-nonfinite")))
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(count <= 20000 and depth <= 16, "metadata-complexity")
        children = item.values() if type(item) is dict else item if type(item) is list else ()
        pending.extend((child, depth + 1) for child in children)
    return value


def regular_info(path: Path, *, missing: bool = False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise
    require(stat.S_ISREG(info.st_mode) and not info.st_file_attributes & 0x400
            if hasattr(info, "st_file_attributes") else stat.S_ISREG(info.st_mode), "unsafe-cache-file:" + path.name)
    return info


def read_small(path: Path) -> bytes:
    regular_info(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_METADATA + 1)
    require(len(raw) <= MAX_METADATA, "metadata-size:" + path.name)
    return raw


def verify_file(path: Path, size: int | None, sha: str | None, *, maximum: int) -> tuple[int, str]:
    info = regular_info(path)
    require(0 < info.st_size <= maximum and (size is None or info.st_size == size), "size-mismatch:" + path.name)
    result, total = hashlib.sha256(), 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), "cache-file-changed:" + path.name)
        while block := stream.read(CHUNK):
            total += len(block)
            require(total <= maximum, "size-mismatch:" + path.name)
            result.update(block)
        final = os.fstat(stream.fileno())
    require(total == info.st_size and (final.st_size, final.st_mtime_ns) == (info.st_size, info.st_mtime_ns),
            "cache-file-changed:" + path.name)
    actual = result.hexdigest()
    require(sha is None or actual == sha, "hash-mismatch:" + path.name)
    return total, actual


def prepare_directory(path: Path) -> Path:
    path = Path(os.path.abspath(path.expanduser()))
    repo = Path(__file__).resolve().parents[2]
    require(not path.resolve().is_relative_to(repo) and path != Path(path.anchor), "cache-must-be-outside-repository")
    for parent in (*reversed(path.parents), path):
        if parent.exists():
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                    "unsafe-cache-directory")
            require(not (parent / ".git").exists(), "cache-must-be-outside-repository")
        else:
            parent.mkdir(mode=0o700)
    return path


def secure_url(url: str) -> None:
    address = urlsplit(url)
    require(address.scheme == "https" and bool(address.hostname) and address.username is None
            and address.password is None and not address.fragment, "https-required")


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        secure_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def default_opener():
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    return build_opener(ProxyHandler({}), HTTPSHandler(context=context), HTTPSRedirects())


def install_file(path: Path, url: str, *, size: int | None = None, sha: str | None = None,
                 maximum: int = MAX_METADATA, validate=None, opener=None) -> tuple[int, str, bool]:
    """Publish only complete validated bytes; never replace an existing path."""
    secure_url(url)
    if regular_info(path, missing=True) is not None:
        actual_size, actual_hash = verify_file(path, size, sha, maximum=maximum)
        if validate is not None:
            validate(read_small(path))
        return actual_size, actual_hash, False
    temporary = path.with_name("." + path.name + "." + str(uuid.uuid4()) + ".part")
    total, digest = 0, hashlib.sha256()
    started, last_report = time.monotonic(), time.monotonic()
    try:
        with temporary.open("xb") as stream:
            request = Request(url, headers={"User-Agent": "AIOS-local-inference-preparer/1",
                                           "Accept-Encoding": "identity"})
            with (opener or default_opener()).open(request, timeout=45) as response:
                secure_url(response.geturl())
                require(response.status == 200 and response.headers.get("Content-Encoding", "identity") == "identity", "download-response")
                length = response.headers.get("Content-Length")
                if length is not None:
                    require(length.isdecimal() and 0 < int(length) <= maximum
                            and (size is None or int(length) == size), "download-content-length")
                while block := response.read(CHUNK):
                    require(time.monotonic() - started <= 3600, "download-time-limit")
                    total += len(block)
                    require(total <= maximum and (size is None or total <= size), "download-size-limit")
                    stream.write(block)
                    digest.update(block)
                    if time.monotonic() - last_report >= 30:
                        print(f"AIOS download: {path.name}: {total} bytes", file=sys.stderr, flush=True)
                        last_report = time.monotonic()
                require(total > 0 and (size is None or total == size)
                        and (length is None or total == int(length)), "download-truncated")
            stream.flush()
            os.fsync(stream.fileno())
        actual_hash = digest.hexdigest()
        require(sha is None or actual_hash == sha, "download-hash-mismatch:" + path.name)
        if validate is not None:
            validate(read_small(temporary))
        try:
            # A hard-link publishes an already closed complete file atomically
            # while retaining create-only semantics on both Windows and Linux.
            os.link(temporary, path)
        except FileExistsError:
            actual_size, actual_hash = verify_file(path, size, sha, maximum=maximum)
            if validate is not None:
                validate(read_small(path))
            return actual_size, actual_hash, False
        return total, actual_hash, True
    finally:
        temporary.unlink(missing_ok=True)


def metadata_validator(name: str):
    def validate(raw: bytes):
        value = decode(raw)
        if name == "llamafile-release.json":
            require(type(value) is dict and value.get("tag_name") == "0.10.5" and value.get("draft") is False
                    and value.get("prerelease") is False and type(value.get("assets")) is list, "release-metadata")
            matching = [row for row in value["assets"] if type(row) is dict and row.get("name") == ARTIFACTS[0]["upstream_name"]]
            require(len(matching) == 1, "release-asset-count")
            asset = matching[0]
            require(asset.get("digest") == "sha256:" + BACKEND_SHA and same(asset.get("size"), ARTIFACTS[0]["size"])
                    and asset.get("browser_download_url") == ARTIFACTS[0]["url"] and asset.get("state") == "uploaded", "release-asset-pin")
        elif name == "llamafile-tag.json":
            require(type(value) is dict and value.get("ref") == "refs/tags/0.10.5"
                    and type(value.get("object")) is dict and value["object"].get("type") == "commit"
                    and value["object"].get("sha") == BACKEND_REV, "release-tag-pin")
        elif name == "llamafile-tree.json":
            require(type(value) is dict and value.get("sha") == BACKEND_REV and value.get("truncated") is False
                    and type(value.get("tree")) is list, "backend-tree-pin")
            submodule = [row for row in value["tree"] if type(row) is dict and row.get("path") == "llama.cpp"]
            require(len(submodule) == 1 and submodule[0].get("type") == "commit"
                    and submodule[0].get("mode") == "160000" and submodule[0].get("sha") == LLAMA_REV, "llama-submodule-pin")
        elif name == "qwen-gguf-model.json":
            require(type(value) is dict and value.get("id") == "Qwen/Qwen3-0.6B-GGUF" and value.get("sha") == MODEL_REV
                    and value.get("private") is False and value.get("disabled") is False and value.get("gated") is False
                    and type(value.get("cardData")) is dict and value["cardData"].get("license") == "apache-2.0", "model-revision-pin")
        elif name == "qwen-gguf-tree.json":
            require(type(value) is list, "model-tree-type")
            matching = [row for row in value if type(row) is dict and row.get("path") == ARTIFACTS[1]["name"]]
            require(len(matching) == 1, "model-tree-count")
            row = matching[0]
            require(row.get("type") == "file" and same(row.get("size"), ARTIFACTS[1]["size"])
                    and type(row.get("lfs")) is dict and row["lfs"].get("oid") == MODEL_SHA
                    and same(row["lfs"].get("size"), ARTIFACTS[1]["size"]), "model-lfs-pin")
        else:
            raise PreparationError("unknown-metadata")
    return validate


def validate_receipt(value: dict) -> dict:
    require(type(value) is dict and set(value) == RECEIPT_KEYS, "existing-receipt-schema")
    require(same(value["schema_version"], 1) and same(value["artifacts"], list(ARTIFACTS))
            and all(value[name] is False for name in ("repository_import", "repository_manifest_changed",
                                                     "host_global_install", "redistribution_approved")), "existing-receipt-pin")
    for name in ("purpose", "dependency_license_review", "windows_probe", "linux_probe", "prepared_at"):
        require(type(value[name]) is str and 0 < len(value[name]) <= 512, "existing-receipt-text")
    moment = datetime.fromisoformat(value["prepared_at"])
    require(moment.tzinfo is not None and moment.utcoffset() == timezone.utc.utcoffset(moment), "existing-receipt-time")
    rows = value["source_receipts"]
    require(type(rows) is list and len(rows) == len(SOURCES), "existing-receipt-sources")
    result = {}
    planned = {"provenance/" + name: (url, size, sha) for name, url, size, sha in SOURCES}
    for row in rows:
        require(type(row) is dict and set(row) == {"path", "url", "size", "sha256"}, "existing-source-schema")
        name = row["path"]
        require(type(name) is str and name in planned and name not in result, "existing-source-path")
        url, size, sha = planned[name]
        require(row["url"] == url and type(row["size"]) is int and 0 < row["size"] <= MAX_METADATA
                and type(row["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None
                and (size is None or row["size"] == size) and (sha is None or row["sha256"] == sha), "existing-source-pin")
        result[name] = row
    return result


def prepare(cache: Path) -> dict:
    cache = prepare_directory(cache)
    receipt_path = cache / "provenance-receipt.json"
    existing = decode(read_small(receipt_path)) if regular_info(receipt_path, missing=True) is not None else None
    recorded = validate_receipt(existing) if existing is not None else {}
    prepare_directory(cache / "provenance")
    downloaded, reused, receipts = [], [], []
    for name, url, size, sha in SOURCES:
        relative = "provenance/" + name
        if relative in recorded:
            size, sha = recorded[relative]["size"], recorded[relative]["sha256"]
        print("AIOS provenance: " + name, file=sys.stderr, flush=True)
        length, checksum, fresh = install_file(cache / relative, url, size=size, sha=sha,
                        validate=metadata_validator(name) if name.endswith(".json") else None)
        (downloaded if fresh else reused).append(relative)
        receipts.append({"path": relative, "url": url, "size": length, "sha256": checksum})
    for artifact in ARTIFACTS:
        print("AIOS dependency: " + artifact["name"], file=sys.stderr, flush=True)
        _length, _checksum, fresh = install_file(cache / artifact["name"], artifact["url"],
                        size=artifact["size"], sha=artifact["sha256"], maximum=artifact["size"])
        (downloaded if fresh else reused).append(artifact["name"])
    if existing is None:
        value = {"schema_version": 1, "prepared_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "AIOS external local-development inference artifact preparation",
            "repository_import": False, "repository_manifest_changed": False, "host_global_install": False,
            "redistribution_approved": False,
            "dependency_license_review": "Local execution dependency only; not repository code import or distribution approval",
            "artifacts": list(ARTIFACTS), "source_receipts": receipts, "windows_probe": "NOT_RUN", "linux_probe": "NOT_RUN"}
        validate_receipt(value)
        raw = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        temporary = cache / (".provenance-receipt." + str(uuid.uuid4()) + ".part")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, receipt_path)
            except FileExistsError:
                # Another preparation may have completed with the same bytes
                # under a different timestamp. Validate it; never replace it.
                raced = validate_receipt(decode(read_small(receipt_path)))
                require(same(raced, {row["path"]: row for row in receipts}), "concurrent-receipt-mismatch")
        finally:
            temporary.unlink(missing_ok=True)
    return {"schema_version": 1, "outcome": "READY", "scope": "dependency-artifacts-only", "cache": str(cache),
            "provenance": str(receipt_path), "provenance_sha256": hashlib.sha256(read_small(receipt_path)).hexdigest(),
            "downloaded": downloaded, "reused": reused, "runtime_verification": "NOT_RUN"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True, help="External local cache directory; Git repositories are refused.")
    args = parser.parse_args()
    try:
        result = prepare(args.cache)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, http.client.HTTPException) as exc:
        reason = str(exc) if isinstance(exc, PreparationError) else type(exc).__name__
        print(json.dumps({"schema_version": 1, "outcome": "FAIL", "error": reason}), flush=True)
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
