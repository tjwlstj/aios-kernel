"""Hostile copied evidence must fail independent resource replay."""
from __future__ import annotations

import ast
import copy
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
from resource_output_contract import (validate_backend_proof, validate_relation, validate_relation_progression,
                                      validate_resource_result)
from test_hosted_resources_management import fixture, pressure_fixture, receipt_fixture, sample_fixture


class ResourceVerifierTests(unittest.TestCase):
    def accepted(self, data, **kwargs):
        validate_resource_result(data["result"], source=data["source"], snapshot=data["snapshot"],
                                 config=data["config"], **kwargs)

    def rejected(self, mutator, *, kind="sample", reason=None):
        data = fixture(kind)
        mutator(data)
        with self.assertRaisesRegex(ValueError, reason or ".*"):
            self.accepted(data)

    def test_independent_module_imports_no_producer_or_management(self):
        tree = ast.parse((ROOT / "tools/hosted/resource_output_contract.py").read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith("aios_") for name in imports))
        self.assertIn("newagent_output_contract", imports)

    def test_fixture_sample_and_request_replay_successfully(self):
        self.accepted(fixture())
        data = fixture("request")
        self.accepted(data, receipt=receipt_fixture(data))

    def test_fixture_never_promoted_by_outer_live_claim(self):
        data = fixture()
        data["result"]["capture_kind"] = "live"
        with self.assertRaises(ValueError):
            self.accepted(data, require_live=True)
        data = fixture(capture_kind="live")
        self.accepted(data, require_live=True)
        data["result"]["observation"]["before"]["backend_proof"]["capture_kind"] = "fixture"
        with self.assertRaises(ValueError):
            self.accepted(data, require_live=True)

    def test_unknown_fields_bool_ids_and_overflow_fail(self):
        for key, value in (("schema_version", True), ("relation_current", 1), ("unknown", None)):
            self.rejected(lambda d, k=key, v=value: d["result"].update({k: v}))
        for field, value in (("relation_generation", True), ("relation_generation", 65),
                             ("binding_generation", 1 << 63)):
            relation = fixture()["relation"]
            relation[field] = value
            with self.assertRaises(ValueError):
                validate_relation(relation)

    def test_canonical_and_authority_identity_mismatch_fail(self):
        self.rejected(lambda d: d["result"]["relation"]["canonical"].update(id=123))
        self.rejected(lambda d: d["result"]["relation"].update(authority_instance=str(uuid.uuid4())))
        self.rejected(lambda d: d["result"]["relation"]["parent"].update(id=True))

    def test_claimed_cpu_or_memory_number_must_match_raw(self):
        for field in ("user_ticks", "system_ticks", "cpu_total_ns", "rss_pages", "rss_bytes_estimate", "virtual_bytes"):
            self.rejected(lambda d, f=field: d["result"]["observation"]["after"]["main"].__setitem__(f,
                d["result"]["observation"]["after"]["main"][f] + 1), reason="raw_accounting")

    def test_negative_boolean_and_overflow_counters_fail(self):
        for value in (-1, True, 1 << 64):
            self.rejected(lambda d, v=value: d["result"]["observation"]["after"]["backend"].update(user_ticks=v))
        self.rejected(lambda d: d["result"]["observation"]["after"]["main"].update(clock_ticks_per_second=0))

    def test_process_start_change_raw_and_identity_still_invalidates_window(self):
        self.rejected(lambda d: d["result"]["observation"]["after"].update(main=sample_fixture(123, 112, at=2000)))

    def test_cpu_counter_regression_fails_even_with_consistent_raw(self):
        self.rejected(lambda d: d["result"]["observation"]["after"].update(main=sample_fixture(123, 111, at=2000, user=9)))

    def test_rss_decline_and_more_than_one_cpu_are_valid(self):
        data = fixture()
        self.accepted(data)
        self.assertGreater(data["observation"]["cpu"]["backend"]["cpu_time_ns"],
                           data["observation"]["cpu"]["backend"]["elapsed_ns"])
        self.assertLess(data["observation"]["after"]["main"]["rss_bytes_estimate"],
                        data["observation"]["before"]["main"]["rss_bytes_estimate"])

    def test_derived_cpu_values_are_recomputed(self):
        for field in ("elapsed_ns", "user_ticks", "system_ticks", "total_ticks", "cpu_time_ns"):
            self.rejected(lambda d, f=field: d["result"]["observation"]["cpu"]["backend"].__setitem__(f,
                d["result"]["observation"]["cpu"]["backend"][f] + 1), reason="cpu_delta")

    def test_raw_status_pid_uid_and_duplicate_claims_fail(self):
        for raw in ("Pid: 123\nTgid: 124\nUid: 1000 1000 1000 1000\n",
                    "Pid: 123\nTgid: 123\nUid: 1000 0 1000 1000\n",
                    "Pid: 123\nPid: 123\nTgid: 123\nUid: 1000 1000 1000 1000\n"):
            self.rejected(lambda d, r=raw: d["result"]["observation"]["after"]["main"].update(raw_status=r))

    def test_model_hash_config_hash_and_backend_listener_tamper_fail(self):
        self.rejected(lambda d: d["config"].update(provenance_sha256="6" * 64), reason="config_hash")
        for mutation in (lambda p: p["descriptor"].update(model_sha256="6" * 64),
                lambda p: p["listener_proof"].update(fd_target="socket:[999]"),
                lambda p: p["listener_proof"].update(raw_tcp_line=p["listener_proof"]["raw_tcp_line"].replace("0A", "01")),
                lambda p: p.update(peer_pid=234), lambda p: p["descriptor"].update(source_generation=True)):
            proof = fixture()["backend_proof"]
            mutation(proof)
            with self.assertRaises(ValueError):
                validate_backend_proof(proof, config=fixture()["config"])

    def test_proof_nonce_replay_fails(self):
        self.rejected(lambda d: d["result"]["observation"]["after"].update(
            backend_proof=copy.deepcopy(d["result"]["observation"]["before"]["backend_proof"])), reason="proof_replay")

    def test_relation_change_during_window_fails(self):
        self.rejected(lambda d: d["result"]["observation"]["relation_after"].update(relation_generation=2))

    def test_pressure_values_and_scope_are_independently_checked(self):
        self.rejected(lambda d: d["result"]["observation"]["after"]["pressure"].update(attribution="node-101"))
        self.rejected(lambda d: d["result"]["observation"]["after"]["pressure"]["metrics"]["cpu"].update(full_valid=True))
        self.rejected(lambda d: d["result"]["observation"]["after"]["pressure"]["metrics"]["memory"]["some"].update(avg10_bp=126))
        self.rejected(lambda d: d["result"]["observation"]["after"].update(pressure=pressure_fixture(at=2000, total=99)))

    def test_pressure_unavailable_nulls_and_malformed_raw_are_distinct(self):
        data = fixture()
        row = data["result"]["observation"]["after"]["pressure"]["metrics"]["memory"]
        row.update(state="UNAVAILABLE", error="pressure-missing", raw=None, some=None, full=None, full_valid=False)
        self.accepted(data)
        row["raw"] = ""
        with self.assertRaises(ValueError):
            self.accepted(data)
        row.update(error="pressure-format", raw="bad\n")
        self.accepted(data)
        row["raw"] = pressure_fixture()["metrics"]["memory"]["raw"]
        with self.assertRaises(ValueError):
            self.accepted(data)

    def test_no_ownership_or_aggregation_can_be_added(self):
        self.rejected(lambda d: d["result"].update(ownership_valid=True))
        self.rejected(lambda d: d["result"]["observation"].update(rss_total_bytes=1))
        self.rejected(lambda d: d["result"]["observation"]["cpu"].update(total=0))

    def test_request_receipt_identity_model_and_counter_must_match(self):
        data = fixture("request")
        receipt = receipt_fixture(data)
        self.accepted(data, receipt=receipt)
        for field, value in (("request_id", str(uuid.uuid4())), ("authority_instance", str(uuid.uuid4())),
                             ("binding_generation", 2), ("backend_sha256", "9" * 64)):
            invalid = copy.deepcopy(receipt)
            invalid[field] = value
            with self.assertRaises(ValueError):
                self.accepted(data, receipt=invalid)

    def test_sample_cannot_claim_request_cost(self):
        data = fixture()
        with self.assertRaises(ValueError):
            self.accepted(data, receipt=receipt_fixture(fixture("request")))
        self.rejected(lambda d: d["result"]["observation"].update(request_id=str(uuid.uuid4())))

    def test_cached_status_allows_request_progress_with_same_relation(self):
        data = fixture("request")
        data["result"]["action"] = "status"
        data["source"] = {**data["source"], "completed_requests": 5}
        data["authority"].observe(data["source"])
        data["snapshot"] = data["authority"].snapshot()
        self.accepted(data)
        data["source"]["completed_requests"] = 1
        data["snapshot"]["current_source"]["completed_requests"] = 1
        with self.assertRaisesRegex(ValueError, "cached_source_regression"):
            self.accepted(data)

    def test_error_is_distinct_from_observation_and_does_not_require_model_failure(self):
        data = fixture()
        data["result"].update(outcome="ERROR", error="process-exited", observation=None, relation_current=False)
        self.accepted(data)
        self.assertTrue(data["source"]["model_ready"])
        data["result"]["relation_current"] = True
        with self.assertRaises(ValueError):
            self.accepted(data)

    def test_unlinked_status_and_request_error_are_valid_but_no_success_window(self):
        data = fixture()
        data["result"].update(action="status", relation=None, relation_current=False, observation=None)
        self.accepted(data)
        data["result"].update(action="request", outcome="ERROR", error="resource-unlinked")
        self.accepted(data)
        data["result"].update(outcome="OK", error=None)
        with self.assertRaises(ValueError):
            self.accepted(data)

    def test_relation_progression_accepts_idempotence_and_counter_progress(self):
        previous = fixture()["relation"]
        validate_relation_progression(previous, copy.deepcopy(previous))
        current = copy.deepcopy(previous)
        current["relation_generation"] += 1
        current["source_record"]["completed_requests"] += 3
        validate_relation_progression(previous, current)
        self.assertEqual(previous["source_record"]["completed_requests"], 1)

    def test_relation_progression_rejects_management_source_and_counter_rollback(self):
        for kind in ("canonical", "binding", "source", "counter"):
            previous = fixture()["relation"]
            current = copy.deepcopy(previous)
            current["relation_generation"] = 2
            if kind == "canonical":
                previous["canonical"]["generation"] = previous["parent"]["generation"] = 2
            elif kind == "binding":
                previous["binding_generation"] = 2
            elif kind == "source":
                previous["source_record"]["source_generation"] = 2
            else:
                previous["source_record"]["completed_requests"] = 2
            with self.assertRaisesRegex(ValueError, "rollback|regression", msg=kind):
                validate_relation_progression(previous, current)

    def test_relation_progression_requires_same_lineage_and_next_generation(self):
        previous = fixture()["relation"]
        for name, value in (("authority_instance", str(uuid.uuid4())), ("relation_id", str(uuid.uuid4())),
                             ("relation_generation", 1), ("relation_generation", 3)):
            current = copy.deepcopy(previous)
            current.update(relation_generation=2)
            current["source_record"]["completed_requests"] = 2
            current[name] = value
            with self.assertRaises(ValueError, msg=name):
                validate_relation_progression(previous, current)

    def test_relation_progression_new_instance_requires_later_start_and_new_binding(self):
        previous = fixture()["relation"]
        current = copy.deepcopy(previous)
        current.update(relation_generation=2, binding_generation=2)
        current["source_record"].update(source_instance=str(uuid.uuid4()), service_start_generation=2, process_id=124)
        current["main_identity"].update(process_id=124, process_start_ticks=112)
        validate_relation_progression(previous, current)
        for name in ("service_start_generation", "binding_generation"):
            invalid = copy.deepcopy(current)
            if name == "service_start_generation":
                invalid["source_record"][name] = 1
            else:
                invalid[name] = 1
            with self.assertRaises(ValueError, msg=name):
                validate_relation_progression(previous, invalid)

    def test_relation_progression_same_instance_cannot_change_process_or_semantics_silently(self):
        previous = fixture()["relation"]
        for change in (lambda r: r["main_identity"].update(process_start_ticks=112),
                       lambda r: r["source_record"].update(service_start_generation=2),
                       lambda r: r["source_record"].update(warmup_response_sha256="9" * 64),
                       lambda r: r["source_record"].update(source_generation=2)):
            current = copy.deepcopy(previous)
            current["relation_generation"] = 2
            change(current)
            with self.assertRaises(ValueError):
                validate_relation_progression(previous, current)

    def test_request_duration_must_fit_each_process_window(self):
        for shorter in ("main", "backend"):
            data = fixture("request")
            receipt = receipt_fixture(data)
            observation = data["result"]["observation"]
            observation["after"][shorter]["read_start_ns"] -= 1
            observation["after"][shorter]["read_end_ns"] -= 1
            observation["cpu"][shorter]["elapsed_ns"] -= 1
            with self.assertRaisesRegex(ValueError, "receipt_outside_window", msg=shorter):
                self.accepted(data, receipt=receipt)


if __name__ == "__main__":
    unittest.main()
