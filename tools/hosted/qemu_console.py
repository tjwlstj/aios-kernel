#!/usr/bin/env python3
"""Boot a Linux development guest and enter the AIOS userspace console."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from verify_console import verify_execution
from verify_service import verify_control, verify_service_runs, verify_workflow
from verify_agent import RESOURCE_COMMANDS, CELL_COMMANDS, BACKEND_COMMANDS

SMOKE_COMMANDS = ["about", "status", "hardware", "net status", "resolve example.com",
                  "fetch https://example.com/", "no-such-command", "help", "exit"]
SERIAL_LIMIT = 32 * 1024 * 1024
AGENT_PROMPT_SECONDS = 2450
# The full hosted suite includes process lifecycles and retained-image replay.
# On TCG it outgrew the original 400-second setup checkpoint.
GUEST_TEST_SECONDS = 1200
TASK_SMOKE_SECONDS = 6000
PROMPT_PATTERN = rb"(?:^|\r?\n)aios> "
SERVICE_DIR = "/tmp/aios-runtime"
SERVICE_FIRST = ["service status", "about", "resolve example.com", "fetch https://example.com/", "exit"]
SERVICE_SECOND = ["service status", "service restart", "service status", "service stop",
                  "service status", "service start", "service status", "exit"]
AGENT_DIR = "/tmp/aios-agent"
AGENT_CONFIG = "/tmp/aios-agent-config.json"
AGENT_FIRST = ["agent start", "room status", "room discover", "room bind",
               "ask What is the capital of France? Answer in one short sentence.", "agent status", "exit"]
AGENT_SECOND = ["agent status", "ask Say hello in one short sentence.", "agent restart", "room status", "ask Say hello.", "room reconcile",
                "room discover", "room reconcile", "ask What is the Moon? Answer in one short sentence.",
                "agent stop", "room status", "exit"]


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def export_guest(guest, directory: str, output: Path, names: list[str] | None = None) -> None:
    """Copy bounded regular evidence files; never export a socket or follow links."""
    output.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    script = ("import base64,json,pathlib; p=pathlib.Path(" + repr(directory) + "); "
              "fs=" + ("[p/n for n in " + repr(names) + "]" if names is not None else
                       "[f for f in p.rglob('*') if f.is_file() and not f.is_symlink()]") + "; "
              "assert len(fs)<=256; assert all(not f.is_symlink() and f.stat().st_size<=4194304 for f in fs); "
              "d={f.relative_to(p).as_posix():base64.b64encode(f.read_bytes()).decode() for f in fs}; "
              "print('" + token + "'+base64.b64encode(json.dumps(d).encode()).decode()+'" + token + "')")
    encoded = base64.b64encode(script.encode()).decode()
    guest.send("python3 -c \"import base64;exec(base64.b64decode('" + encoded + "'))\"")
    match, _ = guest.wait(token.encode() + rb"([A-Za-z0-9+/=]+)" + token.encode(), 90)
    bundle = json.loads(base64.b64decode(match.group(1), validate=True))
    if type(bundle) is not dict or len(bundle) > 256 or names is not None and set(bundle) != set(names):
        raise ValueError("unexpected evidence files")
    for name, value in bundle.items():
        if not re.fullmatch(r"[a-zA-Z0-9_./-]+", name) or any(p in ("", ".", "..") for p in name.split("/")):
            raise ValueError("unsafe evidence path")
        raw = base64.b64decode(value, validate=True)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("evidence size limit")
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)


def control_guest(guest, action: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    nonce = uuid.uuid4().hex
    begin, end = "__SERVICE_START_" + nonce + "__", "__SERVICE_EXIT_" + nonce + "_"
    stderr_path = "/tmp/service-" + nonce
    guest.send("printf '\\n" + begin + "\\n'; su aios -s /bin/sh -c "
               "'PYTHONDONTWRITEBYTECODE=1 python3 /mnt/aios/hosted/linux/aios-service.py " + action +
               " --state-dir " + SERVICE_DIR + "' 2>" + stderr_path + "; printf '\\n" + end + "%s__\\n' \"$?\"")
    guest.wait(rb"\r?\n" + begin.encode() + rb"\r?\n")
    start = guest.cursor
    match, base = guest.wait(rb"\r?\n" + end.encode() + rb"([0-9]+)__\r?\n", 45)
    stdout = bytes(guest.transcript[start:base + match.start()]).replace(b"\r\n", b"\n")
    (output / "stdout.log").write_bytes(stdout)
    export_guest(guest, "/tmp", output, ["service-" + nonce])
    (output / ("service-" + nonce)).rename(output / "stderr.log")
    save_json(output / "execution.json", {"schema_version": 1, "action": action,
              "process_exit_code": int(match.group(1)), "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
              "stderr_sha256": hashlib.sha256((output / "stderr.log").read_bytes()).hexdigest()})
    return verify_control(output, action)


def run_task_smoke_guest(guest, output: Path, retained: Path) -> dict:
    """Run one owned CLI scenario and preserve a failed driver before shutdown."""
    driver_error = None
    try:
        guest.step("Testing real model Task answer and cancellation in one CLI",
                   "su aios -s /bin/sh -c 'PYTHONDONTWRITEBYTECODE=1 python3 "
                   "/mnt/aios/tools/hosted/task_smoke_guest.py /tmp/aios-task-smoke'",
                   TASK_SMOKE_SECONDS)
    except RuntimeError as exc:
        # A completed driver may fail its scenario; still export evidence and
        # let the normal MAIN/backend/service cleanup and VM poweroff run.
        driver_error = str(exc)
    export_guest(guest, "/tmp/aios-task-smoke", output)
    if driver_error is not None:
        verdict = {"outcome": "FAIL", "reasons": ["task_smoke_driver", driver_error]}
    else:
        verdict = verify_execution(output, source_root=retained, require_live=True, require_internet=True)
    save_json(output / "verdict.json", verdict)
    return verdict


class SpaceSmokeScript:
    """Send one fixed scenario with one intentional observation-expiry pause.

    The host pause does not establish freshness. The guest's observation and
    request evidence must independently establish the TTL transition.
    """
    def __init__(self, commands: list[str] | None):
        from space_output_contract import SPACE_COMMANDS, SPACE_STALE_COMMAND_INDEX, SPACE_STALE_WAIT_SECONDS
        if commands != SPACE_COMMANDS:
            raise ValueError("SpaceSmoke requires its exact command plan")
        if (type(SPACE_STALE_COMMAND_INDEX) is not int or not 0 < SPACE_STALE_COMMAND_INDEX < len(SPACE_COMMANDS)
                or SPACE_COMMANDS[SPACE_STALE_COMMAND_INDEX - 1] != 'space'
                or not SPACE_COMMANDS[SPACE_STALE_COMMAND_INDEX].startswith('ask ')
                or type(SPACE_STALE_WAIT_SECONDS) is not int or SPACE_STALE_WAIT_SECONDS != 31):
            raise ValueError("SpaceSmoke observation-expiry plan is invalid")
        self.commands = tuple(SPACE_COMMANDS)
        self.wait_index = SPACE_STALE_COMMAND_INDEX
        self.wait_seconds = SPACE_STALE_WAIT_SECONDS
        self.position = 0

    def before_send(self, line: str) -> None:
        if self.position >= len(self.commands) or line != self.commands[self.position]:
            raise ValueError("SpaceSmoke command order changed")
        if self.position == self.wait_index:
            print("[AIOS] Waiting 31 seconds for the saved space observation to become stale...", flush=True)
            time.sleep(self.wait_seconds)
        self.position += 1

    def complete(self) -> None:
        if self.position != len(self.commands):
            raise RuntimeError("CLI exited before the SpaceSmoke command plan completed")


def run_session(guest, output: Path, retained: Path, commands: list[str] | None, guest_name: str, *,
                agent: bool = False, space_smoke: bool = False) -> dict:
    if space_smoke and not agent:
        raise ValueError("SpaceSmoke requires the model-enabled console")
    space_script = SpaceSmokeScript(commands) if space_smoke else None
    output.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    begin_marker, end_marker = "__AIOS_CONSOLE_START_" + nonce + "__", "__AIOS_CONSOLE_EXIT_" + nonce + "_"
    guest_dir = "/tmp/" + guest_name
    launch = ("printf '\\n" + begin_marker + "\\n'; su aios -s /bin/sh -c "
              "'PYTHONDONTWRITEBYTECODE=1 python3 /mnt/aios/hosted/linux/aios-console.py --artifact-dir " + guest_dir +
              " --service-dir " + SERVICE_DIR +
              (" --agent-dir " + AGENT_DIR + " --agent-config " + AGENT_CONFIG if agent else "") +
              "' 2>" + guest_dir + "-stderr.log; printf '\\n" + end_marker + "%s__\\n' \"$?\"")
    guest.send(launch)
    guest.wait(rb"\r?\n" + begin_marker.encode() + rb"\r?\n")
    start = printed = guest.cursor
    end_pattern = rb"\r?\n" + end_marker.encode() + rb"([0-9]+)__\r?\n"
    script = iter(commands or [])
    while True:
        match, base = guest.wait(end_pattern + rb"|" + PROMPT_PATTERN, AGENT_PROMPT_SECONDS if agent else 60)
        if match.group(1) is not None:
            if space_script is not None:
                space_script.complete()
            finish = base + match.start()
            display(bytes(guest.transcript[printed:finish]))
            process_exit = int(match.group(1))
            break
        display(bytes(guest.transcript[printed:guest.cursor]))
        printed = guest.cursor
        if commands is not None:
            line = next(script, None)
            if line is None:
                raise RuntimeError("CLI did not exit after the smoke commands")
            print(line, flush=True)
        else:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                print("exit", flush=True)
                line = "exit"
        if len(line) > 2048 or any(not c.isprintable() for c in line):
            print("[AIOS] Enter one printable command of at most 2048 characters.", flush=True)
            line = "help"
        if space_script is not None:
            space_script.before_send(line)
        guest.send(line)
    stdout = bytes(guest.transcript[start:finish]).replace(b"\r\n", b"\n")
    (output / "stdout.log").write_bytes(stdout)
    export_guest(guest, guest_dir, output / "session", ["console.log", "session.events.jsonl", "session-result.json",
                 "boot/boot.log", "boot/events.jsonl", "boot/inventory.json", "boot/result.json"])
    export_guest(guest, "/tmp", output, [guest_name + "-stderr.log"])
    (output / (guest_name + "-stderr.log")).rename(output / "stderr.log")
    save_json(output / "execution.json", {"schema_version": 1, "mode": "smoke" if commands else "interactive",
              "process_exit_code": process_exit, "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
              "stderr_sha256": hashlib.sha256((output / "stderr.log").read_bytes()).hexdigest(),
              "requested_commands": commands})
    verdict = verify_execution(output, source_root=retained, require_live=True,
                               require_internet=space_smoke or commands in (SMOKE_COMMANDS, SERVICE_FIRST, RESOURCE_COMMANDS, CELL_COMMANDS, BACKEND_COMMANDS))
    save_json(output / "verdict.json", verdict)
    return verdict


def control_backend_guest(guest, action: str, output: Path) -> None:
    """Capture the real product controller exit and its exact public response."""
    guest.step('Model backend ' + action, "su aios -s /bin/sh -c 'PYTHONDONTWRITEBYTECODE=1 python3 "
               '/mnt/aios/hosted/linux/aios-backend.py ' + action + ' --state-dir /tmp/aios-model-backend' +
               (' --config ' + AGENT_CONFIG if action == 'start' else '') +
               "' >/tmp/backend-" + action + '.stdout 2>/tmp/backend-' + action + '.stderr', 240)
    export_guest(guest, '/tmp', output, ['backend-' + action + '.stdout', 'backend-' + action + '.stderr'])
    (output / ('backend-' + action + '.stdout')).rename(output / 'stdout.log')
    (output / ('backend-' + action + '.stderr')).rename(output / 'stderr.log')
    save_json(output / 'execution.json', {'schema_version': 1, 'action': action, 'process_exit_code': 0,
              'stdout_sha256': hashlib.sha256((output / 'stdout.log').read_bytes()).hexdigest(),
              'stderr_sha256': hashlib.sha256((output / 'stderr.log').read_bytes()).hexdigest()})


def task_stop_guest(guest, component: str, state_dir: str, output: Path) -> int:
    """Retain a Task attempt's real stop exit even when the service failed."""
    if component not in ('agent', 'backend'):
        raise ValueError('unknown Task cleanup component')
    marker = '__AIOS_TASK_STOP_' + uuid.uuid4().hex + '_'
    stem = '/tmp/task-' + component + '-stop'
    guest.send("su aios -s /bin/sh -c 'PYTHONDONTWRITEBYTECODE=1 python3 "
               '/mnt/aios/hosted/linux/aios-' + component + '.py stop --state-dir ' + state_dir +
               "' >" + stem + '.stdout 2>' + stem + ".stderr; printf '\\n" + marker + "%s__\\n' \"$?\"")
    match, _ = guest.wait(rb'\r?\n' + marker.encode() + rb'([0-9]+)__\r?\n', 240)
    code = int(match.group(1))
    names = ['task-' + component + '-stop.stdout', 'task-' + component + '-stop.stderr']
    export_guest(guest, '/tmp', output, names)
    for name, target in zip(names, ('stdout.log', 'stderr.log')):
        (output / name).rename(output / target)
    save_json(output / 'execution.json', {'schema_version': 1, 'action': 'stop', 'process_exit_code': code,
              'stdout_sha256': hashlib.sha256((output / 'stdout.log').read_bytes()).hexdigest(),
              'stderr_sha256': hashlib.sha256((output / 'stderr.log').read_bytes()).hexdigest()})
    return code


def cleanup_task_model(guest, output: Path) -> list[str]:
    """Try both owned guest service stops and exports after a failed scenario."""
    issues = []
    for component, state_dir, destination in (('agent', AGENT_DIR, 'agent'),
            ('backend', '/tmp/aios-model-backend', 'model-backend')):
        try:
            code = task_stop_guest(guest, component, state_dir, output / (component + '-stop'))
            if code != 0:
                issues.append(component + '_stop_exit_' + str(code))
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            issues.append(component + '_stop:' + str(exc))
        try:
            export_guest(guest, state_dir, output / destination)
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            issues.append(component + '_export:' + str(exc))
    save_json(output / 'task-cleanup.json', {'outcome': 'FAIL' if issues else 'PASS', 'reasons': issues})
    return issues


def display(data: bytes) -> None:
    # Linux tty output already has CRLF; let the host text stream translate once.
    print(data.decode("utf-8", errors="replace").replace("\r\n", "\n"), end="", flush=True)


class SerialGuest:
    def __init__(self, command: list[str], log: Path):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, bufsize=0)
        try:
            self.chunks: queue.Queue[bytes | None] = queue.Queue()
            self.transcript = bytearray()
            self.cursor = 0
            self.reader_error = None

            def drain():
                total = 0
                try:
                    with log.open("wb") as stream:
                        while data := self.process.stdout.read(65536):
                            total += len(data)
                            if total > SERIAL_LIMIT:
                                raise ValueError("serial output limit")
                            stream.write(data)
                            stream.flush()
                            self.chunks.put(data)
                except (OSError, ValueError) as exc:
                    self.reader_error = str(exc)
                finally:
                    self.chunks.put(None)
            self.reader = threading.Thread(target=drain, daemon=True)
            self.reader.start()
        except BaseException as failure:
            # Assignment in the caller has not completed yet. This constructor
            # must retain responsibility for the Popen it already created.
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                self.process.wait(timeout=5)
            except BaseException:
                try:
                    if self.process.poll() is None:
                        self.process.kill()
                    self.process.wait(timeout=5)
                except BaseException as cleanup_error:
                    failure.add_note('Owned serial process cleanup failed: ' + str(cleanup_error))
            try:
                reader = getattr(self, 'reader', None)
                if reader is not None and reader.ident is not None:
                    reader.join(timeout=5)
                    if reader.is_alive():
                        failure.add_note('Serial reader did not stop during constructor cleanup.')
            except BaseException as cleanup_error:
                failure.add_note('Serial reader cleanup failed: ' + str(cleanup_error))
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except BaseException as cleanup_error:
                    failure.add_note('Serial stream cleanup failed: ' + str(cleanup_error))
            raise

    def wait(self, pattern: bytes, seconds: float = 60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            match = re.search(pattern, self.transcript[self.cursor:])
            if match:
                base = self.cursor
                self.cursor += match.end()
                return match, base
            try:
                data = self.chunks.get(timeout=0.25)
            except queue.Empty:
                continue
            if data is None:
                raise RuntimeError(self.reader_error or "guest exited before checkpoint")
            self.transcript.extend(data)
            if len(self.transcript) > SERIAL_LIMIT:
                raise ValueError("serial transcript limit")
        raise TimeoutError("guest checkpoint timeout")

    def send(self, line: str):
        for value in (line + "\n").encode("utf-8"):
            self.process.stdin.write(bytes([value]))
            self.process.stdin.flush()
            time.sleep(0.002)

    def step(self, label: str, command: str, seconds: float = 60):
        marker = "__AIOS_SETUP_" + uuid.uuid4().hex + "_"
        print("[AIOS] " + label, flush=True)
        self.send(command + "; printf '\\n" + marker + "%s__\\n' \"$?\"")
        match, _ = self.wait(rb"\r?\n" + marker.encode() + rb"([0-9]+)__\r?\n", seconds)
        if match.group(1) != b"0":
            raise RuntimeError(label + " failed (exit " + match.group(1).decode() + ")")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("qemu", "iso", "kernel", "initramfs", "artifact-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--smoke", action="store_true", help="Run fixed CLI commands including real DNS and verified HTTPS.")
    parser.add_argument("--guest-tests", action="store_true")
    parser.add_argument("--guest-test-pattern", default="test_hosted*.py",
                        help="Bound the guest test run to one unittest filename pattern.")
    parser.add_argument("--service-smoke", action="store_true", help="Verify service survival across two consoles and three generations.")
    parser.add_argument("--agent", action="store_true", help="Prepare the pinned local model for interactive agent commands.")
    parser.add_argument("--agent-smoke", action="store_true", help="Run actual model requests and explicit hosted bindings across restart.")
    parser.add_argument('--resource-smoke', action='store_true', help='Verify MAIN/backend CPU and RSS during a real model request.')
    parser.add_argument('--cell-smoke', action='store_true', help='Verify Cell lifecycle and explicit rebind with a real model request.')
    parser.add_argument('--backend-smoke', action='store_true', help='Verify owned backend replacement, MAIN rejection and explicit recovery.')
    parser.add_argument('--recovery-smoke', action='store_true', help='Inject supervisor loss and verify same-CLI recovery with the real model.')
    parser.add_argument('--space-smoke', action='store_true', help='Verify actual model consumption of fresh and stale space observations, target rejection and recovery.')
    parser.add_argument("--task-smoke", action="store_true", help="Verify one real model answer and an explicitly cancelled second Task in the same CLI.")
    parser.add_argument("--inference-cache", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AIOS/hosted-inference")
    args = parser.parse_args()
    if re.fullmatch(r"test_hosted[a-zA-Z0-9_*]*\.py", args.guest_test_pattern) is None:
        parser.error("Invalid guest test filename pattern")
    if sum((args.smoke, args.service_smoke, args.agent_smoke, args.resource_smoke, args.cell_smoke, args.backend_smoke, args.recovery_smoke, args.space_smoke, args.task_smoke)) > 1:
        parser.error("Choose one smoke workflow")
    if args.agent and (args.smoke or args.service_smoke):
        parser.error("Choose one console workflow with the MAIN model")
    space_commands = None
    if args.space_smoke:
        from space_output_contract import SPACE_COMMANDS
        space_commands = list(SPACE_COMMANDS)
        SpaceSmokeScript(space_commands)
    use_agent = args.agent or args.agent_smoke or args.resource_smoke or args.cell_smoke or args.backend_smoke or args.recovery_smoke or args.space_smoke or args.task_smoke
    root = Path(__file__).resolve().parents[2]
    output = args.artifact_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    verdict: dict = {"outcome": "FAIL", "reasons": ["not_completed"]}
    guest = None
    killed, shutdown = False, False
    iso_hash = hashlib.sha256(args.iso.read_bytes()).hexdigest()
    if iso_hash != args.sha256.lower():
        (output / "vm-verdict.json").write_text(json.dumps({"outcome": "FAIL", "reasons": ["iso_checksum"]}))
        return 1
    with tempfile.TemporaryDirectory(prefix="aios-console-") as scratch:
        owned = Path(scratch).resolve()
        if owned.parent != Path(tempfile.gettempdir()).resolve() or not owned.name.startswith("aios-console-"):
            raise ValueError("unexpected temporary workspace")
        share = owned / "share"
        shutil.copytree(root / "hosted/linux", share / "hosted/linux", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(root / "hosted/contracts", share / "hosted/contracts")
        shutil.copytree(root / "tools/hosted", share / "tools/hosted", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if args.guest_tests:
            # The management test compares semantic constants with this C oracle;
            # the Linux runtime neither imports nor executes the native kernel.
            oracle = Path("kernel/include/kernel/kernel_room_management.h")
            (share / oracle).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / oracle, share / oracle)
        if use_agent:
            from model_backend import BACKEND_NAME, BACKEND_SHA, MODEL_NAME, MODEL_SHA, MODEL_BYTES, MODEL_ID
            backend = args.inference_cache / BACKEND_NAME
            model = args.inference_cache / MODEL_NAME
            if hashlib.sha256(backend.read_bytes()).hexdigest() != BACKEND_SHA:
                raise ValueError("pinned backend checksum")
            with model.open("rb") as model_stream:
                if hashlib.file_digest(model_stream, "sha256").hexdigest() != MODEL_SHA:
                    raise ValueError("pinned model checksum")
            if model.stat().st_size != MODEL_BYTES:
                raise ValueError("pinned model size")
            (share / "inference").mkdir()
            shutil.copy2(backend, share / "inference" / BACKEND_NAME)
            # Raw read-only virtio disk avoids the small vvfat directory capacity.
            model_disk = owned / "model.raw"
            with model.open("rb") as model_stream, model_disk.open("xb") as disk:
                shutil.copyfileobj(model_stream, disk, 1024 * 1024)
                disk.write(b"\0" * (-MODEL_BYTES % 512))
            provenance = (args.inference_cache / "provenance-receipt.json").read_bytes()
            (output / "inference-provenance.json").write_bytes(provenance)
            shutil.copytree(args.inference_cache / "provenance", output / "provenance")
            config = {"schema_version": 1, "endpoint": "http://127.0.0.1:18081", "model_id": MODEL_ID,
                      "model_path": "/tmp/aios-model/" + MODEL_NAME, "model_sha256": MODEL_SHA,
                      "backend_path": "/mnt/aios/inference/" + BACKEND_NAME, "backend_sha256": BACKEND_SHA,
                      "provenance_sha256": hashlib.sha256(provenance).hexdigest()}
            save_json(output / "agent-config.json", config)
            save_json(share / "inference/agent-config.json", config)
            save_json(output / "inference-integrity.json", {"schema_version": 1, "model_bytes": MODEL_BYTES,
                      "model_sha256": MODEL_SHA, "backend_sha256": BACKEND_SHA, "verification": "host-read-complete"})
        retained = output / "runtime-source"
        shutil.copytree(share / "hosted/linux", retained)
        if args.recovery_smoke or args.space_smoke or args.task_smoke:
            shutil.copytree(share / "tools/hosted", output / "verification-source")
        command = [str(args.qemu), "-machine", "q35", "-accel", "tcg", "-m", "3072" if use_agent else "768", "-smp", "2",
                   "-kernel", str(args.kernel), "-initrd", str(args.initramfs),
                   "-append", "console=ttyS0,115200 modules=loop,squashfs,sd-mod,usb-storage" + (' psi=1' if use_agent else ''),
                   "-cdrom", str(args.iso), "-display", "none", "-monitor", "none", "-serial", "stdio", "-no-reboot",
                   "-nic", "user,model=e1000", "-device", "qemu-xhci", "-device", "usb-kbd",
                   "-drive", "file=fat:ro:" + share.as_posix() + ",format=raw,if=virtio,readonly=on"]
        if use_agent:
            command += ["-cpu", "max", "-drive", "file=" + str(model_disk) + ",format=raw,if=virtio,readonly=on"]
        def git(*parts):
            return subprocess.run(["git", "-c", "safe.directory=" + root.as_posix(), "--no-optional-locks", *parts], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        environment = {"schema_version": 1, "purpose": "aios-userspace-console-development-guest",
                       "physical_host_inventory": False, "persistence": "ephemeral", "guest_user": "aios",
                       "git_head": git("rev-parse", "HEAD"), "git_status": git("status", "--porcelain").splitlines(),
                       "iso_sha256": iso_hash, "kernel_sha256": hashlib.sha256(args.kernel.read_bytes()).hexdigest(),
                       "initramfs_sha256": hashlib.sha256(args.initramfs.read_bytes()).hexdigest(),
                       "qemu_version": subprocess.run([str(args.qemu), "--version"], capture_output=True, text=True, check=True).stdout.strip(),
                       "network": "QEMU user NAT; no host forwarding", "guest_tests": args.guest_tests,
                       "guest_test_pattern": args.guest_test_pattern if args.guest_tests else None,
                       "guest_test_timeout_seconds": GUEST_TEST_SECONDS if args.guest_tests else None,
                       "service_smoke": args.service_smoke, "agent": use_agent, "agent_smoke": args.agent_smoke,
                       'resource_smoke': args.resource_smoke, 'cell_smoke': args.cell_smoke,
                       'backend_smoke': args.backend_smoke, 'recovery_smoke': args.recovery_smoke, "command": command}
        if args.space_smoke:
            environment['space_smoke'] = True
        if args.task_smoke:
            environment['task_smoke'] = True
            environment['task_smoke_timeout_seconds'] = TASK_SMOKE_SECONDS
        (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n", encoding="utf-8")
        try:
            print("[AIOS] Starting the Linux hardware layer...", flush=True)
            guest = SerialGuest(command, output / "linux-serial.log")
            guest.wait(rb"login:\s*$", 120)
            guest.send("root")
            guest.wait(rb"localhost:~#")
            guest.process.stdin.write(b"\x1b[1;1R")
            guest.process.stdin.flush()
            guest.step("Preparing the console", "export TERM=dumb; stty -echo")
            guest.step("Connecting the guest network", "ifconfig lo 127.0.0.1 netmask 255.0.0.0 up && ip link set eth0 up && udhcpc -i eth0 -q -n -t 5 -T 3", 45)
            guest.step("Preparing Python and trusted certificates", "printf '%s\\n' 'https://dl-cdn.alpinelinux.org/alpine/v3.24/main' > /etc/apk/repositories && apk add --no-cache python3 ca-certificates", 180)
            guest.step("Loading AIOS", "mkdir -p /mnt/aios && mount -t vfat -o ro /dev/vda1 /mnt/aios && adduser -D -s /bin/sh aios")
            # Service state belongs to this ephemeral guest's user, separately
            # from the read-only source share and exported console artifacts.
            guest.step("Preparing the private service directory", "mkdir -m 700 " + SERVICE_DIR + " && chown aios:aios " + SERVICE_DIR)
            if args.guest_tests:
                guest.step("Running Linux console tests as the AIOS user", "su aios -s /bin/sh -c 'cd /mnt/aios && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/hosted/tests -p \"" + args.guest_test_pattern + "\" -v'", GUEST_TEST_SECONDS)
            if use_agent:
                guest.step("Loading the pinned local model", "python3 /mnt/aios/tools/hosted/model_backend.py prepare && "
                           "chmod 755 /tmp/aios-model && cp /mnt/aios/inference/agent-config.json " + AGENT_CONFIG, 120)
                export_guest(guest, "/tmp/aios-model", output / "model-integrity", ["integrity.json"])
                if not (args.recovery_smoke or args.task_smoke):
                    control_backend_guest(guest, 'start', output / 'backend-start')
            if args.service_smoke:
                print("[AIOS] Starting the private runtime service...", flush=True)
                control_guest(guest, "start", output / "service-start")
            commands = BACKEND_COMMANDS if args.backend_smoke else CELL_COMMANDS if args.cell_smoke else RESOURCE_COMMANDS if args.resource_smoke else AGENT_FIRST if args.agent_smoke else SERVICE_FIRST if args.service_smoke else SMOKE_COMMANDS if args.smoke else None
            if args.space_smoke:
                commands = space_commands
            if args.task_smoke:
                verdict = run_task_smoke_guest(guest, output, retained)
            elif args.recovery_smoke:
                try:
                    guest.step("Testing explicit model recovery in the same CLI", "su aios -s /bin/sh -c 'PYTHONDONTWRITEBYTECODE=1 python3 /mnt/aios/tools/hosted/backend_recovery_guest.py /tmp/aios-recovery'", 1800)
                except RuntimeError:
                    # Preserve a failed attempt before the guest is terminated.
                    export_guest(guest, "/tmp/aios-recovery", output)
                    export_guest(guest, AGENT_DIR, output / "agent")
                    export_guest(guest, "/tmp/aios-model-backend", output / "model-backend")
                    raise
                export_guest(guest, "/tmp/aios-recovery", output)
                verdict = verify_execution(output, source_root=retained, require_live=True, require_internet=True)
                save_json(output / "verdict.json", verdict)
            else:
                verdict = run_session(guest, output, retained, commands, "aios-session", agent=use_agent,
                                      space_smoke=args.space_smoke)
            if args.service_smoke:
                print("[AIOS] Reopening the console while the service stays alive...", flush=True)
                second = run_session(guest, output / "second-console", retained, SERVICE_SECOND, "aios-second")
                if second["outcome"] != "PASS":
                    verdict = second
            if args.agent_smoke:
                second = run_session(guest, output / "second-console", retained, AGENT_SECOND, "aios-second", agent=True)
                if second["outcome"] != "PASS":
                    verdict = second
            if use_agent:
                if args.task_smoke:
                    cleanup_issues = cleanup_task_model(guest, output)
                    if cleanup_issues:
                        verdict = {"outcome": "FAIL", "reasons": ["task_cleanup", cleanup_issues, verdict]}
                else:
                    guest.step("Stopping the MAIN agent", "su aios -s /bin/sh -c 'PYTHONDONTWRITEBYTECODE=1 python3 "
                               "/mnt/aios/hosted/linux/aios-agent.py stop --state-dir " + AGENT_DIR +
                               "' >/tmp/agent-stop.stdout 2>/tmp/agent-stop.stderr", 120)
                    export_guest(guest, "/tmp", output / "agent-stop", ["agent-stop.stdout", "agent-stop.stderr"])
                    (output / "agent-stop/agent-stop.stdout").rename(output / "agent-stop/stdout.log")
                    (output / "agent-stop/agent-stop.stderr").rename(output / "agent-stop/stderr.log")
                    save_json(output / "agent-stop/execution.json", {"schema_version": 1, "action": "stop", "process_exit_code": 0,
                              "stdout_sha256": hashlib.sha256((output / "agent-stop/stdout.log").read_bytes()).hexdigest(),
                              "stderr_sha256": hashlib.sha256((output / "agent-stop/stderr.log").read_bytes()).hexdigest()})
                    export_guest(guest, AGENT_DIR, output / "agent")
                    control_backend_guest(guest, 'stop', output / 'backend-stop')
                    export_guest(guest, "/tmp/aios-model-backend", output / "model-backend")
                from verify_agent import verify_workflow as verify_agent_workflow, verify_interactive
                agent_verdict = (verify_agent_workflow(output, require_shutdown=False) if args.agent_smoke else
                                 verify_interactive(output, source_root=retained, require_shutdown=False,
                                                    resource_smoke=args.resource_smoke, cell_smoke=args.cell_smoke,
                                                    backend_smoke=args.backend_smoke, recovery_smoke=args.recovery_smoke,
                                                    space_smoke=args.space_smoke, task_smoke=args.task_smoke))
                save_json(output / "agent-verdict.json", agent_verdict)
                if agent_verdict["outcome"] != "PASS":
                    verdict = agent_verdict
            print("[AIOS] Stopping the guest service before poweroff...", flush=True)
            control_guest(guest, "stop", output / "service-stop")
            export_guest(guest, SERVICE_DIR, output / "service")
            if args.service_smoke:
                service_verdict = verify_workflow(output, source_root=retained)
            else:
                service_verdict = verify_service_runs(output / "service", source_root=retained, allow_absent=True)
            save_json(output / "service-verdict.json", service_verdict)
            if service_verdict["outcome"] != "PASS":
                verdict = service_verdict
            print("[AIOS] Session closed. Shutting down the guest...", flush=True)
            guest.send("printf '\\n'; poweroff -f")
            guest.wait(rb"reboot: Power down", 30)
            shutdown = True
            guest.process.wait(timeout=30)
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            verdict = {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}
        finally:
            if guest is not None:
                if guest.process.poll() is None:
                    killed = True
                    guest.process.kill()
                    guest.process.wait(timeout=15)
                guest.reader.join(timeout=10)
                if killed or guest.process.returncode != 0 or not shutdown or guest.reader.is_alive() or guest.reader_error:
                    verdict = {"outcome": "FAIL", "reasons": ["vm_not_clean", verdict, guest.reader_error]}
                for stream in (guest.process.stdin, guest.process.stdout):
                    stream.close()
            verdict.update(vm_exit_code=guest.process.returncode if guest else None, host_killed=killed, shutdown_observed=shutdown)
            (output / "vm-verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
            if use_agent and verdict["outcome"] == "PASS":
                from verify_agent import verify_workflow as verify_agent_workflow, verify_interactive
                final_agent = (verify_agent_workflow(output) if args.agent_smoke else verify_interactive(output,
                    resource_smoke=args.resource_smoke, cell_smoke=args.cell_smoke, backend_smoke=args.backend_smoke,
                    recovery_smoke=args.recovery_smoke, space_smoke=args.space_smoke, task_smoke=args.task_smoke))
                save_json(output / "agent-verdict.json", final_agent)
                if final_agent["outcome"] != "PASS":
                    verdict = {**verdict, "outcome": "FAIL", "reasons": ["agent_final_verification", final_agent]}
                    save_json(output / "vm-verdict.json", verdict)
            print(json.dumps(verdict, sort_keys=True), flush=True)
            print("[AIOS] Session evidence: " + str(output), flush=True)
    return 0 if verdict["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
