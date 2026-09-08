"""Pure relation fixtures; these do not establish live Linux resource evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
sys.path.insert(0, str(ROOT / "tools/hosted"))
from aios_management.binding import Authority
from aios_management.resources import build_observation, config_digest, is_current, link, validate_relation
from aios_resources import ResourceError
from aios_resources.proc import accounting, parse_pressure, parse_stat
from test_hosted_management import source


def proof_fixture(*, at=100, capture_kind="fixture"):
    return {"schema_version": 1, "nonce": str(uuid.uuid4()), "descriptor": {
        "schema_version": 1, "source_namespace": "linux-model-backend", "source_instance": "2f04f83e-559a-4196-a728-d6121e381a50",
        "source_generation": 1, "lifecycle_state": "active", "source_only": True, "producer_owned": True,
        "host_boot_id": source()["host_boot_id"], "process_id": 234, "process_start_ticks": 222,
        "launcher_process_id": 233, "launcher_start_ticks": 221, "model_id": "aios-fixture",
        "model_sha256": "1" * 64, "backend_sha256": "4" * 64, "endpoint": "http://127.0.0.1:18081", "listener_inode": 345},
        "listener_proof": {"raw_tcp_line": " 0: 0100007F:46A1 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 345 1\n",
                           "fd_target": "socket:[345]", "fd_number": 7},
        "peer_pid": 233, "peer_uid": 1000, "observed_monotonic_ns": at, "capture_kind": capture_kind}


def sample_fixture(pid, start, *, at=1000, user=10, system=5, rss=64):
    fields = ["0"] * 50
    for index, value in {0: "S", 1: "1", 11: str(user), 12: str(system), 19: str(start),
                         20: "1048576", 21: str(rss)}.items():
        fields[index] = value
    raw = str(pid) + " (fixture (cpu) worker) " + " ".join(fields) + "\n"
    return {"schema_version": 1, "host_boot_id": source()["host_boot_id"], "process_id": pid,
        "process_start_ticks": start, "uid": 1000, "read_start_ns": at, "read_end_ns": at + 10,
        "clock_ticks_per_second": 100, "page_bytes": 4096, "raw_stat": raw,
        "raw_status": f"Name:\tfixture\nPid:\t{pid}\nTgid:\t{pid}\nUid:\t1000\t1000\t1000\t1000\n",
        **accounting(parse_stat(raw, pid), 100, 4096), "scope": "single-linux-process",
        "consistency": "sequential-copied-read", "memory_accuracy": "kernel-approximate", "source_only": True}


def pressure_fixture(*, at=1000, total=100):
    raw = f"some avg10=1.25 avg60=0.20 avg300=0.01 total={total}\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    return {"schema_version": 1, "read_start_ns": at, "read_end_ns": at + 10,
        "scope": "linux-system", "attribution": "unattributed", "source_only": True,
        "metrics": {kind: parse_pressure(raw, kind) for kind in ("cpu", "memory", "io")}}


def fixture(kind="sample", *, capture_kind="fixture"):
    """Return a fully consistent bounded fixture for producer/CLI/verifier tests."""
    config = {"schema_version": 1, "endpoint": "http://127.0.0.1:18081", "model_id": "aios-fixture",
        "model_path": "/fixture/model.gguf", "model_sha256": "1" * 64,
        "backend_path": "/fixture/backend", "backend_sha256": "4" * 64, "provenance_sha256": "5" * 64}
    authority = Authority()
    authority.initialize()
    authority.discover([source()])
    authority.bind(source())
    snapshot = authority.snapshot()
    main = {"host_boot_id": source()["host_boot_id"], "process_id": 123, "process_start_ticks": 111, "uid": 1000}
    proof = proof_fixture(capture_kind=capture_kind)
    relation = link(snapshot, source(), main, proof, config)
    before = {"main": sample_fixture(123, 111), "backend": sample_fixture(234, 222, user=100),
        "pressure": pressure_fixture(), "backend_proof": proof_fixture(at=1000, capture_kind=capture_kind)}
    after = {"main": sample_fixture(123, 111, at=1_000_001_000, user=11, rss=32),
        "backend": sample_fixture(234, 222, at=1_000_001_000, user=260, system=20, rss=48),
        "pressure": pressure_fixture(at=1_000_001_000, total=110),
        "backend_proof": proof_fixture(at=1_000_001_000, capture_kind=capture_kind)}
    after_source = source(completed_requests=1 + (kind == "request"))
    authority.observe(after_source)
    kwargs = {"kind": kind, "request_id": str(uuid.uuid4()) if kind == "request" else None,
        "relation_before": relation, "relation_after": copy.deepcopy(relation), "before": before, "after": after,
        "source_before": source(), "source_after": after_source, "management_before": snapshot,
        "management_after": authority.snapshot(), "config": config}
    observation = build_observation(**kwargs)
    result = {"schema_version": 1, "action": kind, "outcome": "OK", "error": None, "relation": copy.deepcopy(relation),
        "relation_current": True, "observation": observation, "observation_only": True, "ownership_valid": False,
        "resource_actions": "UNSUPPORTED", "capture_kind": capture_kind}
    return {"config": config, "authority": authority, "relation": relation, "main_identity": main,
        "backend_proof": proof, "kwargs": kwargs, "observation": observation, "result": result,
        "source": after_source, "snapshot": authority.snapshot()}


def receipt_fixture(data):
    from aios_agent.inference import request_body
    request = request_body("Say hello.").decode("utf-8")
    response = json.dumps({"model": data["config"]["model_id"], "content": "Hello.", "tokens_predicted": 2})
    return {"schema_version": 1, "request_id": data["observation"]["request_id"], "started_at": "2026-09-07T00:00:00+00:00",
        "purpose": "user", "model_id": data["config"]["model_id"], "model_sha256": "1" * 64,
        "backend_sha256": "4" * 64, "provenance_sha256": "5" * 64, "request_body": request,
        "request_sha256": hashlib.sha256(request.encode()).hexdigest(), "response_body": response,
        "response_sha256": hashlib.sha256(response.encode()).hexdigest(), "content": "Hello.", "tokens_predicted": 2,
        "elapsed_ns": 1_000_000_000, "outcome": "OK", "error": None,
        "source_before": data["kwargs"]["source_before"], "source_after": data["kwargs"]["source_after"],
        "authority_instance": data["relation"]["authority_instance"], "binding_generation": 1}


class ResourceManagementTests(unittest.TestCase):
    def test_link_is_copy_pure_and_typed_canonical_relation(self):
        data = fixture()
        relation = data["relation"]
        validate_relation(relation)
        self.assertEqual((relation["canonical"]["id"], relation["parent"]["id"]), (101, 1))
        relation["canonical"]["id"] = 999
        self.assertEqual(data["snapshot"]["canonical"]["id"], 101)
        self.assertEqual(data["result"]["relation"]["canonical"]["id"], 101)

    def test_counter_progress_changes_no_relationship_generation(self):
        data = fixture("request")
        before = copy.deepcopy(data["relation"])
        self.assertTrue(is_current(before, data["snapshot"], data["source"], data["main_identity"],
                                   data["backend_proof"], data["config"]))
        self.assertEqual(data["relation"], before)
        self.assertEqual(data["observation"]["relation_before"], data["observation"]["relation_after"])

    def test_explicit_relink_only_advances_relation_generation(self):
        data = fixture()
        second = link(data["snapshot"], data["source"], data["main_identity"], data["backend_proof"],
                      data["config"], previous=data["relation"])
        self.assertEqual(second["relation_id"], data["relation"]["relation_id"])
        self.assertEqual(second["relation_generation"], 2)
        self.assertEqual(second["binding_generation"], 1)
        second["relation_generation"] = 64
        with self.assertRaisesRegex(ResourceError, "relation-limit"):
            link(data["snapshot"], data["source"], data["main_identity"], data["backend_proof"], data["config"], second)

    def test_missing_discovery_and_binding_cannot_link(self):
        data = fixture()
        authority = Authority()
        authority.initialize()
        for snapshot in (authority.snapshot(), {**data["snapshot"], "binding_current": False}):
            with self.assertRaises(ResourceError):
                link(snapshot, data["source"], data["main_identity"], data["backend_proof"], data["config"])

    def test_identity_config_and_backend_replacement_are_stale(self):
        data = fixture()
        baseline = (data["snapshot"], data["source"], data["main_identity"], data["backend_proof"], data["config"])
        for position, path, changed in ((2, ["process_start_ticks"], 112),
                (3, ["descriptor", "source_instance"], str(uuid.uuid4())),
                (3, ["descriptor", "process_start_ticks"], 223), (4, ["provenance_sha256"], "6" * 64)):
            args = copy.deepcopy(baseline)
            target = args[position]
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = changed
            self.assertFalse(is_current(data["relation"], *args))

    def test_backend_dynamic_nonce_does_not_change_freshness(self):
        data = fixture()
        later = proof_fixture(at=200)
        self.assertTrue(is_current(data["relation"], data["snapshot"], data["source"], data["main_identity"], later, data["config"]))

    def test_source_readiness_and_binding_must_be_current(self):
        data = fixture()
        changed = source(model_ready=False, source_generation=2)
        data["authority"].observe(changed)
        self.assertFalse(is_current(data["relation"], data["authority"].snapshot(), changed,
            data["main_identity"], data["backend_proof"], data["config"]))

    def test_bool_integer_overflow_and_promoted_ownership_rejected(self):
        data = fixture()
        for path, value in ((["relation_generation"], True), (["relation_generation"], 65),
                (["binding_generation"], 1 << 63), (["canonical", "id"], True),
                (["main_identity", "process_start_ticks"], False), (["ownership_valid"], True),
                (["resource_actions"], "SUPPORTED"), (["source_record", "model_ready"], False)):
            relation = copy.deepcopy(data["relation"])
            target = relation
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            with self.assertRaises(ResourceError, msg=str(path)):
                validate_relation(relation)

    def test_window_computes_two_cpu_values_and_allows_rss_decline(self):
        value = fixture()["observation"]
        self.assertEqual(value["cpu"]["main"]["cpu_time_ns"], 10_000_000)
        self.assertEqual(value["cpu"]["backend"]["cpu_time_ns"], 1_750_000_000)
        self.assertGreater(value["cpu"]["backend"]["cpu_time_ns"], value["cpu"]["backend"]["elapsed_ns"])
        self.assertLess(value["after"]["main"]["rss_bytes_estimate"], value["before"]["main"]["rss_bytes_estimate"])
        self.assertEqual(set(value["cpu"]), {"main", "backend"})

    def test_counter_rollback_and_pid_reuse_reject_entire_window(self):
        for field, sample in (("user_ticks", sample_fixture(123, 111, at=2000, user=9)),
                               ("start", sample_fixture(123, 112, at=2000))):
            args = fixture()["kwargs"]
            args["after"]["main"] = sample
            with self.assertRaises(ResourceError, msg=field):
                build_observation(**args)

    def test_replayed_proof_or_overlapping_window_rejected(self):
        for tamper in (lambda k: k["after"].update(backend_proof=k["before"]["backend_proof"]),
                lambda k: k["after"]["main"].update(read_start_ns=1005, read_end_ns=1020)):
            args = fixture()["kwargs"]
            tamper(args)
            with self.assertRaises(ResourceError):
                build_observation(**args)

    def test_request_count_and_sample_kind_are_exact(self):
        args = fixture("request")["kwargs"]
        args.update(kind="sample", request_id=None)
        with self.assertRaisesRegex(ResourceError, "resource-request-counter"):
            build_observation(**args)

    def test_invalid_raw_never_becomes_an_observation(self):
        for name, value in (("raw_status", "Pid: 123\nTgid: 124\nUid: 1000 1000 1000 1000\n"),
                            ("rss_bytes_estimate", 1), ("user_ticks", True), ("raw_stat", "123 (broken) S\n")):
            args = fixture()["kwargs"]
            args["after"]["main"][name] = value
            with self.assertRaises(ResourceError):
                build_observation(**args)

    def test_pressure_unavailable_is_explicit_not_zero(self):
        args = fixture()["kwargs"]
        args["after"]["pressure"]["metrics"]["memory"] = {"state": "UNAVAILABLE", "error": "pressure-missing",
            "raw": None, "some": None, "full": None, "full_valid": False}
        value = build_observation(**args)
        self.assertIsNone(value["after"]["pressure"]["metrics"]["memory"]["some"])

    def test_system_pressure_cannot_be_attributed_to_main(self):
        args = fixture()["kwargs"]
        args["after"]["pressure"]["attribution"] = "node-101"
        with self.assertRaises(ResourceError):
            build_observation(**args)

    def test_failure_does_not_mutate_management_or_relation(self):
        data = fixture()
        before = copy.deepcopy(data["kwargs"])
        data["kwargs"]["after"]["main"]["cpu_total_ns"] = -1
        invalid = copy.deepcopy(data["kwargs"])
        with self.assertRaises(ResourceError):
            build_observation(**data["kwargs"])
        self.assertEqual(data["kwargs"], invalid)
        self.assertEqual(data["authority"].snapshot(), before["management_after"])
        self.assertTrue(data["source"]["model_ready"])

    def test_relink_rejects_persisted_management_generation_rollback(self):
        data = fixture()
        for kind in ("canonical", "binding", "source", "counter"):
            previous = copy.deepcopy(data["relation"])
            if kind == "canonical":
                previous["canonical"]["generation"] = previous["parent"]["generation"] = 2
            elif kind == "binding":
                previous["binding_generation"] = 2
            elif kind == "source":
                previous["source_record"]["source_generation"] = 2
            else:
                previous["source_record"]["completed_requests"] = 2
            validate_relation(previous)
            with self.assertRaisesRegex(ResourceError, "rollback|regression", msg=kind):
                link(data["snapshot"], data["source"], data["main_identity"], data["backend_proof"], data["config"], previous)

    def test_relink_same_instance_preserves_process_and_start_identity(self):
        data = fixture()
        for mutate in (lambda r: r["main_identity"].update(process_start_ticks=112),
                       lambda r: r["source_record"].update(service_start_generation=2)):
            previous = copy.deepcopy(data["relation"])
            mutate(previous)
            with self.assertRaisesRegex(ResourceError, "resource-participant"):
                link(data["snapshot"], data["source"], data["main_identity"], data["backend_proof"], data["config"], previous)

    def test_new_source_instance_requires_later_start_and_explicit_binding(self):
        data = fixture()
        changed = source(source_instance=str(uuid.uuid4()), service_start_generation=2, process_id=124)
        authority = data["authority"]
        authority.discover([changed])
        authority.reconcile(changed)
        main = {**data["main_identity"], "process_id": 124, "process_start_ticks": 112}
        relation = link(authority.snapshot(), changed, main, data["backend_proof"], data["config"], data["relation"])
        self.assertEqual((relation["relation_generation"], relation["binding_generation"]), (2, 2))
        snapshot = authority.snapshot()
        changed["service_start_generation"] = 1
        for record in (snapshot["current_source"], snapshot["discovered_source"], snapshot["binding"]["source"]):
            record["service_start_generation"] = 1
        with self.assertRaisesRegex(ResourceError, "resource-generation-rollback"):
            link(snapshot, changed, main, data["backend_proof"], data["config"], data["relation"])

    def test_same_binding_generation_cannot_capture_changed_source_semantics(self):
        data = fixture()
        changed = source(source_generation=2)
        snapshot = copy.deepcopy(data["snapshot"])
        for record in (snapshot["current_source"], snapshot["discovered_source"], snapshot["binding"]["source"]):
            record["source_generation"] = 2
        with self.assertRaisesRegex(ResourceError, "resource-binding-generation"):
            link(snapshot, changed, data["main_identity"], data["backend_proof"], data["config"], data["relation"])


if __name__ == "__main__":
    unittest.main()
