"""Unit tests for the FastAPI REST endpoints and state management."""

import asyncio

from src.btc_straddle.api import (
    update_state,
    get_state_snapshot,
    get_status,
    get_atm,
    get_orders,
    get_positions,
)


def test_api_status_endpoint():
    """Verify /api/status returns the active node state."""
    update_state(
        node_status="RUNNING",
        is_connected=True,
        bybit_environment="TESTNET",
        reference_price=80500.0,
    )
    data = asyncio.run(get_status())
    assert data["node_status"] == "RUNNING"
    assert data["is_connected"] is True
    assert data["bybit_environment"] == "TESTNET"
    assert data["reference_price"] == 80500.0


def test_api_atm_endpoint():
    """Verify /api/atm returns selected straddle details."""
    update_state(
        atm_selected=True,
        atm_strike=80000.0,
        atm_expiry="2026-11-20T08:00:00Z",
        atm_call_id="BTC-20NOV26-80000-C.BYBIT",
        atm_put_id="BTC-20NOV26-80000-P.BYBIT",
        call_bid=150.0,
        call_ask=160.0,
        put_bid=140.0,
        put_ask=150.0,
    )
    data = asyncio.run(get_atm())
    assert data["atm_selected"] is True
    assert data["strike"] == 80000.0
    assert data["call"]["instrument_id"] == "BTC-20NOV26-80000-C.BYBIT"
    assert data["put"]["instrument_id"] == "BTC-20NOV26-80000-P.BYBIT"
    assert data["call"]["bid"] == 150.0
    assert data["put"]["ask"] == 150.0


def test_api_orders_endpoint():
    """Verify /api/orders returns call and put leg states."""
    update_state(
        straddle_status="ACCEPTED",
        call_order_status="ACCEPTED",
        call_submitted_qty=0.01,
        call_filled_qty=0.01,
        put_order_status="ACCEPTED",
        put_submitted_qty=0.01,
        put_filled_qty=0.01,
    )
    data = asyncio.run(get_orders())
    assert data["straddle_status"] == "ACCEPTED"
    assert data["call"]["status"] == "ACCEPTED"
    assert data["put"]["status"] == "ACCEPTED"
    assert data["call"]["submitted_qty"] == 0.01
    assert data["put"]["submitted_qty"] == 0.01


def test_api_positions_endpoint():
    """Verify /api/positions returns short positions."""
    update_state(
        straddle_established=True,
        call_position_side="SHORT",
        call_position_qty=0.01,
        put_position_side="SHORT",
        put_position_qty=0.01,
    )
    data = asyncio.run(get_positions())
    assert data["straddle_established"] is True
    assert data["call"]["side"] == "SHORT"
    assert data["put"]["side"] == "SHORT"
