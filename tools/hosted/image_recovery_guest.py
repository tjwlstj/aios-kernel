#!/usr/bin/env python3
"""Single-use fault injector installed only in an explicitly instrumented clone.

The ordinary image worker owns the CLI and its recovery lease. This helper never
starts a CLI or signals a model child; it only injects the declared supervisor
failure over a second serial channel after authenticating the existing worker.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import termios
import time
import tty

PRODUCT = Path("/opt/aios/linux")
INSTALLED = Path("/usr/local/libexec/aios-image-recovery-test.py")
SCENARIO = "image-worker-recover-exit"
IDENTITY = ("host_boot_id", "process_id", "process_start_ticks", "uid")
COMMANDS_BEFORE = [("about", []), ("backend", ["start"]), ("backend", ["status"])]
LIMIT = 2 * 1024 * 1024


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def decode(raw):
    require(type(raw) is bytes and 0 < len(raw) <= LIMIT, "json-size")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate-key")
            result[key] = value
        return result
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    require(type(value) is dict, "json-object")
    pending, count = [(value, 1)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        require(count <= 16384 and depth <= 20, "json-complexity")
        children = node.values() if type(node) is dict else node if type(node) is list else ()
        pending.extend((child, depth + 1) for child in children)
    return value


def read_file(path, *, owner, limit=LIMIT):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner and before.st_nlink == 1
                and 0 < before.st_size <= limit, "unsafe-file")
        raw = bytearray()
        while len(raw) <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        require(len(raw) == before.st_size and len(raw) <= limit
                and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "file-changed")
        return bytes(raw)
    finally:
        os.close(descriptor)


def emit(event, **fields):
    raw = json.dumps({"schema_version": 1, "event": event, **fields},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    require(len(raw) <= LIMIT, "channel-output-size")
    view = memoryview(raw)
    while view:
        written = os.write(1, view)
        require(written > 0, "channel-write")
        view = view[written:]


def receive(action, timeout):
    raw, deadline = bytearray(), time.monotonic() + timeout
    while not raw.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        require(remaining > 0 and select.select([0], [], [], max(0, remaining))[0], "channel-timeout")
        chunk = os.read(0, 1)
        require(chunk and len(raw) < 4096, "channel-input")
        raw.extend(chunk)
    value = decode(bytes(raw))
    require(set(value) == {"schema_version", "action"} and type(value["schema_version"]) is int
            and value["schema_version"] == 1 and value["action"] == action, "channel-action")


def identity(value):
    return {key: value[key] for key in IDENTITY}


def inject(boot_id, source_sha):
    from aios_backend.client import control
    from aios_resources.proc import ProcessReader, parse_stat

    live = Path("/run/aios/boots") / boot_id
    info = live.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 1000
            and stat.S_IMODE(info.st_mode) == 0o700, "live-directory")
    events_raw = read_file(live / "session/session.events.jsonl", owner=1000)
    events = [decode(line) for line in events_raw.splitlines()]
    require(events and events[0]["event"] == "START" and type(events[0]["schema_version"]) is int
            and events[0]["schema_version"] == 7, "image-session-start")
    source = events[0]["data"]["source_process"]
    commands = [row["data"] for row in events if row["event"] == "COMMAND"]
    require([(row["name"], row["args"]) for row in commands] == COMMANDS_BEFORE
            and all(row["outcome"] == "OK" for row in commands), "pre-fault-command-plan")
    require(source["host_boot_id"] == boot_id and source["uid"] == 1000, "image-worker-identity")
    directory = live / "backend"
    before = control(directory, "status")
    require(before["outcome"] == "OK" and before["state"] == "RUNNING"
            and before == commands[-1]["result"], "authenticated-running")
    record = before["service_record"]
    supervisor_id, child_id = record["supervisor_identity"], record["child_identity"]
    with ProcessReader(source["process_id"], source["process_start_ticks"], boot_id) as owner, \
            ProcessReader(supervisor_id["process_id"], supervisor_id["process_start_ticks"], boot_id) as supervisor, \
            ProcessReader(child_id["process_id"], child_id["process_start_ticks"], boot_id) as model:
        held = os.pidfd_open(supervisor_id["process_id"])
        try:
            acquired = time.monotonic_ns()
            owner_before, supervisor_before, child_before = owner.sample(), supervisor.sample(), model.sample()
            require(owner.identity == identity(source) and supervisor.identity == supervisor_id
                    and model.identity == child_id, "fault-identity")
            require(parse_stat(supervisor_before["raw_stat"])["parent_pid"] == owner_before["process_id"]
                    and parse_stat(child_before["raw_stat"])["parent_pid"] == supervisor_before["process_id"],
                    "fault-parent-chain")
            # The freshly read records cannot confer a recovery lease on this
            # helper. They authorize only this explicit test's supervisor fault.
            require(control(directory, "status") == before and not select.select([held], [], [], 0)[0],
                    "fault-target-changed")
            sent = time.monotonic_ns()
            signal.pidfd_send_signal(held, signal.SIGKILL)
            require(select.select([held], [], [], 10)[0], "supervisor-exit-timeout")
            observed = time.monotonic_ns()
            child_after = model.sample()
            fresh = subprocess.run(["/usr/bin/python3", "-B", str(PRODUCT / "aios-backend.py"),
                "recover", "--state-dir", str(directory)], capture_output=True, timeout=30,
                env={"PATH": "/usr/bin:/bin", "HOME": "/home/aios", "USER": "aios", "LOGNAME": "aios",
                     "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8"})
            require(len(fresh.stdout) <= 16384 and len(fresh.stderr) <= 16384, "fresh-output-size")
            response = decode(fresh.stdout)
            require(fresh.returncode == 1 and not fresh.stderr
                    and response.get("error") == "recovery-owner-required", "fresh-owner-rejection")
            child_after_fresh = model.sample()
            owner.sample()
            return {"schema_version": 1, "scenario": "running-supervisor-loss", "capture_kind": "live",
                "source_only": True, "injector_source_sha256": source_sha, "backend_status": before,
                "cli": owner_before, "supervisor_before": supervisor_before, "child_before": child_before,
                "child_after": child_after, "child_after_fresh": child_after_fresh,
                "signal": {"name": "SIGKILL", "via": "authenticated-pidfd",
                    "pidfd_acquired_monotonic_ns": acquired, "sent_monotonic_ns": sent,
                    "supervisor_exit_observed": True, "supervisor_exit_monotonic_ns": observed},
                "fresh_recover": {"process_exit_code": fresh.returncode, "stdout": fresh.stdout.decode("utf-8"),
                    "stderr": fresh.stderr.decode("utf-8"), "response": response}}
        finally:
            os.close(held)


def main():
    require(os.getuid() == os.geteuid() == 0 and os.getgid() == os.getegid() == 0, "root-bootstrap")
    require(Path(__file__) == INSTALLED and os.ttyname(0) == "/dev/ttyS1"
            and os.ttyname(1) == "/dev/ttyS1", "test-entry-only")
    own = INSTALLED.lstat()
    require(stat.S_ISREG(own.st_mode) and (own.st_uid, own.st_gid, stat.S_IMODE(own.st_mode), own.st_nlink)
            == (0, 0, 0o444, 1), "test-source-owner")
    source_sha = hashlib.sha256(read_file(INSTALLED, owner=0, limit=128 * 1024)).hexdigest()
    tty.setraw(0, when=termios.TCSANOW)
    # The inherited serial descriptors remain usable after this permanent drop.
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 1000
            and os.getgroups() == [], "injector-credentials")
    sys.path.insert(0, str(PRODUCT))
    from aios_resources.proc import ProcessReader, boot_id as current_boot_id
    boot_id = current_boot_id()
    with ProcessReader(os.getpid()) as process:
        sample = process.sample()
    emit("READY", scenario=SCENARIO, test_only=True, source_only=True, boot_id=boot_id,
         uid=os.getuid(), euid=os.geteuid(), gid=os.getgid(), egid=os.getegid(), groups=os.getgroups(),
         injector_source_sha256=source_sha, injector=sample)
    try:
        receive("inject", 900)
        proof = inject(boot_id, source_sha)
        emit("FAULT", proof=proof)
        receive("acknowledge", 30)
        emit("COMPLETE", boot_id=boot_id, outcome="PASS", completed_monotonic_ns=time.monotonic_ns())
        return 0
    except BaseException as exc:
        emit("ERROR", boot_id=boot_id, outcome="FAIL", error=type(exc).__name__ + ":" + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
