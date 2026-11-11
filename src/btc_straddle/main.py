"""Main entry point for Milestone 4: Short Straddle Order Execution & Position Tracking."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from src.btc_straddle.config import load_config
from src.btc_straddle.execution import (
    ShortStraddleConfig,
    ShortStraddleStrategy,
)
from src.btc_straddle.node import create_trading_node

# API bridge (optional — gracefully skipped if fastapi/uvicorn not installed)
try:
    from src.btc_straddle.api import start_api_server, set_strategy_ref, update_state
    _API_AVAILABLE = True
except ImportError:
    _API_AVAILABLE = False
    logger_pre = logging.getLogger("btc_straddle.main")
    logger_pre.warning("fastapi/uvicorn not installed — API server disabled. Run: pip install fastapi uvicorn")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("btc_straddle.main")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Milestone 4: NautilusTrader + Bybit Testnet Short Straddle Execution & Position Tracking"
    )
    parser.add_argument(
        "--run-seconds",
        type=float,
        default=0.0,
        help="Optional number of seconds to run before stopping (e.g. 15 for quick verification). Default 0.0 runs continuously until Ctrl+C.",
    )
    parser.add_argument(
        "--underlying",
        type=str,
        default="BTCUSDT-LINEAR.BYBIT",
        help="Instrument ID for the BTC reference/spot price feed. Default: BTCUSDT-LINEAR.BYBIT.",
    )
    parser.add_argument(
        "--tie-breaker",
        type=str,
        default="lower",
        choices=["lower", "higher"],
        help="Tie-breaking rule when spot is equidistant between strikes. Default: lower.",
    )
    parser.add_argument(
        "--target-expiry",
        type=str,
        default=None,
        help="Optional target expiration datetime in ISO format (e.g. 2026-11-13T08:00:00Z).",
    )
    parser.add_argument(
        "--max-quote-logs",
        type=int,
        default=10,
        help="Maximum number of straddle quote tick events to print to console. Default: 10.",
    )
    parser.add_argument(
        "--execute-trades",
        action="store_true",
        default=False,
        help="Execute real SELL orders for both Call and Put on Bybit Testnet. If omitted, runs in safe DRY-RUN mode.",
    )
    parser.add_argument(
        "--order-type",
        type=str,
        default="LIMIT",
        choices=["LIMIT", "MARKET"],
        help="Order type for straddle legs: LIMIT or MARKET. Default: LIMIT.",
    )
    parser.add_argument(
        "--order-qty",
        type=float,
        default=None,
        help="Order quantity in BTC contracts per leg (e.g. 0.01). If omitted, automatically selects smallest valid lot size.",
    )
    parser.add_argument(
        "--limit-price",
        type=float,
        default=None,
        help="Explicit limit price per contract. If omitted, uses best market bid or fallback.",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="Port for the REST/WebSocket API server used by the React frontend. Default: 8000.",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        default=False,
        help="Disable the FastAPI server (useful when running headless / in tests).",
    )
    return parser.parse_args()


def main() -> int:
    """Run the Short Straddle Order Execution and Position Tracking workflow."""
    args = parse_args()

    logger.info("==================================================")
    logger.info("Starting BTC ATM Short Straddle - Milestone 4")
    logger.info("Short Straddle Order Execution & Position Tracking")
    logger.info("==================================================")

    # 1. Loading configuration
    logger.info("Step 1: Loading configuration from environment / .env...")
    try:
        config = load_config()
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # CLI flag overrides env if provided
    execute_trades = args.execute_trades or config.execute_trades
    order_type = args.order_type or config.order_type
    order_qty = args.order_qty if args.order_qty is not None else config.order_quantity

    summary = config.summary()
    logger.info(
        f"Loaded configuration: TraderID={summary['trader_id']}, "
        f"Environment={summary['environment']}, "
        f"Credentials Configured={summary['has_api_credentials']}, "
        f"Execute Trades={execute_trades}, "
        f"Order Type={order_type}"
    )

    # Safety check: Live execution requires valid credentials
    if execute_trades and not config.has_api_credentials:
        logger.error(
            "EXECUTION BLOCKED: --execute-trades was specified, but no valid Bybit API credentials were found in .env. "
            "Please configure BYBIT_API_KEY and BYBIT_API_SECRET in your .env file."
        )
        return 1

    if not execute_trades:
        logger.info(
            "*** RUNNING IN DRY-RUN MODE *** (EXECUTE_TRADES=False). "
            "ATM Straddle will be selected and orders will be simulated without sending live orders to Bybit."
        )

    # 1b. Start FastAPI server early so port 8000 opens immediately for frontend
    if _API_AVAILABLE and not getattr(args, "no_api", False):
        update_state(
            bybit_environment=config.environment.name,
            execute_trades=execute_trades,
            is_dry_run=not execute_trades,
            node_status="STARTING",
        )
        api_port = getattr(args, "api_port", 8000)
        start_api_server(host="0.0.0.0", port=api_port)
        logger.info(f"React frontend API server listening at http://localhost:{api_port}")

    # 2. Initializing NautilusTrader & Bybit adapter
    logger.info("Step 2: Initializing NautilusTrader TradingNode with Bybit Testnet adapter...")
    try:
        node = create_trading_node(config)
    except Exception as e:
        logger.exception(f"Failed to create TradingNode: {e}")
        return 1

    # 3. Attaching ShortStraddleStrategy
    logger.info(
        f"Step 3: Registering ShortStraddleStrategy (underlying={args.underlying}, "
        f"execute_trades={execute_trades}, order_type={order_type}, order_qty={order_qty})..."
    )
    strategy_config = ShortStraddleConfig(
        underlying_instrument_id=args.underlying,
        tie_breaker=args.tie_breaker,
        max_quote_logs=args.max_quote_logs,
        target_expiry_iso=args.target_expiry,
        execute_trades=execute_trades,
        order_type=order_type,
        order_quantity=order_qty,
        limit_price=args.limit_price,
        environment=config.environment,
    )
    strategy = ShortStraddleStrategy(config=strategy_config)
    node.trader.add_strategy(strategy)

    # 3b. Attach strategy and node reference to API bridge
    if _API_AVAILABLE and not getattr(args, "no_api", False):
        set_strategy_ref(strategy, node)

    # 4. Handle graceful shutdown
    stop_called = False

    def request_shutdown(signum=None, frame=None):
        nonlocal stop_called
        if not stop_called:
            stop_called = True
            logger.info("Shutdown signal received. Stopping TradingNode gracefully...")
            node.stop()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    # Optional auto-stop timer for verification / automated testing
    if args.run_seconds > 0:
        logger.info(f"Auto-stop timer armed: will stop after {args.run_seconds} seconds.")

        def auto_stop():
            time.sleep(args.run_seconds)
            logger.info(f"Auto-stop timer elapsed ({args.run_seconds}s). Initiating graceful shutdown...")
            request_shutdown()

        timer_thread = threading.Thread(target=auto_stop, daemon=True)
        timer_thread.start()

    # 5. Connecting and running
    logger.info("Step 4: Connecting to Bybit Testnet, streaming reference price, pairing ATM straddle, and executing...")
    try:
        node.run()
    except Exception as e:
        logger.exception(f"Error during TradingNode execution: {e}")
        return 1
    finally:
        logger.info("Step 5: TradingNode stopped. Disposing resources...")
        try:
            node.dispose()
        except Exception:
            pass

        report = strategy.get_execution_report()
        if report:
            print("\n" + report.summary() + "\n")
        else:
            logger.warning("No ATM straddle execution report was generated during this run.")

        logger.info("Shutdown complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
