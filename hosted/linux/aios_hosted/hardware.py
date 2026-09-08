"""Bounded, read-only Linux-visible inventory; enumeration is not an I/O test.

Only documented identity/status attributes are read. In particular, no config,
BAR, ROM, /dev, enable, rescan, reset, bind or unbind access is performed.
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

MAX_DEVICES = 256
MAX_ERRORS = 32
MAX_TEXT = 65536
SECTIONS = ("cpu", "memory", "pci", "usb", "block", "net")


class ObservationError(ValueError):
    pass


def text_at(path: Path, root: Path, limit: int = MAX_TEXT) -> str:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve(strict=True)):
            raise ObservationError("source_escape")
        with resolved.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ObservationError("source_too_large")
        value = raw.decode("utf-8", errors="strict").strip()
        if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
            raise ObservationError("control_character")
        return value
    except (OSError, RuntimeError, UnicodeError) as exc:
        raise ObservationError(type(exc).__name__) from exc


def section(source: str, data: dict | list) -> dict:
    return {"status": "observed", "source": source, "data": data, "errors": []}


def error(target: dict, reason: str, unavailable: bool = False) -> None:
    target["status"] = "unavailable" if unavailable else "partial"
    if len(target["errors"]) < MAX_ERRORS:
        target["errors"].append(reason)


def unsigned(value: str, maximum: int = (1 << 63) - 1) -> int:
    if not re.fullmatch(r"[0-9]+", value) or len(value) > 20:
        raise ObservationError("invalid_integer")
    result = int(value)
    if result > maximum:
        raise ObservationError("integer_overflow")
    return result


def collect_cpu(root: Path) -> dict:
    result = section("/proc/cpuinfo", {"logical_count": None, "model": None})
    try:
        raw = text_at(root / "cpuinfo", root, 1024 * 1024)
        processors = re.findall(r"^processor\s*:\s*([0-9]+)\s*$", raw, re.M)
        if not processors or len(processors) != len(set(processors)):
            raise ObservationError("missing_or_duplicate_processor")
        result["data"]["logical_count"] = len(processors)
        model = re.search(r"^(?:model name|Hardware|Processor)\s*:\s*(.+)$", raw, re.M)
        result["data"]["model"] = model.group(1).strip()[:256] if model else None
    except ObservationError as exc:
        error(result, str(exc), True)
    return result


def collect_memory(root: Path) -> dict:
    result = section("/proc/meminfo", {"total_bytes": None, "available_bytes": None,
                                      "available_is_estimate": True})
    try:
        raw = text_at(root / "meminfo", root)
        for key, field in (("MemTotal", "total_bytes"), ("MemAvailable", "available_bytes")):
            rows = [row for row in raw.splitlines() if row.startswith(key + ":")]
            if not rows and key == "MemAvailable":
                continue
            if len(rows) != 1:
                raise ObservationError("missing_or_duplicate_" + key)
            match = re.fullmatch(key + r":\s+([0-9]+)\s+kB", rows[0])
            if not match:
                raise ObservationError("invalid_unit_" + key)
            result["data"][field] = unsigned(match.group(1), ((1 << 63) - 1) // 1024) * 1024
        total, available = result["data"]["total_bytes"], result["data"]["available_bytes"]
        if not total or (available is not None and available > total):
            raise ObservationError("invalid_memory_range")
    except ObservationError as exc:
        error(result, str(exc), True)
    return result


def driver_name(device: Path, root: Path) -> str | None:
    link = device / "driver"
    if not link.is_symlink():
        return None
    try:
        target = link.resolve(strict=True)
        if not target.is_relative_to(root.resolve(strict=True)):
            raise ObservationError("driver_source_escape")
        return target.name
    except (OSError, RuntimeError) as exc:
        raise ObservationError("driver_unavailable") from exc


def subsystem_dir(root: Path, name: str) -> Path:
    # Honor the documented classification layout without assuming parent depth.
    candidates = (root / "subsystem" / name / "devices", root / "bus" / name / "devices",
                  root / "class" / name)
    if name == "block":
        candidates += (root / "block",)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[1] if name in ("pci", "usb") else candidates[2]


def collect_devices(root: Path, name: str) -> dict:
    directory = subsystem_dir(root, name)
    result = section("/sys/" + directory.relative_to(root).as_posix(), [])
    try:
        actual = directory.resolve(strict=True)
        if not actual.is_relative_to(root.resolve(strict=True)):
            raise ObservationError("directory_source_escape")
        # Limit enumeration itself as well as the number of serialized records.
        entries = list(itertools.islice(directory.iterdir(), MAX_DEVICES + 1))
    except (OSError, RuntimeError, ObservationError) as exc:
        error(result, "directory_unavailable:" + type(exc).__name__, True)
        return result
    if len(entries) > MAX_DEVICES:
        error(result, "device_capacity_exceeded")
    for entry in sorted(entries, key=lambda item: item.name)[:MAX_DEVICES]:
        # USB interfaces have no idVendor/idProduct; enumerate device objects.
        if name == "usb" and ":" in entry.name:
            continue
        row = {"name": entry.name, "source_path": None, "driver": None,
               "driver_bound": False, "usability": "UNTESTED", "errors": []}
        try:
            device = entry.resolve(strict=True)
            if not device.is_relative_to(root.resolve(strict=True)) or not device.is_dir():
                raise ObservationError("device_source_escape")
            row["source_path"] = "/sys/" + device.relative_to(root.resolve()).as_posix()
            row["driver"] = driver_name(device, root)
            row["driver_bound"] = row["driver"] is not None

            def read(attribute: str, optional: bool = False) -> str | None:
                path = device / attribute
                if optional and not path.exists() and not path.is_symlink():
                    return None
                return text_at(path, root)

            def number(attribute: str, optional: bool = False, minimum: int = 0) -> int | None:
                value = read(attribute, optional)
                if value is None:
                    return None
                parsed = unsigned(value)
                if parsed < minimum:
                    raise ObservationError("invalid_" + attribute.replace("/", "_"))
                return parsed

            def hex_id(attribute: str, digits: int, optional: bool = False) -> str | None:
                value = read(attribute, optional)
                if value is None:
                    return None
                value = value.removeprefix("0x")
                if not re.fullmatch(r"[0-9a-fA-F]{" + str(digits) + "}", value):
                    raise ObservationError("invalid_" + attribute)
                return "0x" + value.lower()

            if name == "pci":
                row.update(vendor=hex_id("vendor", 4), device=hex_id("device", 4),
                           class_code=hex_id("class", 6),
                           subsystem_vendor=hex_id("subsystem_vendor", 4, True),
                           subsystem_device=hex_id("subsystem_device", 4, True))
            elif name == "usb":
                row.update(vendor=hex_id("idVendor", 4), device=hex_id("idProduct", 4),
                           class_code=hex_id("bDeviceClass", 2),
                           bus_number=number("busnum", minimum=1), device_number=number("devnum", minimum=1))
            elif name == "block":
                sectors = number("size")
                if sectors > ((1 << 63) - 1) // 512:
                    raise ObservationError("block_size_overflow")
                row.update(size_bytes=sectors * 512, partition=number("partition", True, 1),
                           logical_block_size=number("queue/logical_block_size", True, 1))
            elif name == "net":
                state = read("operstate")
                if state not in ("unknown", "notpresent", "down", "lowerlayerdown", "testing", "dormant", "up"):
                    raise ObservationError("invalid_operstate")
                row.update(ifindex=number("ifindex", minimum=1), type=number("type"), operstate=state)
        except (OSError, RuntimeError, ObservationError) as exc:
            row["errors"].append(str(exc) if isinstance(exc, ObservationError) else type(exc).__name__)
            error(result, entry.name + ":" + row["errors"][0])
        result["data"].append(row)
    return result


def collect_hardware(proc_root: Path = Path("/proc"), sys_root: Path = Path("/sys")) -> dict:
    return {"schema_version": 1, "scope": "linux-visible", "source_only": True,
            "observation_only": True, "consistency": "best-effort",
            "cpu": collect_cpu(proc_root), "memory": collect_memory(proc_root),
            **{name: collect_devices(sys_root, name) for name in SECTIONS[2:]}}
