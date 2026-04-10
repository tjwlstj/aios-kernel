from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(os.path.abspath(__file__)).parents[2]
BUILD_DIR = REPO_ROOT / "build"
SERIAL_LOG = BUILD_DIR / "serial_output.log"
TOOL_SMOKE_DIR = BUILD_DIR / "tool-smoke"
LOCK_DIR = BUILD_DIR / ".testkit-lock"
LOCK_OWNER = LOCK_DIR / "owner.json"
DEFAULT_QEMU_TIMEOUT = 20


class ToolError(RuntimeError):
    pass


def host_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def shell_join(cmd: Iterable[str]) -> str:
    return subprocess.list2cmdline(list(cmd))


def print_step(message: str) -> None:
    print(f"[AIOS] {message}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def which_any(*names: str) -> str | None:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def run(
    cmd: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print_step(f"RUN {shell_join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


class BuildLock:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> "BuildLock":
        ensure_dir(BUILD_DIR)
        owner = {
            "label": self.label,
            "pid": os.getpid(),
            "host": platform.node(),
            "cwd": str(REPO_ROOT),
            "created_unix": int(time.time()),
        }

        try:
            LOCK_DIR.mkdir(exist_ok=False)
        except FileExistsError as exc:
            details = ""
            if LOCK_OWNER.exists():
                try:
                    payload = json.loads(LOCK_OWNER.read_text(encoding="utf-8"))
                    details = (
                        " "
                        f"(label={payload.get('label')}, pid={payload.get('pid')}, "
                        f"host={payload.get('host')})"
                    )
                except (json.JSONDecodeError, OSError):
                    details = ""
            raise ToolError(
                "Another AIOS testkit run is already active. "
                f"Wait for it to finish or remove {LOCK_DIR} if the previous run crashed."
                f"{details}"
            ) from exc

        LOCK_OWNER.write_text(json.dumps(owner, indent=2), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if LOCK_OWNER.exists():
                LOCK_OWNER.unlink()
        finally:
            if LOCK_DIR.exists():
                LOCK_DIR.rmdir()

