"""Pure hosted authority lifecycle, identity and hostile-state contracts.

These tests supply explicit producer fixtures. Actual inference and authenticated
Linux reads are independently required by the MAIN-service integration lane.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_management.binding import (
    Authority, BindingError, CELL_ID, MAX_GENERATION, MAX_JSON, MAX_RETIRED,
    NODE_ID, PRESENT_ID, SOURCE_BOUND_ID, strict_json, validate_source,
)


def source(**overrides):
    return {"schema_version": 1, "source_namespace": "linux-userspace-service",
            "source_id": "c12dc859-2576-4bff-9d29-b7ea889f496a",
            "source_instance": "324aac51-1d07-44d8-8860-e2b77bd7433c",
            "source_generation": 1, "service_start_generation": 1,
            "source_kind": "ai-service", "source_role": "main",
            "lifecycle_state": "active", "producer_owned": True, "copied_read": True,
            "model_ready": True, "model_sha256": "1" * 64,
            "warmup_request_sha256": "2" * 64, "warmup_response_sha256": "3" * 64,
            "completed_requests": 1, "host_boot_id": "3c929d56-d918-4a3c-a9f7-07e40283aa73",
            "process_id": 123, "source_only": True, **overrides}


class ManagementTests(unittest.TestCase):
    def ready(self):
        authority = Authority()
        self.assertAccepted(authority.initialize())
        self.assertAccepted(authority.discover([source()]))
        self.assertAccepted(authority.bind(source()))
        return authority

    def assertAccepted(self, result):
        self.assertEqual((result["outcome"], result["reason"]), ("accepted", "none"))

    def assertRejected(self, result, reason, *, stale=True):
        self.assertEqual((result["outcome"], result["reason"]), ("rejected", reason))
        if stale:
            self.assertFalse(result["snapshot"]["binding_current"])
            self.assertFalse(result["snapshot"]["binding_valid"])

    def test_explicit_hierarchy_initialization_and_typed_identity(self):
        authority = Authority()
        self.assertEqual(authority.snapshot()["state"], "UNINITIALIZED")
        for call in (lambda: authority.discover([source()]), lambda: authority.bind(source()),
                     lambda: authority.observe(source()), lambda: authority.reconcile(source())):
            self.assertRejected(call(), "init-order")
        self.assertRejected(authority.initialize(parent_cell_id=2), "orphan")
        self.assertRejected(authority.initialize(cell_id=True), "schema")
        self.assertAccepted(authority.initialize())
        self.assertNotEqual(authority.authority_instance, source()["source_instance"])
        self.assertEqual(authority.snapshot()["canonical"]["parent_cell_id"], CELL_ID)

    def test_semantic_counterparts_stay_equal_to_native_header_without_importing_abi(self):
        header = (ROOT / "kernel/include/kernel/kernel_room_management.h").read_text(encoding="utf-8")
        for name, value in (("CELL_ID_MAIN", CELL_ID), ("NODE_ID_MAIN_AI", NODE_ID),
                            ("NODEBIT_ID_PRESENT", PRESENT_ID),
                            ("NODEBIT_ID_SOURCE_BOUND", SOURCE_BOUND_ID)):
            match = re.search(r"^#define KERNEL_ROOM_" + name + r"\s+([0-9]+)U$", header, re.M)
            self.assertIsNotNone(match, name)
            self.assertEqual(int(match.group(1)), value)
        view = self.ready().snapshot()
        self.assertEqual([(row["id"], row["name"]) for row in view["nodebits"]],
                         [(1001, "present"), (1002, "source-bound")])
        self.assertEqual([row["class"] for row in view["nodebits"]], ["state", "validity"])
        self.assertEqual(view["authority_namespace"], "aios-hosted-management")
        self.assertTrue(view["observation_only"] and view["management_only"])
        self.assertEqual(view["resource_actions"], "UNSUPPORTED")

    def test_counter_increases_do_not_change_any_generation(self):
        authority = self.ready()
        before = authority.snapshot()
        self.assertAccepted(authority.observe(source(completed_requests=40)))
        view = authority.snapshot()
        self.assertEqual(view["binding"], before["binding"])
        self.assertEqual(view["canonical"], before["canonical"])
        self.assertEqual(view["parent"], before["parent"])
        self.assertEqual(view["current_source"]["source_generation"], 1)
        self.assertEqual(view["current_source"]["completed_requests"], 40)
        self.assertRejected(authority.observe(source(completed_requests=39)), "counter-regression")

    def test_missing_observation_and_duplicate_discovery_invalidate_cached_binding(self):
        for operation in (lambda a: a.observe(None), lambda a: a.discover([]),
                          lambda a: a.discover([source(), source()])):
            authority = self.ready()
            result = operation(authority)
            self.assertIn(result["reason"], {"missing", "duplicate"})
            self.assertEqual(result["snapshot"]["state"], "STALE")
            self.assertFalse(authority.snapshot()["binding_current"])
            self.assertRejected(authority.observe(source()), "stale")
            self.assertAccepted(authority.discover([source()]))
            self.assertFalse(authority.snapshot()["binding_current"])
            self.assertAccepted(authority.reconcile(source()))
            self.assertEqual(authority.snapshot()["binding"]["generation"], 2)

    def test_request_progress_between_discovery_and_binding_is_accepted(self):
        authority = Authority()
        authority.initialize()
        authority.discover([source()])
        self.assertAccepted(authority.bind(source(completed_requests=2)))
        self.assertEqual(authority.snapshot()["binding"]["generation"], 1)

    def test_new_instance_requires_rediscovery_and_reconcile(self):
        authority = self.ready()
        restarted = source(source_instance=str(uuid.uuid4()), service_start_generation=2,
                           process_id=456, completed_requests=1)
        self.assertRejected(authority.observe(restarted), "stale")
        self.assertRejected(authority.reconcile(restarted), "stale")
        self.assertAccepted(authority.discover([restarted]))
        self.assertFalse(authority.snapshot()["binding_current"])
        self.assertRejected(authority.bind(restarted), "already-bound")
        self.assertAccepted(authority.reconcile(restarted))
        view = authority.snapshot()
        self.assertEqual(view["binding"]["generation"], 2)
        self.assertEqual(view["current_source"]["source_generation"], 1)
        self.assertRejected(authority.discover([source(service_start_generation=3)]), "retired-instance")

    def test_same_instance_cannot_change_boot_pid_or_start_generation(self):
        for changes in ({"host_boot_id": str(uuid.uuid4())}, {"process_id": 456},
                        {"service_start_generation": 2}, {"source_id": str(uuid.uuid4())}):
            with self.subTest(changes=changes):
                self.assertRejected(self.ready().observe(source(**changes)), "instance")

    def test_new_boot_with_new_instance_is_explicitly_reconciled(self):
        authority = self.ready()
        restarted = source(source_instance=str(uuid.uuid4()), host_boot_id=str(uuid.uuid4()),
                           service_start_generation=2)
        self.assertRejected(authority.observe(restarted), "stale")
        self.assertAccepted(authority.discover([restarted]))
        self.assertAccepted(authority.reconcile(restarted))

    def test_new_instance_requires_initial_source_generation_and_increasing_start(self):
        for changes, reason in (({"source_generation": 2, "service_start_generation": 2}, "stale"),
                                ({"service_start_generation": 1}, "generation-rollback")):
            row = source(source_instance=str(uuid.uuid4()), **changes)
            self.assertRejected(self.ready().discover([row]), reason)

    def test_model_identity_change_requires_semantic_generation_and_reconcile(self):
        authority = self.ready()
        updated = source(model_sha256="4" * 64)
        self.assertRejected(authority.observe(updated), "stale")
        updated["source_generation"] = 2
        self.assertRejected(authority.observe(updated), "stale")
        self.assertAccepted(authority.discover([updated]))
        self.assertAccepted(authority.reconcile(updated))
        self.assertRejected(authority.observe(source()), "generation-rollback")

    def test_backend_readiness_failure_is_distinct_from_source_exit(self):
        authority = self.ready()
        failed = source(source_generation=2, model_ready=False)
        self.assertRejected(authority.observe(failed), "model-not-ready")
        self.assertEqual(authority.snapshot()["current_source"]["lifecycle_state"], "active")
        self.assertAccepted(authority.discover([failed]))
        self.assertRejected(authority.reconcile(failed), "model-not-ready")
        exited = source(source_generation=3, model_ready=False, lifecycle_state="exited")
        self.assertRejected(authority.observe(exited), "source-exited")
        self.assertRejected(authority.discover([source(source_generation=4)]), "retired-instance")

    def test_warmup_evidence_is_required_before_binding(self):
        cold = source(model_ready=False, completed_requests=0, model_sha256=None,
                      warmup_request_sha256=None, warmup_response_sha256=None)
        authority = Authority()
        authority.initialize()
        self.assertAccepted(authority.discover([cold]))
        self.assertRejected(authority.bind(cold), "model-not-ready")
        for changes in ({"model_ready": True}, {"completed_requests": 1},
                        {"warmup_request_sha256": "2" * 64}):
            with self.assertRaises(BindingError):
                validate_source({**cold, **changes})

    def test_exited_producer_cannot_claim_current_model_readiness(self):
        with self.assertRaisesRegex(BindingError, "source-exited"):
            validate_source(source(lifecycle_state="exited"))
        authority = self.ready()
        self.assertRejected(authority.observe(source(lifecycle_state="exited", model_ready=False,
                                                     source_generation=2)), "source-exited")
        view = authority.snapshot()
        self.assertFalse(view["nodebits"][0]["value"])
        self.assertFalse(view["nodebits"][0]["valid"])
        self.assertFalse(view["nodebits"][1]["value"])
        self.assertEqual(view["nodebits"][1]["class"], "validity")

    def test_parent_lifecycle_invalidates_parent_and_node_generations(self):
        authority = self.ready()
        self.assertAccepted(authority.set_parent(False))
        self.assertEqual(authority.snapshot()["parent"]["generation"], 2)
        self.assertEqual(authority.snapshot()["canonical"]["generation"], 2)
        self.assertRejected(authority.discover([source()]), "orphan")
        self.assertAccepted(authority.set_parent(True))
        self.assertAccepted(authority.discover([source()]))
        self.assertAccepted(authority.reconcile(source()))
        view = authority.snapshot()
        self.assertEqual(view["binding"]["parent_generation"], 3)
        self.assertEqual(view["binding"]["generation"], 2)
        self.assertEqual(view["nodebits"][1]["parent_node_generation"], 3)

    def test_inactive_observation_copies_known_source_exit_without_binding(self):
        authority = self.ready()
        binding = authority.snapshot()["binding"]
        authority.set_parent(False)
        exited = source(lifecycle_state="exited", model_ready=False, source_generation=2)
        self.assertRejected(authority.observe(exited), "orphan")
        view = authority.snapshot()
        self.assertEqual(view["current_source"], exited)
        self.assertEqual(view["binding"], binding)
        self.assertFalse(view["source_trusted"] or view["binding_confirmed"])
        self.assertIsNone(view["discovered_source"])
        self.assertEqual(Authority.from_state(authority.export_state()).snapshot(), view)

    def test_inactive_observation_retains_counter_floor_without_advancing_generations(self):
        authority = self.ready()
        authority.set_parent(False)
        previous = authority.snapshot()
        self.assertRejected(authority.observe(source(completed_requests=5)), "orphan")
        view = authority.snapshot()
        self.assertEqual(view["current_source"]["completed_requests"], 5)
        for key in ("parent", "canonical", "binding"):
            self.assertEqual(view[key], previous[key])
        self.assertRejected(authority.observe(source(completed_requests=4)), "counter-regression")
        self.assertEqual(authority.snapshot(), view)

    def test_inactive_observation_cannot_introduce_or_replace_a_source(self):
        authority = Authority()
        authority.initialize()
        authority.set_parent(False)
        before = authority.snapshot()
        self.assertRejected(authority.observe(source()), "orphan")
        self.assertEqual(authority.snapshot(), before)
        authority = self.ready()
        authority.set_parent(False)
        before = authority.snapshot()
        replacement = source(source_instance=str(uuid.uuid4()), service_start_generation=2, process_id=124)
        self.assertRejected(authority.observe(replacement), "orphan")
        self.assertEqual(authority.snapshot(), before)
        self.assertRejected(authority.discover([replacement]), "orphan")
        self.assertEqual(authority.snapshot(), before)

    def test_inactive_same_instance_still_rejects_identity_and_generation_tamper(self):
        for changed, reason in ((source(process_id=124), "instance"),
                (source(service_start_generation=2), "instance"),
                (source(host_boot_id=str(uuid.uuid4())), "instance"),
                (source(model_ready=False), "stale"), (source(source_generation=True), "schema")):
            authority = self.ready()
            authority.set_parent(False)
            before = authority.snapshot()
            self.assertRejected(authority.observe(changed), reason)
            self.assertEqual(authority.snapshot(), before)

    def test_inactive_exit_cannot_resurrect_and_activation_does_not_rebind(self):
        authority = self.ready()
        authority.set_parent(False)
        exited = source(lifecycle_state="exited", model_ready=False, source_generation=2)
        self.assertRejected(authority.observe(exited), "orphan")
        self.assertRejected(authority.observe(source(source_generation=3)), "retired-instance")
        authority = Authority.from_state(authority.export_state())
        self.assertAccepted(authority.set_parent(True))
        self.assertFalse(authority.snapshot()["binding_current"])
        self.assertIsNone(authority.snapshot()["discovered_source"])
        replacement = source(source_instance=str(uuid.uuid4()), service_start_generation=2, process_id=124)
        self.assertRejected(authority.observe(replacement), "stale")
        self.assertAccepted(authority.discover([replacement]))
        self.assertAccepted(authority.reconcile(replacement))
        self.assertEqual(authority.snapshot()["binding"]["generation"], 2)
        self.assertEqual(authority.snapshot()["parent"]["generation"], 3)

    def test_source_shape_namespace_kind_role_and_numeric_contracts(self):
        for key, value, reason in (("extra", 1, "schema"), ("schema_version", True, "schema"),
                                  ("source_namespace", "native-slm-agent-tree", "namespace"),
                                  ("source_kind", "console-runtime", "kind"),
                                  ("source_role", "worker", "role"),
                                  ("source_generation", 0, "zero-generation"),
                                  ("source_generation", True, "schema"),
                                  ("source_generation", 1.0, "schema"),
                                  ("source_generation", MAX_GENERATION + 1, "overflow"),
                                  ("producer_owned", 1, "schema"),
                                  ("source_instance", "00000000-0000-0000-0000-000000000000", "instance"),
                                  ("model_sha256", "x" * 64, "schema")):
            with self.subTest(key=key, value=value):
                self.assertRejected(self.ready().observe(source(**{key: value})), reason)
        authority = self.ready()
        self.assertRejected(authority.discover([source()] * 3), "overflow")

    def test_all_exported_copies_are_independent_from_authority(self):
        authority = self.ready()
        view = authority.snapshot()
        view["binding"]["source"]["source_generation"] = 400
        view["parent"]["active"] = False
        exported = authority.export_state()
        exported["current_source"]["source_id"] = str(uuid.uuid4())
        self.assertTrue(authority.snapshot()["binding_current"])
        supplied = source(completed_requests=2)
        self.assertAccepted(authority.observe(supplied))
        supplied["completed_requests"] = 500
        self.assertEqual(authority.snapshot()["current_source"]["completed_requests"], 2)

    def test_valid_state_round_trip_in_every_lifecycle_phase(self):
        authority = Authority()
        phases = [lambda: None, authority.initialize, lambda: authority.discover([source()]),
                  lambda: authority.bind(source()), lambda: authority.observe(source(completed_requests=2)),
                  lambda: authority.observe(None), lambda: authority.discover([source(completed_requests=2)]),
                  lambda: authority.reconcile(source(completed_requests=2)),
                  lambda: authority.set_parent(False)]
        for phase in phases:
            phase()
            encoded = json.dumps(authority.export_state()).encode()
            restored = Authority.from_state(strict_json(encoded))
            self.assertEqual(restored.snapshot(), authority.snapshot())

    def test_corrupt_persisted_relationships_and_unsupported_extensions_reject(self):
        baseline = self.ready().export_state()
        def changed(path, value):
            row = copy.deepcopy(baseline)
            cursor = row
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = value
            return row
        for path, value in ((["unknown"], 1), (["schema_version"], True),
                            (["canonical", "parent_cell_id"], 2),
                            (["binding", "parent_generation"], 2),
                            (["binding", "generation"], 0),
                            (["current_source", "source_generation"], 0),
                            (["binding", "source", "process_id"], 456),
                            (["discovered_source", "model_sha256"], "4" * 64),
                            (["source_trusted"], False), (["binding"], None),
                            (["parent", "active"], False),
                            (["retired_instances"], [source()["source_instance"]])):
            with self.subTest(path=path), self.assertRaises(BindingError):
                Authority.from_state(changed(path, value))

    def test_json_transport_rejects_duplicates_nonfinite_depth_size_and_bom(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
                    b'{"x":' + b'[' * 20 + b'0' + b']' * 20 + b'}',
                    b'{"x":"' + b'a' * MAX_JSON + b'"}', b'\xef\xbb\xbf{}', b'[]'):
            with self.subTest(raw=raw[:25]), self.assertRaises(BindingError):
                strict_json(raw)

    def test_bounded_retired_instances_and_generation_exhaustion_fail_closed(self):
        authority = self.ready()
        authority.retired_instances = [str(uuid.uuid4()) for _ in range(MAX_RETIRED)]
        self.assertRejected(authority.discover([source(source_instance=str(uuid.uuid4()),
                                                       service_start_generation=2)]), "overflow")
        authority = self.ready()
        authority.binding["generation"] = MAX_GENERATION
        authority.observe(None)
        authority.discover([source()])
        self.assertRejected(authority.reconcile(source()), "overflow")
        authority = self.ready()
        authority.parent["generation"] = MAX_GENERATION
        authority.canonical["generation"] = MAX_GENERATION
        self.assertRejected(authority.set_parent(False), "overflow")


if __name__ == "__main__":
    unittest.main()
