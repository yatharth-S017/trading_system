# Python → Rust Experimental Code Generator

An **experimental** AST-based transpiler that reads selected Python source files
and generates equivalent Rust code for the constructs that can be safely converted.

> **⚠️ This is an experiment.** The generated Rust code does NOT replace the
> existing Python trading system. It does NOT rewrite NautilusTrader or Bybit
> integration. The original Python project remains completely unchanged.

## What This Tool Does

1. Parses Python source files using Python's built-in `ast` module.
2. Translates supported Python constructs into valid Rust code.
3. Creates a standalone Cargo project under `rust_experiment/`.
4. Compiles the generated Rust code with `cargo build`.
5. Produces a runnable Rust executable.
6. Reports exactly which constructs were converted and which were not.

## What This Tool Does NOT Do

- Does **not** modify any existing Python files.
- Does **not** replace the NautilusTrader execution system.
- Does **not** rewrite Bybit API integration in Rust.
- Does **not** convert WebSocket infrastructure, asyncio, or FastAPI.
- Does **not** claim the generated Rust is faster or production-ready.
- Does **not** silently generate incorrect code — unsupported constructs
  produce clear warnings.

## Supported Python Constructs

| Python                    | Rust                         |
|--------------------------|------------------------------|
| `def function()`         | `fn function()`              |
| `int`                    | `i64`                        |
| `float`                  | `f64`                        |
| `bool`                   | `bool`                       |
| `str`                    | `String`                     |
| `list[T]`                | `Vec<T>`                     |
| `float \| None`          | `Option<f64>`                |
| `+`, `-`, `*`, `/`, `%`  | `+`, `-`, `*`, `/`, `%`      |
| `==`, `!=`, `<`, `>`, `>=`, `<=` | Same operators        |
| `and`, `or`, `not`       | `&&`, `\|\|`, `!`            |
| `if / elif / else`       | `if / else if / else`        |
| `for x in range(n)`      | `for x in 0..n`              |
| `while condition`        | `while condition`            |
| `return value`           | `return value;`              |
| Variable assignment      | `let x = value;`             |
| Annotated assignment     | `let x: Type = value;`       |
| `x += 1`                 | `x += 1;`                    |
| Simple class             | `struct` + `impl`            |
| Constants (UPPERCASE)    | `const NAME: Type = value;`  |
| Docstrings               | `///` doc comments            |

## Unsupported Constructs

Constructs that are **not** converted produce clear warnings:

```
WARNING:
Unsupported Python construct:
    async def fetch()

File:
    src/btc_straddle/example.py

Line:
    42

This code was NOT converted.
```

Known unsupported constructs include:
- `async def` / `await`
- Decorators (except `@staticmethod`, `@property`)
- Complex inheritance
- Context managers (`with`)
- Exception handling (`try/except`)
- Generators / `yield`
- List comprehensions
- Lambda functions
- `import` statements (silently skipped, not an error)

## Usage

### Generate, check, and build

```bash
python tools/python_to_rust/cli.py build
```

### Generate, build, and run

```bash
python tools/python_to_rust/cli.py run
```

### Check only (no full build)

```bash
python tools/python_to_rust/cli.py check
```

### Clean build artifacts

```bash
python tools/python_to_rust/cli.py clean
```

### Convert specific files

```bash
python tools/python_to_rust/cli.py build --source-dir src/btc_straddle --files straddle.py
```

## Architecture

```
tools/python_to_rust/
├── __init__.py          # Package exports
├── ast_parser.py        # Python AST parser → intermediate representation
├── rust_generator.py    # IR → Rust source code generation
├── project_builder.py   # Cargo project assembly and build invocation
├── converter.py         # End-to-end pipeline orchestrator
├── cli.py               # CLI entry-point
├── sample_input.py      # Self-contained Python module for conversion demo
├── tests/
│   ├── __init__.py
│   └── test_converter.py  # 35 unit tests for the converter
└── README.md            # This file
```

## Pipeline

```
Python source file
       │
       ▼
  ast.parse()  (Python's standard AST module)
       │
       ▼
  ParsedModule  (intermediate representation)
       │
       ▼
  RustCodeGenerator  (IR → .rs source)
       │
       ▼
  CargoProjectBuilder  (write .rs + Cargo.toml)
       │
       ▼
  cargo check → cargo build
       │
       ▼
  Rust executable
```

## Why This Exists

The purpose is to experiment with selectively moving pure Python logic into
Rust and later benchmarking the two implementations. This is a research tool,
not a production transpiler.

The generated Rust code is separate from and does not interfere with
NautilusTrader's existing Rust core (which powers the trading engine
internally).

## Running Converter Tests

```bash
pytest tools/python_to_rust/tests/test_converter.py -v
```

These tests are independent from the existing NautilusTrader project tests.
