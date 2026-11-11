"""Unit tests for BTC option instrument discovery and metadata parsing."""

from datetime import datetime, timezone
import pytest
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.objects import Currency, Price, Quantity

from src.btc_straddle.instruments import (
    OptionMetadata,
    discover_btc_options,
    extract_option_metadata,
    group_by_expiry,
    is_btc_option,
)


def make_test_crypto_option(
    symbol_str: str,
    underlying_str: str,
    option_kind: OptionKind,
    strike_str: str,
    expiration_ns: int,
) -> CryptoOption:
    """Helper to instantiate valid CryptoOption instances for tests."""
    return CryptoOption(
        instrument_id=InstrumentId.from_str(f"{symbol_str}-OPTION.BYBIT"),
        raw_symbol=Symbol(symbol_str),
        underlying=Currency.from_str(underlying_str),
        quote_currency=Currency.from_str("USDT"),
        settlement_currency=Currency.from_str("USDT"),
        is_inverse=False,
        option_kind=option_kind,
        strike_price=Price.from_str(strike_str),
        activation_ns=1700000000000000000,
        expiration_ns=expiration_ns,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.5"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=1700000000000000000,
        ts_init=1700000000000000000,
    )


def test_is_btc_option_true():
    """Verify that a BTC CryptoOption is recognized as a BTC option."""
    opt = make_test_crypto_option(
        symbol_str="BTC-28MAR25-80000-C-USDT",
        underlying_str="BTC",
        option_kind=OptionKind.CALL,
        strike_str="80000",
        expiration_ns=1743148800000000000,
    )
    assert is_btc_option(opt) is True


def test_is_btc_option_false_for_eth():
    """Verify that an ETH CryptoOption is excluded."""
    opt = make_test_crypto_option(
        symbol_str="ETH-28MAR25-3000-C-USDT",
        underlying_str="ETH",
        option_kind=OptionKind.CALL,
        strike_str="3000",
        expiration_ns=1743148800000000000,
    )
    assert is_btc_option(opt) is False


def test_is_btc_option_false_for_non_option():
    """Verify that an arbitrary non-option object is excluded."""
    class DummySpot:
        id = "BTCUSDT.BYBIT"
        underlying = "BTC"

    assert is_btc_option(DummySpot()) is False


def test_extract_option_metadata():
    """Verify accurate extraction of Call option metadata."""
    exp_ns = 1743148800000000000
    opt = make_test_crypto_option(
        symbol_str="BTC-28MAR25-80000-C-USDT",
        underlying_str="BTC",
        option_kind=OptionKind.CALL,
        strike_str="80000",
        expiration_ns=exp_ns,
    )
    metadata = extract_option_metadata(opt)

    assert metadata.instrument_id == "BTC-28MAR25-80000-C-USDT-OPTION.BYBIT"
    assert metadata.raw_symbol == "BTC-28MAR25-80000-C-USDT"
    assert metadata.underlying == "BTC"
    assert metadata.option_kind == "CALL"
    assert metadata.is_call is True
    assert metadata.is_put is False
    assert metadata.strike_price == 80000.0
    assert metadata.expiration_ns == exp_ns
    assert metadata.expiration_dt == datetime.fromtimestamp(exp_ns / 1e9, tz=timezone.utc)


def test_extract_put_option_metadata():
    """Verify accurate extraction of Put option metadata."""
    exp_ns = 1743148800000000000
    opt = make_test_crypto_option(
        symbol_str="BTC-28MAR25-70000-P-USDT",
        underlying_str="BTC",
        option_kind=OptionKind.PUT,
        strike_str="70000",
        expiration_ns=exp_ns,
    )
    metadata = extract_option_metadata(opt)

    assert metadata.option_kind == "PUT"
    assert metadata.is_call is False
    assert metadata.is_put is True
    assert metadata.strike_price == 70000.0


def test_discover_btc_options_filtering_and_sorting():
    """Verify that discover_btc_options filters and sorts options properly."""
    exp1 = 1740000000000000000
    exp2 = 1750000000000000000

    btc_call_high = make_test_crypto_option("BTC-EXP1-90000-C-USDT", "BTC", OptionKind.CALL, "90000", exp1)
    btc_put_low = make_test_crypto_option("BTC-EXP1-70000-P-USDT", "BTC", OptionKind.PUT, "70000", exp1)
    btc_call_exp2 = make_test_crypto_option("BTC-EXP2-80000-C-USDT", "BTC", OptionKind.CALL, "80000", exp2)
    eth_opt = make_test_crypto_option("ETH-EXP1-3000-C-USDT", "ETH", OptionKind.CALL, "3000", exp1)

    all_instruments = [btc_call_high, eth_opt, btc_call_exp2, btc_put_low]
    discovered = discover_btc_options(all_instruments)

    # ETH must be filtered out
    assert len(discovered) == 3
    # Sorted by expiry, then strike
    assert discovered[0].strike_price == 70000.0
    assert discovered[0].expiration_ns == exp1
    assert discovered[1].strike_price == 90000.0
    assert discovered[1].expiration_ns == exp1
    assert discovered[2].strike_price == 80000.0
    assert discovered[2].expiration_ns == exp2


def test_group_by_expiry():
    """Verify grouping option metadata by expiration datetime."""
    exp1 = 1740000000000000000
    exp2 = 1750000000000000000

    opt1 = extract_option_metadata(make_test_crypto_option("BTC-1", "BTC", OptionKind.CALL, "80000", exp1))
    opt2 = extract_option_metadata(make_test_crypto_option("BTC-2", "BTC", OptionKind.PUT, "80000", exp1))
    opt3 = extract_option_metadata(make_test_crypto_option("BTC-3", "BTC", OptionKind.CALL, "85000", exp2))

    grouped = group_by_expiry([opt1, opt2, opt3])
    assert len(grouped) == 2
    assert len(grouped[opt1.expiration_dt]) == 2
    assert len(grouped[opt3.expiration_dt]) == 1

