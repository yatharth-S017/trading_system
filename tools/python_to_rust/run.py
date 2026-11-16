#!/usr/bin/env python
"""
Run entry point: Python source -> Rust generation -> cargo build -> execute Rust binary.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.python_to_rust.cli import cmd_run
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build and run experimental Rust executable from Python sources.")
    parser.add_argument("--source-dir", default=None, help="Source directory containing Python files")
    parser.add_argument("--output-dir", default=None, help="Output directory for generated Rust crate")
    parser.add_argument("--files", nargs="*", default=None, help="Specific Python files to convert")
    args = parser.parse_args()
    sys.exit(cmd_run(args))
