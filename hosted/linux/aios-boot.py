#!/usr/bin/env python3
"""Direct entry point; no installation, privilege change or init-system coupling."""
from aios_hosted.boot import main

if __name__ == "__main__":
    raise SystemExit(main())
