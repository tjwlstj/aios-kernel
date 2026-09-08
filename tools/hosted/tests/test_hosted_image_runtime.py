"""Installed delivery boot invariants; fixtures are not operating-image proof."""
from __future__ import annotations

import base64
import copy
import io
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted" / "linux"))
from aios_boot import BootError, MAX_FILE_BYTES, MAX_TOTAL_BYTES
from aios_boot import archive, runtime

LINUX = platform.system() == "Linux" and hasattr(os, "O_NOFOLLOW")
ROOT_LINUX = LINUX and os.geteuid() == 0


def image_value():
    return {"schema_version": 1, "image_id": str(uuid.uuid4()), "profile": "basic-cli",
            "substrate": "linux-hosted", "iso": {"url": "https://example.invalid/fixture.iso",
            "sha256": "1" * 64, "version": "fixture"},
            "runtime_files": {"aios-image-boot.py": "2" * 64}, "boot_config_sha256": "3" * 64,
            "installation_files": {name: "4" * 64 for name in runtime.INSTALLATION_FILES},
            "source_only": True, "repository_import": False, "redistribution_approved": False}


def model_values():
    """Small metadata fixtures only; never substitute for real model evidence."""
    from aios_backend import BACKEND_NAME, BACKEND_SHA, MODEL_NAME, MODEL_SHA, MODEL_ID
    files = {name: ("fixture source " + name).encode() for name in runtime.PROVENANCE_FILES}
    provenance = {"schema_version": 1, "repository_import": False,
        "repository_manifest_changed": False, "host_global_install": False,
        "redistribution_approved": False,
        "artifacts": [{"name": name, "size": row["size_bytes"], "sha256": row["sha256"],
                       "local_integrity_verified": True} for name, row in runtime.pinned_model_files().items()],
        "source_receipts": [{"path": name, "url": "https://example.invalid/fixture/" + name,
                             "size": len(raw), "sha256": archive.digest(raw)} for name, raw in files.items()]}
    files["inference-provenance.json"] = archive.encoded(provenance)
    config = {"schema_version": 1, "endpoint": "http://127.0.0.1:18081", "model_id": MODEL_ID,
        "model_path": "/opt/aios/inference/" + MODEL_NAME, "model_sha256": MODEL_SHA,
        "backend_path": "/opt/aios/inference/" + BACKEND_NAME, "backend_sha256": BACKEND_SHA,
        "provenance_sha256": archive.digest(files["inference-provenance.json"])}
    files["model-config.json"] = archive.encoded(config)
    bundle = {"schema_version": 1, "config_sha256": archive.digest(files["model-config.json"]),
              "provenance_sha256": config["provenance_sha256"], "files": runtime.pinned_model_files()}
    files["model-integrity.json"] = archive.encoded({"schema_version": 1,
        "verification": "installed-read-complete", "files": runtime.model_file_records(bundle)})
    image = image_value()
    image.update(schema_version=2, profile="local-model-cli", model_bundle=bundle)
    image["installation_files"].update({name: archive.digest(raw) for name, raw in files.items()})
    return image, files


class ImagePureTests(unittest.TestCase):
    def test_basic_only_config_and_exact_manifest(self):
        runtime.validate_config({"schema_version": 1, "profile": "basic", "model_config": None})
        for wrong in ({"schema_version": True, "profile": "basic", "model_config": None},
                      {"schema_version": 1, "profile": "model", "model_config": None},
                      {"schema_version": 1, "profile": "basic", "model_config": "/tmp/model.json"},
                      {"schema_version": 1, "profile": "basic", "model_config": None, "command": "anything"}):
            with self.assertRaises(BootError):
                runtime.validate_config(wrong)
        value = image_value()
        self.assertEqual(runtime.validate_image(value), value)
        for changed in ({"source_only": False}, {"repository_import": True}, {"schema_version": True},
                        {"installation_files": {"../../etc/shadow": "0" * 64}}):
            with self.assertRaises(BootError):
                runtime.validate_image({**value, **changed})

    def test_model_profile_is_pinned_and_does_not_change_basic_argv(self):
        live = Path("fixture") / str(uuid.uuid4())
        basic = {"schema_version": 1, "profile": "basic", "model_config": None}
        model = {"schema_version": 2, "profile": "local-model", "model_config": "/etc/aios/model.json"}
        argv = runtime.worker_cli_argv(live, basic)
        self.assertNotIn("--agent-config", argv)
        self.assertEqual(runtime.worker_cli_argv(live, model), argv + ["--agent-config", "/etc/aios/model.json"])
        for change in ({"schema_version": 1}, {"model_config": None},
                       {"model_config": "/tmp/model.json"}, {"profile": "basic"}):
            with self.subTest(change=change), self.assertRaises(BootError):
                runtime.worker_cli_argv(live, {**model, **change})
        image, files = model_values()
        runtime.validate_image(image)
        runtime.validate_model_metadata(image["model_bundle"], files, files["model-config.json"])
        for change in ({"profile": "basic-cli"}, {"schema_version": 1},
                       {"installation_files": image_value()["installation_files"]}):
            with self.subTest(change=change), self.assertRaises(BootError):
                runtime.validate_image({**image, **change})
        altered = copy.deepcopy(image)
        next(iter(altered["model_bundle"]["files"].values()))["sha256"] = "0" * 64
        with self.assertRaisesRegex(BootError, "invalid-model-bundle"):
            runtime.validate_image(altered)

    def test_model_metadata_rehash_cannot_redirect_or_discard_source_evidence(self):
        image, files = model_values()
        bundle = image["model_bundle"]
        for field, value in (("model_path", "/tmp/model.gguf"), ("backend_path", "/tmp/backend"),
                             ("endpoint", "http://10.0.2.2:18081"), ("schema_version", True)):
            changed = dict(files)
            config = json.loads(files["model-config.json"])
            config[field] = value
            changed["model-config.json"] = archive.encoded(config)
            resigned = {**bundle, "config_sha256": archive.digest(changed["model-config.json"])}
            with self.subTest(field=field), self.assertRaisesRegex(BootError, "invalid-model-config"):
                runtime.validate_model_metadata(resigned, changed, changed["model-config.json"])
        name = sorted(runtime.PROVENANCE_FILES)[0]
        for changed in ({**files, name: b"changed source"}, {k: v for k, v in files.items() if k != name}):
            with self.assertRaises(BootError):
                runtime.validate_model_metadata(bundle, changed, changed["model-config.json"])
        altered = dict(files)
        receipt = json.loads(files["model-integrity.json"])
        next(iter(receipt["files"].values()))["uid"] = False
        altered["model-integrity.json"] = archive.encoded(receipt)
        with self.assertRaisesRegex(BootError, "model-installation-integrity"):
            runtime.validate_model_metadata(bundle, altered, altered["model-config.json"])

    def test_model_preflight_streams_assets_and_archives_only_small_metadata(self):
        image, files = model_values()
        boot_id = str(uuid.uuid4())
        boot_raw = archive.encoded({"schema_version": 2, "profile": "local-model", "model_config": "/etc/aios/model.json"})
        runtime_files = {"aios-image-boot.py": b"fixture installed runtime"}
        installed = {name: b"fixture installed receipt" for name in runtime.INSTALLATION_FILES}
        installed["installed-runtime.json"] = archive.encoded({name: archive.digest(raw) for name, raw in runtime_files.items()})
        installed.update(files)
        image.update(boot_config_sha256=archive.digest(boot_raw),
            runtime_files={name: archive.digest(raw) for name, raw in runtime_files.items()},
            installation_files={name: archive.digest(raw) for name, raw in installed.items()})
        reads = {runtime.CONFIG_PATH: boot_raw, runtime.IMAGE_PATH: archive.encoded(image),
                 runtime.MODEL_CONFIG_PATH: files["model-config.json"]}
        def stream(path, expected):
            return {"path": path.as_posix(), **expected, "uid": 0, "gid": 0, "mode": 0o444}
        with patch.object(runtime, "read_file", side_effect=lambda path, **_: reads[path]), \
                patch.object(runtime, "snapshot_tree", side_effect=[runtime_files, installed]), \
                patch.object(runtime, "stream_model_file", side_effect=stream) as streaming:
            config_raw, image_raw, manifest, retained, integrity = runtime.installed_metadata(boot_id)
        self.assertEqual(config_raw, boot_raw)
        self.assertEqual(retained, files)
        self.assertLess(sum(map(len, retained.values())), MAX_TOTAL_BYTES)
        self.assertFalse(set(retained) & set(runtime.pinned_model_files()))
        self.assertEqual(streaming.call_args_list[0].args[0], runtime.MODEL_CONFIG_PATH)
        self.assertCountEqual([call.args[0] for call in streaming.call_args_list[1:]],
                              [runtime.INFERENCE_ROOT / name for name in image["model_bundle"]["files"]])
        self.assertEqual(integrity, {"schema_version": 1, "boot_id": boot_id,
            "verification": "boot-read-complete", "files": runtime.model_file_records(image["model_bundle"])})
        with patch.object(runtime, "read_file", side_effect=lambda path, **_: reads[path]), \
                patch.object(runtime, "snapshot_tree", side_effect=[runtime_files, installed]), \
                patch.object(runtime, "stream_model_file", side_effect=BootError("model-hash")), \
                patch.object(runtime, "start_worker") as worker:
            with self.assertRaisesRegex(BootError, "model-hash"):
                runtime.installed_metadata(boot_id)
            worker.assert_not_called()

    def test_export_is_bounded_non_archive_file_records(self):
        boot_id = str(uuid.uuid4())
        files = archive.archive_files(boot_id, {"session/console.log": b"AIOS\n", "empty.json": b"{}"})
        lines = list(archive.export_lines(boot_id, files))
        start = json.loads(lines[0].split("=", 1)[1])
        self.assertEqual(json.loads(lines[-1].split("=", 1)[1]), start)
        self.assertEqual(start["file_count"], 3)
        restored = {}
        for line in lines[1:-1]:
            record = json.loads(line.split("=", 1)[1])
            raw = base64.b64decode(record["data_base64"], validate=True)
            self.assertEqual((len(raw), archive.digest(raw)), (record["bytes"], record["sha256"]))
            restored[record["path"]] = raw
        self.assertEqual(restored, files)

    def test_paths_capacity_duplicate_json_and_complexity_fail_closed(self):
        for name in ("", "/etc/shadow", "../escape", "a/../x", "a//x", "a\\b", "./x"):
            with self.assertRaises(BootError):
                archive.relative_path(name)
        with self.assertRaisesRegex(BootError, "file-capacity"):
            archive.files_manifest({"x": b"x" * (MAX_FILE_BYTES + 1)})
        with self.assertRaisesRegex(BootError, "evidence-capacity"):
            archive.files_manifest({str(i): b"" for i in range(513)})
        with self.assertRaisesRegex(BootError, "evidence-capacity"):
            archive.files_manifest({str(i): b"x" * MAX_FILE_BYTES for i in range(9)})
        with self.assertRaisesRegex(BootError, "invalid-json"):
            archive.json_object(b'{"x":1,"x":2}')
        with self.assertRaisesRegex(BootError, "json-complexity"):
            archive.json_object(b'{"x":' + b'[' * 21 + b'0' + b']' * 21 + b'}')

    def test_fixed_worker_credentials_and_clean_environment(self):
        with patch.object(runtime.subprocess, "Popen") as spawn:
            runtime.start_worker()
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], ["/usr/bin/python3", "-B", "/opt/aios/linux/aios-image-boot.py", "--session"])
        self.assertEqual((kwargs["user"], kwargs["group"], kwargs["extra_groups"]), (1000, 1000, []))
        self.assertEqual(kwargs["cwd"], "/")
        self.assertNotIn("PYTHONPATH", kwargs["env"])
        self.assertNotIn("stdin", kwargs)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("shell", kwargs)

    def test_cleanup_is_ordered_and_failed_stop_never_becomes_ok(self):
        from aios_agent import client as main_client
        from aios_backend import client as backend_client
        from aios_service import client as service_client
        good = {"outcome": "OK", "error": None, "state": "ABSENT"}
        calls = []
        def call(name, reply):
            def execute(path, action):
                calls.append((name, path.name, action))
                return reply
            return execute
        with patch.object(main_client, "control", side_effect=call("main", good)), \
                patch.object(backend_client, "control", side_effect=call("backend", {"outcome": "ERROR", "error": "stop-timeout", "state": "STOPPING"})), \
                patch.object(service_client, "control", side_effect=call("service", good)):
            rows, okay = runtime._cleanup(Path("fixture"))
        self.assertFalse(okay)
        self.assertEqual(calls, [("main", "main", "stop"), ("backend", "backend", "stop"), ("service", "service", "stop")])
        self.assertEqual([r["outcome"] for r in rows], ["OK", "ERROR", "OK"])

    def recovered_worker(self, base):
        from test_hosted_backend_recovery_verifier import recovery_fixture
        from aios_backend import client as backend
        state, _source, _runs, path, owner = recovery_fixture(base)
        receipt = json.loads(path.read_bytes())
        response = backend._recovered_reply("stop", receipt)
        absent = {"outcome": "OK", "error": None, "state": "ABSENT"}
        value = {"schema_version": 1, "boot_id": owner["host_boot_id"], "uid": 1000, "euid": 1000,
            "gid": 1000, "egid": 1000, "session_id": str(uuid.uuid4()), "session_exit_code": 0,
            "error": None, "cleanup_ok": True, "completed_monotonic_ns": 1700,
            "cleanup": [{"service": name, "action": "stop", "outcome": "OK", "error": None, "response": reply}
                for name, reply in (("MAIN", absent), ("MODEL_BACKEND", response), ("CONSOLE_RUNTIME", absent))]}
        evidence = {"backend/" + path.relative_to(state).as_posix(): path.read_bytes()
                    for path in state.rglob("*") if path.is_file()}
        return value, evidence, owner

    def test_recovered_backend_cleanup_keeps_distinct_state_and_requires_its_copied_receipt(self):
        from aios_agent import client as main_client
        from aios_backend import client as backend_client
        from aios_service import client as service_client
        with tempfile.TemporaryDirectory() as temporary:
            value, evidence, owner = self.recovered_worker(Path(temporary))
            recovered = value["cleanup"][1]["response"]
            absent = value["cleanup"][0]["response"]
            with patch.object(main_client, "control", return_value=absent), \
                    patch.object(backend_client, "control", return_value=recovered), \
                    patch.object(service_client, "control", return_value=absent):
                rows, okay = runtime._cleanup(Path("fixture"))
            self.assertTrue(okay)
            self.assertEqual(rows, value["cleanup"])
            self.assertEqual(rows[1]["response"]["state"], "RECOVERED")
            self.assertEqual(runtime.validate_worker(value, value["boot_id"], evidence=evidence,
                                                     worker_pid=owner["process_id"]), value)
            with self.assertRaisesRegex(BootError, "worker-recovery"):
                runtime.validate_worker(value, value["boot_id"])
            missing = {key: raw for key, raw in evidence.items() if "/recoveries/" not in key}
            with self.assertRaisesRegex(BootError, "worker-recovery"):
                runtime.validate_worker(value, value["boot_id"], evidence=missing, worker_pid=owner["process_id"])

    def test_recovered_cleanup_rejects_wrong_owner_changed_files_and_false_terminal_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            value, evidence, owner = self.recovered_worker(Path(temporary))
            for worker_pid in (None, True, owner["process_id"] + 1):
                with self.subTest(worker_pid=worker_pid), self.assertRaisesRegex(BootError, "worker-recovery"):
                    runtime.validate_worker(value, value["boot_id"], evidence=evidence, worker_pid=worker_pid)
            receipt_name = next(key for key in evidence if "/recoveries/" in key)
            mutations = [lambda rows: rows.update({next(key for key in rows if key.endswith("/stdout.log")): b"changed"})]
            for field, replacement in (("supervisor_reaped", False), ("child_exit_observed", 1),
                                       ("child_exit_code", 0), ("completed_monotonic_ns", 1800)):
                def change(rows, field=field, replacement=replacement):
                    receipt = json.loads(rows[receipt_name])
                    receipt["recovery"][field] = replacement
                    rows[receipt_name] = archive.encoded(receipt)
                mutations.append(change)
            for mutate in mutations:
                changed = dict(evidence)
                mutate(changed)
                with self.assertRaisesRegex(BootError, "worker-recovery"):
                    runtime.validate_worker(value, value["boot_id"], evidence=changed, worker_pid=owner["process_id"])
            for index in (0, 2):
                changed = copy.deepcopy(value)
                changed["cleanup"][index]["response"] = changed["cleanup"][1]["response"]
                with self.assertRaisesRegex(BootError, "worker-result"):
                    runtime.validate_worker(changed, value["boot_id"], evidence=evidence, worker_pid=owner["process_id"])

    def test_normal_shutdown_dispatch_is_bounded_and_never_force_retries(self):
        boot_id = str(uuid.uuid4())
        with patch.object(runtime.subprocess, "run") as execute:
            self.assertTrue(runtime.request_poweroff(boot_id))
        execute.assert_called_once_with(["/sbin/poweroff"], check=True, timeout=10,
                                        stdin=subprocess.DEVNULL)
        for failure in (subprocess.TimeoutExpired(["/sbin/poweroff"], 10),
                        subprocess.CalledProcessError(1, ["/sbin/poweroff"]),
                        OSError("fixture poweroff unavailable")):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(runtime.subprocess, "run", side_effect=failure) as execute, \
                    patch.object(runtime, "marker") as emit:
                self.assertFalse(runtime.request_poweroff(boot_id))
            execute.assert_called_once_with(["/sbin/poweroff"], check=True, timeout=10,
                                            stdin=subprocess.DEVNULL)
            emit.assert_called_once_with("POWEROFF_FAILED", {"schema_version": 1,
                "boot_id": boot_id, "error": "poweroff-failed"})


@unittest.skipUnless(LINUX, "Linux dirfd and NOFOLLOW semantics required")
class ImageArchiveLinuxTests(unittest.TestCase):
    def test_snapshot_rejects_symlink_hardlink_fifo_and_skips_socket(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "good").write_bytes(b"kept")
            self.assertEqual(archive.snapshot_tree(root, owner=os.getuid()), {"good": b"kept"})
            (root / "link").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(BootError, "unsafe-file"):
                archive.snapshot_tree(root, owner=os.getuid())
            (root / "link").unlink()
            os.link(root / "good", root / "alias")
            with self.assertRaisesRegex(BootError, "unsafe-file"):
                archive.snapshot_tree(root, owner=os.getuid())
            (root / "alias").unlink()
            os.mkfifo(root / "fifo")
            with self.assertRaisesRegex(BootError, "unsafe-file"):
                archive.snapshot_tree(root, owner=os.getuid())
            (root / "fifo").unlink()
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(root / "control.sock"))
                self.assertEqual(archive.snapshot_tree(root, owner=os.getuid()), {"good": b"kept"})
                with self.assertRaisesRegex(BootError, "unsafe-file"):
                    archive.snapshot_tree(root, owner=os.getuid(), skip_sockets=False)

    def test_symlink_ancestor_and_mutating_open_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(OSError):
                archive.directory_fd(root / "alias")
            path = root / "data"
            path.write_bytes(b"old")
            original, mutated = os.read, False
            def changing(handle, size):
                nonlocal mutated
                data = original(handle, size)
                if not mutated:
                    mutated = True
                    path.write_bytes(b"different")
                return data
            with patch.object(archive.os, "read", side_effect=changing):
                with self.assertRaisesRegex(BootError, "file-changed"):
                    archive.read_file(path, owner=os.getuid())


@unittest.skipUnless(LINUX and hasattr(os, "pidfd_open") and hasattr(socket, "SO_PEERCRED"),
                     "Linux backend processes, pidfd and private Unix IPC required")
class ImageBackendPathLinuxTests(unittest.TestCase):
    def test_model_backend_long_run_path_uses_short_socket_and_cleans_up(self):
        from test_hosted_backend_runtime import BackendProcessTests
        from aios_resources.backend import attest
        fixture = BackendProcessTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.state = fixture.base / "boots" / str(uuid.uuid4()) / "backend"
        self.assertLess(len(os.fsencode(fixture.state / "control.sock")), 104)
        self.assertGreaterEqual(len(os.fsencode(fixture.state / "runs" / str(uuid.uuid4()) / "control.sock")), 104)

        started = fixture.start()
        run = fixture.state / "runs" / started["service_record"]["instance_id"]
        proof = attest(fixture.state, fixture.config)
        self.assertEqual(proof["descriptor"], started["descriptor"])
        self.assertTrue((fixture.state / "backend.sock").is_socket())
        self.assertFalse((run / "backend.sock").exists())
        self.assertEqual((run.stat().st_uid, run.stat().st_mode & 0o777), (os.getuid(), 0o700))
        self.assertEqual(json.loads((run / "backend-source.json").read_bytes()), started["descriptor"])
        self.assertEqual(len((run / "backend-attestations.jsonl").read_bytes().splitlines()), 1)
        stopped = fixture.okay("stop")
        result = fixture.result(stopped)
        self.assertEqual((result["state"], result["exit_code"]), ("STOPPED", 0))
        self.assertTrue(result["child_exit_verified"])
        self.assertFalse(result["forced"])
        self.assertFalse((fixture.state / "backend.sock").exists())

    def attester_paths(self):
        temporary = tempfile.TemporaryDirectory(prefix="aios-ip-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        short, long = base / "short", base / ("run-" + "x" * 90)
        short.mkdir(mode=0o700)
        long.mkdir(mode=0o700)
        self.assertLess(len(os.fsencode(short / "backend.sock")), 104)
        self.assertGreaterEqual(len(os.fsencode(long / "backend.sock")), 104)
        child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        self.addCleanup(stop)
        _image, metadata = model_values()
        return short, long, child, json.loads(metadata["model-config.json"])

    def test_attester_rejects_long_explicit_and_default_socket_paths(self):
        from aios_resources import ResourceError
        from aios_resources.backend import BackendAttester
        short, long, child, config = self.attester_paths()
        for output, options in ((long, {}), (long, {"socket_dir": long}), (short, {"socket_dir": long})):
            with self.subTest(separate_socket=bool(options)), self.assertRaisesRegex(ResourceError, "backend-path"):
                BackendAttester(output, child, config, capture_kind="fixture", **options)
        self.assertFalse((short / "backend-attestations.jsonl").exists())
        self.assertFalse((long / "backend-attestations.jsonl").exists())

    def test_long_output_still_rejects_unsafe_mode_symlink_and_owner(self):
        from aios_resources import ResourceError
        from aios_resources.backend import BackendAttester
        short, long, child, config = self.attester_paths()
        long.chmod(0o755)
        with self.assertRaisesRegex(ResourceError, "backend-path"):
            BackendAttester(long, child, config, capture_kind="fixture", socket_dir=short)
        long.chmod(0o700)
        alias = long.parent / "alias"
        alias.symlink_to(long, target_is_directory=True)
        with self.assertRaisesRegex(ResourceError, "backend-path"):
            BackendAttester(alias, child, config, capture_kind="fixture", socket_dir=short)
        if os.getuid() == 0:
            os.chown(long, 1000, 1000)
            try:
                with self.assertRaisesRegex(ResourceError, "backend-path"):
                    BackendAttester(long, child, config, capture_kind="fixture", socket_dir=short)
            finally:
                os.chown(long, 0, 0)
        self.assertFalse((short / "backend.sock").exists())
        self.assertFalse((long / "backend-attestations.jsonl").exists())


@unittest.skipUnless(ROOT_LINUX, "Disposable Linux root test guest required")
class ImageRootLinuxTests(unittest.TestCase):
    def test_model_stream_read_exceeds_archive_limit_and_rejects_changes(self):
        with tempfile.TemporaryDirectory(prefix="aios-model-read-test-", dir="/") as temp:
            root = Path(temp)
            path = root / "fixture-model"
            raw = b"fixture" * (MAX_FILE_BYTES // 7 + 2)
            self.assertGreater(len(raw), MAX_FILE_BYTES)
            path.write_bytes(raw)
            path.chmod(0o444)
            expected = {"size_bytes": len(raw), "sha256": archive.digest(raw)}
            proof = runtime.stream_model_file(path, expected)
            self.assertEqual(proof, {"path": path.as_posix(), **expected, "uid": 0, "gid": 0, "mode": 0o444})
            with self.assertRaisesRegex(BootError, "model-size"):
                runtime.stream_model_file(path, {**expected, "size_bytes": len(raw) - 1})
            with self.assertRaisesRegex(BootError, "model-hash"):
                runtime.stream_model_file(path, {**expected, "sha256": "0" * 64})
            path.chmod(0o644)
            with self.assertRaisesRegex(BootError, "unsafe-model-owner"):
                runtime.stream_model_file(path, expected)
            path.chmod(0o444)
            original, mutated = os.read, False
            def changing(handle, count):
                nonlocal mutated
                block = original(handle, count)
                if not mutated:
                    mutated = True
                    path.write_bytes(b"x" * len(raw))
                return block
            with patch.object(runtime.os, "read", side_effect=changing):
                with self.assertRaisesRegex(BootError, "model-changed"):
                    runtime.stream_model_file(path, expected)
            path.unlink()
            path.symlink_to("/etc/passwd")
            with self.assertRaisesRegex(BootError, "unsafe-model-file"):
                runtime.stream_model_file(path, expected)
            path.unlink()
            path.write_bytes(raw)
            path.chmod(0o444)
            alias = root / "alias"
            os.link(path, alias)
            with self.assertRaisesRegex(BootError, "unsafe-model-file"):
                runtime.stream_model_file(path, expected)

    def test_worker_model_profile_uses_fixed_config_without_automatic_start(self):
        if not Path("/proc/sys/kernel/random/boot_id").exists() or not hasattr(os, "pidfd_open"):
            self.skipTest("procfs and pidfd required")
        with tempfile.TemporaryDirectory(prefix="aios-model-session-test-", dir="/") as temp:
            root = Path(temp)
            root.chmod(0o755)
            boots = root / "boots"
            boots.mkdir(mode=0o711)
            boot_id = runtime.current_boot_id()
            live = runtime.create_live_directory(boots, boot_id)
            config = root / "boot.json"
            config.write_bytes(archive.encoded({"schema_version": 2, "profile": "local-model",
                                               "model_config": "/etc/aios/model.json"}))
            config.chmod(0o444)
            script = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from aios_boot import runtime
runtime.LIVE_PARENT = Path(sys.argv[2])
runtime.CONFIG_PATH = Path(sys.argv[3])
sys.argv = [str(runtime.ENTRY), '--session']
raise SystemExit(runtime.main())
"""
            child = subprocess.Popen([sys.executable, "-B", "-c", script, str(ROOT / "hosted/linux"),
                str(boots), str(config)], user=1000, group=1000, extra_groups=[],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env={"PATH": "/usr/bin:/bin", "HOME": "/home/aios", "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                stdout, stderr = child.communicate("about\nbackend status\nagent status\nask unavailable model fixture\nexit\n", timeout=30)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            result = runtime.validate_worker(json.loads((live / "worker-result.json").read_bytes()), boot_id)
            self.assertEqual([row["response"]["state"] for row in result["cleanup"]], ["ABSENT"] * 3)
            self.assertTrue(result["cleanup_ok"])
            self.assertFalse((live / "main/management.json").exists())
            self.assertFalse((live / "backend/registry.json").exists())
            self.assertEqual(stdout, (live / "session/console.log").read_text())

    def test_boot_freshness_private_uid_and_previous_payload_preservation(self):
        # A fresh root-owned subtree permits the real trusted-ancestor and
        # UID-drop checks without touching any fixed installation/control path.
        with tempfile.TemporaryDirectory(prefix="aios-image-test-", dir="/") as temp:
            parent = Path(temp)
            parent.chmod(0o755)
            live, histories = parent / "run/aios/boots", parent / "history"
            previous_umask = os.umask(0o077)
            try:
                runtime.ensure_root_directory(live, leaf_mode=0o711)
            finally:
                os.umask(previous_umask)
            runtime.ensure_root_directory(histories, leaf_mode=0o700)
            first, second = str(uuid.uuid4()), str(uuid.uuid4())
            created = runtime.create_live_directory(live, first)
            self.assertEqual((created.stat().st_uid, created.stat().st_mode & 0o777), (1000, 0o700))
            self.assertEqual((parent / "run/aios").stat().st_mode & 0o777, 0o755)
            traversed = subprocess.run([sys.executable, "-B", "-c", "import os,sys;print(os.listdir(sys.argv[1]))", str(created)],
                user=1000, group=1000, extra_groups=[], capture_output=True, text=True, timeout=5)
            self.assertEqual(traversed.returncode, 0, traversed.stderr)
            self.assertEqual(traversed.stdout.strip(), "[]")
            with self.assertRaisesRegex(BootError, "live-state-exists"):
                runtime.create_live_directory(live, first)
            self.assertNotEqual(runtime.create_live_directory(live, second), created)
            files = archive.archive_files(first, {"root-result.json": archive.encoded({"boot_id": first}),
                                                  "session/console.log": b"retained\n"})
            archive.write_tree(histories, first, files)
            before = archive.previous_histories(histories, second)
            self.assertEqual(set(before), {first})
            self.assertEqual(before[first]["archive_manifest_sha256"], archive.digest(files["archive-manifest.json"]))
            (histories / first / "session" / "console.log").unlink()
            with self.assertRaisesRegex(BootError, "history-corrupt"):
                archive.previous_histories(histories, second)

    def test_real_uid1000_console_and_owned_service_cleanup(self):
        if not Path("/proc/sys/kernel/random/boot_id").exists() or not hasattr(os, "pidfd_open"):
            self.skipTest("procfs and pidfd required")
        with tempfile.TemporaryDirectory(prefix="aios-image-session-") as temp:
            outer = Path(temp)
            outer.chmod(0o755)
            boot_id = runtime.current_boot_id()
            # Match the installed root-owned searchable-only live parent:
            # O_RDONLY ancestor traversal fails here for UID 1000.
            boots = outer / "boots"
            boots.mkdir(mode=0o711)
            boots.chmod(0o711)
            self.assertEqual((boots.stat().st_uid, boots.stat().st_mode & 0o777), (0, 0o711))
            live = boots / boot_id
            live.mkdir(mode=0o700)
            os.chown(live, 1000, 1000)
            script = """import sys, os, errno
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from aios_boot.runtime import run_worker
from aios_boot.archive import directory_fd
live = Path(sys.argv[2])
try:
    handle = os.open(live.parent, os.O_RDONLY | os.O_DIRECTORY)
except PermissionError as exc:
    assert exc.errno == errno.EACCES
else:
    os.close(handle)
    raise AssertionError('fixture must deny parent directory reads')
handle = directory_fd(live)
os.close(handle)
raise SystemExit(run_worker(live, sys.argv[3], boot_config={'schema_version':1,'profile':'basic','model_config':None}))
"""
            child = subprocess.Popen([sys.executable, "-B", "-c", script, str(ROOT / "hosted/linux"),
                                      str(live), boot_id], user=1000, group=1000, extra_groups=[],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env={"PATH": "/usr/bin:/bin", "HOME": "/home/aios",
                                                    "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                stdout, stderr = child.communicate("about\nservice start\nagent status\nask offline fixture\nexit\n", timeout=40)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            value = runtime.validate_worker(json.loads((live / "worker-result.json").read_text()), boot_id)
            self.assertEqual((value["uid"], value["euid"], value["gid"], value["egid"]), (1000, 1000, 1000, 1000))
            self.assertTrue(value["cleanup_ok"])
            self.assertEqual([row["response"]["state"] for row in value["cleanup"]], ["ABSENT", "ABSENT", "STOPPED"])
            self.assertEqual(stdout, (live / "session/console.log").read_text())
            self.assertIsNone(value["error"])
            self.assertFalse((live / "main/management.json").exists())
            self.assertEqual(json.loads((live / "service/latest.json").read_text())["state"], "STOPPED")


if __name__ == "__main__":
    unittest.main()
