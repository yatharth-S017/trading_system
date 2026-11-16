"""
Main converter orchestrator for the experimental Python-to-Rust system.

Coordinates the pipeline:
    Python source → AST parser → Rust code generator → Cargo project builder
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from .ast_parser import ParsedModule, PythonASTParser
from .rust_generator import RustCodeGenerator
from .project_builder import CargoProjectBuilder


class ConversionResult:
    """Holds the outcome of converting one Python file."""

    def __init__(self, source_path: str, module_name: str) -> None:
        self.source_path = source_path
        self.module_name = module_name
        self.generated_path: str | None = None
        self.parsed_module: ParsedModule | None = None
        self.rust_source: str | None = None
        self.success: bool = False


class PythonToRustConverter:
    """
    End-to-end converter: reads Python files, generates Rust code, and
    assembles a Cargo project.
    """

    # Python modules whose names or imports indicate external library
    # dependencies that should NOT be converted.
    EXTERNAL_SKIP_PATTERNS = frozenset({
        "nautilus_trader",
        "bybit",
        "asyncio",
        "aiohttp",
        "fastapi",
        "uvicorn",
        "websocket",
        "pydantic",
    })

    def __init__(
        self,
        source_dir: Path | str,
        output_dir: Path | str,
        source_files: list[str] | None = None,
    ) -> None:
        self.source_dir = Path(source_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.source_files = source_files or []
        self.results: list[ConversionResult] = []
        self._parser = PythonASTParser()
        self._generator = RustCodeGenerator()
        self._builder = CargoProjectBuilder(self.output_dir)

    def discover_source_files(self) -> list[Path]:
        """
        Return the list of Python files to convert.

        If explicit ``source_files`` were given, use those.
        Otherwise scan ``source_dir`` for ``.py`` files that are NOT test files,
        ``__init__.py``, ``__main__.py``, or heavy-dependency modules.
        """
        if self.source_files:
            return [self.source_dir / f for f in self.source_files]

        candidates: list[Path] = []
        for py_file in sorted(self.source_dir.rglob("*.py")):
            name = py_file.name
            if name.startswith("__"):
                continue
            if name.startswith("test_"):
                continue
            # Quick scan for heavy external dependencies
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            has_external = any(
                f"import {pkg}" in text or f"from {pkg}" in text
                for pkg in self.EXTERNAL_SKIP_PATTERNS
            )
            if has_external:
                continue
            candidates.append(py_file)
        return candidates

    def convert_file(self, py_path: Path) -> ConversionResult:
        """Convert a single Python file to Rust."""
        module_name = py_path.stem
        result = ConversionResult(source_path=str(py_path), module_name=module_name)

        # Parse
        parsed = self._parser.parse_file(py_path)
        result.parsed_module = parsed

        # Generate Rust source
        rust_code = self._generator.generate_module(parsed)

        # Append a demo() function so main.rs can call it
        demo_lines = self._build_demo_function(parsed)
        rust_code += "\n" + "\n".join(demo_lines) + "\n"

        result.rust_source = rust_code

        # Write to project
        rs_path = self._builder.write_module(module_name, rust_code)
        result.generated_path = str(rs_path)
        result.success = True

        self.results.append(result)
        return result

    def convert_all(self) -> list[ConversionResult]:
        """Run the full conversion pipeline for all discovered source files."""
        files = self.discover_source_files()
        if not files:
            print("No Python source files discovered for conversion.")
            return []

        self.results.clear()

        for py_file in files:
            result = self.convert_file(py_file)
            print(f"  Converted: {py_file.name} → {result.module_name}.rs")

        return self.results

    def build_project(self) -> None:
        """
        Assemble the Cargo project: write Cargo.toml, main.rs, and invoke
        ``cargo check`` + ``cargo build``.
        """
        module_names = [r.module_name for r in self.results if r.success]
        if not module_names:
            print("No modules were converted. Nothing to build.")
            return

        # Write project files
        self._builder.write_cargo_toml()
        self._builder.write_main(module_names)

        # Print the conversion report
        self._print_report()

        # cargo check
        print("\nRunning:\n    cargo check\n")
        check = self._builder.cargo_check()
        if check.returncode != 0:
            print("✗ cargo check FAILED\n")
            print(check.stderr)
            return
        print("✓ cargo check successful\n")

        # cargo build
        print("Running:\n    cargo build\n")
        build = self._builder.cargo_build()
        if build.returncode != 0:
            print("✗ cargo build FAILED\n")
            print(build.stderr)
            return
        print("✓ build successful\n")

        exe = self._builder.get_executable_path()
        print(f"Rust executable:\n    {exe}\n")

    def _build_demo_function(self, module: ParsedModule) -> list[str]:
        """
        Generate a ``pub fn demo()`` that exercises the converted functions
        with safe default arguments for quick validation.
        """
        lines: list[str] = []
        lines.append("/// Auto-generated demonstration function.")
        lines.append("#[allow(unused_variables, unused_mut)]")
        lines.append("pub fn demo() {")

        if not module.functions and not module.structs:
            lines.append('    println!("  (no convertible constructs found)");')
            lines.append("}")
            return lines

        for func in module.functions:
            # Build safe default arguments for a demo call
            args_demo: list[str] = []
            for arg in func.args:
                args_demo.append(self._default_value_for_type(arg.type_hint))
            call = f"{func.name}({', '.join(args_demo)})"
            lines.append(f'    println!("  Calling {func.name}()...");')
            lines.append(f"    let _result = {call};")
            lines.append(f'    println!("    → {{:?}}", _result);')

        for struct in module.structs:
            lines.append(f'    println!("  Struct {struct.name} is defined.");')

        lines.append("}")
        return lines

    @staticmethod
    def _default_value_for_type(py_type: str | None) -> str:
        """Return a safe Rust default value suitable for a demo call."""
        if py_type is None:
            return "0.0_f64"
        t = py_type.strip()
        defaults: dict[str, str] = {
            "int": "0_i64",
            "float": "0.0_f64",
            "bool": "false",
            "str": 'String::from("")',
        }
        if t in defaults:
            return defaults[t]
        if "| None" in t or t.startswith("Optional"):
            return "None"
        if t.startswith(("list", "List")):
            return "vec![]"
        return "Default::default()"

    def _print_report(self) -> None:
        """Print the post-conversion summary report."""
        print()
        print("=" * 48)
        print("  Python → Rust Experimental Build")
        print("=" * 48)
        print()

        total_funcs = 0
        total_vars = 0
        total_exprs = 0
        total_unsupported = 0
        total_structs = 0

        for result in self.results:
            m = result.parsed_module
            if m is None:
                continue
            print(f"Source:\n    {result.source_path}\n")
            print(f"Generated:\n    {result.generated_path}\n")
            print("Converted:")
            print(f"    {m.stats['functions']} functions")
            print(f"    {m.stats['structs']} structs")
            print(f"    {m.stats['variables']} variables")
            print(f"    {m.stats['expressions']} expressions")
            print()
            if m.warnings:
                print(f"Unsupported:\n    {len(m.warnings)} construct(s)")
                for w in m.warnings:
                    print()
                    print(textwrap.indent(w.format_warning(), "    "))
                print()

            total_funcs += m.stats["functions"]
            total_structs += m.stats["structs"]
            total_vars += m.stats["variables"]
            total_exprs += m.stats["expressions"]
            total_unsupported += m.stats["unsupported"]

        print("-" * 48)
        print(f"Total: {total_funcs} functions, {total_structs} structs, "
              f"{total_vars} variables, {total_exprs} expressions")
        if total_unsupported:
            print(f"Unsupported: {total_unsupported} construct(s)")
        print("-" * 48)
