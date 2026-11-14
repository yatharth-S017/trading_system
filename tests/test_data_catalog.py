"""
Unit tests for NautilusTrader ParquetDataCatalog integration.

All tests run completely offline and locally without any dependency on
the Bybit Testnet API.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.btc_straddle.data_catalog import StraddleDataCatalog


def make_synthetic_quote_tick(
    instrument_id_str: str,
    bid_px: str = "67000.50",
    ask_px: str = "67001.00",
    bid_qty: str = "1.5",
    ask_qty: str = "2.0",
    ts: int = 1_700_000_000_000_000_000,
) -> QuoteTick:
    """Helper to construct a valid NautilusTrader QuoteTick with exact values."""
    return QuoteTick(
        instrument_id=InstrumentId.from_str(instrument_id_str),
        bid_price=Price.from_str(bid_px),
        ask_price=Price.from_str(ask_px),
        bid_size=Quantity.from_str(bid_qty),
        ask_size=Quantity.from_str(ask_qty),
        ts_event=ts,
        ts_init=ts,
    )


def test_catalog_initialization_and_directory_creation():
    """Verify that StraddleDataCatalog auto-creates its target folder and initializes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        catalog_path = Path(tmp_dir) / "nested" / "catalog"
        assert not catalog_path.exists()

        catalog = StraddleDataCatalog(catalog_path=catalog_path, batch_size=10)

        assert catalog_path.exists()
        assert catalog.catalog_path == catalog_path.resolve()
        assert catalog.buffer_size == 0


def test_record_and_read_quote_ticks():
    """Verify writing, buffering, flushing, and reading back QuoteTicks."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        catalog = StraddleDataCatalog(catalog_path=tmp_dir, batch_size=2)

        underlying_id = "BTCUSDT-LINEAR.BYBIT"
        call_id = "BTC-15NOV26-67500-C-USDT-OPTION.BYBIT"

        tick1 = make_synthetic_quote_tick(
            underlying_id, bid_px="67500.00", ask_px="67501.00", ts=1_700_000_000_000_000_000
        )
        tick2 = make_synthetic_quote_tick(
            underlying_id, bid_px="67502.00", ask_px="67503.00", ts=1_700_000_001_000_000_000
        )
        tick3 = make_synthetic_quote_tick(
            call_id, bid_px="150.00", ask_px="155.00", ts=1_700_000_002_000_000_000
        )

        # First quote -> buffered (batch_size=2)
        catalog.record_quote(tick1)
        assert catalog.buffer_size == 1

        # Second quote -> triggers auto-flush of 2 quotes
        catalog.record_quote(tick2)
        assert catalog.buffer_size == 0

        # Third quote -> buffered
        catalog.record_quote(tick3)
        assert catalog.buffer_size == 1

        # Manual flush flushes the remaining quote
        flushed_count = catalog.flush()
        assert flushed_count == 1
        assert catalog.buffer_size == 0

        # 1. Read back all quotes
        all_quotes = catalog.read_quotes()
        assert len(all_quotes) == 3

        # 2. Query only underlying quotes
        underlying_quotes = catalog.read_quotes(instrument_ids=[underlying_id])
        assert len(underlying_quotes) == 2
        assert underlying_quotes[0].instrument_id == InstrumentId.from_str(underlying_id)
        assert underlying_quotes[0].bid_price == Price.from_str("67500.00")
        assert underlying_quotes[0].ask_price == Price.from_str("67501.00")
        assert underlying_quotes[0].ts_event == 1_700_000_000_000_000_000
        assert underlying_quotes[1].bid_price == Price.from_str("67502.00")

        # 3. Query only ATM Call option quotes
        call_quotes = catalog.read_quotes(instrument_ids=[call_id])
        assert len(call_quotes) == 1
        assert call_quotes[0].instrument_id == InstrumentId.from_str(call_id)
        assert call_quotes[0].bid_price == Price.from_str("150.00")
        assert call_quotes[0].ask_price == Price.from_str("155.00")


def test_record_and_read_instruments():
    """Verify persisting and reading back NautilusTrader Instrument definitions."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        catalog = StraddleDataCatalog(catalog_path=tmp_dir)

        inst1 = TestInstrumentProvider.btcusdt_binance()
        inst2 = TestInstrumentProvider.ethusdt_binance()

        # Persist instruments
        catalog.record_instruments([inst1, inst2])

        # Verify reading back instruments
        all_instruments = catalog.read_instruments()
        assert len(all_instruments) == 2
        stored_ids = {str(i.id) for i in all_instruments}
        assert str(inst1.id) in stored_ids
        assert str(inst2.id) in stored_ids

        # Query single instrument by ID
        single = catalog.read_instruments(instrument_ids=[str(inst1.id)])
        assert len(single) == 1
        assert single[0].id == inst1.id

        # Verify deduplication: recording again does not error or duplicate
        catalog.record_instruments([inst1])
        assert len(catalog.read_instruments()) == 2


def test_physical_parquet_files_on_disk():
    """Verify that physical .parquet files are written into the expected catalog structure."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        catalog = StraddleDataCatalog(catalog_path=tmp_dir)

        inst = TestInstrumentProvider.btcusdt_binance()
        catalog.record_instruments([inst])

        tick = make_synthetic_quote_tick(str(inst.id))
        catalog.record_quote(tick)
        catalog.flush()

        # Find all generated parquet files
        parquet_files = list(Path(tmp_dir).rglob("*.parquet"))
        assert len(parquet_files) >= 2, f"Expected at least 2 parquet files, found: {parquet_files}"

        file_names = [p.name for p in parquet_files]
        assert any("quote_tick" in str(p) for p in parquet_files)


def test_catalog_idempotency_and_reopen():
    """Verify that an existing catalog can be re-opened and queried safely."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Session 1: Write data
        catalog1 = StraddleDataCatalog(catalog_path=tmp_dir)
        tick = make_synthetic_quote_tick("BTCUSDT-LINEAR.BYBIT", bid_px="68000.00")
        catalog1.record_quote(tick)
        catalog1.flush()

        # Session 2: Re-open existing directory
        catalog2 = StraddleDataCatalog(catalog_path=tmp_dir)
        quotes = catalog2.read_quotes(["BTCUSDT-LINEAR.BYBIT"])
        assert len(quotes) == 1
        assert quotes[0].bid_price == Price.from_str("68000.00")


def test_empty_catalog_and_nonexistent_instrument():
    """Verify safe behavior when reading from an empty catalog or querying unknown IDs."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        catalog = StraddleDataCatalog(catalog_path=tmp_dir)

        # Empty catalog read returns empty list without error
        assert catalog.read_quotes() == []
        assert catalog.read_instruments() == []

        # Querying non-existent instrument returns empty list
        assert catalog.read_quotes(["NON-EXISTENT.VENUE"]) == []
        assert catalog.read_instruments(["NON-EXISTENT.VENUE"]) == []


def test_flush_on_empty_buffer():
    """Verify flush on empty buffer returns 0 and does not error."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        catalog = StraddleDataCatalog(catalog_path=tmp_dir)
        assert catalog.flush() == 0
