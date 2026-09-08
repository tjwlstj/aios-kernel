"""Independent semantic output contract for a clean AIOS console session.

Do not import the runtime renderer here. A matching artifact hash alone does
not establish that command responses or terminal output obeyed the contract.
This module renders only known command semantics, then compares all UTF-8 bytes.
Network result semantics are additionally checked by the calling verifier.
"""
from __future__ import annotations

import unicodedata

from newagent_output_contract import render_agent
from backend_output_contract import render_backend

_SECTIONS = ("cpu", "memory", "pci", "usb", "block", "net")
_KNOWN = ("help", "about", "status", "hardware", "net", "resolve", "fetch", "clear", "exit")
_PROMPT = "aios> "
_MAX_OUTPUT = 4 * 1024 * 1024
_HELP = [
    "help                         Show commands",
    "about                        AIOS identity and current scope",
    "status                       Show this session's startup state",
    "hardware [all|cpu|memory|pci|usb|block|net]",
    "                             Show the startup hardware snapshot",
    "net status                   Show observed network interfaces",
    "resolve HOST                 Resolve a host name (DNS)",
    "fetch URL                    Read an HTTP or verified HTTPS response",
    "clear                        Clear an interactive terminal",
    "exit                         Close the AIOS session",
]
_SERVICE_HELP = ["service status|start|stop|restart",
                 "                             Manage the AIOS console runtime service"]
_BACKEND_HELP = ["backend status|start|stop|restart",
                 "                             Manage the separate model backend process"]
_RECOVERY_HELP = ["backend status|start|stop|restart|recover", _BACKEND_HELP[1]]
_AGENT_HELP = ["agent status|start|stop|restart",
               "                             Manage the MAIN AI service",
               "room status|discover|bind|reconcile",
               "                             Inspect and explicitly bind the MAIN service",
               "ask PROMPT                   Ask the bound MAIN model (text only)"]
_RESOURCE_HELP = ["resources link|status|sample",
                  "                             Link and observe MAIN/backend CPU, RSS and system PSI"]
_CELL_HELP = ["cell status|activate|deactivate",
              "                             Inspect or change Cell 1's management activity"]
_BANNER = (
    "     /\\    ___  ___  ___\n"
    "    /  \\    |  /   \\/ __|\n"
    "   / /\\ \\   | | () |\\__ \\\n"
    "  /_/  \\_\\ ___ \\___/ |___/\n"
    "\n"
    "  AIOS Console 0.1.0\n"
)
_ERRORS = {
    "unknown_command": "Unknown command. Type help to see AIOS commands.",
    "invalid_arguments": "Invalid arguments. Type help for command syntax.",
    "not_a_terminal": "Clear requires an interactive terminal.",
    "invalid_input": "Control characters are not accepted in commands.",
    "interrupted": "Command interrupted. The session remains open.",
}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("console_output:" + reason)


def _same(actual: object, expected: object) -> bool:
    """Structural equality keeps JSON bool, int and float distinct."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return actual.keys() == expected.keys() and all(_same(actual[key], value) for key, value in expected.items())
    if type(expected) is list:
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _text(value: object, maximum: int = 2048) -> str:
    return "".join(character if character.isprintable() and unicodedata.category(character) != "Cf"
                   else " " for character in str(value))[:maximum]


def _size(value: int | None) -> str:
    return "unavailable" if value is None else f"{value / (1024 * 1024):.2f} MiB"


def _hardware(inventory: dict, selection: str) -> str:
    output: list[str] = []
    for name in _SECTIONS if selection == "all" else (selection,):
        section = inventory[name]
        rows = section["data"]
        output.append(f"{name.upper()} [{section['status']}] - startup snapshot")
        if name == "cpu":
            output.append(f"  Logical CPUs: {rows['logical_count']}; model: {_text(rows['model'] or 'unavailable')}")
        elif name == "memory":
            output.append(f"  Linux usable RAM: {_size(rows['total_bytes'])}")
            output.append(f"  Available estimate: {_size(rows['available_bytes'])}")
        elif not rows:
            output.append("  No objects observed.")
        else:
            for row in rows[:16]:
                label, driver = _text(row["name"], 64), _text(row.get("driver") or "none", 64)
                if name in ("pci", "usb"):
                    detail = f"{row.get('vendor', '?')}:{row.get('device', '?')}; driver={driver}"
                elif name == "block":
                    detail = f"{_size(row.get('size_bytes'))}; partition={row.get('partition') or 'no'}; driver={driver}"
                else:
                    detail = f"state={row.get('operstate', 'unavailable')}; ifindex={row.get('ifindex', '?')}; driver={driver}"
                output.append(f"  {label}: {detail}")
                if row["errors"]:
                    output.append("    Observation errors: " + ", ".join(_text(value, 128) for value in row["errors"][:4]))
            if len(rows) > 16:
                output.append(f"  Showing 16 of {len(rows)} objects; full snapshot: boot/inventory.json")
        if section["errors"]:
            output.append("  Section errors: " + ", ".join(_text(value, 128) for value in section["errors"][:4]))
    return "".join(line + "\n" for line in output)


def _response(command: dict, inventory: dict, start: dict, completed: int) -> str:
    name, args, result = command["name"], command["args"], command["result"]
    current = start["runtime_version"] in ("0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0")
    agent = start["runtime_version"] in ("0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0")
    resources = start["runtime_version"] in ("0.4.0", "0.5.0", "0.6.0", "0.7.0")
    cell = start["runtime_version"] in ("0.5.0", "0.6.0", "0.7.0")
    backend = start["runtime_version"] in ("0.6.0", "0.7.0")
    recovery = start["runtime_version"] == "0.7.0"
    help_rows = ([*_HELP[:-2], *_SERVICE_HELP, *(_RECOVERY_HELP if recovery else _BACKEND_HELP if backend else []), *(_AGENT_HELP if agent else []),
                  *(_CELL_HELP if cell else []), *(_RESOURCE_HELP if resources else []), *_HELP[-2:]]
                 if current else _HELP)

    def success(expected: dict, rendered: str) -> str:
        _require(command["outcome"] == "OK" and _same(result, expected), name + "_result")
        return rendered

    def error(code: str) -> str:
        expected = {"error": code, "message": _ERRORS[code]}
        _require(command["outcome"] == "ERROR" and _same(result, expected), name + "_error_result")
        return "Error: " + _ERRORS[code] + "\n"

    if name == "" and not args and result.get("error") in ("invalid_input", "interrupted"):
        code = result["error"]
        return ("\n" if code == "interrupted" else "") + error(code)
    if name == "help" and not args:
        return success({"commands": help_rows}, "AIOS commands:\n" + "".join("  " + row + "\n" for row in help_rows))
    if name == "about" and not args:
        expected = {"product": "AIOS", "version": start["runtime_version"], "runtime_kind": "linux-hosted-userspace",
                    "binding_status": "EXPLICIT" if agent else "UNBOUND", "management_actions": "UNSUPPORTED"}
        boundary = (("Use backend to manage the separate model process; MAIN has its own lifecycle.\n" if backend else "")
                    + "Use agent and room for explicit hosted AI service bindings.\n"
                    + ("Use cell to activate or deactivate Cell 1's management state.\n" if cell else "")
                    + ("Use resources for explicit MAIN/backend observation; Linux system pressure is unattributed.\n" if resources else "")
                    + "Kernel resource actions remain unsupported. Model answers are text only.\n") if agent else (
                    "Canonical service binding and kernel resource actions are not available in this preview.\n"
                    "Use service to manage only the AIOS console runtime process.\n") if current else (
                    "Service binding and management actions are not available in this preview.\n")
        return success(expected,
                       "AIOS owns its management model: Room -> Cell -> Node -> NodeBit.\n"
                       "This console uses the Linux kernel and its drivers through userspace interfaces.\n"
                       "The native AIOS kernel remains a separate implementation.\n" + boundary)
    if name == "status" and not args:
        expected = {"boot_state": "READY", "boot_run_id": start["boot_run_id"],
                    "commands_completed": completed, "visibility": "linux-visible"}
        return success(expected,
                       f"AIOS session: running; startup: READY; completed commands: {completed}\n"
                       "Hardware visibility: Linux guest/host view; snapshot taken at startup.\n")
    if name == "hardware" and (not args or len(args) == 1 and args[0] in (*_SECTIONS, "all")):
        selection = args[0] if args else "all"
        selected = _SECTIONS if selection == "all" else (selection,)
        expected = {"section": selection, "snapshot": "startup", "sections": {key: inventory[key] for key in selected}}
        return success(expected, _hardware(inventory, selection))
    if name == "net" and args == ["status"]:
        expected = {"operation": "status", "snapshot": "startup", "status": inventory["net"]["status"],
                    "interfaces": inventory["net"]["data"]}
        return success(expected, _hardware(inventory, "net") +
                       "Link state alone does not establish Internet connectivity; use resolve and fetch.\n")
    if current and name == "service" and len(args) == 1 and args[0] in ("status", "start", "stop", "restart"):
        # Exact fields/types/action-state semantics are independently validated
        # by verify_console.service_result before this renderer is called.
        rendered = ""
        if command["outcome"] == "ERROR":
            rendered += f"Service {args[0]} failed: {_text(result['error'], 64)}.\n"
        generation = result["generation"] if result["generation"] is not None else "none"
        instance = _text(result["instance_id"][:8], 8) if result["instance_id"] else "none"
        rendered += f"AIOS console service: {_text(result['state'], 16)}; generation {generation}; instance {instance}\n"
        rendered += f"  Observations: {result['observation_sequence']}; identity: source-only / UNBOUND.\n"
        return rendered
    if backend and name == "backend" and len(args) == 1 and args[0] in ("status", "start", "stop", "restart", *(('recover',) if recovery else ())):
        return render_backend(command)
    if agent and (name == "agent" and len(args) == 1 and args[0] in ("status", "start", "stop", "restart")
                  or name == "room" and len(args) == 1 and args[0] in ("status", "discover", "bind", "reconcile")
                  or cell and name == "cell" and len(args) == 1 and args[0] in ("status", "activate", "deactivate")
                  or resources and name == "resources" and len(args) == 1 and args[0] in ("link", "status", "sample")
                  or name == "ask" and bool(args)):
        return render_agent(command)
    if name in ("resolve", "fetch") and len(args) == 1:
        _require(result.get("operation") == name and result.get("outcome") == command["outcome"], name + "_operation")
        _require(type(result.get("elapsed_ms")) is int and result["elapsed_ms"] >= 0, name + "_duration")
        if name == "resolve":
            if command["outcome"] == "OK":
                _require(type(result.get("addresses")) is list and bool(result["addresses"]), "dns_addresses")
                _require(type(result.get("host")) is str, "dns_host")
                return f"DNS {_text(result['host'])}: {', '.join(_text(value) for value in result['addresses'])} ({result['elapsed_ms']} ms)\n"
            _require(type(result.get("error")) is str, "dns_error")
            return f"DNS request failed: {_text(result['error'])}. You can try another host.\n"
        if command["outcome"] == "OK":
            _require(result.get("protocol") in ("http", "https"), "fetch_protocol")
            _require(type(result.get("status")) is int and 200 <= result["status"] <= 299, "fetch_status")
            _require(type(result.get("received_bytes")) is int and result["received_bytes"] >= 0, "fetch_bytes")
            _require(type(result.get("tls_verified")) is bool and type(result.get("truncated")) is bool, "fetch_flags")
            _require(type(result.get("body_preview")) is str, "fetch_preview")
            transport = "certificate verified" if result["tls_verified"] else "plain HTTP"
            rendered = f"{result['protocol'].upper()} {result['status']}; {result['received_bytes']} bytes; {transport}; {result['elapsed_ms']} ms\n"
            if result["truncated"]:
                rendered += "Response reached the bounded read limit; showing a partial body.\n"
            if result["body_preview"]:
                rendered += "  " + _text(result["body_preview"]) + "\n"
            return rendered
        _require(type(result.get("error")) is str, "fetch_error")
        return f"HTTP request failed: {_text(result['error'])}. You can try another URL.\n"
    if name == "clear" and not args:
        return success({"cleared": True}, "\x1b[2J\x1b[H\n") if command["outcome"] == "OK" else error("not_a_terminal")
    if name == "exit" and not args:
        return success({"closing": True}, "AIOS session closed.\n")
    known = (name in _KNOWN or current and name == "service" or agent and name in ("agent", "room", "ask")
             or resources and name == "resources" or cell and name == "cell" or backend and name == "backend")
    return error("invalid_arguments" if known else "unknown_command")


def validate_output(events: list[dict], inventory: dict, raw_console: bytes) -> None:
    """Reject any semantic response drift, missing output, or extra output.

    `inventory` is the independently validated bare startup inventory object.
    This gate accepts only READY -> CLOSED/0 sessions; command errors within a
    clean session are valid and must match their defined human-facing messages.
    """
    try:
        _require(type(raw_console) is bytes and len(raw_console) <= _MAX_OUTPUT, "size_or_type")
        raw_console.decode("utf-8", errors="strict")
        _require(type(events) is list and 2 <= len(events) <= 256, "event_count")
        _require([event["event"] for event in events] == ["START", *["COMMAND"] * (len(events) - 2), "STOP"], "event_order")
        start, stop = events[0]["data"], events[-1]["data"]
        version = start["runtime_version"]
        _require(start["boot_state"] == "READY" and version in ("0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0"), "startup")
        _require(stop.get("reason") in ("exit", "eof") and _same(stop, {"state": "CLOSED", "exit_code": 0, "reason": stop["reason"]}), "stop")
        output = [_BANNER.replace("0.1.0", version), "  Linux-backed userspace preview | type help to begin\n",
                  f"  Startup READY | {inventory['cpu']['data']['logical_count']} CPUs | RAM {_size(inventory['memory']['data']['total_bytes'])}\n",
                  "  Devices: " + " | ".join(f"{key.upper()} {len(inventory[key]['data'])}" for key in _SECTIONS[2:]) + "\n",
                  "  Hardware is a Linux-visible startup snapshot. DNS/HTTP run on request.\n\n"]
        commands = events[1:-1]
        for index, event in enumerate(commands):
            command = event["data"]
            _require(type(command) is dict and set(command) == {"name", "args", "outcome", "result"}, "command_shape")
            name, args = command["name"], command["args"]
            _require(type(name) is str and name == name.lower() and type(args) is list and all(type(value) is str for value in args), "command_input")
            _require(all(value and all(c.isprintable() and not c.isspace() and unicodedata.category(c) != "Cf" for c in value)
                         for value in ([name] if name else []) + args), "command_token")
            _require(len(name) + sum(map(len, args)) + len(args) <= 2048, "command_length")
            _require(command["outcome"] in ("OK", "ERROR") and type(command["result"]) is dict, "command_outcome")
            if name == "exit" and not args:
                _require(index == len(commands) - 1 and stop["reason"] == "exit", "early_exit")
            output.extend((_PROMPT, _response(command, inventory, start, index)))
        if stop["reason"] == "exit":
            _require(bool(commands) and commands[-1]["data"]["name"] == "exit" and commands[-1]["data"]["args"] == [], "missing_exit")
        else:
            output.extend((_PROMPT, "End of input. AIOS session closed.\n"))
        expected = "".join(output).encode("utf-8")
        _require(len(expected) <= _MAX_OUTPUT and expected == raw_console, "transcript_mismatch")
    except (TypeError, KeyError, IndexError, AttributeError, UnicodeError) as exc:
        raise ValueError("console_output:malformed:" + type(exc).__name__) from exc
