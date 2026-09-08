"""Independent raw evidence checks for an explicitly bound HTTP recipient."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from newagent_output_contract import identity, same
from resource_output_contract import DESCRIPTOR, config_hash, validate_sample

PROBE_KEYS = {"read_start_ns", "read_end_ns", "backend", "launcher", "listener_proof"}
SEND_KEYS = {"read_start_ns", "read_end_ns", "client_address", "server_address", "raw_client_tcp_line",
    "raw_server_tcp_line", "client_fd_number", "client_fd_target", "server_fd_number", "server_fd_target",
    "client", "backend", "launcher"}


def require(condition, code):
    if not condition:
        raise ValueError("execution_contract:" + code)


def keys(value, expected):
    require(type(value) is dict and value.keys() == expected, "keys")


def uint(value, minimum=0):
    require(type(value) is int and minimum <= value <= (1 << 64) - 1, "integer")
    return value


def validate_descriptor(value, config=None):
    keys(value, DESCRIPTOR)
    require(same(value["schema_version"], 1) and same(value["source_generation"], 1)
            and value["source_namespace"] == "linux-model-backend" and value["lifecycle_state"] == "active"
            and value["source_only"] is True and value["producer_owned"] is True, "descriptor")
    for field in ("source_instance", "host_boot_id"):
        identity(value[field])
    for field in ("process_id", "process_start_ticks", "launcher_process_id", "launcher_start_ticks", "listener_inode"):
        uint(value[field], 1)
    require(value["process_id"] != value["launcher_process_id"], "parent")
    for field in ("model_sha256", "backend_sha256"):
        require(type(value[field]) is str and re.fullmatch(r"[0-9a-f]{64}", value[field]) is not None, "hash")
    require(type(value["model_id"]) is str and 0 < len(value["model_id"]) <= 1024
            and all(c.isprintable() for c in value["model_id"]), "model")
    require(type(value["endpoint"]) is str and 0 < len(value["endpoint"]) <= 1024, "endpoint")
    address = urlsplit(value["endpoint"])
    require(address.scheme == "http" and address.hostname == "127.0.0.1" and address.port is not None
            and 1024 <= address.port <= 65535 and address.username is None and address.password is None
            and address.path in ("", "/") and not address.query and not address.fragment, "endpoint")
    if config is not None:
        config_hash(config)
        require(all(same(value[field], config[field]) for field in
                    ("model_id", "model_sha256", "backend_sha256", "endpoint")), "config")


def _window(value):
    uint(value["read_start_ns"], 1)
    uint(value["read_end_ns"], 1)
    require(value["read_start_ns"] <= value["read_end_ns"], "time")


def _parent(sample):
    # validate_sample has already checked the raw stat shape and identity.
    raw = sample["raw_stat"]
    return int(raw[raw.rfind(")") + 2:].split()[1])


def _participants(value, descriptor):
    _window(value)
    for role in ("backend", "launcher"):
        sample = value[role]
        validate_sample(sample)
        require(value["read_start_ns"] <= sample["read_start_ns"] <= sample["read_end_ns"] <= value["read_end_ns"],
                "sample_outside_check")
        prefix = "" if role == "backend" else "launcher_"
        require(sample["host_boot_id"] == descriptor["host_boot_id"]
                and sample["process_id"] == descriptor[prefix + "process_id"]
                and sample["process_start_ticks"] == descriptor["process_start_ticks" if role == "backend" else "launcher_start_ticks"],
                "process_identity")
    require(value["backend"]["uid"] == value["launcher"]["uid"]
            and _parent(value["backend"]) == descriptor["launcher_process_id"], "process_parent")


def _tcp(raw, local, remote, state, uid):
    require(type(raw) is str and 0 < len(raw.encode("ascii")) <= 4096 and "\n" not in raw and "\r" not in raw,
            "tcp_line")
    fields = raw.split()
    require(len(fields) >= 10 and re.fullmatch(r"[0-9]+:", fields[0]) is not None
            and fields[1] == local and fields[2] == remote and fields[3] == state, "tcp_tuple")
    require(re.fullmatch(r"[0-9]+", fields[7]) is not None and re.fullmatch(r"[0-9]+", fields[9]) is not None
            and int(fields[7]) == uid, "tcp_owner")
    return uint(int(fields[9]), 1)


def _probe(value, descriptor):
    keys(value, PROBE_KEYS)
    _participants(value, descriptor)
    listener = value["listener_proof"]
    keys(listener, {"raw_tcp_line", "fd_target", "fd_number"})
    uint(listener["fd_number"])
    port = urlsplit(descriptor["endpoint"]).port
    inode = _tcp(listener["raw_tcp_line"], f"0100007F:{port:04X}", "00000000:0000", "0A", value["backend"]["uid"])
    require(inode == descriptor["listener_inode"] and listener["fd_target"] == f"socket:[{inode}]", "listener")


def _send(value, descriptor):
    keys(value, SEND_KEYS)
    _participants(value, descriptor)
    for role in ("client", "server"):
        address = value[role + "_address"]
        keys(address, {"address", "port"})
        require(address["address"] == "127.0.0.1", "address")
        require(type(address["port"]) is int and 1024 <= address["port"] <= 65535, "port")
    require(value["client_address"] != value["server_address"]
            and value["server_address"]["port"] == urlsplit(descriptor["endpoint"]).port, "server")
    sample = value["client"]
    validate_sample(sample)
    require(value["read_start_ns"] <= sample["read_start_ns"] <= sample["read_end_ns"] <= value["read_end_ns"],
            "client_time")
    require(sample["host_boot_id"] == descriptor["host_boot_id"] and sample["uid"] == value["backend"]["uid"]
            and sample["process_id"] not in (descriptor["process_id"], descriptor["launcher_process_id"]), "client_identity")
    local = "0100007F:" + format(value["client_address"]["port"], "04X")
    remote = "0100007F:" + format(value["server_address"]["port"], "04X")
    client_inode = _tcp(value["raw_client_tcp_line"], local, remote, "01", sample["uid"])
    server_inode = _tcp(value["raw_server_tcp_line"], remote, local, "01", sample["uid"])
    require(len({client_inode, server_inode, descriptor["listener_inode"]}) == 3, "socket_identity")
    for role, inode in (("client", client_inode), ("server", server_inode)):
        uint(value[role + "_fd_number"])
        require(value[role + "_fd_target"] == f"socket:[{inode}]", "socket_owner")


def validate_execution(value, config=None, require_live=False, descriptor=None):
    """Validate a complete successful execution; partial proofs are rejected."""
    keys(value, {"schema_version", "descriptor", "capture_kind", "before", "send", "after"})
    require(same(value["schema_version"], 1), "schema")
    require(value["capture_kind"] in ("live", "fixture")
            and (not require_live or value["capture_kind"] == "live"), "capture")
    current = value["descriptor"]
    validate_descriptor(current, config)
    if descriptor is not None:
        validate_descriptor(descriptor, config)
        require(same(current, descriptor), "binding_changed")
    _probe(value["before"], current)
    _send(value["send"], current)
    _probe(value["after"], current)
    require(value["before"]["read_end_ns"] <= value["send"]["read_start_ns"]
            and value["send"]["read_end_ns"] <= value["after"]["read_start_ns"], "check_order")
    for role in ("backend", "launcher"):
        samples = [value[part][role] for part in ("before", "send", "after")]
        for left, right in zip(samples, samples[1:]):
            require(all(same(left[key], right[key]) for key in
                        ("host_boot_id", "process_id", "process_start_ticks", "uid", "clock_ticks_per_second", "page_bytes")),
                    "identity_changed")
            require(left["user_ticks"] <= right["user_ticks"] and left["system_ticks"] <= right["system_ticks"],
                    "counter_regression")
