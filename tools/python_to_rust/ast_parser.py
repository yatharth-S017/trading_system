"""
Python AST parser for the experimental Python-to-Rust conversion system.

Uses Python's standard `ast` module to safely inspect, extract, and classify
Python constructs into supported structures (functions, arguments, types,
expressions, statements, and simple structs) and unsupported structures.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UnsupportedConstructWarning:
    """Warning emitted when an unsupported Python construct is encountered."""

    construct: str
    file_path: str
    line: int
    details: str = ""

    def format_warning(self) -> str:
        """Format warning matching the exact project specification."""
        msg = (
            "WARNING:\n"
            f"Unsupported Python construct:\n"
            f"    {self.construct}\n\n"
            f"File:\n"
            f"    {self.file_path}\n\n"
            f"Line:\n"
            f"    {self.line}\n\n"
            "This code was NOT converted."
        )
        if self.details:
            msg += f"\nDetails: {self.details}"
        return msg


@dataclass
class ParsedArg:
    """Represents a function parameter."""

    name: str
    type_hint: str | None = None
    default_repr: str | None = None


@dataclass
class ParsedFunction:
    """Represents a parsed function definition."""

    name: str
    args: list[ParsedArg]
    return_type: str | None
    body: list[ast.stmt]
    docstring: str | None
    line: int
    is_method: bool = False


@dataclass
class ParsedField:
    """Represents a field in a struct/dataclass."""

    name: str
    type_hint: str
    default_value: ast.expr | None = None


@dataclass
class ParsedStruct:
    """Represents a parsed struct or dataclass."""

    name: str
    fields: list[ParsedField]
    methods: list[ParsedFunction]
    docstring: str | None
    line: int


@dataclass
class ParsedConstant:
    """Represents a top-level constant or variable."""

    name: str
    type_hint: str | None
    value: ast.expr
    line: int


@dataclass
class ParsedModule:
    """Represents the parsed intermediate representation of a Python module."""

    file_path: str
    module_name: str
    functions: list[ParsedFunction] = field(default_factory=list)
    structs: list[ParsedStruct] = field(default_factory=list)
    constants: list[ParsedConstant] = field(default_factory=list)
    warnings: list[UnsupportedConstructWarning] = field(default_factory=list)
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "functions": 0,
            "structs": 0,
            "variables": 0,
            "expressions": 0,
            "unsupported": 0,
        }
    )


class PythonASTParser:
    """
    Parses Python source files using standard library `ast`.

    Categorizes supported pure Python constructs and produces structured
    warnings for unsupported constructs.
    """

    SUPPORTED_STMTS = (
        ast.FunctionDef,
        ast.Return,
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.If,
        ast.For,
        ast.While,
        ast.Pass,
        ast.Expr,
    )

    SUPPORTED_EXPRS = (
        ast.Constant,
        ast.Name,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.BoolOp,
        ast.Call,
        ast.List,
        ast.Tuple,
        ast.Subscript,
        ast.Attribute,
        ast.IfExp,
    )

    def __init__(self, file_path: str = "<source>") -> None:
        self.file_path = file_path
        self.warnings: list[UnsupportedConstructWarning] = []
        self.stats = {
            "functions": 0,
            "structs": 0,
            "variables": 0,
            "expressions": 0,
            "unsupported": 0,
        }

    def _warn(self, construct: str, line: int, details: str = "") -> None:
        """Register an unsupported construct warning."""
        warning = UnsupportedConstructWarning(
            construct=construct,
            file_path=self.file_path,
            line=line,
            details=details,
        )
        self.warnings.append(warning)
        self.stats["unsupported"] += 1

    def parse_source(self, source_code: str, file_path: str | None = None) -> ParsedModule:
        """
        Parse raw Python source string into a ParsedModule.
        """
        if file_path:
            self.file_path = file_path

        module_name = Path(self.file_path).stem if self.file_path != "<source>" else "module"
        self.warnings.clear()
        self.stats = {
            "functions": 0,
            "structs": 0,
            "variables": 0,
            "expressions": 0,
            "unsupported": 0,
        }

        try:
            tree = ast.parse(source_code, filename=self.file_path)
        except SyntaxError as e:
            self._warn(f"Python SyntaxError: {e.msg}", e.lineno or 1)
            return ParsedModule(
                file_path=self.file_path,
                module_name=module_name,
                warnings=list(self.warnings),
                stats=dict(self.stats),
            )

        functions: list[ParsedFunction] = []
        structs: list[ParsedStruct] = []
        constants: list[ParsedConstant] = []

        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Safe skip: external libraries are not converted, but not flagged as errors
                continue
            elif isinstance(node, ast.FunctionDef):
                parsed_fn = self._parse_function(node)
                if parsed_fn:
                    functions.append(parsed_fn)
                    self.stats["functions"] += 1
            elif isinstance(node, ast.AsyncFunctionDef):
                self._warn(
                    f"async def {node.name}()",
                    node.lineno,
                    "Async functions are not supported in experimental pure Rust translation.",
                )
            elif isinstance(node, ast.ClassDef):
                parsed_cls = self._parse_class(node)
                if parsed_cls:
                    structs.append(parsed_cls)
                    self.stats["structs"] += 1
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    constants.append(
                        ParsedConstant(
                            name=node.target.id,
                            type_hint=ast.unparse(node.annotation) if node.annotation else None,
                            value=node.value,
                            line=node.lineno,
                        )
                    )
                    self.stats["variables"] += 1
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants.append(
                            ParsedConstant(
                                name=target.id,
                                type_hint=None,
                                value=node.value,
                                line=node.lineno,
                            )
                        )
                        self.stats["variables"] += 1
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                # Module-level docstrings
                continue
            else:
                self._warn(
                    f"{type(node).__name__}",
                    getattr(node, "lineno", 1),
                    f"Top-level {type(node).__name__} is not supported for direct conversion.",
                )

        return ParsedModule(
            file_path=self.file_path,
            module_name=module_name,
            functions=functions,
            structs=structs,
            constants=constants,
            warnings=list(self.warnings),
            stats=dict(self.stats),
        )

    def parse_file(self, file_path: Path | str) -> ParsedModule:
        """Parse a Python source file from disk."""
        path = Path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
        return self.parse_source(code, file_path=str(path))

    def _parse_function(self, node: ast.FunctionDef, is_method: bool = False) -> ParsedFunction | None:
        """Parse a FunctionDef AST node."""
        docstring = ast.get_docstring(node)

        # Check for unsupported decorators
        if node.decorator_list:
            for dec in node.decorator_list:
                dec_name = ast.unparse(dec) if hasattr(ast, "unparse") else str(dec)
                # Allow @staticmethod or property
                if dec_name not in ("staticmethod", "property"):
                    self._warn(
                        f"Decorator @{dec_name} on {node.name}",
                        node.lineno,
                        "Arbitrary decorators are not translated.",
                    )

        args: list[ParsedArg] = []
        for a in node.args.args:
            if is_method and a.arg == "self":
                continue
            hint = ast.unparse(a.annotation) if a.annotation else None
            args.append(ParsedArg(name=a.arg, type_hint=hint))

        return_type = ast.unparse(node.returns) if node.returns else None

        valid_body_stmts: list[ast.stmt] = []
        for stmt in node.body:
            # Skip docstring statement if present
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue
            if self._is_supported_stmt(stmt):
                valid_body_stmts.append(stmt)
            else:
                self._warn(
                    f"{type(stmt).__name__} statement in function {node.name}",
                    getattr(stmt, "lineno", node.lineno),
                    f"Unsupported statement type: {type(stmt).__name__}",
                )

        return ParsedFunction(
            name=node.name,
            args=args,
            return_type=return_type,
            body=valid_body_stmts,
            docstring=docstring,
            line=node.lineno,
            is_method=is_method,
        )

    def _parse_class(self, node: ast.ClassDef) -> ParsedStruct | None:
        """Parse a simple class or dataclass into a ParsedStruct."""
        docstring = ast.get_docstring(node)
        fields: list[ParsedField] = []
        methods: list[ParsedFunction] = []

        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                field_name = item.target.id
                field_type = ast.unparse(item.annotation) if item.annotation else "Any"
                fields.append(ParsedField(name=field_name, type_hint=field_type, default_value=item.value))
                self.stats["variables"] += 1
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        fields.append(ParsedField(name=target.id, type_hint="Any", default_value=item.value))
                        self.stats["variables"] += 1
            elif isinstance(item, ast.FunctionDef):
                method = self._parse_function(item, is_method=True)
                if method:
                    methods.append(method)
                    self.stats["functions"] += 1
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant):
                continue
            else:
                self._warn(
                    f"{type(item).__name__} in class {node.name}",
                    getattr(item, "lineno", node.lineno),
                    "Complex class constructs (metaclasses, dynamic inheritance) are not converted.",
                )

        return ParsedStruct(
            name=node.name,
            fields=fields,
            methods=methods,
            docstring=docstring,
            line=node.lineno,
        )

    def _is_supported_stmt(self, stmt: ast.stmt) -> bool:
        """Verify whether an AST statement is supported."""
        if not isinstance(stmt, self.SUPPORTED_STMTS):
            return False

        if isinstance(stmt, (ast.If, ast.While)):
            return self._is_supported_expr(stmt.test)
        elif isinstance(stmt, ast.For):
            return self._is_supported_expr(stmt.iter)
        elif isinstance(stmt, ast.Return):
            return stmt.value is None or self._is_supported_expr(stmt.value)
        elif isinstance(stmt, ast.Assign):
            return self._is_supported_expr(stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            return stmt.value is None or self._is_supported_expr(stmt.value)
        elif isinstance(stmt, ast.AugAssign):
            return self._is_supported_expr(stmt.value)
        elif isinstance(stmt, ast.Expr):
            return self._is_supported_expr(stmt.value)
        return True

    def _is_supported_expr(self, expr: ast.expr) -> bool:
        """Verify whether an AST expression is supported."""
        if not isinstance(expr, self.SUPPORTED_EXPRS):
            return False

        self.stats["expressions"] += 1

        if isinstance(expr, ast.BinOp):
            return self._is_supported_expr(expr.left) and self._is_supported_expr(expr.right)
        elif isinstance(expr, ast.UnaryOp):
            return self._is_supported_expr(expr.operand)
        elif isinstance(expr, ast.Compare):
            return self._is_supported_expr(expr.left) and all(self._is_supported_expr(c) for c in expr.comparators)
        elif isinstance(expr, ast.BoolOp):
            return all(self._is_supported_expr(v) for v in expr.values)
        elif isinstance(expr, ast.Call):
            return all(self._is_supported_expr(a) for a in expr.args)
        elif isinstance(expr, (ast.List, ast.Tuple)):
            return all(self._is_supported_expr(e) for e in expr.elts)
        return True
