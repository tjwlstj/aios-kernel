#!/usr/bin/env python3
"""AIOS-owned external model backend; no init service installation."""
import argparse
import sys
from pathlib import Path

from aios_agent.inference import encoded
from aios_backend.client import control
from aios_backend.daemon import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="status", choices=("start", "status", "stop", "restart", "recover"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture-backend", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        if args.config is None:
            parser.error("--serve requires --config")
        return serve(args.state_dir, args.config, fixture_backend=args.fixture_backend)
    value = control(args.state_dir, args.action, args.config, fixture_backend=args.fixture_backend)
    sys.stdout.buffer.write(encoded(value) + b"\n")
    return 0 if value["outcome"] == "OK" else 3 if value["state"] == "UNSUPPORTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
