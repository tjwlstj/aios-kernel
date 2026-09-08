"""Independent Cell transition replay, separate from Linux source ownership."""
from __future__ import annotations

import ast
import copy
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_management.binding import Authority, MAX_GENERATION
from cell_output_contract import validate_cell_transition, validate_parent_continuity
from newagent_output_contract import validate_snapshot
from test_hosted_management import source


def ready():
    authority = Authority()
    authority.initialize()
    authority.discover([source()])
    authority.bind(source())
    return authority


class CellVerifierTests(unittest.TestCase):
    def test_checker_imports_no_runtime_implementation(self):
        tree = ast.parse((ROOT / "tools/hosted/cell_output_contract.py").read_text(encoding="utf-8"))
        modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(module and module.startswith("aios_") for module in modules))

    def test_explicit_deactivate_activate_and_reconcile_generations(self):
        authority = ready()
        before = authority.snapshot()
        result = authority.set_parent(False)
        inactive = result["snapshot"]
        validate_cell_transition(before, inactive, "cell-deactivate", None)
        self.assertEqual(inactive["parent"]["generation"], 2)
        self.assertEqual(inactive["binding"], before["binding"])
        self.assertFalse(inactive["binding_current"])
        result = authority.set_parent(True)
        active = result["snapshot"]
        validate_cell_transition(inactive, active, "cell-activate", None)
        self.assertEqual(active["parent"]["generation"], 3)
        self.assertFalse(active["binding_current"])
        authority.discover([source()])
        authority.reconcile(source())
        validate_parent_continuity(active, authority.snapshot())
        self.assertEqual(authority.snapshot()["binding"]["generation"], 2)

    def test_same_state_and_status_are_exactly_idempotent(self):
        authority = ready()
        for active, action in ((True, "cell-activate"), (False, "cell-deactivate")):
            if not active:
                authority.set_parent(False)
            before = authority.snapshot()
            result = authority.set_parent(active)
            validate_cell_transition(before, result["snapshot"], action, None)
            validate_cell_transition(before, authority.snapshot(), "cell-status", None)
            self.assertEqual(authority.snapshot(), before)

    def test_initial_unbound_cell_lifecycle_keeps_no_binding(self):
        authority = Authority()
        authority.initialize()
        before = authority.snapshot()
        after = authority.set_parent(False)["snapshot"]
        validate_cell_transition(before, after, "cell-deactivate", None)
        self.assertEqual(after["state"], "UNBOUND")
        self.assertIsNone(after["binding"])

    def test_generation_flip_requires_exact_one_increment(self):
        authority = ready()
        before = authority.snapshot()
        after = authority.set_parent(False)["snapshot"]
        for generation in (1, 3):
            changed = copy.deepcopy(after)
            changed["parent"]["generation"] = changed["canonical"]["generation"] = generation
            for bit in changed["nodebits"]:
                bit["parent_node_generation"] = generation
            validate_snapshot(changed)
            with self.assertRaisesRegex(ValueError, "transition_changed"):
                validate_cell_transition(before, changed, "cell-deactivate", None)

    def test_cell_transition_cannot_alter_source_counter_or_lifecycle(self):
        authority = ready()
        before = authority.snapshot()
        after = authority.set_parent(False)["snapshot"]
        for edit in (lambda row: row.update(completed_requests=2),
                     lambda row: row.update(source_generation=2, lifecycle_state="exited", model_ready=False)):
            changed = copy.deepcopy(after)
            edit(changed["current_source"])
            validate_snapshot(changed)
            with self.assertRaisesRegex(ValueError, "transition_changed"):
                validate_cell_transition(before, changed, "cell-deactivate", None)

    def test_cell_transition_cannot_advance_binding_or_change_authority(self):
        authority = ready()
        before = authority.snapshot()
        after = authority.set_parent(False)["snapshot"]
        for edit in (lambda value: value["binding"].update(generation=2),
                     lambda value: value.update(authority_instance=str(uuid.uuid4())),
                     lambda value: value["retired_instances"].append(str(uuid.uuid4()))):
            changed = copy.deepcopy(after)
            edit(changed)
            validate_snapshot(changed)
            with self.assertRaisesRegex(ValueError, "transition_changed"):
                validate_cell_transition(before, changed, "cell-deactivate", None)

    def test_activate_does_not_restore_old_discovery_or_binding(self):
        authority = ready()
        authority.set_parent(False)
        before = authority.snapshot()
        authority.set_parent(True)
        authority.discover([source()])
        authority.reconcile(source())
        with self.assertRaises(ValueError):
            validate_cell_transition(before, authority.snapshot(), "cell-activate", None)

    def test_status_cannot_hide_source_refresh_or_parent_transition(self):
        authority = ready()
        before = authority.snapshot()
        authority.observe(source(completed_requests=2))
        with self.assertRaisesRegex(ValueError, "status_changed"):
            validate_cell_transition(before, authority.snapshot(), "cell-status", None)
        with self.assertRaises(ValueError):
            validate_cell_transition(before, before, "cell-status", "orphan")

    def test_idempotent_command_cannot_advance_generation_or_invalidate(self):
        authority = ready()
        before = authority.snapshot()
        authority.observe(None)
        with self.assertRaisesRegex(ValueError, "idempotent_changed"):
            validate_cell_transition(before, authority.snapshot(), "cell-activate", None)
        with self.assertRaises(ValueError):
            validate_cell_transition(before, before, "cell-activate", "overflow")

    def test_overflow_preserves_generations_active_source_and_binding_but_invalidates(self):
        authority = ready()
        authority.parent["generation"] = authority.canonical["generation"] = MAX_GENERATION
        authority.binding["canonical_generation"] = authority.binding["parent_generation"] = MAX_GENERATION
        before = authority.snapshot()
        result = authority.set_parent(False)
        self.assertEqual(result["reason"], "overflow")
        validate_cell_transition(before, result["snapshot"], "cell-deactivate", "overflow")
        self.assertTrue(result["snapshot"]["parent"]["active"])
        self.assertFalse(result["snapshot"]["binding_current"])
        with self.assertRaises(ValueError):
            validate_cell_transition(before, before, "cell-deactivate", "overflow")
        with self.assertRaises(ValueError):
            validate_cell_transition(before, result["snapshot"], "cell-deactivate", None)

    def test_overflow_cannot_be_faked_before_maximum_generation(self):
        authority = ready()
        before = authority.snapshot()
        authority.observe(None)
        with self.assertRaises(ValueError):
            validate_cell_transition(before, authority.snapshot(), "cell-deactivate", "overflow")

    def test_uninitialized_transition_is_only_init_order_rejection(self):
        before = Authority().snapshot()
        validate_cell_transition(before, before, "cell-status", None)
        for action in ("cell-activate", "cell-deactivate"):
            validate_cell_transition(before, before, action, "init-order")
            with self.assertRaises(ValueError):
                validate_cell_transition(before, before, action, None)

    def test_noncell_parent_continuity_allows_source_exit_but_not_cell_change(self):
        authority = ready()
        before = authority.snapshot()
        authority.observe(source(source_generation=2, lifecycle_state="exited", model_ready=False))
        validate_parent_continuity(before, authority.snapshot())
        authority.set_parent(False)
        with self.assertRaisesRegex(ValueError, "unexpected_parent_transition"):
            validate_parent_continuity(before, authority.snapshot())

    def test_inactive_state_restores_and_observes_exit_without_reactivation(self):
        authority = ready()
        before = authority.snapshot()
        authority.set_parent(False)
        inactive = authority.snapshot()
        validate_cell_transition(before, inactive, "cell-deactivate", None)
        restored = Authority.from_state(authority.export_state())
        validate_cell_transition(inactive, restored.snapshot(), "cell-status", None)
        exited = source(source_generation=2, lifecycle_state="exited", model_ready=False)
        restored.observe(exited)
        validate_parent_continuity(inactive, restored.snapshot())
        self.assertEqual(restored.snapshot()["current_source"], exited)
        self.assertFalse(restored.snapshot()["parent"]["active"])

    def test_unknown_action_and_malformed_snapshot_fail_closed(self):
        before = ready().snapshot()
        with self.assertRaises(ValueError):
            validate_cell_transition(before, before, "cell-toggle", None)
        for key, value in (("bound_nodes", True), ("extra", 0)):
            after = copy.deepcopy(before)
            after[key] = value
            with self.assertRaises(ValueError):
                validate_cell_transition(before, after, "cell-status", None)


class CellRunVerifierTests(unittest.TestCase):
    """Rehash every changed artifact, then replay the full schema-3 run."""

    def setUp(self):
        from test_hosted_agent_verifier import AgentVerifierTests
        from verify_agent import SOURCES, digest
        base = AgentVerifierTests("test_consistent_fixture_passes_without_claiming_process_or_model_bytes")
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.fixture = base
        # Reuse the real artifact builder, retaining an explicit fixture source
        # tree instead of making these records appear to be executed code.
        for name in SOURCES:
            path = base.sources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(("# retained Cell test fixture " + name + "\n").encode())
        base.start.update(schema_version=3, source_hashes={name: digest((base.sources / name).read_bytes()) for name in SOURCES})
        base.source = copy.deepcopy(base.events[3]["source_record"])
        base.authority = Authority(base.events[3]["management_snapshot"]["authority_instance"])
        base.authority.initialize()
        base.authority.discover([base.source])
        base.authority.bind(base.source)
        base.events = base.events[:4]
        for active, action in ((False, "cell-deactivate"), (False, "cell-deactivate"),
                               (True, "cell-activate"), (True, "cell-activate")):
            base.authority.set_parent(active)
            base.event("COMMAND", base.source, action=action)
        base.authority.discover([base.source])
        base.event("COMMAND", base.source, action="room-discover")
        base.authority.reconcile(base.source)
        base.event("COMMAND", base.source, action="room-reconcile")
        base.user.update(source_before=copy.deepcopy(base.source), binding_generation=2)
        base.source["completed_requests"] += 1
        base.authority.observe(base.source)
        base.user["source_after"] = copy.deepcopy(base.source)
        base.event("COMMAND", base.source, action="ask", file=base.user_path)
        base.source.update(lifecycle_state="exited", model_ready=False, source_generation=2)
        base.authority.observe(base.source)
        base.event("STOPPING", base.source)
        base.event("STOPPED", base.source)
        for event in base.events:
            event.update(schema_version=3, resource_file=None)
        base.result.update(schema_version=3, source_record=base.source, management_snapshot=base.authority.snapshot())
        (base.run / "resources").mkdir()
        base.save()

    def fails_after_rehash(self, *reasons):
        self.fixture.save()
        result = self.fixture.verify()
        self.assertEqual(result["outcome"], "FAIL", result)
        if reasons:
            self.assertTrue(any(reason in result["reasons"][0] for reason in reasons), result)

    def test_complete_schema3_cell_fixture_passes_and_is_not_live(self):
        result = self.fixture.verify()
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertFalse(result["process_exit_verified"])
        self.assertFalse(result["model_bytes_verified"])
        self.assertEqual(result["management_snapshot"]["parent"]["generation"], 3)
        self.assertEqual(result["management_snapshot"]["binding"]["generation"], 2)
        self.assertEqual(self.fixture.verify(require_live=True)["outcome"], "FAIL")

    def test_rehashed_wrong_parent_generation_rejected(self):
        snapshot = self.fixture.events[4]["management_snapshot"]
        snapshot["parent"]["generation"] = snapshot["canonical"]["generation"] = 3
        for bit in snapshot["nodebits"]:
            bit["parent_node_generation"] = 3
        self.fails_after_rehash("cell_contract:transition_changed")

    def test_rehashed_parent_change_without_cell_action_rejected(self):
        self.fixture.events[4]["action"] = "room-discover"
        self.fails_after_rehash("unexpected_parent_transition")

    def test_rehashed_cell_source_mutation_rejected(self):
        event = self.fixture.events[4]
        event["source_record"]["completed_requests"] = 2
        event["management_snapshot"]["current_source"]["completed_requests"] = 2
        self.fails_after_rehash("cell_contract:transition_changed", "cell_changed_producer")

    def test_rehashed_cell_binding_mutation_rejected(self):
        self.fixture.events[4]["management_snapshot"]["binding"]["generation"] = 2
        self.fails_after_rehash("cell_contract:transition_changed")

    def test_rehashed_unknown_cell_action_rejected(self):
        self.fixture.events[5]["action"] = "cell-toggle"
        self.fails_after_rehash("event_action")

    def test_run2_cannot_claim_cell_lifecycle_after_rehash(self):
        self.fixture.start["schema_version"] = self.fixture.result["schema_version"] = 2
        for event in self.fixture.events:
            event["schema_version"] = 2
        self.fails_after_rehash("unexpected_parent_transition", "event_action")

    def test_protocol2_cell_success_is_not_a_legacy_cli4_operation(self):
        from newagent_output_contract import agent_result
        event = self.fixture.events[4]
        value = {"schema_version": 2, "action": "cell-deactivate", "outcome": "OK", "error": None,
            "state": "RUNNING", "service_kind": "AI_SERVICE", "source_record": event["source_record"],
            "management_snapshot": event["management_snapshot"], "management_outcome": "accepted",
            "inference_receipt": None, "resource_result": None, "resource_actions": "UNSUPPORTED", "capture_kind": "fixture"}
        command = {"name": "cell", "args": ["deactivate"], "outcome": "OK", "result": value}
        with self.assertRaisesRegex(ValueError, "command_arguments"):
            agent_result(command, 4)

    def test_rehashed_idempotent_cell_command_cannot_hide_source_refresh(self):
        event = self.fixture.events[5]
        event["source_record"]["completed_requests"] = 2
        event["management_snapshot"]["current_source"]["completed_requests"] = 2
        self.fails_after_rehash("cell_contract:idempotent_changed", "cell_changed_producer")


if __name__ == "__main__":
    unittest.main()
