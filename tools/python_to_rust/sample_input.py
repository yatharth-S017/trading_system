"""
Sample pure-Python module for the experimental Python → Rust converter.

This file contains extracted, self-contained risk management functions from
the BTC Short Straddle project. It has NO external dependencies (no
NautilusTrader, no Bybit, no asyncio) and is designed to demonstrate the
converter on realistic project logic.

DO NOT import this from the live trading system. It exists only as a
conversion target for the experimental Rust code generator.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOP_LOSS_PCT: float = 0.01     # +1% stop loss threshold
TAKE_PROFIT_PCT: float = 0.02   # 2% take profit threshold
EPSILON: float = 1e-9           # floating point tolerance


# ---------------------------------------------------------------------------
# Premium Calculations
# ---------------------------------------------------------------------------

def calculate_combined_entry_premium(
    call_entry_price: float,
    call_qty: float,
    put_entry_price: float,
    put_qty: float,
) -> float:
    """Calculate the total premium received from selling both straddle legs."""
    call_premium = call_entry_price * call_qty
    put_premium = put_entry_price * put_qty
    return call_premium + put_premium


def calculate_stop_loss_threshold(
    combined_entry_premium: float,
    stop_loss_pct: float,
) -> float:
    """Calculate the stop-loss threshold for the combined premium."""
    return combined_entry_premium * (1.0 + stop_loss_pct)


def calculate_take_profit_threshold(
    combined_entry_premium: float,
    take_profit_pct: float,
) -> float:
    """Calculate the take-profit threshold for the combined premium."""
    return combined_entry_premium * (1.0 - take_profit_pct)


def calculate_combined_buyback_premium(
    call_ask: float,
    call_remaining_qty: float,
    put_ask: float,
    put_remaining_qty: float,
) -> float:
    """
    Calculate the total cost to buy back both legs using current ASK prices.

    Returns the combined buyback premium.
    """
    call_buyback = call_ask * call_remaining_qty
    put_buyback = put_ask * put_remaining_qty
    return call_buyback + put_buyback


# ---------------------------------------------------------------------------
# Risk Evaluation
# ---------------------------------------------------------------------------

def should_stop_loss(
    combined_buyback: float,
    stop_loss_threshold: float,
) -> bool:
    """Return True if the combined buyback premium has breached the stop-loss level."""
    return combined_buyback >= stop_loss_threshold - EPSILON


def should_take_profit(
    combined_buyback: float,
    take_profit_threshold: float,
) -> bool:
    """Return True if the combined buyback premium has dropped to the take-profit level."""
    return combined_buyback <= take_profit_threshold + EPSILON


def evaluate_risk(
    combined_buyback: float,
    stop_loss_threshold: float,
    take_profit_threshold: float,
) -> str:
    """
    Evaluate the current risk state.

    Returns
    -------
    str
        One of: "STOP_LOSS", "TAKE_PROFIT", or "HOLD".
    """
    if should_stop_loss(combined_buyback, stop_loss_threshold):
        return "STOP_LOSS"
    elif should_take_profit(combined_buyback, take_profit_threshold):
        return "TAKE_PROFIT"
    return "HOLD"


# ---------------------------------------------------------------------------
# Position Tracking
# ---------------------------------------------------------------------------

def remaining_quantity(position_qty: float, filled_qty: float) -> float:
    """Calculate remaining open quantity for a leg."""
    remaining = position_qty - filled_qty
    if remaining < 0.0:
        remaining = 0.0
    return remaining


def is_leg_closed(position_qty: float, filled_qty: float) -> bool:
    """Return True when all of the position quantity has been filled."""
    return remaining_quantity(position_qty, filled_qty) <= EPSILON


def is_straddle_exit_complete(
    call_position_qty: float,
    call_filled_qty: float,
    put_position_qty: float,
    put_filled_qty: float,
) -> bool:
    """Return True when both legs have been fully closed."""
    call_closed = is_leg_closed(call_position_qty, call_filled_qty)
    put_closed = is_leg_closed(put_position_qty, put_filled_qty)
    return call_closed and put_closed


def update_average_fill_price(
    prev_avg_price: float,
    prev_filled_qty: float,
    new_fill_price: float,
    new_fill_qty: float,
) -> float:
    """
    Compute the updated average fill price after a new partial fill.

    Uses the volume-weighted average formula:
        new_avg = (prev_avg * prev_qty + new_price * new_qty) / total_qty
    """
    total_qty = prev_filled_qty + new_fill_qty
    if total_qty <= 0.0:
        return 0.0
    prev_value = prev_avg_price * prev_filled_qty
    new_value = new_fill_price * new_fill_qty
    return (prev_value + new_value) / total_qty


# ---------------------------------------------------------------------------
# ATM Strike Selection (pure logic, extracted)
# ---------------------------------------------------------------------------

def select_atm_strike_pure(
    spot_price: float,
    strikes: list[float],
) -> float:
    """
    Select the ATM strike nearest to spot_price.

    If two strikes are equidistant, picks the lower one.

    Returns -1.0 if strikes is empty or spot_price <= 0.
    """
    if spot_price <= 0.0:
        return -1.0
    if not strikes:
        return -1.0

    best_strike = strikes[0]
    best_distance = abs(strikes[0] - spot_price)

    for strike in strikes:
        distance = abs(strike - spot_price)
        if distance < best_distance:
            best_distance = distance
            best_strike = strike
        elif distance == best_distance and strike < best_strike:
            best_strike = strike

    return best_strike


# ---------------------------------------------------------------------------
# Simple Risk Report
# ---------------------------------------------------------------------------

class RiskSnapshot:
    """A snapshot of the current risk state for reporting."""

    combined_entry: float
    stop_loss_threshold: float
    take_profit_threshold: float
    combined_buyback: float
    call_remaining: float
    put_remaining: float
    risk_state: str

    def __init__(
        self,
        combined_entry: float,
        stop_loss_threshold: float,
        take_profit_threshold: float,
        combined_buyback: float,
        call_remaining: float,
        put_remaining: float,
    ) -> None:
        self.combined_entry = combined_entry
        self.stop_loss_threshold = stop_loss_threshold
        self.take_profit_threshold = take_profit_threshold
        self.combined_buyback = combined_buyback
        self.call_remaining = call_remaining
        self.put_remaining = put_remaining
        self.risk_state = evaluate_risk(
            combined_buyback, stop_loss_threshold, take_profit_threshold,
        )

    def pnl_percent(self) -> float:
        """Return current P&L as a percentage of entry premium."""
        if self.combined_entry <= 0.0:
            return 0.0
        return ((self.combined_entry - self.combined_buyback) / self.combined_entry) * 100.0

    def is_exit_required(self) -> bool:
        """Return True if either stop-loss or take-profit has been triggered."""
        return self.risk_state != "HOLD"
