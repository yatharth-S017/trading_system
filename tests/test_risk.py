"""
Unit tests for Level 2 – Automated Risk Management for BTC Short Straddle.

Covers all 15 required risk management scenarios completely offline without
any dependency on Bybit Testnet API.
"""

from __future__ import annotations

import pytest
from src.btc_straddle.execution import OrderStatus

from src.btc_straddle.risk import (
    ExitReason,
    RiskConfig,
    RiskState,
    StraddleRiskManager,
)


@pytest.fixture
def risk_manager() -> StraddleRiskManager:
    """Fixture providing a standard StraddleRiskManager with equal 1.0 quantities and 500.0 entry prices."""
    return StraddleRiskManager(
        call_instrument_id="BTC-15NOV26-68000-C-USDT-OPTION.BYBIT",
        put_instrument_id="BTC-15NOV26-68000-P-USDT-OPTION.BYBIT",
        call_qty=1.0,
        put_qty=1.0,
        call_entry_price=500.0,
        put_entry_price=500.0,
        config=RiskConfig(stop_loss_pct=0.01, take_profit_pct=0.02),
    )


def test_1_combined_premium_calculation(risk_manager: StraddleRiskManager):
    """
    Test 1: Given Call ASK = 500, Put ASK = 500, Call qty = 1, Put qty = 1.
    Expected: Combined Buyback Premium = 1000.
    """
    risk_manager.update_quote("BTC-15NOV26-68000-C-USDT-OPTION.BYBIT", 500.0)
    risk_manager.update_quote("BTC-15NOV26-68000-P-USDT-OPTION.BYBIT", 500.0)

    combined = risk_manager.calculate_combined_buyback_premium()
    assert combined == 1000.0


def test_2_stop_loss_threshold(risk_manager: StraddleRiskManager):
    """
    Test 2: Given Entry combined premium = 1000.
    Expected: Stop-loss threshold (+1%) = 1010.
    """
    assert risk_manager.combined_entry_premium == 1000.0
    assert risk_manager.stop_loss_threshold == 1010.0


def test_3_take_profit_threshold(risk_manager: StraddleRiskManager):
    """
    Test 3: Given Entry combined premium = 1000.
    Expected: Take-profit threshold (-2% buyback cost) = 980.
    """
    assert risk_manager.combined_entry_premium == 1000.0
    assert risk_manager.take_profit_threshold == 980.0


def test_4_stop_loss_trigger(risk_manager: StraddleRiskManager):
    """
    Test 4: When Combined Buyback Premium >= 1010, Stop-Loss triggers.
    Also test values above the threshold (e.g. 1025).
    """
    # Exactly at threshold: Call 505 + Put 505 = 1010
    risk_manager.update_quote("BTC-15NOV26-68000-C-USDT-OPTION.BYBIT", 505.0)
    risk_manager.update_quote("BTC-15NOV26-68000-P-USDT-OPTION.BYBIT", 505.0)

    exit_reason = risk_manager.evaluate_risk()
    assert exit_reason == ExitReason.STOP_LOSS
    assert risk_manager.state == RiskState.EXIT_IN_PROGRESS
    assert risk_manager.exit_reason == ExitReason.STOP_LOSS

    # Reset manager and test strictly above threshold: Call 515 + Put 510 = 1025
    rm2 = StraddleRiskManager(
        call_instrument_id="C", put_instrument_id="P",
        call_qty=1.0, put_qty=1.0, call_entry_price=500.0, put_entry_price=500.0
    )
    rm2.update_quote("C", 515.0)
    rm2.update_quote("P", 510.0)
    assert rm2.evaluate_risk() == ExitReason.STOP_LOSS


def test_5_take_profit_trigger(risk_manager: StraddleRiskManager):
    """
    Test 5: When Combined Buyback Premium <= 980, Take-Profit triggers.
    Also test values below the threshold (e.g. 950).
    """
    # Exactly at threshold: Call 490 + Put 490 = 980
    risk_manager.update_quote("BTC-15NOV26-68000-C-USDT-OPTION.BYBIT", 490.0)
    risk_manager.update_quote("BTC-15NOV26-68000-P-USDT-OPTION.BYBIT", 490.0)

    exit_reason = risk_manager.evaluate_risk()
    assert exit_reason == ExitReason.TAKE_PROFIT
    assert risk_manager.state == RiskState.EXIT_IN_PROGRESS
    assert risk_manager.exit_reason == ExitReason.TAKE_PROFIT

    # Reset manager and test strictly below threshold: Call 470 + Put 480 = 950
    rm2 = StraddleRiskManager(
        call_instrument_id="C", put_instrument_id="P",
        call_qty=1.0, put_qty=1.0, call_entry_price=500.0, put_entry_price=500.0
    )
    rm2.update_quote("C", 470.0)
    rm2.update_quote("P", 480.0)
    assert rm2.evaluate_risk() == ExitReason.TAKE_PROFIT


def test_6_no_exit_inside_the_range(risk_manager: StraddleRiskManager):
    """
    Test 6: When Entry = 1000, Current Buyback = 1005 (within 980 < value < 1010).
    Expected: No exit triggers; strategy remains in MONITORING state.
    """
    risk_manager.update_quote("BTC-15NOV26-68000-C-USDT-OPTION.BYBIT", 502.50)
    risk_manager.update_quote("BTC-15NOV26-68000-P-USDT-OPTION.BYBIT", 502.50)

    exit_reason = risk_manager.evaluate_risk()
    assert exit_reason is None
    assert risk_manager.state == RiskState.MONITORING
    assert risk_manager.exit_reason == ExitReason.NONE


def test_7_missing_call_quote(risk_manager: StraddleRiskManager):
    """
    Test 7: When Call ASK is unavailable/None, combined value is not calculated
    and no exit triggers.
    """
    risk_manager.update_quote("BTC-15NOV26-68000-P-USDT-OPTION.BYBIT", 505.0)
    # Call ASK is not updated (remains None)

    assert risk_manager.calculate_combined_buyback_premium() is None
    assert risk_manager.evaluate_risk() is None
    assert risk_manager.state == RiskState.MONITORING


def test_8_missing_put_quote(risk_manager: StraddleRiskManager):
    """
    Test 8: When Put ASK is unavailable/None, combined value is not calculated
    and no exit triggers.
    """
    risk_manager.update_quote("BTC-15NOV26-68000-C-USDT-OPTION.BYBIT", 505.0)
    # Put ASK is not updated (remains None)

    assert risk_manager.calculate_combined_buyback_premium() is None
    assert risk_manager.evaluate_risk() is None
    assert risk_manager.state == RiskState.MONITORING


def test_9_partial_fill(risk_manager: StraddleRiskManager):
    """
    Test 9: Simulate Call exit quantity = 1, Filled = 0.5, Remaining = 0.5.
    Verify the strategy correctly tracks the remaining open quantity.
    """
    risk_manager.record_exit_submission("CALL", "ORDER-EXIT-C1", 1.0)
    risk_manager.record_exit_fill("CALL", fill_qty=0.5, fill_px=505.0)

    call_leg = risk_manager.call_leg
    assert call_leg.exit_filled_qty == 0.5
    assert call_leg.remaining_qty == 0.5
    assert call_leg.is_closed is False
    assert call_leg.exit_order_status == OrderStatus.PARTIALLY_FILLED
    assert risk_manager.state == RiskState.MONITORING  # not marked fully closed


def test_10_one_leg_fully_filled(risk_manager: StraddleRiskManager):
    """
    Test 10: Call leg fully filled (closed), Put leg still open (unfilled).
    Verify strategy does NOT mark the straddle as closed.
    """
    risk_manager.record_exit_submission("CALL", "ORDER-EXIT-C1", 1.0)
    risk_manager.record_exit_submission("PUT", "ORDER-EXIT-P1", 1.0)

    # Call fully fills
    risk_manager.record_exit_fill("CALL", fill_qty=1.0, fill_px=505.0)

    assert risk_manager.call_leg.is_closed is True
    assert risk_manager.put_leg.is_closed is False
    assert risk_manager.state != RiskState.EXIT_COMPLETED


def test_11_both_legs_fully_filled(risk_manager: StraddleRiskManager):
    """
    Test 11: When both Call and Put exit orders are fully filled,
    strategy transitions to EXIT_COMPLETED.
    """
    risk_manager.state = RiskState.EXIT_IN_PROGRESS
    risk_manager.exit_reason = ExitReason.STOP_LOSS

    risk_manager.record_exit_submission("CALL", "ORDER-EXIT-C1", 1.0)
    risk_manager.record_exit_submission("PUT", "ORDER-EXIT-P1", 1.0)

    risk_manager.record_exit_fill("CALL", fill_qty=1.0, fill_px=505.0)
    risk_manager.record_exit_fill("PUT", fill_qty=1.0, fill_px=505.0)

    assert risk_manager.call_leg.is_closed is True
    assert risk_manager.put_leg.is_closed is True
    assert risk_manager.state == RiskState.EXIT_COMPLETED


def test_12_duplicate_trigger_prevention(risk_manager: StraddleRiskManager):
    """
    Test 12: Ensure that sending multiple quote updates while stop-loss (or take-profit)
    condition remains true does NOT repeatedly trigger exits.
    """
    # 1. First trigger
    risk_manager.update_quote("BTC-15NOV26-68000-C-USDT-OPTION.BYBIT", 510.0)
    risk_manager.update_quote("BTC-15NOV26-68000-P-USDT-OPTION.BYBIT", 510.0)

    first_trigger = risk_manager.evaluate_risk()
    assert first_trigger == ExitReason.STOP_LOSS
    assert risk_manager.state == RiskState.EXIT_IN_PROGRESS

    # 2. Subsequent quote arrives satisfying stop-loss even further
    risk_manager.update_quote("BTC-15NOV26-68000-C-USDT-OPTION.BYBIT", 520.0)
    second_trigger = risk_manager.evaluate_risk()
    assert second_trigger is None  # Blocked from duplicate firing

    # 3. Third quote arrives
    third_trigger = risk_manager.evaluate_risk()
    assert third_trigger is None


def test_13_order_rejection(risk_manager: StraddleRiskManager):
    """
    Test 13: Simulate an exit order rejection.
    Verify position remains open, is not falsely marked closed, and remaining qty is tracked.
    """
    risk_manager.record_exit_submission("CALL", "ORDER-EXIT-C1", 1.0)
    risk_manager.record_exit_rejection("CALL", "10006: Insufficient margin")

    call_leg = risk_manager.call_leg
    assert call_leg.exit_order_status == OrderStatus.REJECTED
    assert call_leg.is_closed is False
    assert call_leg.remaining_qty == 1.0
    assert call_leg.rejection_reason == "10006: Insufficient margin"
    assert risk_manager.state != RiskState.EXIT_COMPLETED


def test_14_order_cancellation(risk_manager: StraddleRiskManager):
    """
    Test 14: Simulate order cancellation after partial fill.
    Verify remaining quantity and state remain open.
    """
    risk_manager.record_exit_submission("PUT", "ORDER-EXIT-P1", 1.0)
    risk_manager.record_exit_fill("PUT", fill_qty=0.4, fill_px=500.0)
    risk_manager.record_exit_cancellation("PUT")

    put_leg = risk_manager.put_leg
    assert put_leg.exit_order_status == OrderStatus.CANCELED
    assert put_leg.exit_filled_qty == 0.4
    assert put_leg.remaining_qty == 0.6
    assert put_leg.is_closed is False


def test_15_different_quantities():
    """
    Test 15: Call quantity != Put quantity (e.g. Call qty = 0.02, Put qty = 0.01).
    Verify combined entry and buyback premiums are calculated with exact leg-specific quantities.
    """
    rm = StraddleRiskManager(
        call_instrument_id="C",
        put_instrument_id="P",
        call_qty=0.02,
        put_qty=0.01,
        call_entry_price=500.0,
        put_entry_price=500.0,
        config=RiskConfig(stop_loss_pct=0.01, take_profit_pct=0.02),
    )

    # Call entry = 0.02 * 500 = 10.0; Put entry = 0.01 * 500 = 5.0 -> Combined entry = 15.0
    assert rm.combined_entry_premium == 15.0
    assert rm.stop_loss_threshold == 15.0 * 1.01  # 15.15
    assert rm.take_profit_threshold == 15.0 * 0.98  # 14.70

    # Quotes: Call ask = 520, Put ask = 490
    # Call buyback = 0.02 * 520 = 10.40; Put buyback = 0.01 * 490 = 4.90 -> Combined = 15.30 (>= 15.15)
    rm.update_quote("C", 520.0)
    rm.update_quote("P", 490.0)

    buyback = rm.calculate_combined_buyback_premium()
    assert abs(buyback - 15.30) < 1e-9
    assert rm.evaluate_risk() == ExitReason.STOP_LOSS
