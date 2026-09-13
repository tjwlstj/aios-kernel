"""RESEARCH: one real file, bounded proposals and declared experimental writers.

Ownership is application-level simulation, not an OS ACL or canonical AIOS Gate.
Only this single-process harness writes the private directory. Link/reparse and
integrity checks reject substitution; they are not hostile concurrent OS isolation.
Model text never supplies a path, command, arbitrary value or executable code.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

SCHEMA_VERSION = 1
MAX_STEPS = 12
MAX_FILE_BYTES = 2048
MAX_EVENT_BYTES = 32768
GOAL_VALUE = 7
SCENARIOS = ("normal", "stale_after_observe", "revoke_before_apply", "channel_lost",
             "external_after_apply", "owner_replaced")
ACTIONS = frozenset(("OBSERVE", "SET", "WAIT", "FINISH"))
PREDICTIONS = frozenset(("OBSERVED", "APPLIED", "STALE", "DENIED", "UNAVAILABLE", "NOOP"))
ATTRIBUTIONS = frozenset(("SELF", "OTHER", "UNKNOWN"))
PROPOSAL_KEYS = frozenset(("action", "expected_revision", "prediction", "attribution"))


class WorldError(RuntimeError):
    """A world integrity or persistence failure; never a model success verdict."""


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _require(ok, reason):
    if not ok:
        raise WorldError(reason)


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _directories(path):
    for item in reversed((path, *path.parents)):
        info = item.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not _linked(info), "unsafe-directory")


def validate_proposal(value):
    """Return a copied exact proposal; bool is not a revision integer."""
    if type(value) is not dict or value.keys() != PROPOSAL_KEYS:
        raise ValueError("proposal-keys")
    for key, allowed in (("action", ACTIONS), ("prediction", PREDICTIONS), ("attribution", ATTRIBUTIONS)):
        if type(value[key]) is not str or value[key] not in allowed:
            raise ValueError("proposal-" + key)
    revision = value["expected_revision"]
    if value["action"] == "SET":
        if type(revision) is not int or not 0 <= revision <= MAX_STEPS * 2:
            raise ValueError("proposal-revision")
    elif revision is not None:
        raise ValueError("proposal-revision")
    return copy.deepcopy(value)


def _rejected_input(value):
    """Preserve bounded JSON input, otherwise a type/size/hash diagnostic only."""
    try:
        raw = encoded(value)
        if len(raw) <= 4096:
            return json.loads(raw)
        return {"unretained_type": type(value).__name__, "bytes": len(raw), "sha256": digest(raw)}
    except (ValueError, TypeError, RecursionError):
        return {"unretained_type": type(value).__name__}


class World:
    """New exclusive sandbox. FINISH ends the episode, not proof of goal success.

    OBSERVE is the only operation that refreshes the cached object observation.
    SET always requests value 7, needs this exact instance/epoch's ownership and
    an expected revision; WAIT/FINISH do not touch the object. Prediction and
    attribution are recorded but never used to authorize an operation.
    """

    def __init__(self, root: Path, scenario: str, run_id: str):
        _require(scenario in SCENARIOS, "unknown-scenario")
        _require(type(run_id) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_id), "run-id")
        path = Path(root)
        _require(".." not in path.parts, "root-traversal")
        self.root = path.absolute()
        _directories(self.root.parent)
        self.root.mkdir(mode=0o700, exist_ok=False)
        info = self.root.lstat()
        self._root_identity = (info.st_dev, info.st_ino)
        self._events = []
        self._observation = self._last_result = None
        self._last_set_step = None
        self._done = self._failed = self._injected = False
        self._channel = {"channel_id": "object-channel-A", "enabled": True}
        self._identity = {"agent_id": "research-agent-A",
                          "instance_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "aios-research:" + run_id)), "epoch": 1}
        self._other = {"agent_id": "experimental-writer-B",
                       "instance_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "aios-experimental-writer:" + run_id)), "epoch": 1}
        self._goal = {"object_id": "counter-A", "value": GOAL_VALUE}
        self.scenario = scenario
        initial = {"schema_version": SCHEMA_VERSION, "object_id": "counter-A", "revision": 0,
                   "value": 0, "owner": copy.deepcopy(self._identity), "last_writer": None}
        self._object_hash = None
        self._write_object(initial)
        self._manifest = {"schema_version": SCHEMA_VERSION, "kind": "self-reference-world",
                          "relationship": "RESEARCH", "scenario": scenario, "run_id": run_id,
                          "identity": copy.deepcopy(self._identity), "goal": copy.deepcopy(self._goal),
                          "max_steps": MAX_STEPS, "initial_object_sha256": self._object_hash,
                          "ownership": "application-simulation-not-os-acl-or-canonical-gate",
                          "writer_attribution": "declared-experimental-writer-not-physical-attestation",
                          "concurrency": "single-harness-no-hostile-concurrent-writer",
                          "intervention_boundary": {"normal": None,
                              "stale_after_observe": "after-first-observed",
                              "revoke_before_apply": "before-first-valid-set",
                              "channel_lost": "before-first-valid-set",
                              "external_after_apply": "after-first-applied",
                              "owner_replaced": "after-first-observed"}[scenario]}
        self._exclusive_json("manifest.json", self._manifest)

    @property
    def done(self):
        return self._done or self._failed

    @property
    def events(self):
        return copy.deepcopy(self._events)

    @property
    def manifest(self):
        return copy.deepcopy(self._manifest)

    def context(self):
        return copy.deepcopy({"schema_version": SCHEMA_VERSION, "identity": self._identity,
                              "goal": self._goal, "observation": self._observation,
                              "last_result": self._last_result, "channel": self._channel,
                              "step": len(self._events), "last_set_step": self._last_set_step})

    def _guard(self):
        _directories(self.root)
        info = self.root.lstat()
        _require((info.st_dev, info.st_ino) == self._root_identity, "root-replaced")

    def _regular(self, path):
        info = path.lstat()
        _require(stat.S_ISREG(info.st_mode) and not _linked(info) and info.st_nlink == 1, "unsafe-file")
        return info

    def _read(self, name, maximum):
        self._guard()
        path = self.root / name
        info = self._regular(path)
        _require(info.st_size <= maximum, "file-size")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            _require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino), "file-replaced")
            raw = stream.read(maximum + 1)
        _require(len(raw) <= maximum, "file-size")
        return raw

    def _create(self, name, raw):
        self._guard()
        fd = os.open(self.root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _exclusive_json(self, name, value):
        raw = encoded(value)
        _require(len(raw) <= MAX_EVENT_BYTES, "event-size")
        self._create(name, raw)
        _require(self._read(name, MAX_EVENT_BYTES) == raw, "write-readback")

    def _snapshot(self):
        raw = self._read("object.json", MAX_FILE_BYTES)
        _require(digest(raw) == self._object_hash, "object-changed-outside-harness")
        return {"state": json.loads(raw), "sha256": self._object_hash}

    def _write_object(self, value):
        raw = encoded(value)
        _require(len(raw) <= MAX_FILE_BYTES, "object-size")
        temporary = ".object-" + uuid.uuid4().hex + ".tmp"
        self._create(temporary, raw)
        self._guard()
        target = self.root / "object.json"
        if self._object_hash is not None:
            self._snapshot()
        else:
            _require(not os.path.lexists(target), "object-already-exists")
        os.replace(self.root / temporary, target)
        _require(self._read("object.json", MAX_FILE_BYTES) == raw, "object-readback")
        self._object_hash = digest(raw)

    def _attribution(self, writer):
        return "UNKNOWN" if writer is None else "SELF" if writer == self._identity else "OTHER"

    def _intervene(self, boundary, action, outcome, step):
        if self._injected or self.scenario == "normal":
            return []
        before_set = boundary == "before" and action == "SET"
        after_observe = boundary == "after" and outcome == "OBSERVED"
        trigger = ((self.scenario in ("revoke_before_apply", "channel_lost") and before_set)
                   or (self.scenario in ("stale_after_observe", "owner_replaced") and after_observe)
                   or (self.scenario == "external_after_apply" and boundary == "after" and outcome == "APPLIED"))
        if not trigger:
            return []
        before, channel_before = self._snapshot(), copy.deepcopy(self._channel)
        state = copy.deepcopy(before["state"])
        actor = copy.deepcopy(self._other)
        if self.scenario == "channel_lost":
            self._channel["enabled"] = False
        else:
            state["revision"] += 1
            if self.scenario in ("stale_after_observe", "external_after_apply"):
                state["value"] = 3
            elif self.scenario == "revoke_before_apply":
                state["owner"] = copy.deepcopy(self._other)
            else:
                actor = {**self._identity, "instance_id": str(uuid.uuid5(uuid.UUID(self._identity["instance_id"]), "replacement")), "epoch": 2}
                state["owner"] = copy.deepcopy(actor)
            state["last_writer"] = actor
            self._write_object(state)
        self._injected = True
        return [{"kind": self.scenario, "boundary": boundary, "step": step,
                 "experimental": True, "actor": actor, "before": before, "after": self._snapshot(),
                 "channel_before": channel_before, "channel_after": copy.deepcopy(self._channel)}]

    def _apply(self, proposal, snapshot, step):
        action, state = proposal["action"], snapshot["state"]
        result = {"action": action, "outcome": "NOOP", "attribution": "UNKNOWN", "revision": None,
                  "sha256": None, "completed_step": step, "observation_updated": False}
        if action in ("WAIT", "FINISH"):
            return result
        if not self._channel["enabled"]:
            return {**result, "outcome": "UNAVAILABLE"}
        if action == "OBSERVE":
            self._observation = {**copy.deepcopy(state), "sha256": snapshot["sha256"], "observed_step": step}
            return {**result, "outcome": "OBSERVED", "attribution": self._attribution(state["last_writer"]),
                    "revision": state["revision"], "sha256": snapshot["sha256"], "observation_updated": True}
        if state["owner"] != self._identity:
            return {**result, "outcome": "DENIED"}
        if proposal["expected_revision"] != state["revision"]:
            return {**result, "outcome": "STALE", "revision": state["revision"], "sha256": snapshot["sha256"],
                    "attribution": self._attribution(state["last_writer"])}
        if state["value"] == GOAL_VALUE:
            return {**result, "revision": state["revision"], "sha256": snapshot["sha256"],
                    "attribution": self._attribution(state["last_writer"])}
        changed = {**copy.deepcopy(state), "revision": state["revision"] + 1,
                   "value": GOAL_VALUE, "last_writer": copy.deepcopy(self._identity)}
        self._write_object(changed)
        return {**result, "outcome": "APPLIED", "attribution": "SELF", "revision": changed["revision"],
                "sha256": self._object_hash}

    def step(self, proposal):
        _require(not self.done, "world-ended")
        try:
            return self._step(proposal)
        except (OSError, ValueError, WorldError) as exc:
            self._failed = True
            raise WorldError("world-integrity-or-persistence-failed:" + str(exc)) from exc

    def _step(self, proposal):
        step = len(self._events) + 1
        _require(self._read("manifest.json", MAX_EVENT_BYTES) == encoded(self._manifest), "manifest-rewritten")
        for previous in self._events:
            _require(self._read(f"event-{previous['step']:03d}.json", MAX_EVENT_BYTES) == encoded(previous), "event-rewritten")
        input_context, before = self.context(), self._snapshot()
        rejection = None
        try:
            accepted = validate_proposal(proposal)
        except ValueError as exc:
            accepted, rejection = None, str(exc)
        if accepted is not None and accepted["action"] == "SET":
            self._last_set_step = step
        interventions = [] if accepted is None else self._intervene("before", accepted["action"], None, step)
        action_before = self._snapshot()
        result = ({"action": None, "outcome": "REJECTED", "attribution": "UNKNOWN", "revision": None,
                   "sha256": None, "completed_step": step, "observation_updated": False}
                  if accepted is None else self._apply(accepted, action_before, step))
        action_after = self._snapshot()
        if accepted is not None:
            interventions += self._intervene("after", accepted["action"], result["outcome"], step)
        explicit_finish = accepted is not None and accepted["action"] == "FINISH"
        stop_reason = "FINISH" if explicit_finish else "STEP_LIMIT" if step >= MAX_STEPS else None
        event = {"schema_version": SCHEMA_VERSION, "step": step, "input_context": input_context,
                 "proposal": accepted if accepted is not None else _rejected_input(proposal),
                 "proposal_valid": accepted is not None, "rejection": rejection,
                 "before": before, "action_before": action_before, "result": result,
                 "action_after": action_after, "after": self._snapshot(), "interventions": interventions,
                 "done": stop_reason is not None, "stop_reason": stop_reason,
                 "previous_event_sha256": self._events[-1]["event_sha256"] if self._events else None}
        event["event_sha256"] = digest(encoded(event))
        self._exclusive_json(f"event-{step:03d}.json", event)
        self._events.append(event)
        self._last_result = copy.deepcopy(result)
        self._done = stop_reason is not None
        return copy.deepcopy(event)
