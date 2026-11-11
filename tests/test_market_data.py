"""Unit tests for OptionMarketDataStrategy and quote handling."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from src.btc_straddle.instruments import OptionMetadata
from src.btc_straddle.market_data import (
    OptionMarketDataConfig,
    OptionMarketDataStrategy,
)


def make_test_quote_tick(instrument_str: str, bid: str, ask: str) -> QuoteTick:
    """Helper to create valid QuoteTick objects for unit tests."""
    return QuoteTick(
        instrument_id=InstrumentId.from_str(instrument_str),
        bid_price=Price.from_str(bid),
        ask_price=Price.from_str(ask),
        bid_size=Quantity.from_str("1.000"),
        ask_size=Quantity.from_str("1.500"),
        ts_event=1700000000000000000,
        ts_init=1700000000000000000,
    )


def test_market_data_config():
    """Verify default and customized OptionMarketDataConfig."""
    default_cfg = OptionMarketDataConfig()
    assert default_cfg.underlying_instrument_id == "BTCUSDT-LINEAR.BYBIT"
    assert default_cfg.tie_breaker == "lower"
    assert default_cfg.max_quote_logs == 10
    assert default_cfg.target_expiry_iso is None

    custom_cfg = OptionMarketDataConfig(
        underlying_instrument_id="BTCUSDT-SPOT.BYBIT",
        tie_breaker="higher",
        max_quote_logs=5,
        target_expiry_iso="2026-11-06T08:00:00Z",
    )
    assert custom_cfg.underlying_instrument_id == "BTCUSDT-SPOT.BYBIT"
    assert custom_cfg.tie_breaker == "higher"
    assert custom_cfg.max_quote_logs == 5
    assert custom_cfg.target_expiry_iso == "2026-11-06T08:00:00Z"


def test_quote_tick_handling_and_straddle_trigger():
    """Verify that receiving the reference quote triggers ATM pairing and subsequent quote tracking."""
    cfg = OptionMarketDataConfig(
        underlying_instrument_id="BTCUSDT-LINEAR.BYBIT",
        tie_breaker="lower",
        max_quote_logs=2,
    )
    strat = OptionMarketDataStrategy(config=cfg)

    # Provide synthetic discovered options
    now = datetime.now(timezone.utc)
    future_expiry = now + timedelta(days=2)
    strat.discovered_options = [
        OptionMetadata(
            instrument_id="BTC-CALL-100000-OPTION.BYBIT",
            raw_symbol="BTC-CALL-100000",
            underlying="BTC",
            option_kind="CALL",
            strike_price=100000.0,
            expiration_ns=int(future_expiry.timestamp() * 1e9),
            expiration_dt=future_expiry,
            price_precision=1,
            size_precision=3,
        ),
        OptionMetadata(
            instrument_id="BTC-PUT-100000-OPTION.BYBIT",
            raw_symbol="BTC-PUT-100000",
            underlying="BTC",
            option_kind="PUT",
            strike_price=100000.0,
            expiration_ns=int(future_expiry.timestamp() * 1e9),
            expiration_dt=future_expiry,
            price_precision=1,
            size_precision=3,
        ),
    ]

    # Mock subscription methods to avoid uninitialized trader bus errors in standalone test
    strat.subscribe_quote_ticks = MagicMock()
    strat.unsubscribe_quote_ticks = MagicMock()

    # 1. Deliver underlying reference quote
    ref_tick = make_test_quote_tick("BTCUSDT-LINEAR.BYBIT", "99990.0", "100010.0")
    strat.on_quote_tick(ref_tick)

    assert strat.reference_price == 100000.0
    assert strat.straddle_selected is True
    assert strat.selected_straddle is not None
    assert strat.selected_straddle.strike == 100000.0
    assert strat.selected_straddle.call_instrument.instrument_id == "BTC-CALL-100000-OPTION.BYBIT"
    assert strat.selected_straddle.put_instrument.instrument_id == "BTC-PUT-100000-OPTION.BYBIT"

    # Verify that subscribe_quote_ticks was called for both legs
    assert strat.subscribe_quote_ticks.call_count == 2

    # 2. Deliver straddle option quotes
    call_tick = make_test_quote_tick("BTC-CALL-100000-OPTION.BYBIT", "1200.0", "1210.0")
    strat.on_quote_tick(call_tick)

    assert strat.straddle_quotes_received == 1
    assert "BTC-CALL-100000-OPTION.BYBIT" in strat.latest_quotes
    assert strat.latest_quotes["BTC-CALL-100000-OPTION.BYBIT"].bid_price == Price.from_str("1200.0")
