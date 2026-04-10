#!/usr/bin/env python3
"""
Compatibility wrapper for the modular AIOS testkit.

Canonical entrypoint:
- testkit/aios-testkit.py
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


TARGET = Path(os.path.abspath(__file__)).parents[1] / "testkit" / "aios-testkit.py"
sys.path.insert(0, str(TARGET.parent))


if __name__ == "__main__":
    runpy.run_path(str(TARGET), run_name="__main__")
