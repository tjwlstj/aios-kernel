"""Pure, bounded daemon-local environment observations and model input.

The caller owns actual OS reads, authenticated source/authority validation and
request permission. This module performs no I/O, adopts no process, and grants
no action. A packet is evidence at its recorded monotonic check, not a claim
that the observation remains current whenever a stored packet is read later.
"""
from __future__ import annotations

import copy
import json
import posixpath
import re
import uuid

SCHEMA_VERSION = 1
TTL_NS = 30_000_000_000
MAX_PROMPT_BYTES = 4096
MAX_PACKET_BYTES = 8192
MAX_INTEGER = (1 << 63) - 1
SCOPE = "linux-hosted-main-service"
UNKNOWN = ["network", "selected_workspace"]
RAW_KEYS = {"working_directory", "logical_cpu_count", "mem_total_line"}
NORMALIZED_KEYS = {"working_directory", "logical_cpu_count", "memory_total_bytes"}
OBSERVATION_KEYS = {"schema_version", "observation_id", "host_boot_id", "process_id",
                    "observed_monotonic_ns", "scope", "raw", "normalized"}
CONSUMER_KEYS = {"source_id", "source_instance", "service_start_generation",
                 "source_generation", "model_id", "model_sha256"}
MANAGEMENT_KEYS = {"authority_namespace", "authority_instance", "state", "binding_current",
                   "cell_id", "cell_generation", "node_id", "node_generation", "binding_generation"}
PACKET_KEYS = {"schema_version", "observation", "checked_monotonic_ns", "validity",
               "consumer", "management", "unknown"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MEM_TOTAL = re.compile(r"MemTotal:[ \t]+([1-9][0-9]*)[ \t]+kB\n?\Z")


def _require(condition: bool, reason: str = "space-invalid") -> None:
    if not condition:
        raise ValueError(reason)


def _keys(value: object, expected: set) -> None:
    _require(type(value) is dict and value.keys() == expected)


def _integer(value: object, minimum: int = 0, maximum: int = MAX_INTEGER) -> None:
    _require(type(value) is int and minimum <= value <= maximum)


def _text(value: object, maximum: int, *, whitespace: bool = False) -> None:
    _require(type(value) is str and bool(value.strip()))
    try:
        _require(len(value.encode("utf-8")) <= maximum, "space-overflow")
    except UnicodeError as exc:
        raise ValueError("space-invalid") from exc
    _require(all(ord(char) >= 32 and ord(char) != 127 or
                 whitespace and char in "\n\t" for char in value))


def _uuid(value: object) -> None:
    _require(type(value) is str)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("space-invalid") from exc
    _require(str(parsed) == value and parsed.int != 0)


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("space-invalid") from exc


def _bounded(value: object, maximum: int = MAX_PACKET_BYTES) -> None:
    # Bound traversal before serialization/copying; cycles also exhaust the
    # explicit node/depth budget instead of reaching interpreter recursion.
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        _require(count <= 256 and depth <= 8, "space-overflow")
        _require(type(item) in (dict, list, str, int, bool, type(None)))
        if type(item) is dict:
            _require(len(item) <= 64 and all(type(key) is str for key in item), "space-overflow")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            _require(len(item) <= 64, "space-overflow")
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            try:
                _require(len(item.encode("utf-8")) <= maximum, "space-overflow")
            except UnicodeError as exc:
                raise ValueError("space-invalid") from exc
    _require(len(_encoded(value)) <= maximum, "space-overflow")


def _normalize(raw: object) -> dict:
    _keys(raw, RAW_KEYS)
    cwd = raw["working_directory"]
    if cwd is not None:
        _text(cwd, 512)
        _require(cwd.startswith("/") and not cwd.startswith("//") and posixpath.normpath(cwd) == cwd)
    cpus = raw["logical_cpu_count"]
    if cpus is not None:
        _integer(cpus, 1, 1_048_576)
    line = raw["mem_total_line"]
    memory = None
    if line is not None:
        _text(line, 128, whitespace=True)
        match = _MEM_TOTAL.fullmatch(line)
        _require(match is not None)
        memory = int(match[1]) * 1024
        _integer(memory, 1)
    return {"working_directory": cwd, "logical_cpu_count": cpus, "memory_total_bytes": memory}


def _observation_id(observation: dict) -> str:
    # Content-derived ID keeps construction pure and binds original sample time
    # to the raw/normalized pair. It is not authentication or a live OS proof.
    body = {key: value for key, value in observation.items() if key != "observation_id"}
    return str(uuid.uuid5(uuid.NAMESPACE_URL, _encoded(body).decode("utf-8")))


def validate_observation(observation: object) -> dict:
    """Validate exact raw-to-normalized evidence and return an independent copy."""
    _bounded(observation)
    _keys(observation, OBSERVATION_KEYS)
    _require(type(observation["schema_version"]) is int and observation["schema_version"] == SCHEMA_VERSION)
    _require(observation["scope"] == SCOPE)
    _uuid(observation["observation_id"])
    _uuid(observation["host_boot_id"])
    _integer(observation["process_id"], 1)
    _integer(observation["observed_monotonic_ns"])
    _keys(observation["normalized"], NORMALIZED_KEYS)
    expected = _normalize(observation["raw"])
    _require(_encoded(observation["normalized"]) == _encoded(expected))
    _require(observation["observation_id"] == _observation_id(observation))
    return copy.deepcopy(observation)


def build_observation(*, host_boot_id: str, process_id: int, observed_monotonic_ns: int,
                      working_directory: str | None, logical_cpu_count: int | None,
                      mem_total_line: str | None) -> dict:
    """Normalize caller-read getcwd/cpu_count/MemTotal facts; None is unknown.

    MemTotal is the one original /proc/meminfo line, including its optional LF;
    it describes Linux-visible total memory, not free RAM or Cell ownership.
    Freshness uses the supplied boot-local monotonic sample time, never mtime.
    """
    raw = {"working_directory": working_directory, "logical_cpu_count": logical_cpu_count,
           "mem_total_line": mem_total_line}
    _bounded(raw)
    observation = {"schema_version": SCHEMA_VERSION, "host_boot_id": host_boot_id,
                   "process_id": process_id, "observed_monotonic_ns": observed_monotonic_ns,
                   "scope": SCOPE, "raw": raw, "normalized": _normalize(raw)}
    _bounded(observation)
    observation["observation_id"] = _observation_id(observation)
    return validate_observation(observation)


def _validate_consumer(consumer: object) -> None:
    _keys(consumer, CONSUMER_KEYS)
    for key in ("source_id", "source_instance"):
        _uuid(consumer[key])
    for key in ("service_start_generation", "source_generation"):
        _integer(consumer[key], 1)
    _text(consumer["model_id"], 256)
    _require(type(consumer["model_sha256"]) is str and _HASH.fullmatch(consumer["model_sha256"]) is not None)


def _consumer(source: dict, model_id: str) -> dict:
    _require(type(source) is dict)
    _require({"host_boot_id", "process_id"} | (CONSUMER_KEYS - {"model_id"}) <= source.keys())
    _uuid(source["host_boot_id"])
    _integer(source["process_id"], 1)
    value = {key: source[key] for key in CONSUMER_KEYS - {"model_id"}}
    value["model_id"] = model_id
    _validate_consumer(value)
    return value


def _validate_management(value: object) -> None:
    _keys(value, MANAGEMENT_KEYS)
    _require(value["authority_namespace"] == "aios-hosted-management")
    _uuid(value["authority_instance"])
    _require(type(value["state"]) is str and value["state"] in ("UNBOUND", "DISCOVERED", "BOUND", "STALE"))
    _require(type(value["binding_current"]) is bool)
    _require(type(value["cell_id"]) is int and value["cell_id"] == 1)
    _require(type(value["node_id"]) is int and value["node_id"] == 101)
    for key in ("cell_generation", "node_generation"):
        _integer(value[key], 1)
    _require(value["cell_generation"] == value["node_generation"])
    if value["binding_generation"] is not None:
        _integer(value["binding_generation"], 1)
    _require(value["binding_current"] == (value["state"] == "BOUND"))
    _require((value["binding_generation"] is None) == (value["state"] in ("UNBOUND", "DISCOVERED")))


def _management(snapshot: dict) -> dict:
    _require(type(snapshot) is dict)
    _require({"authority_namespace", "authority_instance", "state", "binding_current", "parent",
              "canonical", "binding"} <= snapshot.keys())
    parent, node, binding = snapshot["parent"], snapshot["canonical"], snapshot["binding"]
    _require(type(parent) is dict and {"id", "generation"} <= parent.keys())
    _require(type(node) is dict and {"id", "generation"} <= node.keys())
    _require(binding is None or type(binding) is dict and "generation" in binding)
    value = {"authority_namespace": snapshot["authority_namespace"],
             "authority_instance": snapshot["authority_instance"], "state": snapshot["state"],
             "binding_current": snapshot["binding_current"], "cell_id": parent["id"],
             "cell_generation": parent["generation"], "node_id": node["id"],
             "node_generation": node["generation"],
             "binding_generation": None if binding is None else binding["generation"]}
    _validate_management(value)
    return value


def build_packet(observation: dict, *, source_record: dict, management_snapshot: dict,
                 checked_monotonic_ns: int, model_id: str) -> dict:
    """Join retained facts to the caller's current validated source/authority.

    Unbound/STALE management may be observed. Only the daemon's separate
    authoritative ask gate can permit a model request.
    """
    observation = validate_observation(observation)
    _integer(checked_monotonic_ns)
    _require(observation["observed_monotonic_ns"] <= checked_monotonic_ns, "space-future")
    packet = {"schema_version": SCHEMA_VERSION, "observation": observation,
              "checked_monotonic_ns": checked_monotonic_ns,
              "validity": "CURRENT" if checked_monotonic_ns - observation["observed_monotonic_ns"] <= TTL_NS else "STALE",
              "consumer": _consumer(source_record, model_id), "management": _management(management_snapshot),
              "unknown": list(UNKNOWN)}
    return validate_packet(packet, source_record=source_record, management_snapshot=management_snapshot)


def validate_packet(packet: object, *, source_record: dict | None = None,
                    management_snapshot: dict | None = None, now_ns: int | None = None) -> dict:
    """Validate a recorded packet, optionally joining caller-validated originals.

    With no originals this validates shape/internal consistency, not provenance.
    now_ns rejects a future check; recorded CURRENT/STALE is always evaluated
    against checked_monotonic_ns, allowing honest historical replay.
    """
    _bounded(packet)
    _keys(packet, PACKET_KEYS)
    _require(type(packet["schema_version"]) is int and packet["schema_version"] == SCHEMA_VERSION)
    observation = validate_observation(packet["observation"])
    checked = packet["checked_monotonic_ns"]
    _integer(checked)
    _require(observation["observed_monotonic_ns"] <= checked, "space-future")
    if now_ns is not None:
        _integer(now_ns)
        _require(checked <= now_ns, "space-future")
    expected = "CURRENT" if checked - observation["observed_monotonic_ns"] <= TTL_NS else "STALE"
    _require(packet["validity"] == expected)
    _require(type(packet["unknown"]) is list and packet["unknown"] == UNKNOWN)
    _validate_consumer(packet["consumer"])
    _validate_management(packet["management"])
    if source_record is not None:
        _require(packet["consumer"] == _consumer(source_record, packet["consumer"]["model_id"]), "space-source-mismatch")
        _require(observation["host_boot_id"] == source_record["host_boot_id"] and
                 observation["process_id"] == source_record["process_id"], "space-source-mismatch")
    if management_snapshot is not None:
        _require(packet["management"] == _management(management_snapshot), "space-source-mismatch")
    return copy.deepcopy(packet)


def context_for_model(packet: dict) -> dict:
    """Only CURRENT values reach the model; retained STALE values stay in evidence."""
    packet = validate_packet(packet)
    observation = packet["observation"]
    facts = {}
    for name, value in observation["normalized"].items():
        status = "UNKNOWN" if value is None else packet["validity"]
        facts[name] = {"status": status, "value": value if status == "CURRENT" else None}
    for name in UNKNOWN:
        facts[name] = {"status": "UNKNOWN", "value": None}
    return {"schema_version": SCHEMA_VERSION, "scope": observation["scope"],
            "observation_id": observation["observation_id"],
            "observed_monotonic_ns": observation["observed_monotonic_ns"],
            "checked_monotonic_ns": packet["checked_monotonic_ns"], "validity": packet["validity"],
            "consumer": packet["consumer"], "management": packet["management"], "facts": facts}


def prompt_with_context(prompt: str, packet: dict) -> str:
    """Canonical data envelope only; inference owns its fixed system policy.

    JSON decoding recovers the exact user text. Escapes prevent supplied text
    from creating ChatML role delimiters; they are not a claim of semantic
    prompt-injection prevention. Model output remains data, never executable.
    """
    _text(prompt, MAX_PROMPT_BYTES, whitespace=True)
    value = {"space_data": context_for_model(packet), "question": prompt}
    text = _encoded(value).decode("utf-8").replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    _require(len(text.encode("utf-8")) <= MAX_PROMPT_BYTES, "space-budget")
    return text
