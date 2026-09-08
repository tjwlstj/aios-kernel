from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_hosted import hardware
from aios_hosted.boot import run_boot

spec = importlib.util.spec_from_file_location("verify_boot", ROOT / "tools/hosted/verify_boot.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def put(root: Path, relative: str, value: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def fixture(root: Path) -> tuple[Path, Path]:
    proc, sysfs = root / "proc", root / "sys"
    put(proc, "cpuinfo", "processor : 0\nmodel name : Fixture CPU\n\nprocessor : 1\n")
    put(proc, "meminfo", "MemTotal: 4096 kB\nMemAvailable: 2048 kB\n")
    for name in ("bus/pci/devices", "bus/usb/devices", "class/block", "class/net"):
        (sysfs / name).mkdir(parents=True)
    for key, value in {"vendor": "0x8086", "device": "0x100e", "class": "0x020000"}.items():
        put(sysfs, "bus/pci/devices/pci-device0/" + key, value)
    for key, value in {"ifindex": "1", "type": "772", "operstate": "unknown"}.items():
        put(sysfs, "class/net/lo/" + key, value)
    return proc, sysfs


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.sysfs = fixture(self.root)

    def test_observed_empty_is_distinct_from_missing_bus(self):
        data = hardware.collect_hardware(self.proc, self.sysfs)
        self.assertEqual(data["usb"]["status"], "observed")
        self.assertEqual(data["usb"]["data"], [])
        (self.sysfs / "bus/usb/devices").rmdir()
        data = hardware.collect_hardware(self.proc, self.sysfs)
        self.assertEqual(data["usb"]["status"], "unavailable")

    def test_bad_pci_id_is_not_an_observed_device(self):
        put(self.sysfs, "bus/pci/devices/pci-device0/vendor", "0x8086 forged=1")
        value = hardware.collect_hardware(self.proc, self.sysfs)
        self.assertEqual(value["pci"]["status"], "partial")
        self.assertTrue(value["pci"]["data"][0]["errors"])

    def test_memory_units_duplicates_and_impossible_range(self):
        for raw in ("MemTotal: 4 MB\n", "MemTotal: 4 kB\nMemTotal: 4 kB\n",
                    "MemTotal: 4 kB\nMemAvailable: 5 kB\n", "MemTotal: 0 kB\n"):
            with self.subTest(raw=raw):
                put(self.proc, "meminfo", raw)
                self.assertEqual(hardware.collect_memory(self.proc)["status"], "unavailable")

    def test_memory_available_missing_remains_unknown(self):
        put(self.proc, "meminfo", "MemTotal: 4 kB\n")
        value = hardware.collect_memory(self.proc)
        self.assertEqual(value["status"], "observed")
        self.assertIsNone(value["data"]["available_bytes"])

    def test_empty_cpu_and_oversized_attribute_fail(self):
        put(self.proc, "cpuinfo", "model name: invented\n")
        self.assertEqual(hardware.collect_cpu(self.proc)["status"], "unavailable")
        put(self.sysfs, "bus/pci/devices/pci-device0/vendor", "a" * (hardware.MAX_TEXT + 1))
        self.assertEqual(hardware.collect_devices(self.sysfs, "pci")["status"], "partial")

    def test_device_capacity_is_degraded_not_silently_complete(self):
        for index in range(hardware.MAX_DEVICES + 2):
            (self.sysfs / "class/block" / str(index)).mkdir()
        value = hardware.collect_devices(self.sysfs, "block")
        self.assertEqual(len(value["data"]), hardware.MAX_DEVICES)
        self.assertIn("device_capacity_exceeded", value["errors"])
        self.assertEqual(value["status"], "partial")

    def test_partition_does_not_become_a_second_physical_disk(self):
        put(self.sysfs, "class/block/sda1/size", "128")
        put(self.sysfs, "class/block/sda1/partition", "1")
        row = hardware.collect_devices(self.sysfs, "block")["data"][0]
        self.assertEqual(row["size_bytes"], 65536)
        self.assertEqual(row["partition"], 1)
        self.assertIsNone(row["logical_block_size"])

    def test_direct_driver_only_and_symlink_escape_rejected(self):
        driver = self.sysfs / "bus/pci/drivers/example"
        driver.mkdir(parents=True)
        link = self.sysfs / "bus/pci/devices/pci-device0/driver"
        try:
            link.symlink_to(driver, target_is_directory=True)
        except OSError as exc:
            self.skipTest("symlink privilege unavailable: " + str(exc))
        row = hardware.collect_devices(self.sysfs, "pci")["data"][0]
        self.assertEqual(row["driver"], "example")
        self.assertEqual(row["usability"], "UNTESTED")
        link.unlink()
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(hardware.collect_devices(self.sysfs, "pci")["status"], "partial")
        link.unlink()
        link.symlink_to(self.sysfs / "gone", target_is_directory=True)
        self.assertEqual(hardware.collect_devices(self.sysfs, "pci")["status"], "partial")

    def test_parent_driver_is_not_inherited(self):
        (self.sysfs / "class/net/driver").mkdir()
        # driver_name is deliberately local to the supplied device.
        self.assertIsNone(hardware.driver_name(self.sysfs / "class/net/lo", self.sysfs))

    def test_zero_interface_index_is_partial_at_the_producer(self):
        put(self.sysfs, "class/net/lo/ifindex", "0")
        self.assertEqual(hardware.collect_devices(self.sysfs, "net")["status"], "partial")


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.sysfs = fixture(self.root)
        self.out = self.root / "run"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_boot(self.out, proc_root=self.proc, sys_root=self.sysfs, test_system="Linux"), 0)

    def rewrite(self, change):
        inventory = json.loads((self.out / "inventory.json").read_bytes())
        events = [json.loads(line) for line in (self.out / "events.jsonl").read_bytes().splitlines()]
        result = json.loads((self.out / "result.json").read_bytes())
        change(inventory, events, result)
        def encode(value):
            return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (self.out / "inventory.json").write_bytes(encode(inventory))
        events[-2]["data"]["inventory_sha256"] = hashlib.sha256((self.out / "inventory.json").read_bytes()).hexdigest()
        (self.out / "events.jsonl").write_bytes(b"".join(encode(event) for event in events))
        (self.out / "boot.log").write_bytes(b"".join(b"[AIOS-BOOT] " + encode(event) for event in events))
        result["files"] = {name: hashlib.sha256((self.out / name).read_bytes()).hexdigest() for name in verifier.ARTIFACTS[:-1]}
        (self.out / "result.json").write_bytes(encode(result))

    def test_good_fixture_passes_but_is_never_live(self):
        self.assertEqual(verifier.verify_bundle(self.out)["outcome"], "PASS")
        self.assertEqual(verifier.verify_bundle(self.out, require_live=True)["outcome"], "FAIL")

    def test_stale_identity_and_wrong_exit_are_rejected(self):
        self.assertEqual(verifier.verify_bundle(self.out, expected_run_id="old-run")["outcome"], "FAIL")
        self.assertEqual(verifier.verify_bundle(self.out, process_exit=9)["outcome"], "FAIL")

    def test_existing_artifact_directory_is_preserved_and_refused(self):
        before = (self.out / "result.json").read_bytes()
        with self.assertRaises(FileExistsError):
            run_boot(self.out)
        self.assertEqual((self.out / "result.json").read_bytes(), before)

    def test_log_after_ready_cannot_override_failure(self):
        with (self.out / "boot.log").open("ab") as stream:
            stream.write(b"PANIC after READY\n")
        self.assertEqual(verifier.verify_bundle(self.out)["outcome"], "FAIL")

    def test_semantically_invalid_inventory_fails_after_rehash(self):
        self.rewrite(lambda inv, ev, res: inv["inventory"]["memory"]["data"].update(total_bytes=True))
        self.assertEqual(verifier.verify_bundle(self.out)["outcome"], "FAIL")

    def test_action_and_identity_boundary_fails_after_rehash(self):
        self.rewrite(lambda inv, ev, res: ev[0]["data"].update(action_support="SUPPORTED"))
        self.assertEqual(verifier.verify_bundle(self.out)["outcome"], "FAIL")

    def test_duplicate_json_keys_rejected(self):
        path = self.out / "result.json"
        raw = path.read_bytes().replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')
        path.write_bytes(raw)
        self.assertEqual(verifier.verify_bundle(self.out)["outcome"], "FAIL")

    def test_source_hashes_are_checked_against_current_code(self):
        self.rewrite(lambda inv, ev, res: res["source_hashes"].update({key: "0" * 64 for key in res["source_hashes"]}))
        self.assertEqual(verifier.verify_bundle(self.out)["outcome"], "FAIL")

    def test_process_evidence_rejects_timeout_nonzero_and_stdout_tampering(self):
        execution = self.root / "execution"
        execution.mkdir()
        # Move only this test-owned fresh directory into its outer execution bundle.
        self.out.rename(execution / "run")
        output = (execution / "run/boot.log").read_bytes()
        (execution / "stdout.log").write_bytes(output)
        (execution / "stderr.log").write_bytes(b"")
        process = {"outcome": "exited", "exit_code": 0, "command": ["python3", "aios-boot.py"],
                   "stdout_sha256": hashlib.sha256(output).hexdigest(),
                   "stderr_sha256": hashlib.sha256(b"").hexdigest()}
        def check():
            (execution / "process.json").write_text(json.dumps(process))
            return verifier.verify_execution(execution, require_live=False)["outcome"]
        self.assertEqual(check(), "PASS")
        run_id = json.loads((execution / "run/result.json").read_bytes())["run_id"]
        self.assertEqual(verifier.verify_execution(execution, require_live=False, expected_run_id=run_id)["outcome"], "PASS")
        self.assertEqual(verifier.verify_execution(execution, require_live=False, expected_run_id="stale-run")["outcome"], "FAIL")
        process["exit_code"] = 1
        self.assertEqual(check(), "FAIL")
        process.update(exit_code=0, outcome="timeout")
        self.assertEqual(check(), "FAIL")
        process["outcome"] = "exited"
        (execution / "stdout.log").write_bytes(b"fake")
        self.assertEqual(check(), "FAIL")

    def test_unexpected_collector_exception_preserves_failure_reason(self):
        output = self.root / "exception"
        with mock.patch("aios_hosted.boot.collect_hardware", side_effect=RuntimeError("injected")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_boot(output, test_system="Linux"), 1)
        self.assertEqual(verifier.verify_bundle(output)["outcome"], "FAILED")
        self.assertIn("collector_exception:RuntimeError", (output / "boot.log").read_text())

    def test_reordered_or_added_terminal_is_rejected_after_rehash(self):
        self.rewrite(lambda inv, ev, res: ev[-1].update(event="READY"))
        self.assertEqual(verifier.verify_bundle(self.out)["outcome"], "FAIL")

    def test_missing_core_emits_failed_no_ready(self):
        (self.proc / "cpuinfo").unlink()
        failed = self.root / "failed"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_boot(failed, proc_root=self.proc, sys_root=self.sysfs, test_system="Linux"), 1)
        self.assertEqual(verifier.verify_bundle(failed)["outcome"], "FAILED")
        self.assertNotIn('"event":"READY"', (failed / "boot.log").read_text())

    def test_missing_optional_is_degraded_and_not_pass(self):
        (self.sysfs / "bus/usb/devices").rmdir()
        partial = self.root / "partial"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_boot(partial, proc_root=self.proc, sys_root=self.sysfs, test_system="Linux"), 2)
        self.assertEqual(verifier.verify_bundle(partial)["outcome"], "DEGRADED")

    def test_non_linux_is_explicitly_unsupported(self):
        unsupported = self.root / "unsupported"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_boot(unsupported, test_system="Windows"), 3)
        self.assertEqual(verifier.verify_bundle(unsupported)["outcome"], "UNSUPPORTED")

    def test_boot_contract_mirrors_implementation(self):
        contract = json.loads((ROOT / "hosted/contracts/boot-inventory-v1.contract.json").read_bytes())
        self.assertEqual(contract["sections"], list(hardware.SECTIONS))
        self.assertEqual(contract["exit_codes"], verifier.STATES)
        self.assertEqual(contract["max_devices_per_section"], hardware.MAX_DEVICES)
        self.assertEqual(contract["max_errors_per_section"], hardware.MAX_ERRORS)
        self.assertEqual(contract["artifact_files"], list(verifier.ARTIFACTS))


if __name__ == "__main__":
    unittest.main()
