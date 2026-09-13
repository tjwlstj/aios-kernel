"""Real temporary-file world checks; no model, VM or AIOS runtime is exercised."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import self_reference_world as world_module
from self_reference_world import MAX_STEPS, World, WorldError


def proposal(action, revision=None, prediction="NOOP", attribution="UNKNOWN"):
    return {"action": action, "expected_revision": revision, "prediction": prediction,
            "attribution": attribution}


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)

    def make(self, scenario="normal", name="world"):
        return World(self.parent / name, scenario, "test-run")

    def state(self, world):
        return json.loads((world.root / "object.json").read_bytes())

    def check_no_action_write(self, event, outcome):
        self.assertEqual(event["result"]["outcome"], outcome)
        self.assertEqual(event["action_before"], event["action_after"])

    def test_normal_writes_actual_file_and_requires_new_observation(self):
        world = self.make()
        self.assertIsNone(world.context()["observation"])
        self.assertIsNone(world.context()["last_set_step"])
        observed = world.step(proposal("OBSERVE", prediction="OBSERVED"))
        self.assertEqual(observed["result"]["attribution"], "UNKNOWN")
        applied = world.step(proposal("SET", 0, "APPLIED", "SELF"))
        self.assertEqual(applied["result"]["outcome"], "APPLIED")
        self.assertNotEqual(applied["action_before"]["sha256"], applied["action_after"]["sha256"])
        self.assertEqual(self.state(world)["value"], 7)
        self.assertEqual(self.state(world)["revision"], 1)
        self.assertEqual(world.context()["observation"]["value"], 0)
        self.assertEqual(world.context()["last_set_step"], 2)
        world.step(proposal("WAIT"))
        world.step(proposal("OBSERVE", prediction="OBSERVED", attribution="SELF"))
        context = world.context()
        self.assertEqual(context["observation"]["value"], 7)
        self.assertEqual(context["observation"]["observed_step"], 4)
        self.assertEqual(context["last_result"]["completed_step"], 4)
        self.assertTrue(context["last_result"]["observation_updated"])
        self.assertEqual(context["last_result"]["attribution"], "SELF")
        self.assertEqual(context["step"], 4)
        world.step(proposal("FINISH"))
        self.assertTrue(world.done)
        self.assertEqual(world.events[-1]["stop_reason"], "FINISH")

    def test_stale_intervention_keeps_input_context_and_rejects_old_revision(self):
        world = self.make("stale_after_observe")
        observed = world.step(proposal("OBSERVE"))
        self.assertIsNone(observed["input_context"]["observation"])
        self.assertEqual(observed["action_after"]["state"]["value"], 0)
        self.assertEqual(observed["after"]["state"]["value"], 3)
        self.assertEqual(world.context()["observation"]["revision"], 0)
        rejected = world.step(proposal("SET", 0, "APPLIED", "SELF"))
        self.check_no_action_write(rejected, "STALE")
        self.assertEqual(rejected["result"]["attribution"], "OTHER")
        self.assertEqual(rejected["input_context"]["observation"]["revision"], 0)
        world.step(proposal("OBSERVE"))
        self.assertEqual(world.context()["observation"]["value"], 3)
        self.assertEqual(world.context()["last_result"]["attribution"], "OTHER")
        world.step(proposal("SET", 1))
        world.step(proposal("OBSERVE"))
        self.assertEqual(self.state(world)["value"], 7)
        self.assertEqual(self.state(world)["revision"], 2)
        self.assertEqual(sum(len(e["interventions"]) for e in world.events), 1)

    def test_revoked_owner_cannot_be_bypassed_with_current_revision(self):
        world = self.make("revoke_before_apply")
        world.step(proposal("OBSERVE"))
        rejected = world.step(proposal("SET", 0))
        self.check_no_action_write(rejected, "DENIED")
        self.assertEqual(rejected["interventions"][0]["boundary"], "before")
        self.assertEqual(rejected["input_context"]["observation"]["owner"], world.context()["identity"])
        self.assertNotEqual(self.state(world)["owner"], world.context()["identity"])
        self.check_no_action_write(world.step(proposal("SET", 1)), "DENIED")
        self.assertEqual(self.state(world)["value"], 0)
        self.assertEqual(world.context()["last_set_step"], 3)

    def test_lost_channel_blocks_reads_and_writes_without_changing_file(self):
        world = self.make("channel_lost")
        world.step(proposal("OBSERVE"))
        before = (world.root / "object.json").read_bytes()
        rejected = world.step(proposal("SET", 0))
        self.check_no_action_write(rejected, "UNAVAILABLE")
        self.assertEqual(rejected["before"], rejected["after"])
        self.assertTrue(rejected["input_context"]["channel"]["enabled"])
        self.assertFalse(world.context()["channel"]["enabled"])
        self.check_no_action_write(world.step(proposal("OBSERVE")), "UNAVAILABLE")
        self.assertEqual(world.context()["observation"]["observed_step"], 1)
        self.assertEqual((world.root / "object.json").read_bytes(), before)

    def test_external_write_after_apply_is_not_attributed_to_self(self):
        world = self.make("external_after_apply")
        world.step(proposal("OBSERVE"))
        applied = world.step(proposal("SET", 0))
        self.assertEqual(applied["result"]["outcome"], "APPLIED")
        self.assertEqual(applied["result"]["attribution"], "SELF")
        self.assertEqual(applied["action_after"]["state"]["value"], 7)
        self.assertEqual(applied["action_after"]["state"]["revision"], 1)
        self.assertEqual(applied["after"]["state"]["value"], 3)
        self.assertEqual(applied["after"]["state"]["revision"], 2)
        self.assertEqual(world.context()["last_result"]["revision"], 1)
        seen = world.step(proposal("OBSERVE"))
        self.assertEqual(seen["result"]["attribution"], "OTHER")
        self.assertEqual(world.context()["observation"]["revision"], 2)
        world.step(proposal("SET", 2))
        self.assertEqual(self.state(world)["value"], 7)
        self.assertEqual(self.state(world)["revision"], 3)
        self.assertEqual(sum(len(e["interventions"]) for e in world.events), 1)

    def test_same_agent_name_new_instance_epoch_does_not_inherit_ownership(self):
        world = self.make("owner_replaced")
        world.step(proposal("OBSERVE"))
        owner, identity = self.state(world)["owner"], world.context()["identity"]
        self.assertEqual(owner["agent_id"], identity["agent_id"])
        self.assertNotEqual(owner["instance_id"], identity["instance_id"])
        self.assertEqual(owner["epoch"], identity["epoch"] + 1)
        self.check_no_action_write(world.step(proposal("SET", 1)), "DENIED")
        self.assertEqual(world.step(proposal("OBSERVE"))["result"]["attribution"], "OTHER")
        self.assertEqual(self.state(world)["value"], 0)

    def test_prediction_and_attribution_claims_do_not_grant_authority(self):
        world = self.make("revoke_before_apply")
        event = world.step(proposal("SET", 0, "APPLIED", "SELF"))
        self.check_no_action_write(event, "DENIED")
        self.assertEqual(event["proposal"]["prediction"], "APPLIED")
        self.assertEqual(event["result"]["attribution"], "UNKNOWN")
        self.assertTrue(event["proposal_valid"])

    def test_repeat_set_cannot_write_twice_with_old_revision(self):
        world = self.make()
        world.step(proposal("SET", 0))
        before = (world.root / "object.json").read_bytes()
        self.check_no_action_write(world.step(proposal("SET", 0)), "STALE")
        self.check_no_action_write(world.step(proposal("SET", 1)), "NOOP")
        self.assertEqual((world.root / "object.json").read_bytes(), before)
        self.assertEqual(self.state(world)["revision"], 1)

    def test_invalid_proposals_are_bounded_rejections_without_interventions(self):
        invalid = [None, [], {**proposal("SET", 0), "path": "../outside"},
                   proposal("DELETE", 0), proposal("SET", True), proposal("SET", 1.0),
                   proposal("SET", -1), proposal("OBSERVE", 0), proposal("SET", 0, "PASS"),
                   proposal("SET", 0, attribution="ME"), {**proposal("SET", 0), "command": "exit"},
                   {**proposal("SET", 0), "prediction": ["APPLIED"]}]
        for index, value in enumerate(invalid):
            with self.subTest(value=value):
                world = self.make("revoke_before_apply", "world-" + str(index))
                event = world.step(value)
                self.check_no_action_write(event, "REJECTED")
                self.assertFalse(event["proposal_valid"])
                self.assertEqual(event["interventions"], [])
                self.assertEqual(event["before"], event["after"])

    def test_large_invalid_input_is_hashed_not_copied_into_event(self):
        world = self.make()
        event = world.step({"text": "x" * 100000})
        self.assertEqual(event["result"]["outcome"], "REJECTED")
        self.assertIn("sha256", event["proposal"])
        self.assertLess((world.root / "event-001.json").stat().st_size, 32768)

    def test_finish_is_an_action_not_a_success_verdict_and_cap_is_finite(self):
        world = self.make()
        event = world.step(proposal("FINISH"))
        self.assertTrue(world.done)
        self.assertEqual(event["result"]["outcome"], "NOOP")
        self.assertEqual(self.state(world)["value"], 0)
        self.assertNotIn("success", event)
        limited = self.make(name="limited")
        for _ in range(MAX_STEPS):
            limited.step(proposal("WAIT"))
        self.assertEqual(limited.events[-1]["stop_reason"], "STEP_LIMIT")
        with self.assertRaisesRegex(WorldError, "world-ended"):
            limited.step(proposal("SET", 0))
        self.assertEqual(len(limited.events), MAX_STEPS)
        self.assertEqual(self.state(limited)["value"], 0)

    def test_context_and_events_are_copies_and_records_hash_exact_file_bytes(self):
        world = self.make()
        world.step(proposal("OBSERVE"))
        context = world.context()
        context["identity"]["epoch"] = 99
        context["observation"]["value"] = 999
        world.step(proposal("SET", 0))
        self.assertEqual(world.context()["identity"]["epoch"], 1)
        previous = None
        for event in world.events:
            loaded = json.loads((world.root / f"event-{event['step']:03d}.json").read_bytes())
            self.assertEqual(loaded, event)
            self.assertEqual(event["previous_event_sha256"], previous)
            body = copy.deepcopy(event)
            previous = body.pop("event_sha256")
            self.assertEqual(previous, hashlib.sha256(world_module.encoded(body)).hexdigest())
            event["result"]["outcome"] = "CORRUPTED"
        self.assertEqual(world.events[-1]["result"]["outcome"], "APPLIED")
        self.assertEqual(world.events[-1]["after"]["sha256"], hashlib.sha256((world.root / "object.json").read_bytes()).hexdigest())

    def test_exclusive_root_and_traversal_never_overwrite_existing_files(self):
        world = self.make()
        before = (world.root / "object.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.make()
        with self.assertRaisesRegex(WorldError, "root-traversal"):
            World(self.parent / ".." / "escape", "normal", "test-run")
        self.assertEqual((world.root / "object.json").read_bytes(), before)
        with self.assertRaises(WorldError):
            World(self.parent / "unknown", "bad", "test-run")

    def test_file_and_event_tampering_fail_closed(self):
        for filename in ("object.json", "manifest.json", "event-001.json"):
            world = self.make(name=filename.replace(".", "-"))
            world.step(proposal("OBSERVE"))
            (world.root / filename).write_bytes(b"{}\n")
            with self.assertRaises(WorldError):
                world.step(proposal("SET", 0))
            self.assertTrue(world.done)
            self.assertEqual(len(world.events), 1)

    def test_hardlinked_object_is_rejected_without_touching_outside(self):
        world = self.make()
        outside = self.parent / "outside.json"
        try:
            os.link(world.root / "object.json", outside)
        except OSError as exc:
            self.skipTest("hardlinks unavailable: " + str(exc))
        before = outside.read_bytes()
        with self.assertRaisesRegex(WorldError, "unsafe-file"):
            world.step(proposal("SET", 0))
        self.assertEqual(outside.read_bytes(), before)

    def test_symlinked_object_is_rejected_without_touching_outside(self):
        world = self.make()
        outside = self.parent / "outside.json"
        outside.write_bytes(b"private-outside\n")
        target = world.root / "object.json"
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError as exc:
            self.skipTest("symlinks unavailable: " + str(exc))
        with self.assertRaisesRegex(WorldError, "unsafe-file"):
            world.step(proposal("SET", 0))
        self.assertEqual(outside.read_bytes(), b"private-outside\n")

    def test_windows_reparse_flag_is_rejected_even_without_symlink_mode(self):
        self.assertTrue(world_module._linked(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)))

    def test_symlinked_parent_is_rejected(self):
        alias = self.parent / "alias"
        try:
            alias.symlink_to(self.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest("directory symlinks unavailable: " + str(exc))
        with self.assertRaisesRegex(WorldError, "unsafe-directory"):
            World(alias / "escape", "normal", "test-run")
        self.assertFalse((self.parent / "escape").exists())

    def test_replaced_root_cannot_redirect_later_writes(self):
        world = self.make()
        original = self.parent / "original"
        before = (world.root / "object.json").read_bytes()
        world.root.rename(original)
        world.root.mkdir()
        (world.root / "object.json").write_bytes(before)
        with self.assertRaisesRegex(WorldError, "root-replaced"):
            world.step(proposal("SET", 0))
        self.assertEqual((original / "object.json").read_bytes(), before)
        self.assertEqual((world.root / "object.json").read_bytes(), before)

    def test_same_run_id_gives_identical_initial_facts_in_independent_roots(self):
        first = self.make(name="flat")
        second = self.make(name="relational")
        self.assertEqual(first.context(), second.context())
        self.assertEqual(first.manifest, second.manifest)
        first.step(proposal("OBSERVE"))
        second.step(proposal("OBSERVE"))
        self.assertEqual(first.context(), second.context())


if __name__ == "__main__":
    unittest.main()
