"""Root boot/archive boundary and unprivileged, interactive hosted session.

Only the root half creates the boot directory, drops worker credentials, copies
bounded evidence and invokes one fixed poweroff command. Product service control
and all commands run in the UID 1000 worker. No persisted binding is restored.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import BootError, SESSION_UID, SESSION_GID, MAX_FILE_BYTES
from .archive import (archive_files, digest, directory_fd, encoded, export_lines,
                      is_uuid, json_object, previous_histories, read_file,
                      relative_path, snapshot_tree, write_at, write_tree)

RUNTIME_ROOT = Path("/opt/aios/linux")
ENTRY = RUNTIME_ROOT / "aios-image-boot.py"
LIVE_PARENT = Path("/run/aios/boots")
HISTORY_PARENT = Path("/var/lib/aios/history")
CONFIG_PATH = Path("/etc/aios/boot.json")
IMAGE_PATH = Path("/usr/share/aios/image.json")
INSTALLATION_ROOT = Path("/usr/share/aios/installation-files")
MODEL_CONFIG_PATH = Path("/etc/aios/model.json")
INFERENCE_ROOT = Path("/opt/aios/inference")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
# BusyBox init must run the installed OpenRC shutdown actions, including local
# filesystem unmount and the final root read-only remount. No forced fallback.
POWEROFF = ("/sbin/poweroff",)
CONFIG_KEYS = {"schema_version", "profile", "model_config"}
IMAGE_KEYS = {"schema_version", "image_id", "profile", "substrate", "iso", "runtime_files",
              "boot_config_sha256", "installation_files", "source_only", "repository_import",
              "redistribution_approved"}
INSTALLATION_FILES = {"packages.txt", "kernel.txt", "installed-runtime.json",
                      "kernel-image.sha256", "initramfs.sha256", "bootloader-config.sha256",
                      "inittab.sha256", "boot-hook.sha256"}
PROVENANCE_FILES = {"provenance/" + name for name in (
    "llamafile-release.json", "llamafile-tag.json", "llamafile-tree.json",
    "llamafile-LICENSE.txt", "llama.cpp-LICENSE.txt", "llamafile-quickstart.md",
    "llama.cpp-server-README.md", "qwen-gguf-model.json", "qwen-gguf-tree.json",
    "qwen-LICENSE.txt", "qwen-README.md")}
MODEL_METADATA_FILES = {"model-config.json", "inference-provenance.json",
                        "model-integrity.json"} | PROVENANCE_FILES
SESSION_RESULT_KEYS = {"schema_version", "boot_id", "uid", "euid", "gid", "egid",
                       "session_id", "session_exit_code", "error", "cleanup", "cleanup_ok",
                       "completed_monotonic_ns"}
RESERVED = {"boot.json", "boot-config.json", "image.json", "root-result.json",
            "poweroff.json", "archive-manifest.json", "model-config.json",
            "inference-provenance.json", "model-integrity.json", "provenance"}


def current_boot_id() -> str:
    with BOOT_ID_PATH.open("r", encoding="ascii") as stream:
        boot_id = stream.read(65).strip()
    if not is_uuid(boot_id):
        raise BootError("invalid-boot-id")
    return boot_id


def validate_config(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != CONFIG_KEYS
            or type(value["schema_version"]) is not int):
        raise BootError("invalid-config")
    if ((value["schema_version"], value["profile"], value["model_config"])
            not in ((1, "basic", None), (2, "local-model", MODEL_CONFIG_PATH.as_posix()))):
        raise BootError("unsupported-profile")
    return value


def _hash_map(value) -> dict:
    if not isinstance(value, dict) or not value or len(value) > 512:
        raise BootError("invalid-image")
    for name, sha in value.items():
        relative_path(name)
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise BootError("invalid-image")
    return value


def validate_image(value: dict) -> dict:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise BootError("invalid-image")
    model = value["schema_version"] == 2
    if (set(value) != IMAGE_KEYS | ({"model_bundle"} if model else set())
            or value["schema_version"] not in (1, 2)
            or not is_uuid(value["image_id"]) or value["profile"] != ("local-model-cli" if model else "basic-cli")
            or value["substrate"] != "linux-hosted" or value["source_only"] is not True
            or value["repository_import"] is not False or value["redistribution_approved"] is not False
            or not isinstance(value["boot_config_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["boot_config_sha256"])):
        raise BootError("invalid-image")
    iso = value["iso"]
    if (not isinstance(iso, dict) or set(iso) != {"url", "sha256", "version"}
            or not isinstance(iso["url"], str) or not iso["url"].startswith("https://")
            or len(iso["url"]) > 2048 or not isinstance(iso["version"], str)
            or not 1 <= len(iso["version"]) <= 64 or not isinstance(iso["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", iso["sha256"])):
        raise BootError("invalid-image")
    _hash_map(value["runtime_files"])
    _hash_map(value["installation_files"])
    if set(value["installation_files"]) != INSTALLATION_FILES | (MODEL_METADATA_FILES if model else set()):
        raise BootError("invalid-image")
    if ("aios-image-boot.py" not in value["runtime_files"]
            or any(not name.endswith(".py") for name in value["runtime_files"])):
        raise BootError("invalid-image")
    if model:
        validate_model_bundle(value["model_bundle"])
    return value


def pinned_model_files() -> dict:
    # Reuse the existing product profile, not a tool-side model launcher.
    from aios_backend import BACKEND_NAME, BACKEND_SHA, MODEL_NAME, MODEL_BYTES, MODEL_SHA
    return {BACKEND_NAME: {"size_bytes": 42328074, "sha256": BACKEND_SHA},
            MODEL_NAME: {"size_bytes": MODEL_BYTES, "sha256": MODEL_SHA}}


def validate_model_bundle(value: dict) -> dict:
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "config_sha256", "provenance_sha256", "files"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or any(not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key])
                   for key in ("config_sha256", "provenance_sha256"))
            or not isinstance(value["files"], dict) or value["files"] != pinned_model_files()):
        raise BootError("invalid-model-bundle")
    for row in value["files"].values():
        if type(row["size_bytes"]) is not int:
            raise BootError("invalid-model-bundle")
    return value


def model_file_records(bundle: dict) -> dict:
    return {name: {"path": (INFERENCE_ROOT / name).as_posix(), **row,
                   "uid": 0, "gid": 0, "mode": 0o444}
            for name, row in bundle["files"].items()}


def validate_model_metadata(bundle: dict, files: dict[str, bytes], config_raw: bytes) -> None:
    """Bind small installed receipts to the fixed model profile and source bytes."""
    from aios_backend import BACKEND_NAME, BACKEND_SHA, MODEL_NAME, MODEL_SHA, MODEL_ID
    from aios_agent.inference import validate_config as validate_agent_config
    if (set(files) != MODEL_METADATA_FILES or files["model-config.json"] != config_raw
            or digest(config_raw) != bundle["config_sha256"]
            or digest(files["inference-provenance.json"]) != bundle["provenance_sha256"]):
        raise BootError("model-metadata-hash")
    if len(config_raw) > 16384:
        raise BootError("invalid-model-config")
    config = json_object(config_raw)
    expected = {"schema_version": 1, "endpoint": "http://127.0.0.1:18081", "model_id": MODEL_ID,
                "model_path": (INFERENCE_ROOT / MODEL_NAME).as_posix(), "model_sha256": MODEL_SHA,
                "backend_path": (INFERENCE_ROOT / BACKEND_NAME).as_posix(), "backend_sha256": BACKEND_SHA,
                "provenance_sha256": bundle["provenance_sha256"]}
    try:
        validate_agent_config(config)
    except ValueError as exc:
        raise BootError("invalid-model-config") from exc
    if config != expected:
        raise BootError("invalid-model-config")
    installed = json_object(files["model-integrity.json"])
    expected_receipt = {"schema_version": 1, "verification": "installed-read-complete",
                        "files": model_file_records(bundle)}
    # Canonical JSON comparison also distinguishes bool from integer metadata.
    if encoded(installed) != encoded(expected_receipt):
        raise BootError("model-installation-integrity")
    provenance = json_object(files["inference-provenance.json"])
    if (type(provenance.get("schema_version")) is not int or provenance["schema_version"] != 1
            or any(provenance.get(key) is not False for key in ("repository_import",
                "repository_manifest_changed", "host_global_install", "redistribution_approved"))):
        raise BootError("model-provenance")
    artifacts = provenance.get("artifacts")
    if (not isinstance(artifacts, list) or len(artifacts) != 2
            or any(not isinstance(row, dict) for row in artifacts)
            or {row.get("name") for row in artifacts if isinstance(row.get("name"), str)} != set(bundle["files"])):
        raise BootError("model-provenance")
    for row in artifacts:
        expected_file = bundle["files"][row["name"]]
        if (type(row.get("size")) is not int or row["size"] != expected_file["size_bytes"]
                or row.get("sha256") != expected_file["sha256"] or row.get("local_integrity_verified") is not True):
            raise BootError("model-provenance")
    rows = provenance.get("source_receipts")
    if not isinstance(rows, list) or len(rows) != len(PROVENANCE_FILES):
        raise BootError("model-provenance")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"path", "url", "size", "sha256"}
                or not isinstance(row["path"], str) or row["path"] not in PROVENANCE_FILES
                or row["path"] in seen or not isinstance(row["url"], str)
                or not row["url"].startswith("https://") or len(row["url"]) > 2048
                or type(row["size"]) is not int or row["size"] != len(files[row["path"]])
                or row["sha256"] != digest(files[row["path"]])):
            raise BootError("model-provenance")
        seen.add(row["path"])


def stream_model_file(path: Path, expected: dict) -> dict:
    """Read a fixed-size, pinned asset without adding its bytes to the archive.

    The caller supplies only the installed profile's fixed config/asset paths.
    The helper is separately testable with fixture bytes; it never opens a writer.
    """
    if (type(expected.get("size_bytes")) is not int or not 0 < expected["size_bytes"] <= 1024 ** 3
            or not isinstance(expected.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"])):
        raise BootError("invalid-model-bundle")
    parent = directory_fd(path.parent, trusted=True)
    handle = None
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    try:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise BootError("unsafe-model-file")
        if (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) != (0, 0, 0o444):
            raise BootError("unsafe-model-owner")
        if before.st_size != expected["size_bytes"]:
            raise BootError("model-size")
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        opened = os.fstat(handle)
        if stamp(opened) != stamp(before):
            raise BootError("model-changed")
        result, count = hashlib.sha256(), 0
        while True:
            raw = os.read(handle, min(65536, expected["size_bytes"] + 1 - count))
            if not raw:
                break
            count += len(raw)
            if count > expected["size_bytes"]:
                raise BootError("model-size")
            result.update(raw)
        if (count != expected["size_bytes"] or stamp(os.fstat(handle)) != stamp(opened)
                or stamp(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != stamp(opened)):
            raise BootError("model-changed")
        if result.hexdigest() != expected["sha256"]:
            raise BootError("model-hash")
        return {"path": path.as_posix(), "size_bytes": count, "sha256": result.hexdigest(),
                "uid": opened.st_uid, "gid": opened.st_gid, "mode": stat.S_IMODE(opened.st_mode)}
    finally:
        if handle is not None:
            os.close(handle)
        os.close(parent)


def installed_metadata(boot_id: str) -> tuple[bytes, bytes, dict, dict, dict | None]:
    if not is_uuid(boot_id):
        raise BootError("invalid-boot-id")
    config_raw = read_file(CONFIG_PATH, owner=0, trusted=True)
    config = validate_config(json_object(config_raw))
    image_raw = read_file(IMAGE_PATH, owner=0, trusted=True)
    manifest = validate_image(json_object(image_raw))
    if digest(config_raw) != manifest["boot_config_sha256"]:
        raise BootError("config-hash")
    if config["schema_version"] != manifest["schema_version"]:
        raise BootError("profile-mismatch")
    actual_runtime = snapshot_tree(RUNTIME_ROOT, owner=0, immutable=True, skip_sockets=False)
    actual_installation = snapshot_tree(INSTALLATION_ROOT, owner=0, immutable=True, skip_sockets=False)
    if {name: digest(raw) for name, raw in actual_runtime.items()} != manifest["runtime_files"]:
        raise BootError("runtime-hash")
    if {name: digest(raw) for name, raw in actual_installation.items()} != manifest["installation_files"]:
        raise BootError("installation-hash")
    if json_object(actual_installation["installed-runtime.json"]) != manifest["runtime_files"]:
        raise BootError("installation-hash")
    model_files, integrity = {}, None
    if config["profile"] == "local-model":
        model_files = {name: actual_installation[name] for name in MODEL_METADATA_FILES}
        model_raw = read_file(MODEL_CONFIG_PATH, owner=0, trusted=True)
        # Config is small but receives the same exact immutable ownership check.
        stream_model_file(MODEL_CONFIG_PATH, {"size_bytes": len(model_raw), "sha256": digest(model_raw)})
        validate_model_metadata(manifest["model_bundle"], model_files, model_raw)
        observed = {name: stream_model_file(INFERENCE_ROOT / name, row)
                    for name, row in manifest["model_bundle"]["files"].items()}
        integrity = {"schema_version": 1, "boot_id": boot_id,
                     "verification": "boot-read-complete", "files": observed}
    return config_raw, image_raw, manifest, model_files, integrity


def ensure_root_directory(path: Path, *, leaf_mode: int = 0o755) -> None:
    """Only missing fixed, root-owned directory components may be created."""
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts[1:]):
        raise BootError("unsafe-path")
    handle = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(path.parts[1:]):
            desired_mode = leaf_mode if index == len(path.parts) - 2 else 0o755
            created = False
            try:
                os.mkdir(part, desired_mode, dir_fd=handle)
                created = True
            except FileExistsError:
                pass
            next_handle = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handle)
            os.close(handle)
            handle = next_handle
            if created:
                os.fchmod(handle, desired_mode)
            observed = os.fstat(handle)
            if observed.st_uid != 0 or observed.st_mode & 0o022:
                raise BootError("unsafe-owner")
    finally:
        os.close(handle)


def create_live_directory(parent: Path, boot_id: str) -> Path:
    if not is_uuid(boot_id):
        raise BootError("invalid-boot-id")
    handle = directory_fd(parent, trusted=True)
    child = None
    try:
        try:
            os.mkdir(boot_id, 0o700, dir_fd=handle)
        except FileExistsError as exc:
            raise BootError("live-state-exists") from exc
        child = os.open(boot_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=handle)
        os.fchown(child, SESSION_UID, SESSION_GID)
        os.fchmod(child, 0o700)
        os.fsync(handle)
        return parent / boot_id
    finally:
        if child is not None:
            os.close(child)
        os.close(handle)


def session_credentials() -> dict:
    value = {"uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "egid": os.getegid()}
    if value != {"uid": SESSION_UID, "euid": SESSION_UID, "gid": SESSION_GID, "egid": SESSION_GID}:
        raise BootError("session-credentials")
    if os.getgroups():
        raise BootError("session-groups")
    return value


def _cleanup_succeeded(name: str, response: dict) -> bool:
    if (not isinstance(response, dict) or response.get("outcome") != "OK" or response.get("error") is not None):
        return False
    if response.get("state") in ("ABSENT", "STOPPED"):
        return True
    if name == "MODEL_BACKEND" and response.get("state") == "RECOVERED":
        from aios_backend.protocol import validate_reply
        try:
            validate_reply(response, "stop")
            return True
        except (ValueError, TypeError, KeyError):
            return False
    return False


def _cleanup(live: Path) -> tuple[list, bool]:
    from aios_agent.client import control as agent_control
    from aios_backend.client import control as backend_control
    from aios_service.client import control as service_control
    rows = []
    for name, subdir, control in (("MAIN", "main", agent_control),
                                  ("MODEL_BACKEND", "backend", backend_control),
                                  ("CONSOLE_RUNTIME", "service", service_control)):
        try:
            response = control(live / subdir, "stop")
            # Backend control has independently reopened its explicit recovery
            # receipt. Preserve RECOVERED in this response; it is never STOPPED.
            success = _cleanup_succeeded(name, response)
            code = None if success else response.get("error") or "cleanup-incomplete"
            rows.append({"service": name, "action": "stop", "outcome": "OK" if success else "ERROR",
                         "error": code, "response": response})
        except Exception:
            rows.append({"service": name, "action": "stop", "outcome": "ERROR",
                         "error": "cleanup-exception", "response": None})
    return rows, all(row["outcome"] == "OK" for row in rows)


def worker_cli_argv(live: Path, boot_config: dict) -> list[str]:
    config = validate_config(boot_config)
    argv = [str(ENTRY), "--artifact-dir", str(live / "session"),
            "--service-dir", str(live / "service"), "--agent-dir", str(live / "main"),
            "--backend-dir", str(live / "backend")]
    if config["profile"] == "local-model":
        argv += ["--agent-config", MODEL_CONFIG_PATH.as_posix()]
    return argv


def run_worker(live: Path, boot_id: str, *, boot_config: dict) -> int:
    """Internal testable worker; the public entry always supplies fixed paths."""
    credentials = session_credentials()
    if not is_uuid(boot_id) or live.name != boot_id:
        raise BootError("invalid-boot-id")
    root = directory_fd(live)
    try:
        observed = os.fstat(root)
        if observed.st_uid != SESSION_UID or stat.S_IMODE(observed.st_mode) != 0o700:
            raise BootError("unsafe-owner")
        if os.listdir(root):
            raise BootError("live-state-exists")
        # No client state is pre-populated: every boot starts absent/unbound.
        from aios_console import shell
        prior_argv, error, exit_code = sys.argv, None, 1
        sys.argv = worker_cli_argv(live, boot_config)
        try:
            exit_code = shell.main()
            if type(exit_code) is not int or exit_code not in (0, 1, 2, 3, 4):
                exit_code, error = 1, "session-exit"
        except BaseException:
            exit_code, error = 1, "session-exception"
        finally:
            sys.argv = prior_argv
            cleanup, cleanup_ok = _cleanup(live)
        session_id = None
        try:
            session = json_object(read_file(live / "session" / "session-result.json", owner=SESSION_UID))
            if not is_uuid(session.get("session_id")) or session.get("exit_code") != exit_code:
                raise BootError("session-result")
            session_id = session["session_id"]
        except (OSError, BootError):
            error = error or "session-result"
        value = {"schema_version": 1, "boot_id": boot_id, **credentials,
                 "session_id": session_id, "session_exit_code": exit_code, "error": error,
                 "cleanup": cleanup, "cleanup_ok": cleanup_ok, "completed_monotonic_ns": time.monotonic_ns()}
        write_at(root, "worker-result.json", encoded(value))
        os.fsync(root)
        return 0 if exit_code == 0 and error is None and cleanup_ok else 1
    finally:
        os.close(root)


def _validate_recovered_cleanup(response: dict, boot_id: str, completed_ns: int,
                                evidence: dict | None, worker_pid: int | None) -> None:
    """Root joins the copied receipt to the exact UID 1000 worker it reaped."""
    from aios_backend import client as backend
    try:
        if type(evidence) is not dict or type(worker_pid) is not int or worker_pid <= 0:
            raise BootError("worker-recovery")
        record = response["service_record"]
        instance = record["instance_id"]
        prefix = "backend/runs/" + instance + "/"
        identity = json_object(evidence["backend/registry.json"])
        latest = json_object(evidence["backend/latest.json"])
        backend.protocol.validate_reply(latest, "status")
        if (set(identity) != {"schema_version", *backend.IDENTITY_KEYS}
                or type(identity["schema_version"]) is not int or identity["schema_version"] != 1):
            raise BootError("worker-recovery")
        config = json_object(evidence[prefix + "config.json"])
        receipt = json_object(evidence["backend/recoveries/" + instance + ".json"])
        backend.validate_recovery(receipt, identity, latest, config)
        if (receipt["outcome"] != "RECOVERED"
                or receipt["lease"]["owner"]["host_boot_id"] != boot_id
                or receipt["lease"]["owner"]["process_id"] != worker_pid
                or receipt["lease"]["owner"]["uid"] != SESSION_UID
                or receipt["recovery"]["completed_monotonic_ns"] > completed_ns
                or encoded(response) != encoded(backend._recovered_reply("stop", receipt))):
            raise BootError("worker-recovery")
        actual = {name[len(prefix):]: digest(raw) for name, raw in evidence.items() if name.startswith(prefix)}
        start = json_object(evidence[prefix + "start.json"])
        if (set(actual) != backend.RUN_FILES or actual != receipt["old_run_hashes"]
                or receipt["source_hashes"] != start["source_hashes"]
                or digest(evidence[prefix + "config.json"]) != record["config_sha256"]):
            raise BootError("worker-recovery")
    except (ValueError, TypeError, KeyError) as exc:
        raise BootError("worker-recovery") from exc


def validate_worker(value: dict, boot_id: str, *, evidence: dict | None = None,
                    worker_pid: int | None = None) -> dict:
    if (not isinstance(value, dict) or set(value) != SESSION_RESULT_KEYS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["boot_id"] != boot_id
            or any(type(value[key]) is not int or value[key] != expected for key, expected in
                   (("uid", SESSION_UID), ("euid", SESSION_UID), ("gid", SESSION_GID), ("egid", SESSION_GID)))
            or value["session_id"] is not None and not is_uuid(value["session_id"])
            or type(value["session_exit_code"]) is not int or not 0 <= value["session_exit_code"] <= 4
            or type(value["cleanup_ok"]) is not bool
            or type(value["completed_monotonic_ns"]) is not int or value["completed_monotonic_ns"] < 0
            or value["error"] is not None and (not isinstance(value["error"], str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["error"]))):
        raise BootError("worker-result")
    rows = value["cleanup"]
    if not isinstance(rows, list) or len(rows) != 3:
        raise BootError("worker-result")
    for row, name in zip(rows, ("MAIN", "MODEL_BACKEND", "CONSOLE_RUNTIME")):
        if (not isinstance(row, dict) or set(row) != {"service", "action", "outcome", "error", "response"}
                or row["service"] != name or row["action"] != "stop" or row["outcome"] not in ("OK", "ERROR")
                or row["response"] is not None and not isinstance(row["response"], dict)):
            raise BootError("worker-result")
        response = row["response"]
        success = _cleanup_succeeded(name, response)
        if (row["outcome"] == "OK") != success or (row["error"] is None) != success:
            raise BootError("worker-result")
        if success and response["state"] == "RECOVERED":
            _validate_recovered_cleanup(response, boot_id, value["completed_monotonic_ns"], evidence, worker_pid)
    if value["cleanup_ok"] != all(row["outcome"] == "OK" for row in rows):
        raise BootError("worker-result")
    return value


def start_worker() -> subprocess.Popen:
    # Fixed command, fixed credentials and fixed working directory. Neither
    # configuration nor console input can select a privileged command/path.
    return subprocess.Popen(["/usr/bin/python3", "-B", ENTRY.as_posix(), "--session"],
        user=SESSION_UID, group=SESSION_GID, extra_groups=[], cwd="/",
        env={"PATH": "/usr/bin:/bin", "HOME": "/home/aios", "USER": "aios", "LOGNAME": "aios",
             "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"})


@contextlib.contextmanager
def quiet_terminal():
    original = None
    try:
        import termios
        if sys.stdin.isatty():
            original = termios.tcgetattr(sys.stdin.fileno())
            attributes = original.copy()
            attributes[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attributes)
        yield
    finally:
        if original is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, original)


def marker(name: str, value: dict) -> None:
    print("AIOS_IMAGE_" + name + "=" + encoded(value).decode("ascii").rstrip("\n"), flush=True)


def request_poweroff(boot_id: str) -> bool:
    """Request init shutdown; successful dispatch is not completed shutdown proof."""
    try:
        subprocess.run(list(POWEROFF), check=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        marker("POWEROFF_FAILED", {"schema_version": 1, "boot_id": boot_id, "error": "poweroff-failed"})
        return False
    return True


def run_parent() -> int:
    if os.getuid() != 0 or os.geteuid() != 0:
        raise BootError("root-required")
    os.umask(0o077)
    boot_id = current_boot_id()
    config_raw, image_raw, image, model_metadata, model_integrity = installed_metadata(boot_id)
    config = validate_config(json_object(config_raw))
    ensure_root_directory(LIVE_PARENT, leaf_mode=0o711)
    # umask must not remove the worker's traversal permission.
    live_parent_fd = directory_fd(LIVE_PARENT, trusted=True)
    try:
        os.fchmod(live_parent_fd, 0o711)
    finally:
        os.close(live_parent_fd)
    ensure_root_directory(HISTORY_PARENT, leaf_mode=0o700)
    prior = previous_histories(HISTORY_PARENT, boot_id)
    live = create_live_directory(LIVE_PARENT, boot_id)
    boot = {"schema_version": config["schema_version"], "boot_id": boot_id, "image_id": image["image_id"],
            "profile": config["profile"], "substrate": "linux-hosted", "source_only": True,
            "canonical_continuity": False, "config_sha256": digest(config_raw),
            "image_manifest_sha256": digest(image_raw), "runtime_files": image["runtime_files"],
            "installation_files": image["installation_files"], "previous_boots": prior,
            "live_root": str(live), "history_root": str(HISTORY_PARENT / boot_id),
            "worker_uid": SESSION_UID, "worker_gid": SESSION_GID, "root_uid": os.getuid(),
            "root_euid": os.geteuid(), "started_monotonic_ns": time.monotonic_ns()}
    if model_integrity is not None:
        boot.update(model_bundle=image["model_bundle"], model_integrity=model_integrity)
    worker, worker_exit, copied, worker_result, error = None, None, {}, None, None
    with quiet_terminal():
        marker("BOOT", boot)
        marker("SESSION_BEGIN", {"schema_version": 1, "boot_id": boot_id})
        try:
            worker = start_worker()
            while True:
                try:
                    worker_exit = worker.wait()
                    break
                except KeyboardInterrupt:
                    # The same terminal signal reaches the CLI, which handles
                    # command interruption. Root keeps responsibility for exit.
                    continue
        except (OSError, RuntimeError):
            error = "worker-launch"
        marker("SESSION_END", {"schema_version": 1, "boot_id": boot_id,
                               "worker_exit_code": worker_exit})
    try:
        copied = snapshot_tree(live, owner=SESSION_UID)
        if any(name.split("/", 1)[0] in RESERVED for name in copied):
            raise BootError("reserved-evidence")
        if set(name.split("/", 1)[0] for name in copied) - {"session", "main", "backend", "service", "worker-result.json"}:
            raise BootError("unexpected-evidence")
        worker_result = validate_worker(json_object(copied["worker-result.json"]), boot_id,
                                        evidence=copied, worker_pid=worker.pid if worker is not None else None)
    except (OSError, BootError, KeyError) as exc:
        error = error or (exc.code if isinstance(exc, BootError) else "worker-evidence")
        copied = {}
    cleanup_ok = worker_result is not None and worker_result["cleanup_ok"]
    if worker_exit != 0 or worker_result is None or worker_result["error"] is not None or not cleanup_ok:
        error = error or "worker-failed"
    poweroff = {"schema_version": 1, "boot_id": boot_id, "command": list(POWEROFF),
                "root_uid": os.getuid(), "root_euid": os.geteuid(), "source_only": True}
    final = {"schema_version": config["schema_version"], "boot_id": boot_id, "image_id": image["image_id"],
             "profile": config["profile"], "source_only": True, "config_sha256": digest(config_raw),
             "worker_process_id": worker.pid if worker is not None else None,
             "worker_exit_code": worker_exit, "worker_exit_verified": worker is not None and worker_exit is not None,
             "worker_result_sha256": digest(copied["worker-result.json"]) if "worker-result.json" in copied else None,
             "session_id": worker_result["session_id"] if worker_result else None,
             "session_exit_code": worker_result["session_exit_code"] if worker_result else None,
             "cleanup_ok": cleanup_ok, "outcome": "PASS" if error is None else "FAIL", "error": error,
             "archive_complete": bool(copied), "poweroff_intent": "fixed-root-poweroff",
             "completed_monotonic_ns": time.monotonic_ns()}
    if model_integrity is not None:
        final.update(model_bundle=image["model_bundle"], model_integrity=model_integrity)
    metadata = {**model_metadata, "boot.json": encoded(boot), "boot-config.json": config_raw, "image.json": image_raw,
                "poweroff.json": encoded(poweroff)}
    try:
        files = archive_files(boot_id, {**copied, **metadata, "root-result.json": encoded(final)})
    except BootError as exc:
        final.update(outcome="FAIL", error=exc.code, archive_complete=False)
        files = archive_files(boot_id, {**metadata, "root-result.json": encoded(final)})
    archived = write_tree(HISTORY_PARENT, boot_id, files)
    # Reopen the root-owned archive itself for export, rather than exporting a
    # second path traversal into the former UID 1000 live tree.
    retained = snapshot_tree(archived, owner=0, immutable=True, skip_sockets=False)
    if retained != files:
        raise BootError("archive-changed")
    os.sync()
    marker("RESULT", final)
    for line in export_lines(boot_id, retained):
        print(line, flush=True)
    os.sync()
    marker("POWEROFF", poweroff)
    if not request_poweroff(boot_id):
        return 1
    return 0 if final["outcome"] == "PASS" else 1


def main() -> int:
    if platform.system() != "Linux":
        marker("FATAL", {"schema_version": 1, "outcome": "FAIL", "error": "unsupported-platform"})
        return 3
    try:
        if sys.argv[1:] == ["--session"]:
            boot_id = current_boot_id()
            boot_config = validate_config(json_object(read_file(CONFIG_PATH, owner=0, trusted=True)))
            return run_worker(LIVE_PARENT / boot_id, boot_id, boot_config=boot_config)
        if sys.argv[1:]:
            raise BootError("invalid-arguments")
        return run_parent()
    except (OSError, BootError) as exc:
        marker("FATAL", {"schema_version": 1, "outcome": "FAIL",
                         "error": exc.code if isinstance(exc, BootError) else "boot-io"})
        return 1
