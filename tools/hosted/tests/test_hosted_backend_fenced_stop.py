"""Mocked native boundaries test exact-target stop; no Linux lifecycle claim."""
from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_backend import client, protocol
from aios_resources import ResourceError


def process(pid, parent, boot):
    identity = {"host_boot_id": boot, "process_id": pid, "process_start_ticks": pid * 10, "uid": 1000}
    fields = ["S", str(parent), *(["0"] * 20)]
    fields[19] = str(identity["process_start_ticks"])
    return identity, {"raw_stat": f"{pid} (owned fixture) " + " ".join(fields)}


class Reader:
    def __init__(self, identity, sample):
        self.identity = copy.deepcopy(identity)
        self.value = copy.deepcopy(sample)
        self.closed = False

    def sample(self):
        if self.closed:
            raise ResourceError("process-reader-closed")
        return copy.deepcopy(self.value)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FencedStopTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.directory = Path(temp).resolve()
        self.boot = str(uuid.uuid4())
        self.owner, self.owner_sample = process(100, 1, self.boot)
        self.parent, self.parent_sample = process(200, 100, self.boot)
        self.child, self.child_sample = process(300, 200, self.boot)
        self.config = {"schema_version": 1, "endpoint": "http://127.0.0.1:18081", "model_id": "fixture",
            "model_path": "/fixture/model", "model_sha256": "1" * 64, "backend_path": "/fixture/backend",
            "backend_sha256": "2" * 64, "provenance_sha256": "3" * 64}
        record = {"schema_version": 1, "service_id": str(uuid.uuid4()), "instance_id": str(uuid.uuid4()),
            "start_generation": 1, "supervisor_identity": self.parent, "child_identity": self.child,
            "lifecycle_state": "active", "backend_ready": True,
            "config_sha256": hashlib.sha256(client.encoded(self.config) + b"\n").hexdigest(),
            "profile": "python-fixture", "source_only": True, "backend_source_instance": str(uuid.uuid4())}
        descriptor = {"schema_version": 1, "source_namespace": "linux-model-backend",
            "source_instance": record["backend_source_instance"], "source_generation": 1,
            "lifecycle_state": "active", "source_only": True, "producer_owned": True,
            **{k: self.child[k] for k in ("host_boot_id", "process_id", "process_start_ticks")},
            "launcher_process_id": 200, "launcher_start_ticks": 2000, "listener_inode": 555,
            **{k: self.config[k] for k in ("model_id", "model_sha256", "backend_sha256", "endpoint")}}
        self.running = protocol.reply("status", "RUNNING", record=copy.deepcopy(record),
            descriptor=descriptor, capture_kind="fixture")
        self.instance = record["instance_id"]
        terminal_record = {**copy.deepcopy(record), "lifecycle_state": "exited", "backend_ready": False}
        self.terminal = protocol.reply("status", "STOPPED", record=terminal_record, capture_kind="fixture")
        self.stopping = protocol.reply("stop", "STOPPING", record={**copy.deepcopy(record), "backend_ready": False},
            capture_kind="fixture")
        self.sockets = {"control.sock": {"device": 1, "inode": 10}, "backend.sock": {"device": 1, "inode": 11}}
        self.lease = {"directory": str(self.directory), "proof": {"owner": copy.deepcopy(self.owner),
            "service_record": copy.deepcopy(record), "descriptor": copy.deepcopy(descriptor),
            "state_directory": client._stamp(self.directory.stat()), "sockets": copy.deepcopy(self.sockets)},
            "supervisor_reader": Reader(self.parent, self.parent_sample),
            "child_reader": Reader(self.child, self.child_sample)}
        self.owned = Mock(pid=200, returncode=None)
        self.owned.poll.return_value = None
        self.owned.wait.side_effect = lambda **_kw: setattr(self.owned, "returncode", 0) or 0
        self.stack.enter_context(patch.dict(client._LEASES, {self.instance: self.lease}, clear=True))
        self.stack.enter_context(patch.dict(client._CHILDREN, {self.instance: self.owned}, clear=True))
        self.patch(protocol, "supported", return_value=True)
        self.prepare = self.patch(client, "prepare_directory", side_effect=lambda path, **_kw: Path(path))
        self.patch(client.os, "getpid", return_value=100)
        self.patch(client, "read_json", return_value=self.config)
        self.patch(client, "registry_at", side_effect=lambda _d: {"schema_version": 1,
            **{k: self.running["service_record"][k] for k in client.IDENTITY_KEYS}})
        self.latest = self.patch(client, "_latest", side_effect=lambda *_args: copy.deepcopy(self.running))
        self.patch(client, "_socket_stamp", side_effect=lambda _d, name: copy.deepcopy(self.sockets[name]))
        self.patch(client, "ProcessReader", side_effect=self.reader)
        self.status = self.patch(client, "_status", side_effect=[copy.deepcopy(self.running), self.terminal])
        self.connection = Mock()
        self.connection.__enter__ = Mock(return_value=self.connection)
        self.connection.__exit__ = Mock(return_value=False)
        self.patch(client.socket, "AF_UNIX", new=1, create=True)
        self.patch(client.socket, "socket", return_value=self.connection)
        self.patch(protocol, "socket_path", return_value=self.directory / "control.sock")
        self.patch(client, "peer_credentials", return_value=(200, 1000, 1000))
        self.send = self.patch(protocol, "send")
        self.patch(protocol, "receive", return_value=self.stopping)
        self.patch(client.os, "pidfd_open", side_effect=[901, 902], create=True)
        self.select = self.patch(client.select, "select", side_effect=lambda readers, *_args: (readers, [], []))
        self.close = self.patch(client.os, "close")

    def patch(self, target, name, **kwargs):
        return self.stack.enter_context(patch.object(target, name, **kwargs))

    def reader(self, pid, *_args):
        value = {100: (self.owner, self.owner_sample), 200: (self.parent, self.parent_sample),
                 300: (self.child, self.child_sample)}[pid]
        return Reader(*value)

    def run_stop(self, expected=None):
        value = client.stop_bound(self.directory, copy.deepcopy(self.running) if expected is None else expected)
        protocol.validate_reply(value, "stop")
        return value

    def denied_before_send(self, expected=None, error=None):
        value = self.run_stop(expected)
        self.assertEqual(value["outcome"], "ERROR", value)
        if error is not None:
            self.assertEqual(value["error"], error, value)
        self.send.assert_not_called()
        return value

    def test_exact_owner_target_stops_and_reaps_without_status_rpc_inside_guard(self):
        original = copy.deepcopy(self.running)
        def status(_directory):
            if self.status.call_count == 1:
                self.connection.connect.assert_not_called()
                return copy.deepcopy(self.running)
            self.owned.wait.assert_called_once_with(timeout=1)
            return self.terminal
        self.status.side_effect = status
        value = self.run_stop()
        self.assertEqual((value["state"], value["outcome"]), ("STOPPED", "OK"))
        self.assertEqual(self.status.call_count, 2)
        self.send.assert_called_once_with(self.connection, {"schema_version": 1, "action": "stop",
            **{k: original["service_record"][k] for k in client.IDENTITY_KEYS}})
        self.assertEqual(self.running, original)
        self.assertNotIn(self.instance, client._LEASES)
        self.assertNotIn(self.instance, client._CHILDREN)

    def test_unsupported_does_not_read_state(self):
        self.patch(protocol, "supported", return_value=False)
        value = self.run_stop()
        self.assertEqual(value["error"], "unsupported-platform")
        self.prepare.assert_not_called()
        self.send.assert_not_called()

    def test_generic_stop_still_allows_authenticated_caller_without_owned_lease(self):
        client._LEASES.clear()
        client._CHILDREN.clear()
        value = client.control(self.directory, "stop")
        protocol.validate_reply(value, "stop")
        self.assertEqual((value["state"], value["outcome"]), ("STOPPED", "OK"))
        self.send.assert_called_once()
        self.owned.wait.assert_not_called()

    def test_missing_lease_or_owned_popen_never_adopts_persisted_target(self):
        for mapping in (client._LEASES, client._CHILDREN):
            with self.subTest(mapping="lease" if mapping is client._LEASES else "popen"):
                item = mapping.pop(self.instance)
                self.denied_before_send(error="stop-owner-required")
                mapping[self.instance] = item
        self.status.assert_not_called()

    def test_current_cli_identity_and_live_parent_chain_are_required(self):
        cases = [lambda: self.owner.update(process_start_ticks=1001),
                 lambda: setattr(self.owned, "pid", 201),
                 lambda: setattr(self.owned.poll, "return_value", 0),
                 lambda: self.lease["supervisor_reader"].value.update(raw_stat=process(200, 999, self.boot)[1]["raw_stat"]),
                 lambda: self.lease["child_reader"].value.update(raw_stat=process(300, 999, self.boot)[1]["raw_stat"])]
        for mutate in cases:
            with self.subTest(mutation=cases.index(mutate)):
                mutate()
                self.denied_before_send(error="stop-owner-required")
                self.owner["process_start_ticks"] = 1000
                self.owned.pid, self.owned.poll.return_value = 200, None
                self.lease["supervisor_reader"].value = copy.deepcopy(self.parent_sample)
                self.lease["child_reader"].value = copy.deepcopy(self.child_sample)

    def test_exited_or_closed_retained_capability_is_not_reopened(self):
        for name in ("supervisor_reader", "child_reader"):
            with self.subTest(reader=name):
                self.lease[name].closed = True
                self.denied_before_send()
                self.lease[name].closed = False
        self.connection.connect.assert_not_called()

    def test_live_current_replacement_or_descriptor_drift_is_rejected(self):
        for field in (*client.IDENTITY_KEYS, "supervisor_identity", "child_identity", "descriptor"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.running)
                if field == "descriptor":
                    changed[field]["listener_inode"] += 1
                elif field in ("supervisor_identity", "child_identity"):
                    changed["service_record"][field]["uid"] = 1001
                    other = "child_identity" if field == "supervisor_identity" else "supervisor_identity"
                    changed["service_record"][other]["uid"] = 1001
                elif field == "start_generation":
                    changed["service_record"][field] += 1
                else:
                    changed["service_record"][field] = str(uuid.uuid4())
                protocol.validate_reply(changed, "status")
                self.status.side_effect = None
                self.status.return_value = changed
                self.denied_before_send(error="stop-target-mismatch")
        self.connection.connect.assert_not_called()

    def test_already_terminal_or_absent_target_never_counts_as_this_stop(self):
        recovered = {**self.terminal, "state": "RECOVERED"}
        for current in (self.terminal, recovered, protocol.reply("status", "ABSENT")):
            with self.subTest(state=current["state"]):
                self.status.side_effect = None
                self.status.return_value = current
                self.denied_before_send(error="stop-target-mismatch")

    def test_expected_packet_and_lease_must_agree_exactly(self):
        altered = copy.deepcopy(self.running)
        altered["descriptor"]["listener_inode"] += 1
        self.denied_before_send(altered, "stop-target-mismatch")
        self.denied_before_send(self.terminal, "stop-target-mismatch")
        self.denied_before_send({"action": "status"})
        self.denied_before_send([], "stop-target-mismatch")
        self.status.assert_not_called()

    def test_changed_config_does_not_reinterpret_the_saved_descriptor(self):
        self.config["provenance_sha256"] = "4" * 64
        self.denied_before_send(error="stop-target-mismatch")
        self.status.assert_not_called()

    def test_wrong_state_directory_is_not_redirected_to_latest_instance(self):
        self.lease["directory"] = str(self.directory / "different")
        self.denied_before_send(error="stop-target-mismatch")
        self.status.assert_not_called()

    def test_pre_send_socket_or_directory_replacement_is_rejected(self):
        for name in ("control.sock", "backend.sock", "directory"):
            with self.subTest(target=name):
                # Every subcase gets an independent owned capability.
                self.lease["supervisor_reader"].closed = self.lease["child_reader"].closed = False
                client._LEASES[self.instance] = self.lease
                self.patch(client.os, "pidfd_open", side_effect=[901, 902], create=True)
                self.status.side_effect = None
                self.status.return_value = copy.deepcopy(self.running)
                def replaced(*_args):
                    if name == "directory":
                        self.lease["proof"]["state_directory"]["inode"] += 1
                    else:
                        self.sockets[name]["inode"] += 1
                self.connection.connect.side_effect = replaced
                self.denied_before_send(error="stop-target-mismatch")
                self.sockets = copy.deepcopy(self.lease["proof"]["sockets"])
                self.lease["proof"]["state_directory"] = client._stamp(self.directory.stat())

    def test_pre_send_mapping_replacement_is_rejected_without_closing_new_lease(self):
        replacement = {**self.lease, "supervisor_reader": Reader(self.parent, self.parent_sample),
            "child_reader": Reader(self.child, self.child_sample)}
        self.connection.connect.side_effect = lambda *_args: client._LEASES.update({self.instance: replacement})
        self.denied_before_send(error="stop-owner-required")
        self.assertIs(client._LEASES[self.instance], replacement)
        self.assertFalse(replacement["child_reader"].closed)
        self.assertFalse(replacement["supervisor_reader"].closed)
        self.assertTrue(self.lease["child_reader"].closed)
        self.assertTrue(self.lease["supervisor_reader"].closed)

    def test_pre_send_generation_replacement_does_not_reselect_new_target(self):
        def replaced(*_args):
            self.running["service_record"]["start_generation"] += 1
        self.connection.connect.side_effect = replaced
        self.denied_before_send(error="stop-target-mismatch")
        self.status.assert_called_once()

    def test_pre_send_popen_replacement_is_rejected_without_waiting_replacement(self):
        replacement = Mock(pid=200)
        self.connection.connect.side_effect = lambda *_args: client._CHILDREN.update({self.instance: replacement})
        self.denied_before_send(error="stop-owner-required")
        replacement.wait.assert_not_called()
        self.assertIs(client._CHILDREN[self.instance], replacement)

    def test_final_stopped_record_must_match_every_original_identity_field(self):
        # Exercise _stop itself so generic callers cannot accept another run.
        for field in (*client.IDENTITY_KEYS, "supervisor_identity", "child_identity"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.terminal)
                record = changed["service_record"]
                if field in ("supervisor_identity", "child_identity"):
                    record[field]["process_start_ticks"] += 1
                elif field == "start_generation":
                    record[field] += 1
                else:
                    record[field] = str(uuid.uuid4())
                protocol.validate_reply(changed, "status")
                self.patch(client.os, "pidfd_open", side_effect=[901, 902], create=True)
                self.status.side_effect = None
                self.status.return_value = changed
                client._CHILDREN[self.instance] = self.owned
                with self.assertRaisesRegex(ValueError, "stop-failed"):
                    client._stop(self.directory, copy.deepcopy(self.running))

    def test_backend_exit_timeout_remains_error_and_is_not_retried(self):
        self.select.side_effect = lambda *_args: ([], [], [])
        value = self.run_stop()
        self.assertEqual((value["outcome"], value["error"]), ("ERROR", "stop-timeout"))
        self.send.assert_called_once()
        self.status.assert_called_once()
        self.owned.wait.assert_not_called()

    def test_child_still_alive_is_not_success(self):
        self.select.side_effect = [([901], [], []), ([], [], [])]
        value = self.run_stop()
        self.assertEqual((value["outcome"], value["error"]), ("ERROR", "stop-failed"))
        self.owned.wait.assert_not_called()

    def test_reaped_supervisor_nonzero_is_not_success(self):
        self.owned.wait.side_effect = lambda **_kw: setattr(self.owned, "returncode", 1) or 1
        value = self.run_stop()
        self.assertEqual((value["outcome"], value["error"]), ("ERROR", "stop-failed"))

    def test_replaced_popen_after_send_is_never_reaped_as_original(self):
        replacement = Mock(pid=200)
        self.send.side_effect = lambda *_args: client._CHILDREN.update({self.instance: replacement})
        value = self.run_stop()
        self.assertEqual((value["outcome"], value["error"]), ("ERROR", "stop-failed"))
        self.assertIs(client._CHILDREN[self.instance], replacement)
        replacement.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
