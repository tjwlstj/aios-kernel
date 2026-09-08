#!/usr/bin/env python3
"""AIOS MAIN agent command entry; no system service installation."""
import argparse
import sys
from pathlib import Path

from aios_agent.client import control
from aios_agent.daemon import serve
from aios_agent.inference import encoded


def main():
    parser = argparse.ArgumentParser(description="AIOS MAIN agent")
    parser.add_argument("action", nargs="?", default="status", choices=("status", "start", "stop", "restart", "ask",
        "room-status", "room-discover", "room-bind", "room-reconcile", "resources-link", "resources-status", "resources-sample",
        "cell-status", "cell-activate", "cell-deactivate"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--prompt")
    parser.add_argument("--backend-dir", type=Path)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture-backend", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        if args.config is None:
            parser.error("--serve requires --config")
        return serve(args.state_dir, args.config, fixture_backend=args.fixture_backend,
                     backend_dir=args.backend_dir)
    result = control(args.state_dir, args.action, args.config, args.prompt, fixture_backend=args.fixture_backend,
                     backend_dir=args.backend_dir)
    sys.stdout.buffer.write(encoded(result) + b"\n")
    return 0 if result["outcome"] == "OK" else 3 if result["state"] == "UNSUPPORTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
