"""A bounded AIOS command surface with separate, replayable session evidence.

This console runs above Linux. Its inventory is source-only and its explicit
DNS/HTTP requests perform user-requested network I/O. Explicit MAIN commands
consume a separate management authority; Linux IDs remain source-only.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import sys
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Callable, TextIO

from aios_hosted.boot import encoded, run_boot
from aios_hosted.hardware import SECTIONS

from . import VERSION
from .network import fetch_url, resolve_host

MAX_INPUT = 2048
MAX_EVENTS = 256
MAX_SESSION_BYTES = 4 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
MAX_VISIBLE_DEVICES = 16
PROMPT = "aios> "
BANNER = r"""
     /\    ___  ___  ___
    /  \    |  /   \/ __|
   / /\ \   | | () |\__ \
  /_/  \_\ ___ \___/ |___/

  AIOS Console {version}
""".lstrip("\n").replace("{version}", VERSION)
HELP = (
    "help                         Show commands",
    "about                        AIOS identity and current scope",
    "status                       Show this session's startup state",
    "hardware [all|cpu|memory|pci|usb|block|net]",
    "                             Show the startup hardware snapshot",
    "net status                   Show observed network interfaces",
    "resolve HOST                 Resolve a host name (DNS)",
    "fetch URL                    Read an HTTP or verified HTTPS response",
    "service status|start|stop|restart",
    "                             Manage the AIOS console runtime service",
    "backend status|start|stop|restart|recover",
    "                             Manage the separate model backend process",
    "agent status|start|stop|restart",
    "                             Manage the MAIN AI service",
    "room status|discover|bind|reconcile",
    "                             Inspect and explicitly bind the MAIN service",
    "ask PROMPT                   Ask the bound MAIN model (text only)",
    "cell status|activate|deactivate",
    "                             Inspect or change Cell 1's management activity",
    "resources link|status|sample",
    "                             Link and observe MAIN/backend CPU, RSS and system PSI",
    "clear                        Clear an interactive terminal",
    "exit                         Close the AIOS session",
)


def safe_text(value: object, limit: int = 2048) -> str:
    """Render untrusted text without terminal controls or bidi format codes."""
    text = str(value)
    return "".join(c if c.isprintable() and unicodedata.category(c) != "Cf"
                   else " " for c in text)[:limit]


def source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent.parent
    files = ("aios-console.py", "aios-boot.py", "aios-service.py", "aios-agent.py",
             "aios_console/__init__.py", "aios_console/shell.py", "aios_console/network.py",
             "aios_hosted/__init__.py", "aios_hosted/boot.py", "aios_hosted/hardware.py",
             "aios_service/__init__.py", "aios_service/client.py", "aios_service/lifecycle.py",
             "aios_agent/__init__.py", "aios_agent/client.py", "aios_agent/daemon.py",
             "aios_agent/inference.py", "aios_agent/protocol.py",
             "aios_management/__init__.py", "aios_management/binding.py",
             "aios_management/resources.py", "aios_resources/__init__.py",
             "aios_resources/proc.py", "aios_resources/backend.py", "aios_resources/runtime.py",
             "aios-backend.py", "aios_backend/__init__.py", "aios_backend/protocol.py",
             "aios_backend/client.py", "aios_backend/daemon.py", "aios_agent/backend_binding.py")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(files)}


def _size(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value / (1024 * 1024):.2f} MiB"


def _resource_lines(value: dict) -> list[str]:
    """Display copied observations without promoting cached metrics to live state."""
    action = value["action"]
    relation = "linked" if value["relation_current"] else "stale" if value["relation"] else "unlinked"
    lines = []
    if value["outcome"] == "ERROR":
        lines.append(f"Resources {safe_text(action, 16)} failed: {safe_text(value['error'], 64)}.")
    lines.append(f"AIOS resources: {relation}; observation only; ownership unverified.")
    observation = value["observation"]
    if observation is None:
        lines.append("  No resource observation recorded; resource actions unsupported.")
        return lines
    cached = action == "status" or value["outcome"] == "ERROR"
    label = "Last observation (cached)" if cached else "Observed process window"
    lines.append(f"  {label}: {safe_text(observation['observation_id'][:8], 8)}; resource actions unsupported.")
    def milliseconds(ns):
        return f"{ns // 1_000_000}.{ns % 1_000_000 // 1000:03d} ms"
    for role, title in (("main", "MAIN control process"), ("backend", "Model backend process")):
        cpu = observation["cpu"][role]
        rss = observation["after"][role]["rss_bytes_estimate"]
        lines.append(f"  {title}: CPU {milliseconds(cpu['cpu_time_ns'])} over {milliseconds(cpu['elapsed_ns'])}; RSS estimate {_size(rss)}.")
    lines.append("  Linux system PSI (unattributed):")
    for kind in ("cpu", "memory", "io"):
        pressure = observation["after"]["pressure"]["metrics"][kind]
        if pressure["state"] != "AVAILABLE":
            lines.append(f"    {kind.upper()}: unavailable ({safe_text(pressure['error'], 64)}).")
            continue
        def percent(bp):
            return f"{bp // 100}.{bp % 100:02d}%"
        some = percent(pressure["some"]["avg10_bp"])
        full = "undefined for system CPU" if kind == "cpu" else percent(pressure["full"]["avg10_bp"])
        lines.append(f"    {kind.upper()}: some avg10 {some}; full avg10 {full}.")
    return lines


def _hardware_lines(inventory: dict, selected: str) -> list[str]:
    lines: list[str] = []
    for name in SECTIONS if selected == "all" else (selected,):
        section = inventory[name]
        data = section["data"]
        lines.append(f"{name.upper()} [{section['status']}] - startup snapshot")
        if name == "cpu":
            lines.append(f"  Logical CPUs: {data['logical_count']}; model: {safe_text(data['model'] or 'unavailable')}")
        elif name == "memory":
            lines.append(f"  Linux usable RAM: {_size(data['total_bytes'])}")
            lines.append(f"  Available estimate: {_size(data['available_bytes'])}")
        elif not data:
            lines.append("  No objects observed.")
        else:
            for row in data[:MAX_VISIBLE_DEVICES]:
                label = safe_text(row["name"], 64)
                driver = safe_text(row.get("driver") or "none", 64)
                if name in ("pci", "usb"):
                    detail = f"{row.get('vendor', '?')}:{row.get('device', '?')}; driver={driver}"
                elif name == "block":
                    detail = f"{_size(row.get('size_bytes'))}; partition={row.get('partition') or 'no'}; driver={driver}"
                else:
                    detail = f"state={row.get('operstate', 'unavailable')}; ifindex={row.get('ifindex', '?')}; driver={driver}"
                lines.append(f"  {label}: {detail}")
                if row["errors"]:
                    lines.append("    Observation errors: " + ", ".join(safe_text(e, 128) for e in row["errors"][:4]))
            if len(data) > MAX_VISIBLE_DEVICES:
                lines.append(f"  Showing {MAX_VISIBLE_DEVICES} of {len(data)} objects; full snapshot: boot/inventory.json")
        if section["errors"]:
            lines.append("  Section errors: " + ", ".join(safe_text(e, 128) for e in section["errors"][:4]))
    return lines


class Transcript:
    def __init__(self, path: Path, output: TextIO):
        self.path = path
        self.output = output
        self.size = 0
        self.finalizing = False
        path.write_bytes(b"")

    def write(self, value: str) -> None:
        # Persist precisely the application output, including prompts. Input is
        # kept in structured COMMAND records and is not echoed by the program.
        size = len(value.encode("utf-8"))
        limit = MAX_TRANSCRIPT_BYTES if self.finalizing else MAX_TRANSCRIPT_BYTES - 1024
        if self.size + size > limit:
            raise RuntimeError("transcript_capacity")
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
        self.size += size
        self.output.write(value)
        self.output.flush()

    def line(self, value: str = "") -> None:
        self.write(value + "\n")


def run_console(destination: Path, *, input_stream: TextIO | None = None,
                output_stream: TextIO | None = None, proc_root: Path = Path("/proc"),
                sys_root: Path = Path("/sys"), test_system: str | None = None,
                resolver: Callable | None = None, fetcher: Callable | None = None,
                service_dir: Path | None = None, service_control: Callable | None = None,
                agent_dir: Path | None = None, agent_config: Path | None = None,
                agent_control: Callable | None = None, backend_dir: Path | None = None,
                backend_control: Callable | None = None) -> int:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    input_stream = input_stream if input_stream is not None else sys.stdin
    output_stream = output_stream if output_stream is not None else sys.stdout
    fixture = (test_system is not None or proc_root != Path("/proc") or sys_root != Path("/sys")
               or resolver is not None or fetcher is not None or service_control is not None
               or agent_control is not None or backend_control is not None)
    system = test_system if test_system is not None else platform.system()
    resolver = resolver if resolver is not None else resolve_host
    fetcher = fetcher if fetcher is not None else fetch_url
    # A path selects private runtime state, never a canonical Cell/Node. Merely
    # opening or closing the console must not create state or stop the daemon.
    service_dir = Path(service_dir) if service_dir is not None else Path.home() / ".local/state/aios/console-runtime"
    agent_dir = Path(agent_dir) if agent_dir is not None else Path.home() / ".local/state/aios/main-agent"
    backend_dir = Path(backend_dir) if backend_dir is not None else Path("/tmp/aios-model-backend")
    capture_kind = "fixture" if fixture else "live"
    session_id = str(uuid.uuid4())
    transcript = Transcript(destination / "console.log", output_stream)
    events: list[dict] = []
    event_bytes = 0
    started = time.monotonic_ns()

    def emit(name: str, data: dict) -> None:
        nonlocal event_bytes
        if len(events) >= MAX_EVENTS:
            raise RuntimeError("session_event_capacity")
        event = {"schema_version": 7, "session_id": session_id, "sequence": len(events) + 1,
                 "elapsed_ns": time.monotonic_ns() - started, "event": name, "data": data}
        raw = encoded(event)
        limit = MAX_SESSION_BYTES if name == "STOP" else MAX_SESSION_BYTES - 1024
        if event_bytes + len(raw) > limit:
            raise RuntimeError("session_evidence_capacity")
        events.append(event)
        with (destination / "session.events.jsonl").open("ab") as stream:
            stream.write(raw)
        event_bytes += len(raw)

    # Preserve the existing producer and its independent boot evidence, while
    # presenting an intentionally small human-facing startup summary.
    with contextlib.redirect_stdout(io.StringIO()):
        boot_exit = run_boot(destination / "boot", proc_root=proc_root, sys_root=sys_root,
                             test_system=system if fixture else None)
    boot = json.loads((destination / "boot" / "result.json").read_text(encoding="utf-8"))
    inventory = json.loads((destination / "boot" / "inventory.json").read_text(encoding="utf-8"))["inventory"]
    source_process = None
    if not fixture and system == "Linux":
        from aios_resources import ResourceError
        from aios_resources.proc import ProcessReader
        try:
            with ProcessReader(os.getpid()) as reader:
                source_process = reader.sample()
        except (OSError, ResourceError):
            # Keep the console usable, but unavailable identity cannot qualify
            # a live recovery-owner join in the independent verifier.
            pass
    emit("START", {"product": "AIOS", "runtime_version": VERSION,
                   "runtime_kind": "linux-hosted-userspace-console", "capture_kind": capture_kind,
                   "boot_run_id": boot["run_id"], "boot_state": boot["state"],
                   "binding_status": "UNBOUND", "management_actions": "UNSUPPORTED",
                   "hardware_scope": "linux-visible" if system == "Linux" else "unsupported",
                   "network_io": "user-requested", "source_process": source_process})
    state, exit_code, reason = "FAILED", boot_exit, "boot_failed"

    def error(message: str, code: str) -> tuple[str, dict]:
        transcript.line("Error: " + message)
        return "ERROR", {"error": code, "message": message}

    def execute(name: str, args: list[str]) -> tuple[str, dict]:
        if name == "help" and not args:
            transcript.line("AIOS commands:")
            for row in HELP:
                transcript.line("  " + row)
            return "OK", {"commands": list(HELP)}
        if name == "about" and not args:
            result = {"product": "AIOS", "version": VERSION, "runtime_kind": "linux-hosted-userspace",
                      "binding_status": "EXPLICIT", "management_actions": "UNSUPPORTED"}
            transcript.line("AIOS owns its management model: Room -> Cell -> Node -> NodeBit.")
            transcript.line("This console uses the Linux kernel and its drivers through userspace interfaces.")
            transcript.line("The native AIOS kernel remains a separate implementation.")
            transcript.line("Use backend to manage the separate model process; MAIN has its own lifecycle.")
            transcript.line("Use agent and room for explicit hosted AI service bindings.")
            transcript.line("Use cell to activate or deactivate Cell 1's management state.")
            transcript.line("Use resources for explicit MAIN/backend observation; Linux system pressure is unattributed.")
            transcript.line("Kernel resource actions remain unsupported. Model answers are text only.")
            return "OK", result
        if name == "status" and not args:
            result = {"boot_state": boot["state"], "boot_run_id": boot["run_id"],
                      "commands_completed": len(events) - 1, "visibility": "linux-visible"}
            transcript.line(f"AIOS session: running; startup: {boot['state']}; completed commands: {len(events) - 1}")
            transcript.line("Hardware visibility: Linux guest/host view; snapshot taken at startup.")
            return "OK", result
        if name == "hardware" and (not args or len(args) == 1 and args[0] in (*SECTIONS, "all")):
            selected = args[0] if args else "all"
            for row in _hardware_lines(inventory, selected):
                transcript.line(row)
            sections = SECTIONS if selected == "all" else (selected,)
            return "OK", {"section": selected, "snapshot": "startup",
                          "sections": {key: inventory[key] for key in sections}}
        if name == "net" and args == ["status"]:
            for row in _hardware_lines(inventory, "net"):
                transcript.line(row)
            transcript.line("Link state alone does not establish Internet connectivity; use resolve and fetch.")
            return "OK", {"operation": "status", "snapshot": "startup",
                          "status": inventory["net"]["status"], "interfaces": inventory["net"]["data"]}
        if name == "resolve" and len(args) == 1:
            result = resolver(args[0])
            if result["host"] == "<invalid>":
                args[:] = ["<invalid>"]
            if result["outcome"] == "OK":
                transcript.line(f"DNS {safe_text(result['host'])}: {', '.join(safe_text(x) for x in result['addresses'])} ({result['elapsed_ms']} ms)")
            else:
                transcript.line(f"DNS request failed: {safe_text(result['error'])}. You can try another host.")
            return result["outcome"], result
        if name == "fetch" and len(args) == 1:
            result = fetcher(args[0])
            if result["url"] == "<invalid>":
                args[:] = ["<invalid>"]
            if result["outcome"] == "OK":
                tls = "certificate verified" if result["tls_verified"] else "plain HTTP"
                transcript.line(f"{result['protocol'].upper()} {result['status']}; {result['received_bytes']} bytes; {tls}; {result['elapsed_ms']} ms")
                if result["truncated"]:
                    transcript.line("Response reached the bounded read limit; showing a partial body.")
                if result["body_preview"]:
                    transcript.line("  " + safe_text(result["body_preview"]))
            else:
                transcript.line(f"HTTP request failed: {safe_text(result['error'])}. You can try another URL.")
            return result["outcome"], result
        if name == "service" and len(args) == 1 and args[0] in ("status", "start", "stop", "restart"):
            controller = service_control
            if controller is None:
                from aios_service.client import control
                controller = control
            result = controller(service_dir, args[0])
            if result["outcome"] == "ERROR":
                transcript.line(f"Service {args[0]} failed: {safe_text(result['error'], 64)}.")
            generation = result["generation"] if result["generation"] is not None else "none"
            instance = safe_text(result["instance_id"][:8], 8) if result["instance_id"] else "none"
            transcript.line(f"AIOS console service: {safe_text(result['state'], 16)}; generation {generation}; instance {instance}")
            transcript.line(f"  Observations: {result['observation_sequence']}; identity: source-only / UNBOUND.")
            return result["outcome"], result
        if name == "backend" and len(args) == 1 and args[0] in ("status", "start", "stop", "restart", "recover"):
            controller = backend_control
            if controller is None:
                from aios_backend.client import control
                controller = control
            result = controller(backend_dir, args[0], config=agent_config)
            if result["outcome"] == "ERROR":
                transcript.line(f"Backend {args[0]} failed: {safe_text(result['error'], 64)}.")
            record = result["service_record"]
            ready = "ready" if result["state"] == "RUNNING" and record is not None and record["backend_ready"] else "not ready"
            generation = record["start_generation"] if record else "none"
            instance = safe_text(record["instance_id"][:8], 8) if record else "none"
            transcript.line(f"AIOS model backend: {safe_text(result['state'], 16)}; backend {ready}")
            transcript.line(f"  Start generation {generation}; instance {instance}; identity: source-only.")
            transcript.line("  MAIN service and Cell management state are separate; resource actions unsupported.")
            return result["outcome"], result
        if (name == "agent" and len(args) == 1 and args[0] in ("status", "start", "stop", "restart")
                or name == "room" and len(args) == 1 and args[0] in ("status", "discover", "bind", "reconcile")
                or name == "cell" and len(args) == 1 and args[0] in ("status", "activate", "deactivate")
                or name == "resources" and len(args) == 1 and args[0] in ("link", "status", "sample")
                or name == "ask" and bool(args)):
            controller = agent_control
            if controller is None:
                from aios_agent.client import control
                controller = control
            action = "ask" if name == "ask" else name + "-" + args[0] if name in ("room", "cell", "resources") else args[0]
            prompt = " ".join(args) if name == "ask" else None
            options = {"backend_dir": backend_dir} if action in ("resources-link", "start", "restart") else {}
            result = controller(agent_dir, action, config=agent_config, prompt=prompt, **options)
            if result["outcome"] == "ERROR" and name not in ("cell", "resources"):
                transcript.line(f"MAIN {action} failed: {safe_text(result['error'], 64)}.")
            if name == "ask":
                receipt = result["inference_receipt"]
                if result["outcome"] == "OK":
                    transcript.line("MAIN answer:")
                    transcript.line("  " + safe_text(receipt["content"], 4096))
                if result.get("resource_result") is not None:
                    for row in _resource_lines(result["resource_result"]):
                        transcript.line(row)
            elif name == "resources":
                if result["resource_result"] is None:
                    transcript.line(f"Resources {args[0]} failed: {safe_text(result['error'], 64)}.")
                    transcript.line("AIOS resources: unavailable; no resource response.")
                else:
                    for row in _resource_lines(result["resource_result"]):
                        transcript.line(row)
            elif name == "cell":
                if result["outcome"] == "ERROR":
                    transcript.line(f"Cell {args[0]} failed: {safe_text(result['error'], 64)}.")
                snapshot = result["management_snapshot"]
                if snapshot is None or snapshot["parent"] is None:
                    transcript.line("AIOS Cell 1: unavailable; no management snapshot.")
                else:
                    parent = snapshot["parent"]
                    activity = "active" if parent["active"] else "inactive"
                    transcript.line(f"AIOS Cell 1: {activity}; generation {parent['generation']}")
                    transcript.line(f"  Bound MAIN nodes: {snapshot['bound_nodes']}; binding: {safe_text(snapshot['state'], 16)}.")
                    transcript.line(f"  MAIN process: {safe_text(result['state'], 16)}; Cell activity does not start or stop it.")
            elif name == "room":
                snapshot = result["management_snapshot"]
                if snapshot is None:
                    transcript.line("AIOS Room: unavailable; no management snapshot.")
                else:
                    authority = safe_text(snapshot["authority_instance"][:8], 8)
                    binding = snapshot["binding"]
                    generation = binding["generation"] if binding else "none"
                    transcript.line(f"AIOS Room: {safe_text(snapshot['state'], 16)}; authority {authority}")
                    transcript.line(f"  Cell 1 -> Node 101 (MAIN); binding generation {generation}; bound nodes {snapshot['bound_nodes']}.")
            else:
                source = result["source_record"]
                ready = "ready" if source is not None and source["model_ready"] else "not ready"
                instance = safe_text(source["source_instance"][:8], 8) if source else "none"
                generation = source["source_generation"] if source else "none"
                transcript.line(f"AIOS MAIN service: {safe_text(result['state'], 16)}; model {ready}")
                transcript.line(f"  Source generation {generation}; instance {instance}; resource actions unsupported.")
            return result["outcome"], result
        if name == "clear" and not args:
            if output_stream.isatty():
                transcript.write("\x1b[2J\x1b[H\n")
                return "OK", {"cleared": True}
            return error("Clear requires an interactive terminal.", "not_a_terminal")
        if name == "exit" and not args:
            transcript.line("AIOS session closed.")
            return "OK", {"closing": True}
        if name in ("help", "about", "status", "hardware", "net", "resolve", "fetch", "service", "backend", "agent", "room", "cell", "ask", "resources", "clear", "exit"):
            return error("Invalid arguments. Type help for command syntax.", "invalid_arguments")
        return error("Unknown command. Type help to see AIOS commands.", "unknown_command")

    if boot_exit:
        transcript.line(f"AIOS Console could not start: {boot['state']} (exit {boot_exit}).")
        if system != "Linux":
            transcript.line("Run this console on Linux with procfs and sysfs mounted.")
    else:
        transcript.write(BANNER)
        transcript.line("  Linux-backed userspace preview | type help to begin")
        transcript.line(f"  Startup READY | {inventory['cpu']['data']['logical_count']} CPUs | RAM {_size(inventory['memory']['data']['total_bytes'])}")
        transcript.line("  Devices: " + " | ".join(f"{key.upper()} {len(inventory[key]['data'])}" for key in SECTIONS[2:]))
        transcript.line("  Hardware is a Linux-visible startup snapshot. DNS/HTTP run on request.")
        transcript.line()
        state, exit_code, reason = "CLOSED", 0, "eof"
        try:
            while len(events) < MAX_EVENTS - 1:
                transcript.write(PROMPT)
                try:
                    raw = input_stream.readline(MAX_INPUT + 2)
                    if raw == "":
                        transcript.line("End of input. AIOS session closed.")
                        break
                    raw = raw.rstrip("\r\n")
                    if len(raw) > MAX_INPUT:
                        outcome, result = error("Input limit exceeded; start a new session.", "input_limit")
                        emit("COMMAND", {"name": "", "args": [], "outcome": outcome, "result": result})
                        state, exit_code, reason = "FAILED", 2, "input_limit"
                        break
                    if any(not c.isprintable() and c != "\t" or unicodedata.category(c) == "Cf" for c in raw):
                        outcome, result = error("Control characters are not accepted in commands.", "invalid_input")
                        name, args = "", []
                    else:
                        fields = raw.split()
                        name, args = (fields[0].lower(), fields[1:]) if fields else ("", [])
                        outcome, result = execute(name, args)
                except KeyboardInterrupt:
                    transcript.line()
                    outcome, result = error("Command interrupted. The session remains open.", "interrupted")
                    name, args = "", []
                emit("COMMAND", {"name": name, "args": args, "outcome": outcome, "result": result})
                if name == "exit" and outcome == "OK":
                    reason = "exit"
                    break
            else:
                transcript.line("Session command limit reached. Start a new session.")
                state, exit_code, reason = "FAILED", 2, "command_limit"
        except Exception as exc:
            transcript.finalizing = True
            transcript.line("AIOS session failed: " + type(exc).__name__)
            state, exit_code, reason = "FAILED", 1, "runtime_error"
    emit("STOP", {"state": state, "exit_code": exit_code, "reason": reason})
    result = {"schema_version": 7, "session_id": session_id, "capture_kind": capture_kind,
              "state": state, "exit_code": exit_code, "boot_run_id": boot["run_id"],
              "source_hashes": source_hashes(),
              "files": {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                        for name in ("console.log", "session.events.jsonl", "boot/result.json")}}
    (destination / "session-result.json").write_bytes(encoded(result))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the AIOS interactive Linux userspace console.")
    parser.add_argument("--artifact-dir", type=Path, required=True,
                        help="New directory for session and startup evidence; existing paths are refused.")
    parser.add_argument("--service-dir", type=Path,
                        help="Private console runtime state (default: ~/.local/state/aios/console-runtime).")
    parser.add_argument("--agent-dir", type=Path,
                        help="Private MAIN service state (default: ~/.local/state/aios/main-agent).")
    parser.add_argument("--agent-config", type=Path,
                        help="Pinned configuration for explicit MAIN or model backend start.")
    parser.add_argument("--backend-dir", type=Path,
                        help="Private model backend state for backend commands and resources link (default: /tmp/aios-model-backend).")
    args = parser.parse_args()
    try:
        return run_console(args.artifact_dir, service_dir=args.service_dir,
                           agent_dir=args.agent_dir, agent_config=args.agent_config, backend_dir=args.backend_dir)
    except FileExistsError:
        print("AIOS Console refused: artifact directory already exists; choose a new directory.", file=sys.stderr)
        return 4
    except OSError as exc:
        print("AIOS Console artifact error: " + type(exc).__name__, file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
