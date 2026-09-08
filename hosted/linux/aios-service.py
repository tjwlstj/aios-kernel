#!/usr/bin/env python3
"""AIOS console runtime daemon and its explicit local control entrypoint."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aios_hosted.boot import encoded
from aios_service.client import control
from aios_service.lifecycle import ServiceError, serve


def main() -> int:
    parser = argparse.ArgumentParser(description="Control your private AIOS console runtime service.")
    parser.add_argument("action", nargs="?", choices=("start", "status", "stop", "restart"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        if args.action is not None:
            parser.error("--serve cannot be combined with a control action")
        try:
            return serve(args.state_dir)
        except (ServiceError, OSError) as exc:
            print("AIOS service startup failed: " + (exc.code if isinstance(exc, ServiceError) else type(exc).__name__),
                  file=sys.stderr)
            return 1
    if args.action is None:
        parser.error("a control action is required")
    result = control(args.state_dir, args.action)
    sys.stdout.buffer.write(encoded(result))
    sys.stdout.buffer.flush()
    return 0 if result["outcome"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
