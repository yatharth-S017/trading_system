"""
Rust code generator for the experimental Python-to-Rust conversion system.

Translates the parsed intermediate representation (from ast_parser.py) into
valid Rust source code. Prefers correctness over completeness: if a construct
cannot be safely mapped, it emits a comment warning instead of guessing.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .ast_parser import (
    ParsedArg,
    ParsedConstant,
    ParsedField,
    ParsedFunction,
    ParsedModule,
    ParsedStruct,
    UnsupportedConstructWarning,
)


# ---------------------------------------------------------------------------
# Python → Rust type mapping
# ---------------------------------------------------------------------------

PYTHON_TO_RUST_TYPE: dict[str, str] = {
    "int": "i64",
    "float": "f64",
    "bool": "bool",
    "str": "String",
    "None": "()",
    "NoneType": "()",
    "list": "Vec",
    "List": "Vec",
    "dict": "HashMap",
    "Dict": "HashMap",
    "tuple": "tuple",
    "Tuple": "tuple",
    "Optional": "Option",
    "Any": "/* Any */",
}


def map_python_type(py_type: str | None) -> str:
    """
    Map a Python type annotation string to its Rust equivalent.

    Falls back to f64 when no type is specified (most numeric uses),
    or inserts a comment for unmapped types.
    """
    if py_type is None:
        return "f64"

    py_type = py_type.strip()

    # Handle "X | None" → Option<X>
    if " | None" in py_type:
        inner = py_type.replace(" | None", "").strip()
        return f"Option<{map_python_type(inner)}>"
    if "None | " in py_type:
        inner = py_type.replace("None | ", "").strip()
        return f"Option<{map_python_type(inner)}>"

    # Handle Optional[X]
    if py_type.startswith("Optional[") and py_type.endswith("]"):
        inner = py_type[len("Optional["):-1]
        return f"Option<{map_python_type(inner)}>"

    # Handle list[X] / List[X]
    if py_type.startswith(("list[", "List[")):
        inner = py_type[py_type.index("[") + 1 : -1]
        return f"Vec<{map_python_type(inner)}>"

    # Handle dict[K, V]
    if py_type.startswith(("dict[", "Dict[")):
        inner = py_type[py_type.index("[") + 1 : -1]
        parts = [p.strip() for p in inner.split(",", 1)]
        if len(parts) == 2:
            return f"std::collections::HashMap<{map_python_type(parts[0])}, {map_python_type(parts[1])}>"
        return f"std::collections::HashMap<{map_python_type(parts[0])}, /* unknown */>"

    # Handle tuple[A, B, ...]
    if py_type.startswith(("tuple[", "Tuple[")):
        inner = py_type[py_type.index("[") + 1 : -1]
        parts = [map_python_type(p.strip()) for p in inner.split(",")]
        return f"({', '.join(parts)})"

    # Direct lookup
    if py_type in PYTHON_TO_RUST_TYPE:
        return PYTHON_TO_RUST_TYPE[py_type]

    return f"/* {py_type} */"


# ---------------------------------------------------------------------------
# Expression generator
# ---------------------------------------------------------------------------

class RustExprGenerator:
    """Generates Rust expression strings from Python AST expression nodes."""

    def __init__(self, file_path: str = "<source>") -> None:
        self.file_path = file_path
        self.warnings: list[UnsupportedConstructWarning] = []

    def _warn(self, construct: str, line: int, details: str = "") -> None:
        self.warnings.append(
            UnsupportedConstructWarning(
                construct=construct,
                file_path=self.file_path,
                line=line,
                details=details,
            )
        )

    def generate(self, expr: ast.expr) -> str:
        """Translate a single Python AST expression to a Rust expression string."""
        if isinstance(expr, ast.Constant):
            return self._gen_constant(expr)
        elif isinstance(expr, ast.Name):
            return self._gen_name(expr)
        elif isinstance(expr, ast.BinOp):
            return self._gen_binop(expr)
        elif isinstance(expr, ast.UnaryOp):
            return self._gen_unaryop(expr)
        elif isinstance(expr, ast.Compare):
            return self._gen_compare(expr)
        elif isinstance(expr, ast.BoolOp):
            return self._gen_boolop(expr)
        elif isinstance(expr, ast.Call):
            return self._gen_call(expr)
        elif isinstance(expr, ast.List):
            return self._gen_list(expr)
        elif isinstance(expr, ast.Tuple):
            return self._gen_tuple(expr)
        elif isinstance(expr, ast.Attribute):
            return self._gen_attribute(expr)
        elif isinstance(expr, ast.IfExp):
            return self._gen_ifexp(expr)
        elif isinstance(expr, ast.Subscript):
            return self._gen_subscript(expr)
        else:
            self._warn(
                f"{type(expr).__name__} expression",
                getattr(expr, "lineno", 0),
                "Unsupported expression type for Rust conversion.",
            )
            return f"/* unsupported: {type(expr).__name__} */"

    def _gen_constant(self, node: ast.Constant) -> str:
        if isinstance(node.value, bool):
            return "true" if node.value else "false"
        elif isinstance(node.value, int):
            return str(node.value)
        elif isinstance(node.value, float):
            s = repr(node.value)
            if "." not in s and "e" not in s.lower():
                s += ".0"
            return s + "_f64"
        elif isinstance(node.value, str):
            escaped = node.value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            return f'String::from("{escaped}")'
        elif node.value is None:
            return "None"
        else:
            return f"/* unsupported constant: {type(node.value).__name__} */"

    def _gen_name(self, node: ast.Name) -> str:
        name_map = {"True": "true", "False": "false", "None": "None"}
        return name_map.get(node.id, node.id)

    def _gen_binop(self, node: ast.BinOp) -> str:
        op_map = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.Mod: "%",
            ast.Pow: "/* pow */",
            ast.FloorDiv: "/",
            ast.BitAnd: "&",
            ast.BitOr: "|",
            ast.BitXor: "^",
            ast.LShift: "<<",
            ast.RShift: ">>",
        }
        left = self.generate(node.left)
        right = self.generate(node.right)
        op = op_map.get(type(node.op), "/* unknown_op */")

        if isinstance(node.op, ast.Pow):
            return f"{left}.powf({right})"
        if isinstance(node.op, ast.FloorDiv):
            return f"(({left}) / ({right}))"

        return f"({left} {op} {right})"

    def _gen_unaryop(self, node: ast.UnaryOp) -> str:
        operand = self.generate(node.operand)
        if isinstance(node.op, ast.USub):
            return f"(-{operand})"
        elif isinstance(node.op, ast.UAdd):
            return operand
        elif isinstance(node.op, ast.Not):
            # Python `not x` on a collection means `x.is_empty()` in Rust
            if isinstance(node.operand, ast.Name):
                return f"{operand}.is_empty()"
            return f"(!{operand})"
        elif isinstance(node.op, ast.Invert):
            return f"(!{operand})"
        return f"/* unsupported unary op */ {operand}"

    def _gen_compare(self, node: ast.Compare) -> str:
        cmp_map = {
            ast.Eq: "==",
            ast.NotEq: "!=",
            ast.Lt: "<",
            ast.LtE: "<=",
            ast.Gt: ">",
            ast.GtE: ">=",
        }
        left = self.generate(node.left)
        parts = [left]
        for op, comp in zip(node.ops, node.comparators):
            rust_op = cmp_map.get(type(op))
            if rust_op is None:
                self._warn(
                    f"Comparison operator {type(op).__name__}",
                    getattr(node, "lineno", 0),
                    "Operators like 'is', 'in', 'not in' are not directly translated.",
                )
                rust_op = f"/* {type(op).__name__} */"
            parts.append(f"{rust_op} {self.generate(comp)}")
        return f"({' '.join(parts)})"

    def _gen_boolop(self, node: ast.BoolOp) -> str:
        op = "&&" if isinstance(node.op, ast.And) else "||"
        parts = [self.generate(v) for v in node.values]
        return f"({f' {op} '.join(parts)})"

    def _gen_call(self, node: ast.Call) -> str:
        func_name = self.generate(node.func)
        args = [self.generate(a) for a in node.args]

        # Map common Python builtins to Rust equivalents
        builtin_map = {
            "print": "println!",
            "len": "",  # handled separately
            "abs": "",
            "max": "",
            "min": "",
            "round": "",
            "str": "format!",
            "int": "",
            "float": "",
            "range": "",
        }

        if func_name == "print":
            if args:
                format_parts = ["{}" for _ in args]
                format_str = " ".join(format_parts)
                return f'println!("{format_str}", {", ".join(args)})'
            return 'println!()'

        if func_name == "len":
            if args:
                return f"{args[0]}.len()"
            return "/* len() with no args */"

        if func_name == "abs":
            if args:
                return f"({args[0]}).abs()"
            return "/* abs() with no args */"

        if func_name in ("max", "min"):
            if len(args) == 2:
                method = "max" if func_name == "max" else "min"
                return f"({args[0]}).{method}({args[1]})"
            return f"/* {func_name}({', '.join(args)}) */"

        if func_name == "round":
            if args:
                return f"({args[0]}).round()"
            return "/* round() with no args */"

        if func_name == "range":
            if len(args) == 1:
                return f"0..{args[0]}"
            elif len(args) == 2:
                return f"{args[0]}..{args[1]}"
            elif len(args) == 3:
                return f"({args[0]}..{args[1]}).step_by({args[2]} as usize)"

        # Generic function call
        return f"{func_name}({', '.join(args)})"

    def _gen_list(self, node: ast.List) -> str:
        elts = [self.generate(e) for e in node.elts]
        return f"vec![{', '.join(elts)}]"

    def _gen_tuple(self, node: ast.Tuple) -> str:
        elts = [self.generate(e) for e in node.elts]
        return f"({', '.join(elts)})"

    def _gen_attribute(self, node: ast.Attribute) -> str:
        value = self.generate(node.value)
        return f"{value}.{node.attr}"

    def _gen_ifexp(self, node: ast.IfExp) -> str:
        test = self.generate(node.test)
        body = self.generate(node.body)
        orelse = self.generate(node.orelse)
        return f"if {test} {{ {body} }} else {{ {orelse} }}"

    def _gen_subscript(self, node: ast.Subscript) -> str:
        value = self.generate(node.value)
        sl = self.generate(node.slice)
        return f"{value}[{sl}]"


# ---------------------------------------------------------------------------
# Statement & function generator
# ---------------------------------------------------------------------------

class RustCodeGenerator:
    """
    Generates complete Rust source files from ParsedModule objects.

    Translates functions into Rust `fn` definitions and simple classes/dataclasses
    into Rust `struct` + `impl` blocks.
    """

    def __init__(self) -> None:
        self.warnings: list[UnsupportedConstructWarning] = []
        self._expr_gen: RustExprGenerator | None = None
        self._indent = 0

    def generate_module(self, module: ParsedModule) -> str:
        """Generate a complete Rust source file from a ParsedModule."""
        self._expr_gen = RustExprGenerator(file_path=module.file_path)
        self.warnings.clear()
        lines: list[str] = []

        # Header comment
        lines.append(f"// Auto-generated from: {module.file_path}")
        lines.append("// This file was generated by the experimental Python-to-Rust converter.")
        lines.append("// DO NOT edit manually – changes will be overwritten on regeneration.")
        lines.append("")

        # Generate constants
        for const in module.constants:
            rust_const = self._gen_constant(const)
            if rust_const:
                lines.append(rust_const)
                lines.append("")

        # Generate structs
        for struct in module.structs:
            lines.extend(self._gen_struct(struct))
            lines.append("")

        # Generate standalone functions
        for func in module.functions:
            lines.extend(self._gen_function(func))
            lines.append("")

        # Merge warnings from expression generator
        self.warnings.extend(self._expr_gen.warnings)

        return "\n".join(lines)

    def _gen_constant(self, const: ParsedConstant) -> str | None:
        """Generate a Rust constant from a ParsedConstant."""
        assert self._expr_gen is not None
        rust_type = map_python_type(const.type_hint)
        value = self._expr_gen.generate(const.value)

        # All-uppercase names become `const`, others become `static`
        if const.name.isupper():
            return f"const {const.name}: {rust_type} = {value};"
        else:
            # Use `let` inside a function, or `static` at module level
            return f"// Python variable: {const.name}\n// static {const.name}: {rust_type} = {value};"

    def _gen_struct(self, struct: ParsedStruct) -> list[str]:
        """Generate a Rust struct and impl block."""
        lines: list[str] = []

        if struct.docstring:
            for doc_line in struct.docstring.strip().splitlines():
                lines.append(f"/// {doc_line}")

        lines.append(f"#[derive(Debug, Clone)]")
        lines.append(f"pub struct {struct.name} {{")

        for fld in struct.fields:
            rust_type = map_python_type(fld.type_hint)
            lines.append(f"    pub {fld.name}: {rust_type},")

        lines.append("}")

        # Generate impl block for methods
        if struct.methods:
            lines.append("")
            lines.append(f"impl {struct.name} {{")

            for method in struct.methods:
                method_lines = self._gen_method(method, struct_fields=struct.fields)
                for ml in method_lines:
                    lines.append(f"    {ml}")
                lines.append("")

            lines.append("}")

        return lines

    def _gen_method(self, func: ParsedFunction, struct_fields: list[ParsedField] | None = None) -> list[str]:
        """Generate a Rust method (inside an impl block)."""
        assert self._expr_gen is not None
        lines: list[str] = []

        if func.docstring:
            for doc_line in func.docstring.strip().splitlines():
                lines.append(f"/// {doc_line}")

        # Build argument list
        arg_parts: list[str] = ["&self"] if func.is_method else []
        for arg in func.args:
            rust_type = map_python_type(arg.type_hint)
            arg_parts.append(f"{arg.name}: {rust_type}")

        ret = map_python_type(func.return_type) if func.return_type else "()"
        if ret == "()" and func.name == "__init__":
            # Constructor: generate a `new()` that returns Self { field: arg, ... }
            lines.append(f"pub fn new({', '.join(arg_parts[1:])}) -> Self {{")
            if struct_fields:
                # Build Self struct literal from field names matching constructor args
                arg_names = {a.name for a in func.args}
                field_inits: list[str] = []
                for fld in struct_fields:
                    if fld.name in arg_names:
                        field_inits.append(f"            {fld.name}")
                    elif fld.default_value is not None:
                        val = self._expr_gen.generate(fld.default_value)
                        field_inits.append(f"            {fld.name}: {val}")
                    else:
                        field_inits.append(f"            {fld.name}: Default::default()")
                lines.append("        Self {")
                for fi in field_inits:
                    lines.append(fi + ",")
                lines.append("        }")

                # Also generate any body statements that are NOT self.x = x assignments
                # (e.g. computed fields using function calls)
                for stmt in func.body:
                    if isinstance(stmt, ast.Assign):
                        # Skip simple self.field = value assignments
                        is_self_assign = all(
                            isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"
                            for t in stmt.targets
                        )
                        if is_self_assign:
                            continue
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                        continue
                    for sl in self._gen_stmt(stmt):
                        lines.append(f"    {sl}")
            else:
                body_lines = self._gen_body(func.body)
                for bl in body_lines:
                    lines.append(f"    {bl}")
        else:
            args_str = ", ".join(arg_parts)
            lines.append(f"pub fn {func.name}({args_str}) -> {ret} {{")
            body_lines = self._gen_body(func.body)
            for bl in body_lines:
                lines.append(f"    {bl}")

        lines.append("}")
        return lines

    def _gen_function(self, func: ParsedFunction) -> list[str]:
        """Generate a standalone Rust function."""
        assert self._expr_gen is not None
        lines: list[str] = []

        if func.docstring:
            for doc_line in func.docstring.strip().splitlines():
                lines.append(f"/// {doc_line}")

        arg_parts: list[str] = []
        for arg in func.args:
            rust_type = map_python_type(arg.type_hint)
            arg_parts.append(f"{arg.name}: {rust_type}")

        ret = map_python_type(func.return_type) if func.return_type else "()"
        args_str = ", ".join(arg_parts)
        lines.append(f"pub fn {func.name}({args_str}) -> {ret} {{")

        body_lines = self._gen_body(func.body)
        for bl in body_lines:
            lines.append(f"    {bl}")

        lines.append("}")
        return lines

    def _gen_body(self, stmts: list[ast.stmt]) -> list[str]:
        """Generate a list of Rust statements from a list of Python AST statements."""
        lines: list[str] = []
        for stmt in stmts:
            lines.extend(self._gen_stmt(stmt))
        return lines

    def _gen_stmt(self, stmt: ast.stmt) -> list[str]:
        """Generate Rust code for a single statement."""
        assert self._expr_gen is not None

        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                return ["return;"]
            val = self._expr_gen.generate(stmt.value)
            return [f"return {val};"]

        elif isinstance(stmt, ast.Assign):
            lines: list[str] = []
            val = self._expr_gen.generate(stmt.value)
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    lines.append(f"let {target.id} = {val};")
                elif isinstance(target, ast.Attribute):
                    tgt = self._expr_gen.generate(target)
                    lines.append(f"{tgt} = {val};")
                elif isinstance(target, ast.Subscript):
                    tgt = self._expr_gen.generate(target)
                    lines.append(f"{tgt} = {val};")
                else:
                    lines.append(f"/* unsupported assignment target */ // {ast.unparse(target)} = {val};")
            return lines

        elif isinstance(stmt, ast.AnnAssign):
            if stmt.target and isinstance(stmt.target, ast.Name):
                rust_type = map_python_type(ast.unparse(stmt.annotation) if stmt.annotation else None)
                if stmt.value:
                    val = self._expr_gen.generate(stmt.value)
                    return [f"let {stmt.target.id}: {rust_type} = {val};"]
                else:
                    return [f"let {stmt.target.id}: {rust_type};"]
            return [f"/* unsupported annotated assignment */"]

        elif isinstance(stmt, ast.AugAssign):
            op_map = {
                ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=", ast.Div: "/=",
                ast.Mod: "%=", ast.BitAnd: "&=", ast.BitOr: "|=", ast.BitXor: "^=",
            }
            op = op_map.get(type(stmt.op), "/* unknown_augop */=")
            target = self._expr_gen.generate(stmt.target)
            val = self._expr_gen.generate(stmt.value)
            return [f"{target} {op} {val};"]

        elif isinstance(stmt, ast.If):
            return self._gen_if(stmt)

        elif isinstance(stmt, ast.For):
            return self._gen_for(stmt)

        elif isinstance(stmt, ast.While):
            return self._gen_while(stmt)

        elif isinstance(stmt, ast.Pass):
            return ["// pass"]

        elif isinstance(stmt, ast.Expr):
            val = self._expr_gen.generate(stmt.value)
            return [f"{val};"]

        elif isinstance(stmt, ast.FunctionDef):
            # Nested function
            func_lines = self._gen_function(
                ParsedFunction(
                    name=stmt.name,
                    args=[ParsedArg(name=a.arg, type_hint=ast.unparse(a.annotation) if a.annotation else None) for a in stmt.args.args],
                    return_type=ast.unparse(stmt.returns) if stmt.returns else None,
                    body=stmt.body,
                    docstring=ast.get_docstring(stmt),
                    line=stmt.lineno,
                )
            )
            return func_lines

        else:
            return [f"/* unsupported statement: {type(stmt).__name__} */"]

    def _gen_if(self, node: ast.If) -> list[str]:
        """Generate Rust if/else if/else chain."""
        assert self._expr_gen is not None
        lines: list[str] = []
        test = self._expr_gen.generate(node.test)
        lines.append(f"if {test} {{")
        for s in node.body:
            for sl in self._gen_stmt(s):
                lines.append(f"    {sl}")
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                # elif chain
                elif_lines = self._gen_if(node.orelse[0])
                elif_lines[0] = "} else " + elif_lines[0]
                lines.extend(elif_lines)
                return lines
            else:
                lines.append("} else {")
                for s in node.orelse:
                    for sl in self._gen_stmt(s):
                        lines.append(f"    {sl}")
        lines.append("}")
        return lines

    def _gen_for(self, node: ast.For) -> list[str]:
        """Generate a Rust for loop."""
        assert self._expr_gen is not None
        lines: list[str] = []
        target = self._expr_gen.generate(node.target) if isinstance(node.target, ast.Name) else ast.unparse(node.target)
        iter_expr = self._expr_gen.generate(node.iter)
        lines.append(f"for {target} in {iter_expr} {{")
        for s in node.body:
            for sl in self._gen_stmt(s):
                lines.append(f"    {sl}")
        lines.append("}")
        return lines

    def _gen_while(self, node: ast.While) -> list[str]:
        """Generate a Rust while loop."""
        assert self._expr_gen is not None
        lines: list[str] = []
        test = self._expr_gen.generate(node.test)
        lines.append(f"while {test} {{")
        for s in node.body:
            for sl in self._gen_stmt(s):
                lines.append(f"    {sl}")
        lines.append("}")
        return lines
