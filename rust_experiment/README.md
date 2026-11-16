# BTC Straddle – Experimental Rust Port

This directory contains **auto-generated Rust code** produced by the
experimental Python → Rust converter located at `tools/python_to_rust/`.

> **⚠️ EXPERIMENTAL** — This is NOT a production trading system. It does NOT
> replace NautilusTrader, Bybit integration, or any part of the live Python
> trading pipeline.

## What's Here

```
rust_experiment/
├── Cargo.toml            # Auto-generated Cargo project manifest
├── src/
│   ├── main.rs           # Auto-generated entry-point
│   └── sample_input.rs   # Converted from tools/python_to_rust/sample_input.py
├── target/               # Build artifacts (created by cargo build)
└── README.md             # This file
```

## Building

```bash
# From the project root:
python tools/python_to_rust/cli.py build
```

Or directly with Cargo:
```bash
cd rust_experiment
cargo build
```

## Running

```bash
python tools/python_to_rust/cli.py run
```

Or directly:
```bash
cd rust_experiment
cargo run
```

## Regenerating

All `.rs` files under `src/` (except `main.rs`) are overwritten each time you
run the converter. Do not edit them manually.

## Purpose

This exists solely to experiment with transpiling pure Python risk-management
logic into Rust for potential future benchmarking and comparison.
