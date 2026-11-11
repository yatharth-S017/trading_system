"""Unit tests for ATM strike selection, Call/Put pairing, and straddle validation."""

from datetime import datetime, timedelta, timezone
import pytest
from nautilus_trader.model.enums import OptionKind

from src.btc_straddle.instruments import OptionMetadata
from src.btc_straddle.straddle import (
    ATMStraddle,
    pair_atm_straddle,
    select_atm_strike,
    select_target_expiry,
    validate_straddle_pair,
)


def make_dummy_option(
    symbol: str,
    underlying: str,
    option_kind: str,
    strike: float,
    expiry_dt: datetime,
) -> OptionMetadata:
    """Helper to create OptionMetadata for tests."""
    return OptionMetadata(
        instrument_id=f"{symbol}-OPTION.BYBIT",
        raw_symbol=symbol,
        underlying=underlying,
        option_kind=option_kind,
        strike_price=strike,
        expiration_ns=int(expiry_dt.timestamp() * 1e9),
        expiration_dt=expiry_dt,
        price_precision=1,
        size_precision=3,
    )


def test_select_atm_strike_exact():
    """Test 1: Exact ATM match."""
    spot = 100000.0
    strikes = [99000.0, 100000.0, 101000.0]
    assert select_atm_strike(spot, strikes) == 100000.0


def test_select_atm_strike_nearest_lower():
    """Test 2: Spot closer to lower strike."""
    spot = 100250.0
    strikes = [99000.0, 100000.0, 101000.0]
    assert select_atm_strike(spot, strikes) == 100000.0


def test_select_atm_strike_nearest_higher():
    """Test 3: Spot closer to higher strike."""
    spot = 100750.0
    strikes = [99000.0, 100000.0, 101000.0]
    assert select_atm_strike(spot, strikes) == 101000.0


def test_select_atm_strike_tie_breaking():
    """Test 4: Equidistant spot (tie-breaking rule)."""
    spot = 100500.0
    strikes = [100000.0, 101000.0]
    # Default tie-breaker is "lower"
    assert select_atm_strike(spot, strikes, tie_breaker="lower") == 100000.0
    # "higher" selects the larger strike
    assert select_atm_strike(spot, strikes, tie_breaker="higher") == 101000.0


def test_select_atm_strike_empty_strikes():
    """Test 5: Empty strikes sequence raises ValueError."""
    with pytest.raises(ValueError, match="available_strikes list cannot be empty"):
        select_atm_strike(100000.0, [])


def test_select_atm_strike_invalid_spot():
    """Test 9a: Zero or negative spot price raises ValueError."""
    with pytest.raises(ValueError, match="Invalid spot price"):
        select_atm_strike(0.0, [100000.0])
    with pytest.raises(ValueError, match="Invalid spot price"):
        select_atm_strike(-500.0, [100000.0])


def test_expired_contracts_excluded():
    """Test 6: Expired contracts are excluded from target expiry selection."""
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    past_expiry = now - timedelta(days=1)
    future_expiry = now + timedelta(days=1)

    opt_past_call = make_dummy_option("BTC-PAST-C", "BTC", "CALL", 100000.0, past_expiry)
    opt_past_put = make_dummy_option("BTC-PAST-P", "BTC", "PUT", 100000.0, past_expiry)
    opt_future_call = make_dummy_option("BTC-FUT-C", "BTC", "CALL", 100000.0, future_expiry)
    opt_future_put = make_dummy_option("BTC-FUT-P", "BTC", "PUT", 100000.0, future_expiry)

    options = [opt_past_call, opt_past_put, opt_future_call, opt_future_put]
    selected_exp = select_target_expiry(options, current_time=now)
    assert selected_exp == future_expiry


def test_call_put_pairing():
    """Test 7: Correct Call + Put pair identified at ATM strike and target expiry."""
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    expiry1 = now + timedelta(days=1)
    expiry2 = now + timedelta(days=2)

    options = [
        make_dummy_option("BTC-E1-98000-C", "BTC", "CALL", 98000.0, expiry1),
        make_dummy_option("BTC-E1-98000-P", "BTC", "PUT", 98000.0, expiry1),
        make_dummy_option("BTC-E1-100000-C", "BTC", "CALL", 100000.0, expiry1),
        make_dummy_option("BTC-E1-100000-P", "BTC", "PUT", 100000.0, expiry1),
        make_dummy_option("BTC-E1-102000-C", "BTC", "CALL", 102000.0, expiry1),
        make_dummy_option("BTC-E1-102000-P", "BTC", "PUT", 102000.0, expiry1),
        make_dummy_option("BTC-E2-100000-C", "BTC", "CALL", 100000.0, expiry2),
        make_dummy_option("BTC-E2-100000-P", "BTC", "PUT", 100000.0, expiry2),
    ]

    spot = 100100.0  # Closer to 100000
    straddle = pair_atm_straddle(spot_price=spot, options=options, current_time=now)

    assert straddle.spot_price == spot
    assert straddle.expiry == expiry1
    assert straddle.strike == 100000.0
    assert straddle.call_instrument.is_call is True
    assert straddle.put_instrument.is_put is True
    assert straddle.call_instrument.strike_price == 100000.0
    assert straddle.put_instrument.strike_price == 100000.0
    assert straddle.call_instrument.expiration_dt == expiry1
    assert straddle.put_instrument.expiration_dt == expiry1


def test_reject_mismatched_pair():
    """Test 8: Reject Call and Put with mismatched strike, expiry, or underlying."""
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    expiry1 = now + timedelta(days=1)
    expiry2 = now + timedelta(days=2)

    call = make_dummy_option("BTC-CALL-100000", "BTC", "CALL", 100000.0, expiry1)
    put_diff_strike = make_dummy_option("BTC-PUT-99000", "BTC", "PUT", 99000.0, expiry1)
    put_diff_expiry = make_dummy_option("BTC-PUT-100000", "BTC", "PUT", 100000.0, expiry2)
    put_diff_underlying = make_dummy_option("ETH-PUT-100000", "ETH", "PUT", 100000.0, expiry1)

    # Strike mismatch
    with pytest.raises(ValueError, match="strike mismatch"):
        validate_straddle_pair(call, put_diff_strike, spot_price=100000.0, current_time=now)

    # Expiry mismatch
    with pytest.raises(ValueError, match="expiration mismatch"):
        validate_straddle_pair(call, put_diff_expiry, spot_price=100000.0, current_time=now)

    # Underlying mismatch
    with pytest.raises(ValueError, match="underlying must be BTC"):
        validate_straddle_pair(call, put_diff_underlying, spot_price=100000.0, current_time=now)


def test_reject_invalid_spot_in_pair():
    """Test 9b: Zero or negative spot rejected in pair_atm_straddle."""
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    expiry = now + timedelta(days=1)
    options = [
        make_dummy_option("BTC-C", "BTC", "CALL", 100000.0, expiry),
        make_dummy_option("BTC-P", "BTC", "PUT", 100000.0, expiry),
    ]
    with pytest.raises(ValueError, match="Invalid spot price"):
        pair_atm_straddle(spot_price=-10.0, options=options, current_time=now)

