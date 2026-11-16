"""
Tests for the experimental Python → Rust converter.

These tests are completely independent from the NautilusTrader project tests.
They verify the AST parser, Rust code generator, and end-to-end pipeline.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.python_to_rust.ast_parser import PythonASTParser
from tools.python_to_rust.rust_generator import RustCodeGenerator, map_python_type
from tools.python_to_rust.converter import PythonToRustConverter


# =========================================================================
# 1. Python function → Rust function
# =========================================================================

class TestFunctionConversion:
    """Tests for converting Python function definitions to Rust."""

    def test_simple_function(self) -> None:
        source = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b
        """)
        parser = PythonASTParser()
        module = parser.parse_source(source)
        assert len(module.functions) == 1
        assert module.functions[0].name == "add"
        assert len(module.functions[0].args) == 2

        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "fn add(" in rust
        assert "i64" in rust
        assert "-> i64" in rust

    def test_float_function(self) -> None:
        source = textwrap.dedent("""\
            def multiply(x: float, y: float) -> float:
                return x * y
        """)
        parser = PythonASTParser()
        module = parser.parse_source(source)
        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "f64" in rust
        assert "fn multiply(" in rust

    def test_no_type_hints(self) -> None:
        source = textwrap.dedent("""\
            def greet(name):
                return name
        """)
        parser = PythonASTParser()
        module = parser.parse_source(source)
        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "fn greet(" in rust
        # Without type hints, defaults to f64
        assert "f64" in rust

    def test_bool_return(self) -> None:
        source = textwrap.dedent("""\
            def is_valid(x: float) -> bool:
                return x > 0.0
        """)
        parser = PythonASTParser()
        module = parser.parse_source(source)
        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "-> bool" in rust

    def test_docstring_preserved(self) -> None:
        source = textwrap.dedent('''\
            def example():
                """This is a docstring."""
                return 42
        ''')
        parser = PythonASTParser()
        module = parser.parse_source(source)
        assert module.functions[0].docstring == "This is a docstring."
        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "/// This is a docstring." in rust


# =========================================================================
# 2. Python arithmetic → Rust arithmetic
# =========================================================================

class TestArithmeticConversion:
    """Tests for converting arithmetic expressions."""

    def test_addition(self) -> None:
        source = "def f(a: float, b: float) -> float:\n    return a + b\n"
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "+" in rust

    def test_multiplication(self) -> None:
        source = "def f(a: float, b: float) -> float:\n    return a * b\n"
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "*" in rust

    def test_division(self) -> None:
        source = "def f(a: float, b: float) -> float:\n    return a / b\n"
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "/" in rust

    def test_complex_expression(self) -> None:
        source = textwrap.dedent("""\
            def calc(entry: float, pct: float) -> float:
                return entry * (1.0 + pct)
        """)
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "*" in rust
        assert "+" in rust


# =========================================================================
# 3. if/else → Rust if/else
# =========================================================================

class TestIfElseConversion:
    """Tests for converting if/elif/else to Rust."""

    def test_simple_if(self) -> None:
        source = textwrap.dedent("""\
            def check(x: float) -> bool:
                if x > 0.0:
                    return True
                return False
        """)
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "if " in rust
        assert "true" in rust
        assert "false" in rust

    def test_if_else(self) -> None:
        source = textwrap.dedent("""\
            def sign(x: float) -> str:
                if x > 0.0:
                    result = "positive"
                else:
                    result = "non-positive"
                return result
        """)
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "if " in rust
        assert "} else {" in rust

    def test_if_elif_else(self) -> None:
        source = textwrap.dedent("""\
            def classify(x: float) -> str:
                if x > 0.0:
                    return "STOP_LOSS"
                elif x < 0.0:
                    return "TAKE_PROFIT"
                else:
                    return "HOLD"
        """)
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "if " in rust
        assert "} else if " in rust
        assert "} else {" in rust


# =========================================================================
# 4. Comparison → Rust comparison
# =========================================================================

class TestComparisonConversion:
    """Tests for comparison operators."""

    def test_greater_equal(self) -> None:
        source = "def f(a: float, b: float) -> bool:\n    return a >= b\n"
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert ">=" in rust

    def test_less_equal(self) -> None:
        source = "def f(a: float, b: float) -> bool:\n    return a <= b\n"
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "<=" in rust

    def test_equality(self) -> None:
        source = "def f(a: int, b: int) -> bool:\n    return a == b\n"
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "==" in rust

    def test_not_equal(self) -> None:
        source = "def f(a: int, b: int) -> bool:\n    return a != b\n"
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "!=" in rust


# =========================================================================
# 5. Variable assignment → Rust variable
# =========================================================================

class TestVariableConversion:
    """Tests for variable assignments."""

    def test_let_binding(self) -> None:
        source = textwrap.dedent("""\
            def f() -> float:
                x = 10.0
                return x
        """)
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "let x" in rust

    def test_annotated_assignment(self) -> None:
        source = textwrap.dedent("""\
            def f() -> float:
                x: float = 3.14
                return x
        """)
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "let x: f64" in rust

    def test_augmented_assignment(self) -> None:
        source = textwrap.dedent("""\
            def f(x: float) -> float:
                x += 1.0
                return x
        """)
        gen = RustCodeGenerator()
        rust = gen.generate_module(PythonASTParser().parse_source(source))
        assert "+=" in rust


# =========================================================================
# 6. Unsupported Python syntax → warning
# =========================================================================

class TestUnsupportedWarnings:
    """Tests that unsupported constructs produce clear warnings."""

    def test_async_function_warning(self) -> None:
        source = "async def run():\n    pass\n"
        parser = PythonASTParser()
        module = parser.parse_source(source)
        assert len(module.warnings) >= 1
        assert any("async" in w.construct.lower() for w in module.warnings)

    def test_warning_format(self) -> None:
        source = "async def fetch():\n    pass\n"
        parser = PythonASTParser(file_path="test_file.py")
        module = parser.parse_source(source)
        assert module.warnings
        formatted = module.warnings[0].format_warning()
        assert "WARNING:" in formatted
        assert "Unsupported Python construct:" in formatted
        assert "test_file.py" in formatted
        assert "NOT converted" in formatted


# =========================================================================
# 7. Type mapping
# =========================================================================

class TestTypeMapping:
    """Tests for the Python → Rust type mapping."""

    def test_int(self) -> None:
        assert map_python_type("int") == "i64"

    def test_float(self) -> None:
        assert map_python_type("float") == "f64"

    def test_bool(self) -> None:
        assert map_python_type("bool") == "bool"

    def test_str(self) -> None:
        assert map_python_type("str") == "String"

    def test_none(self) -> None:
        assert map_python_type("None") == "()"

    def test_optional(self) -> None:
        result = map_python_type("float | None")
        assert "Option" in result
        assert "f64" in result

    def test_list(self) -> None:
        result = map_python_type("list[float]")
        assert "Vec" in result
        assert "f64" in result

    def test_no_type(self) -> None:
        assert map_python_type(None) == "f64"


# =========================================================================
# 8. Struct / class conversion
# =========================================================================

class TestStructConversion:
    """Tests for converting Python classes to Rust structs."""

    def test_simple_class(self) -> None:
        source = textwrap.dedent("""\
            class Point:
                x: float
                y: float
        """)
        parser = PythonASTParser()
        module = parser.parse_source(source)
        assert len(module.structs) == 1
        assert module.structs[0].name == "Point"
        assert len(module.structs[0].fields) == 2

        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "pub struct Point" in rust
        assert "pub x: f64" in rust
        assert "pub y: f64" in rust

    def test_class_with_method(self) -> None:
        source = textwrap.dedent("""\
            class Calc:
                value: float

                def double(self) -> float:
                    return self.value * 2.0
        """)
        parser = PythonASTParser()
        module = parser.parse_source(source)
        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "impl Calc" in rust
        assert "fn double(" in rust
        assert "&self" in rust


# =========================================================================
# 9. Constants
# =========================================================================

class TestConstantConversion:
    """Tests for top-level constant conversion."""

    def test_uppercase_constant(self) -> None:
        source = "STOP_LOSS_PCT: float = 0.01\n"
        parser = PythonASTParser()
        module = parser.parse_source(source)
        assert len(module.constants) == 1
        gen = RustCodeGenerator()
        rust = gen.generate_module(module)
        assert "const STOP_LOSS_PCT" in rust


# =========================================================================
# 10. End-to-end on sample_input.py
# =========================================================================

class TestEndToEndSampleInput:
    """Test converting the actual sample_input.py file."""

    def test_parse_sample_input(self) -> None:
        sample = Path(__file__).resolve().parent.parent / "sample_input.py"
        if not sample.exists():
            pytest.skip("sample_input.py not found")

        parser = PythonASTParser()
        module = parser.parse_file(sample)

        # Should parse functions successfully
        assert module.stats["functions"] >= 8
        assert module.stats["unsupported"] == 0

    def test_generate_rust_from_sample(self) -> None:
        sample = Path(__file__).resolve().parent.parent / "sample_input.py"
        if not sample.exists():
            pytest.skip("sample_input.py not found")

        parser = PythonASTParser()
        module = parser.parse_file(sample)

        gen = RustCodeGenerator()
        rust = gen.generate_module(module)

        # Key functions should be present in generated Rust
        assert "fn calculate_combined_entry_premium(" in rust
        assert "fn should_stop_loss(" in rust
        assert "fn should_take_profit(" in rust
        assert "fn evaluate_risk(" in rust
        assert "fn remaining_quantity(" in rust
        assert "fn is_leg_closed(" in rust
        assert "fn select_atm_strike_pure(" in rust

    def test_statistics(self) -> None:
        sample = Path(__file__).resolve().parent.parent / "sample_input.py"
        if not sample.exists():
            pytest.skip("sample_input.py not found")

        parser = PythonASTParser()
        module = parser.parse_file(sample)

        assert module.stats["functions"] >= 8
        assert module.stats["structs"] >= 1
        assert module.stats["variables"] >= 3
