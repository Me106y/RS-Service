"""Vendored regional coverage/selection engine.

The engine is a self-contained copy of the SIRIAgent coverage backend
(``satellite-coverage-backend``).  Its modules import each other by their
historical top-level names (``import coverage_analysis``), so the vendor
directory is added to ``sys.path`` once, here.  Do not import engine modules
from elsewhere without going through this package.
"""

from __future__ import annotations

import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

__all__ = ["VENDOR_DIR"]
