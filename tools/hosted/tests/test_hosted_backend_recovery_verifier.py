"""Explicit crash recovery cannot turn incomplete or forged evidence into STOPPED."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
from backend_output_contract import backend_result, validate_backend_result
from verify_backend import FILES, verify_backend_runs
from verify_agent import verify_backend_delivery, verify_execution_backends
from verify_image import validate_model_actors
from test_hosted_backend_verifier import fixture, raw, read, save


def sample(identity, parent, at):
    fields = ["0"] * 50
    for index, value in {0: "S", 1: str(parent), 11: "10", 12: "5", 19: str(identity["process_start_ticks"]),
                         20: "1048576", 21: "64"}.items():
        fields[index] = value
    pid, uid = identity["process_id"], identity["uid"]
    return {"schema_version": 1, **identity, "read_start_ns": at, "read_end_ns": at + 10,
        "clock_ticks_per_second": 100, "page_bytes": 4096,
        "raw_stat": str(pid) + " (fixture (nested) worker) " + " ".join(fields) + "\n",
        "raw_status": f"Pid: {pid}\nTgid: {pid}\nUid: {uid} {uid} {uid} {uid}\n",
        "user_ticks": 10, "system_ticks": 5, "cpu_total_ns": 150000000, "rss_pages": 64,
        "rss_bytes_estimate": 262144, "virtual_bytes": 1048576, "scope": "single-linux-process",
        "consistency": "sequential-copied-read", "memory_accuracy": "kernel-approximate", "source_only": True}


def recovery_fixture(base, *, count=1):
    state, source, runs = fixture(base, count)
    run = runs[0]
    events = [json.loads(line) for line in (run / "events.jsonl").read_bytes().splitlines()][:3]
    (run / "events.jsonl").write_bytes(b"".join(raw(event) for event in events))
    service, descriptor = events[-1]["service_record"], events[-1]["descriptor"]
    save(run / "service.json", service)
    (run / "result.json").unlink()
    parent, child = service["supervisor_identity"], service["child_identity"]
    owner = {**parent, "process_id": 101, "process_start_ticks": 50}
    lease = {"acquired_monotonic_ns": 1450, "owner": sample(owner, 1, 1400),
             "lease_kind": "owned-supervisor-child-pidfd", "child_pidfd_live_at_acquisition": True,
             "child_pidfd_acquired_monotonic_ns": 1435,
             "supervisor": sample(parent, owner["process_id"], 1420),
             "child": sample(child, parent["process_id"], 1440), "service_record": service,
             "descriptor": descriptor, "state_directory": {"device": 123, "inode": 456},
             "sockets": {"control.sock": {"device": 123, "inode": 600}, "backend.sock": {"device": 123, "inode": 601}}}
    recovery = {"started_monotonic_ns": 1500, "completed_monotonic_ns": 1600,
        "supervisor_returncode": -9, "supervisor_reaped": True, "child_exit_observed": True,
        "child_exit_code": None, "signal": "SIGTERM", "signal_sent_monotonic_ns": 1510,
        "child_exit_monotonic_ns": 1550, "signal_via": "retained-pidfd",
        "sockets": {name: {**value, "removed": True} for name, value in lease["sockets"].items()}}
    start = read(run / "start.json")
    receipt = {"schema_version": 1, **{name: start[name] for name in ("service_id", "instance_id", "start_generation")},
        "capture_kind": "fixture", "source_only": True, "outcome": "RECOVERED", "error": None,
        "lease": lease, "recovery": recovery, "old_run_hashes": {}, "source_hashes": start["source_hashes"]}
    (state / "recoveries").mkdir()
    receipt_path = state / "recoveries" / (run.name + ".json")
    save(receipt_path, receipt)
    rehash_recovery(run, receipt_path)
    if count == 1:
        latest = read(state / "latest.json")
        save(state / "latest.json", {**latest, "state": "RUNNING", "service_record": service, "descriptor": descriptor})
    return state, source, runs, receipt_path, owner


def rehash_recovery(run, receipt_path):
    receipt = read(receipt_path)
    receipt["old_run_hashes"] = {name: hashlib.sha256((run / name).read_bytes()).hexdigest() for name in FILES if (run / name).is_file()}
    save(receipt_path, receipt)


class RecoveryVerifierTests(unittest.TestCase):
    def rejected(self, mutate):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, runs, receipt_path, owner = recovery_fixture(Path(temporary))
            mutate(state, runs[0], receipt_path)
            if receipt_path.is_file():
                try:
                    rehash_recovery(runs[0], receipt_path)
                except (ValueError, UnicodeError):
                    pass
            value = verify_backend_runs(state, source, allow_recovered=True, recovery_owner=owner)
            self.assertEqual(value["outcome"], "FAIL", value)

    def test_recovered_is_explicit_and_never_a_normal_stopped_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, runs, _receipt, owner = recovery_fixture(Path(temporary))
            self.assertEqual(verify_backend_runs(state, source)["outcome"], "FAIL")
            value = verify_backend_runs(state, source, allow_recovered=True, recovery_owner=owner)
            self.assertEqual(value["outcome"], "PASS", value)
            self.assertEqual(value["state"], "RECOVERED")
            self.assertEqual(len(value["recovered_runs"]), 1)
            self.assertEqual(value["normal_runs"], [])
            self.assertIsNone(value["runs"][0]["result"])
            self.assertFalse((runs[0] / "result.json").exists())
            self.assertEqual(verify_backend_runs(state, source, require_live=True, allow_recovered=True)["outcome"], "FAIL")

    def test_recovered_then_normal_new_generation_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, _runs, _receipt, owner = recovery_fixture(Path(temporary), count=2)
            value = verify_backend_runs(state, source, allow_recovered=True, recovery_owner=owner)
            self.assertEqual(value["outcome"], "PASS", value)
            self.assertEqual([run["state"] for run in value["runs"]], ["RECOVERED", "STOPPED"])
            self.assertEqual(len(value["recovered_runs"]), len(value["normal_runs"]))

    def test_unowned_wrong_parent_changed_identity_and_dead_acquisition_reject(self):
        mutations = [lambda v: v["lease"]["owner"].update(process_id=102),
            lambda v: v["lease"]["supervisor"].update(raw_stat=v["lease"]["supervisor"]["raw_stat"].replace(") S 101 ", ") S 102 ")),
            lambda v: v["lease"]["child"].update(process_start_ticks=102),
            lambda v: v["lease"]["child"].update(raw_stat=v["lease"]["child"]["raw_stat"].replace(") S ", ") Z ")),
            lambda v: v["lease"]["owner"].update(raw_status="Pid: 101\nTgid: 101\nUid: 0 0 0 0\n")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                def change(_state, _run, path):
                    value = read(path)
                    mutate(value)
                    save(path, value)
                self.rejected(change)

    def test_live_parent_false_zero_manufactured_exit_and_missing_death_reject(self):
        for fields in ({"supervisor_returncode": None}, {"supervisor_returncode": 0}, {"supervisor_returncode": False},
                       {"supervisor_reaped": False}, {"child_exit_observed": False}, {"child_exit_observed": 1},
                       {"child_exit_code": 0}, {"child_exit_code": False}, {"child_exit_monotonic_ns": None}):
            def change(_state, _run, path):
                value = read(path)
                value["recovery"].update(fields)
                save(path, value)
            self.rejected(change)

    def test_unknown_bool_and_reordered_time_fields_reject(self):
        for where, field, bad in (("lease", "acquired_monotonic_ns", 1399),
                ("recovery", "started_monotonic_ns", 1400), ("recovery", "signal_sent_monotonic_ns", 1560),
                ("recovery", "completed_monotonic_ns", 1540), ("recovery", "signal", "SIGKILL"),
                ("recovery", "unreported_field", True)):
            def change(_state, _run, path):
                value = read(path)
                value[where][field] = bad
                save(path, value)
            self.rejected(change)

    def test_replaced_or_unremoved_sockets_fail(self):
        for fields in ({"removed": False}, {"removed": 1}, {"inode": 999}, {"device": True}):
            def change(_state, _run, path):
                value = read(path)
                value["recovery"]["sockets"]["control.sock"].update(fields)
                save(path, value)
            self.rejected(change)

    def test_incomplete_original_and_manufactured_result_reject_after_rehash(self):
        self.rejected(lambda _state, run, _path: (run / "health.json").unlink())
        self.rejected(lambda _state, run, _path: (run / "result.json").write_bytes(b'{"exit_code":0}\n'))
        self.rejected(lambda _state, run, _path: (run / "events.jsonl").write_bytes((run / "events.jsonl").read_bytes()[:-1]))
        def remove_running(_state, run, _path):
            events = (run / "events.jsonl").read_bytes().splitlines(keepends=True)
            (run / "events.jsonl").write_bytes(b"".join(events[:2]))
        self.rejected(remove_running)

    def test_missing_truncated_duplicate_receipt_reject(self):
        self.rejected(lambda _state, _run, path: path.unlink())
        self.rejected(lambda _state, _run, path: path.write_bytes(path.read_bytes()[:-4]))
        self.rejected(lambda _state, _run, path: path.write_bytes(b'{"schema_version":1,"schema_version":1}\n'))

    def test_false_live_claim_and_descriptor_change_reject_after_full_hash_update(self):
        def change(_state, _run, path):
            value = read(path)
            value["lease"]["descriptor"]["listener_inode"] += 1
            save(path, value)
        self.rejected(change)

    def test_public_recovery_only_in_new_console_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, _runs, _receipt, _owner = recovery_fixture(Path(temporary))
            verified = verify_backend_runs(state, source, allow_recovered=True)
            value = {**read(state / "latest.json"), "action": "recover", "state": "RECOVERED",
                     "service_record": verified["runs"][0]["service_record"], "descriptor": None}
            validate_backend_result(value, action="recover")
            command = {"name": "backend", "args": ["recover"], "outcome": "OK", "result": value}
            backend_result(command, 7)
            with self.assertRaises(ValueError):
                backend_result(command, 6)

    def test_recovered_receipt_requires_one_matching_displayed_recover_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, _runs, _receipt, _owner = recovery_fixture(Path(temporary), count=2)
            verified = verify_backend_runs(state, source, allow_recovered=True)
            first = verified['runs'][0]
            value = {**read(state / 'latest.json'), 'action': 'recover', 'state': 'RECOVERED',
                     'service_record': first['service_record'], 'descriptor': None}
            command = {'name': 'backend', 'args': ['recover'], 'outcome': 'OK', 'result': value}
            backend = {'managed_runs': verified['runs']}
            verify_backend_delivery([command], backend)
            for commands in ([], [command, command], [{**command, 'result': {**value, 'action': 'status'}}],
                             [{**command, 'result': {**value, 'state': 'STOPPED'}}]):
                with self.assertRaises(ValueError):
                    verify_backend_delivery(commands, backend)

    def test_execution_must_finish_before_crash_recovery_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, _runs, _receipt, _owner = recovery_fixture(Path(temporary))
            verified = verify_backend_runs(state, source, allow_recovered=True)
            model = verified['runs'][0]
            binding = {'descriptor': model['descriptor'], 'initial_proof': model['proofs'][0]}
            warmup = {'outcome': 'OK', 'backend_execution': {'before': {'read_start_ns': 1350},
                                                           'after': {'read_end_ns': 1400}}}
            run = {'execution_binding': binding, 'warmup': warmup, 'requests': []}
            verify_execution_backends([run], {'managed_runs': verified['runs']})
            warmup['backend_execution']['after']['read_end_ns'] = 1501
            with self.assertRaises(ValueError):
                verify_execution_backends([run], {'managed_runs': verified['runs']})

    def test_only_retained_live_pidfd_acquisition_and_signal_are_accepted(self):
        for where, field, value in (('lease', 'lease_kind', 'stored-pid'),
                ('lease', 'child_pidfd_live_at_acquisition', False), ('lease', 'child_pidfd_live_at_acquisition', 1),
                ('lease', 'child_pidfd_acquired_monotonic_ns', 1451), ('recovery', 'signal_via', 'pid'),
                ('recovery', 'signal_via', None)):
            def change(_state, _run, path):
                receipt = read(path)
                receipt[where][field] = value
                save(path, receipt)
            self.rejected(change)

    def test_generic_image_joins_recovery_to_same_cli_and_fixed_smoke_rejects_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state, source, _runs, _receipt, owner = recovery_fixture(base, count=2)
            archive = base / 'archive'
            archive.mkdir()
            state.rename(archive / 'backend')
            state = archive / 'backend'
            verified = verify_backend_runs(state, source, allow_recovered=True, recovery_owner=owner)
            self.assertEqual(verified['outcome'], 'PASS', verified)
            (base / 'installation-files').mkdir()
            save(base / 'installation-files/model-config.json', read(state / 'config.json'))
            (archive / 'session').mkdir()
            session_start = {'schema_version': 7, 'event': 'START', 'data': {'source_process': sample(owner, 1, 900)}}
            save(archive / 'session/session.events.jsonl', session_start)
            save(archive / 'root-result.json', {'worker_process_id': owner['process_id']})
            public = read(state / 'latest.json')
            first, second = verified['runs']
            commands = []
            for action, record, descriptor, status in (
                    ('start', first['events'][2]['service_record'], first['descriptor'], 'RUNNING'),
                    ('recover', first['service_record'], None, 'RECOVERED'),
                    ('start', second['events'][2]['service_record'], second['descriptor'], 'RUNNING')):
                value = {**public, 'action': action, 'service_record': record, 'descriptor': descriptor, 'state': status}
                commands.append({'name': 'backend', 'args': [action], 'outcome': 'OK', 'result': value})
            worker = {'uid': 1000, 'completed_monotonic_ns': 3000,
                      'cleanup': [{'response': {'state': 'ABSENT'}}, {'response': public}]}
            boot = {'boot_id': owner['host_boot_id'], 'started_monotonic_ns': 500}
            with self.assertRaisesRegex(ValueError, 'model_recovery_not_allowed'):
                validate_model_actors(archive, base, {}, boot, worker, commands, source, require_live=False)
            actors = validate_model_actors(archive, base, {}, boot, worker, commands, source,
                                           require_live=False, allow_recovered=True)
            self.assertEqual([row['state'] for row in actors['backend_runs']], ['RECOVERED', 'STOPPED'])
            stranger = {**owner, 'process_id': 99}
            session_start['data']['source_process'] = sample(stranger, 1, 900)
            save(archive / 'session/session.events.jsonl', session_start)
            with self.assertRaisesRegex(ValueError, 'model_recovery_owner'):
                validate_model_actors(archive, base, {}, boot, worker, commands, source,
                                      require_live=False, allow_recovered=True)

    def test_generic_image_cleanup_can_close_recovered_only_with_full_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state, source, _runs, _receipt, owner = recovery_fixture(base)
            archive = base / 'archive'
            archive.mkdir()
            state.rename(archive / 'backend')
            state = archive / 'backend'
            verified = verify_backend_runs(state, source, allow_recovered=True, recovery_owner=owner)
            self.assertEqual(verified['outcome'], 'PASS', verified)
            (base / 'installation-files').mkdir()
            save(base / 'installation-files/model-config.json', read(state / 'config.json'))
            (archive / 'session').mkdir()
            save(archive / 'session/session.events.jsonl', {'schema_version': 7, 'event': 'START',
                 'data': {'source_process': sample(owner, 1, 900)}})
            save(archive / 'root-result.json', {'worker_process_id': owner['process_id']})
            value = {**read(state / 'latest.json'), 'action': 'recover', 'state': 'RECOVERED',
                     'service_record': verified['runs'][0]['service_record'], 'descriptor': None}
            command = {'name': 'backend', 'args': ['recover'], 'outcome': 'OK', 'result': value}
            worker = {'uid': 1000, 'completed_monotonic_ns': 3000, 'cleanup': [{'response': {'state': 'ABSENT'}},
                      {'response': {**value, 'action': 'stop'}}]}
            boot = {'boot_id': owner['host_boot_id'], 'started_monotonic_ns': 500}
            actors = validate_model_actors(archive, base, {}, boot, worker, [command], source,
                                           require_live=False, allow_recovered=True)
            self.assertEqual([row['state'] for row in actors['backend_runs']], ['RECOVERED'])
            receipt_path = next((state / 'recoveries').iterdir())
            receipt = read(receipt_path)
            receipt['recovery']['child_exit_observed'] = False
            save(receipt_path, receipt)
            with self.assertRaises(ValueError):
                validate_model_actors(archive, base, {}, boot, worker, [command], source,
                                      require_live=False, allow_recovered=True)


if __name__ == "__main__":
    unittest.main()
