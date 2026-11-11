"""
FastAPI REST + WebSocket bridge between the React frontend and the running
ShortStraddleStrategy / NautilusTrader backend.

This module exposes:
  GET  /api/status          - node and strategy status
  GET  /api/instruments     - all discovered BTC option contracts
  GET  /api/atm             - current ATM straddle selection
  GET  /api/orders          - call + put leg order state
  GET  /api/positions       - call + put position state
  POST /api/execute         - trigger SELL STRADDLE (requires execute_trades=True)
  WS   /ws                  - real-time push of all state updates (JSON)

The FastAPI app is started in a background thread so the NautilusTrader event
loop continues running in the main thread unchanged.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared state container (written by NautilusTrader thread, read by API thread)
# ---------------------------------------------------------------------------

@dataclass
class APIState:
    """Thread-safe snapshot of the trading system state exposed to the frontend."""

    # Node / connectivity
    node_status: str = "STARTING"          # STARTING | RUNNING | STOPPED
    is_connected: bool = False
    bybit_environment: str = "TESTNET"
    execute_trades: bool = False
    is_dry_run: bool = True

    # Market data
    reference_price: float | None = None
    reference_price_updated_at: str | None = None
    total_quotes_received: int = 0

    # Instrument discovery
    total_instruments_discovered: int = 0
    total_expiries: int = 0

    # ATM selection
    atm_selected: bool = False
    atm_strike: float | None = None
    atm_expiry: str | None = None
    atm_call_id: str | None = None
    atm_put_id: str | None = None

    # Order / execution state
    straddle_status: str = "NOT_STARTED"

    call_order_status: str = "NOT_SUBMITTED"
    call_instrument_id: str | None = None
    call_client_order_id: str | None = None
    call_venue_order_id: str | None = None
    call_submitted_qty: float = 0.0
    call_filled_qty: float = 0.0
    call_avg_fill_price: float = 0.0
    call_limit_price: float | None = None
    call_rejection_reason: str | None = None

    put_order_status: str = "NOT_SUBMITTED"
    put_instrument_id: str | None = None
    put_client_order_id: str | None = None
    put_venue_order_id: str | None = None
    put_submitted_qty: float = 0.0
    put_filled_qty: float = 0.0
    put_avg_fill_price: float = 0.0
    put_limit_price: float | None = None
    put_rejection_reason: str | None = None

    # Position state
    call_position_id: str | None = None
    call_position_side: str | None = None
    call_position_qty: float = 0.0

    put_position_id: str | None = None
    put_position_side: str | None = None
    put_position_qty: float = 0.0

    # Straddle established flag
    straddle_established: bool = False

    # Latest quotes for the straddle legs
    call_bid: float | None = None
    call_ask: float | None = None
    put_bid: float | None = None
    put_ask: float | None = None


# Global singleton written by the trading thread
_state = APIState()
_state_lock = threading.Lock()

# WebSocket connection pool
_ws_clients: list[WebSocket] = []
_ws_lock = threading.Lock()

# Reference to the running strategy (set by main.py after strategy creation)
_strategy: Any = None   # ShortStraddleStrategy
_node: Any = None       # TradingNode


def set_strategy_ref(strategy: Any, node: Any) -> None:
    """Called by main.py to give the API bridge access to the live strategy."""
    global _strategy, _node
    _strategy = strategy
    _node = node


def update_state(**kwargs: Any) -> None:
    """
    Thread-safe update of APIState fields.
    Called by the NautilusTrader strategy to push state changes to the API layer.
    Triggers a WebSocket broadcast after each update.
    """
    with _state_lock:
        for key, value in kwargs.items():
            if hasattr(_state, key):
                setattr(_state, key, value)
            else:
                logger.warning(f"APIState has no field '{key}'")

    # Kick off a broadcast in the API event loop (non-blocking)
    _schedule_broadcast()


def get_state_snapshot() -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of the current state."""
    with _state_lock:
        d = asdict(_state)
    return d


# ---------------------------------------------------------------------------
# WebSocket broadcasting helpers
# ---------------------------------------------------------------------------

_api_loop: asyncio.AbstractEventLoop | None = None


def _schedule_broadcast() -> None:
    """Schedule a WebSocket broadcast on the API event loop (called from any thread)."""
    with _ws_lock:
        if not _ws_clients:
            return

    if _api_loop is not None and not _api_loop.is_closed() and _api_loop.is_running():
        try:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None

            if current_loop is _api_loop:
                _api_loop.create_task(_broadcast_state())
            else:
                asyncio.run_coroutine_threadsafe(_broadcast_state(), _api_loop)
        except Exception:
            pass


async def _broadcast_state() -> None:
    """Send the current state snapshot to all connected WebSocket clients."""
    snapshot = json.dumps(get_state_snapshot())
    dead: list[WebSocket] = []
    with _ws_lock:
        clients = list(_ws_clients)
    for ws in clients:
        try:
            await ws.send_text(snapshot)
        except Exception:
            dead.append(ws)
    if dead:
        with _ws_lock:
            for ws in dead:
                try:
                    _ws_clients.remove(ws)
                except ValueError:
                    pass


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Capture the API event loop for cross-thread scheduling."""
    global _api_loop
    _api_loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="BTC Short Straddle API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # In production: restrict to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST endpoints ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    """Node connectivity and full trading system state snapshot."""
    return get_state_snapshot()


@app.get("/api/instruments")
async def get_instruments() -> dict[str, Any]:
    """Summary of discovered BTC option instruments."""
    s = get_state_snapshot()
    instruments = []
    if _strategy is not None:
        for opt in getattr(_strategy, "discovered_options", []):
            exp_dt = getattr(opt, "expiration_dt", None) or getattr(opt, "expiry", None)
            instruments.append({
                "instrument_id": opt.instrument_id,
                "underlying": opt.underlying,
                "option_type": getattr(opt, "option_kind", "CALL" if opt.is_call else "PUT"),
                "strike": opt.strike_price,
                "expiry": exp_dt.isoformat() if exp_dt else None,
            })
    return {
        "total": len(instruments),
        "total_expiries": s["total_expiries"],
        "instruments": instruments,
    }


@app.get("/api/atm")
async def get_atm() -> dict[str, Any]:
    """Current ATM straddle selection."""
    s = get_state_snapshot()
    return {
        "atm_selected": s["atm_selected"],
        "reference_price": s["reference_price"],
        "strike": s["atm_strike"],
        "expiry": s["atm_expiry"],
        "call": {
            "instrument_id": s["atm_call_id"],
            "bid": s["call_bid"],
            "ask": s["call_ask"],
        },
        "put": {
            "instrument_id": s["atm_put_id"],
            "bid": s["put_bid"],
            "ask": s["put_ask"],
        },
    }


@app.get("/api/orders")
async def get_orders() -> dict[str, Any]:
    """Current order state for both straddle legs."""
    s = get_state_snapshot()
    return {
        "straddle_status": s["straddle_status"],
        "is_dry_run": s["is_dry_run"],
        "call": {
            "instrument_id": s["call_instrument_id"],
            "status": s["call_order_status"],
            "client_order_id": s["call_client_order_id"],
            "venue_order_id": s["call_venue_order_id"],
            "submitted_qty": s["call_submitted_qty"],
            "filled_qty": s["call_filled_qty"],
            "avg_fill_price": s["call_avg_fill_price"],
            "limit_price": s["call_limit_price"],
            "rejection_reason": s["call_rejection_reason"],
        },
        "put": {
            "instrument_id": s["put_instrument_id"],
            "status": s["put_order_status"],
            "client_order_id": s["put_client_order_id"],
            "venue_order_id": s["put_venue_order_id"],
            "submitted_qty": s["put_submitted_qty"],
            "filled_qty": s["put_filled_qty"],
            "avg_fill_price": s["put_avg_fill_price"],
            "limit_price": s["put_limit_price"],
            "rejection_reason": s["put_rejection_reason"],
        },
    }


@app.get("/api/positions")
async def get_positions() -> dict[str, Any]:
    """Current position state for both straddle legs."""
    s = get_state_snapshot()
    return {
        "straddle_established": s["straddle_established"],
        "call": {
            "instrument_id": s["call_instrument_id"],
            "position_id": s["call_position_id"],
            "side": s["call_position_side"],
            "qty": s["call_position_qty"],
        },
        "put": {
            "instrument_id": s["put_instrument_id"],
            "position_id": s["put_position_id"],
            "side": s["put_position_side"],
            "qty": s["put_position_qty"],
        },
    }


@app.post("/api/execute")
async def trigger_execute(body: dict[str, Any] = {}) -> dict[str, Any]:
    """
    Trigger the SELL STRADDLE execution.

    The strategy must have already selected the ATM straddle.
    If execute_trades=False (dry-run mode), the backend simulates the execution.
    If execute_trades=True, real SELL LIMIT orders are submitted to Bybit Testnet.
    """
    if _strategy is None:
        return {"success": False, "error": "Strategy not initialised yet."}

    straddle = getattr(_strategy, "selected_straddle", None)
    if straddle is None:
        return {"success": False, "error": "ATM straddle not selected yet. Wait for reference price."}

    if getattr(_strategy, "_execution_initiated", False):
        return {"success": False, "error": "Execution already initiated (idempotent guard)."}

    # Delegate to the strategy's internal execution workflow
    try:
        _strategy._trigger_straddle_execution()
        return {
            "success": True,
            "is_dry_run": not _strategy.execute_trades,
            "strike": straddle.strike,
            "expiry": straddle.expiry.isoformat(),
            "call_id": straddle.call_instrument.instrument_id,
            "put_id": straddle.put_instrument.instrument_id,
        }
    except Exception as exc:
        logger.exception(f"/api/execute error: {exc}")
        return {"success": False, "error": str(exc)}


# ── WebSocket endpoint ──────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Real-time state push to connected React clients with heartbeat."""
    await ws.accept()
    with _ws_lock:
        _ws_clients.append(ws)
    logger.info(f"WebSocket client connected. Total: {len(_ws_clients)}")
    # Send current state immediately on connect
    await ws.send_text(json.dumps(get_state_snapshot()))
    try:
        while True:
            try:
                # Wait for any incoming client message or heartbeat timeout
                await asyncio.wait_for(ws.receive_text(), timeout=10.0)
            except asyncio.TimeoutError:
                # Send periodic heartbeat to keep the proxy and socket alive
                await ws.send_text(json.dumps({"type": "heartbeat"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        with _ws_lock:
            try:
                _ws_clients.remove(ws)
            except ValueError:
                pass
        logger.info(f"WebSocket client disconnected. Total: {len(_ws_clients)}")


# ── Mount compiled React frontend if present ────────────────────────────────
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")


# ---------------------------------------------------------------------------
# API server lifecycle helpers
# ---------------------------------------------------------------------------

def start_api_server(host: str = "0.0.0.0", port: int = 8000) -> threading.Thread:
    """
    Start the FastAPI/Uvicorn server in a background daemon thread.

    Returns the thread so the caller can join or inspect it.
    The NautilusTrader event loop is unaffected.
    """
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",   # Keep NautilusTrader logs clean
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    def run() -> None:
        asyncio.run(server.serve())

    thread = threading.Thread(target=run, daemon=True, name="api-server")
    thread.start()
    logger.info(f"API server started at http://{host}:{port}")
    return thread

