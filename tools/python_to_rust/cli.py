#!/usr/bin/env python
"""
CLI entry-point for the experimental Python → Rust converter.

Usage:
    python tools/python_to_rust/cli.py build   # Convert + cargo build
    python tools/python_to_rust/cli.py run     # Convert + cargo build + run
    python tools/python_to_rust/cli.py clean   # Remove generated build artifacts
    python tools/python_to_rust/cli.py check   # Convert + cargo check only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project root is on sys.path so relative imports resolve
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.python_to_rust.converter import PythonToRustConverter
from tools.python_to_rust.project_builder import CargoProjectBuilder


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

SOURCE_DIR = PROJECT_ROOT / "tools" / "python_to_rust"
OUTPUT_DIR = PROJECT_ROOT / "rust_experiment"

# Python files to convert in the first experiment.
# We start with our standalone sample_input.py that contains pure-logic
# risk-management functions extracted from the project.  Files that depend
# heavily on NautilusTrader / Bybit / asyncio are deliberately excluded.
DEFAULT_FILES: list[str] = [
    "sample_input.py",
]


def cmd_build(args: argparse.Namespace) -> int:
    """Convert Python source and build the Cargo project."""
    source_dir = Path(args.source_dir) if args.source_dir else SOURCE_DIR
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    files = args.files if args.files else DEFAULT_FILES

    converter = PythonToRustConverter(
        source_dir=source_dir,
        output_dir=output_dir,
        source_files=files,
    )

    print("Discovering and converting Python source files...")
    converter.convert_all()
    converter.build_project()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Convert, build, and run the generated Rust executable."""
    ret = cmd_build(args)
    if ret != 0:
        return ret

    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    builder = CargoProjectBuilder(output_dir)

    print("Running:\n    cargo run\n")
    result = builder.cargo_run()
    if result.returncode != 0:
        print("✗ cargo run FAILED\n")
        print(result.stderr)
        return 1

    print(result.stdout)
    print("✓ run completed successfully")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Convert and run cargo check (no full build)."""
    source_dir = Path(args.source_dir) if args.source_dir else SOURCE_DIR
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    files = args.files if args.files else DEFAULT_FILES

    converter = PythonToRustConverter(
        source_dir=source_dir,
        output_dir=output_dir,
        source_files=files,
    )

    print("Discovering and converting Python source files...")
    converter.convert_all()

    module_names = [r.module_name for r in converter.results if r.success]
    if not module_names:
        print("No modules to check.")
        return 1

    builder = CargoProjectBuilder(output_dir)
    builder.write_cargo_toml()
    builder.write_main(module_names)
    converter._print_report()

    print("\nRunning:\n    cargo check\n")
    check = builder.cargo_check()
    if check.returncode != 0:
        print("✗ cargo check FAILED\n")
        print(check.stderr)
        return 1

    print("✓ cargo check successful")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Remove the Cargo target directory."""
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR
    builder = CargoProjectBuilder(output_dir)
    builder.clean()
    print(f"Cleaned: {output_dir / 'target'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python_to_rust",
        description="Experimental Python → Rust code generation tool.",
    )
    parser.add_argument(
        "--source-dir",
        type=str,
        default=None,
        help=f"Python source directory (default: {SOURCE_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help=f"Output Cargo project directory (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Specific Python files to convert (filenames relative to source-dir).",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.add_parser("build", help="Convert Python sources and compile Rust")
    subparsers.add_parser("run", help="Convert, compile, and run Rust executable")
    subparsers.add_parser("check", help="Convert and run cargo check only")
    subparsers.add_parser("clean", help="Remove generated build artifacts")

    args = parser.parse_args()

    commands = {
        "build": cmd_build,
        "run": cmd_run,
        "check": cmd_check,
        "clean": cmd_clean,
    }

    if args.command is None:
        parser.print_help()
        return 1

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
