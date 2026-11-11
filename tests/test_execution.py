"""Unit tests for Short Straddle Order Execution and Position Tracking (Milestone 4)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

from nautilus_trader.adapters.bybit import BybitEnvironment
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import (
    LiquiditySide,
    OptionKind,
    OrderSide,
    OrderType,
    PositionSide,
)
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderDenied,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionOpened,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from src.btc_straddle.config import AppConfig
from src.btc_straddle.execution import (
    LegExecutionState,
    OrderStatus,
    ShortStraddleConfig,
    ShortStraddleStrategy,
    StraddleExecutionStatus,
)
from src.btc_straddle.instruments import OptionMetadata
from src.btc_straddle.straddle import ATMStraddle


def make_test_option_metadata(
    symbol_str: str,
    option_kind: str,
    strike: float,
    expiry_dt: datetime,
) -> OptionMetadata:
    """Helper to create OptionMetadata instances for tests."""
    return OptionMetadata(
        instrument_id=f"{symbol_str}-OPTION.BYBIT",
        raw_symbol=symbol_str,
        underlying="BTC",
        option_kind=option_kind,
        strike_price=strike,
        expiration_ns=int(expiry_dt.timestamp() * 1e9),
        expiration_dt=expiry_dt,
        price_precision=1,
        size_precision=3,
    )


def create_test_strategy(
    execute_trades: bool = False,
    order_type: str = "LIMIT",
    order_qty: float | None = 0.01,
    limit_price: float | None = 1500.0,
    environment: BybitEnvironment = BybitEnvironment.TESTNET,
) -> ShortStraddleStrategy:
    """Helper to instantiate and configure ShortStraddleStrategy for isolated unit tests."""
    cfg = ShortStraddleConfig(
        underlying_instrument_id="BTCUSDT-LINEAR.BYBIT",
        tie_breaker="lower",
        execute_trades=execute_trades,
        order_type=order_type,
        order_quantity=order_qty,
        limit_price=limit_price,
        environment=environment,
    )
    strat = ShortStraddleStrategy(config=cfg)

    # Provide synthetic ATM straddle pair
    now = datetime.now(timezone.utc)
    future_expiry = now + timedelta(days=2)
    call_meta = make_test_option_metadata("BTC-28MAR25-100000-C", "CALL", 100000.0, future_expiry)
    put_meta = make_test_option_metadata("BTC-28MAR25-100000-P", "PUT", 100000.0, future_expiry)

    strat.discovered_options = [call_meta, put_meta]
    strat.selected_straddle = ATMStraddle(
        spot_price=100000.0,
        expiry=future_expiry,
        strike=100000.0,
        call_instrument=call_meta,
        put_instrument=put_meta,
    )

    # Mock low-level network/bus actions
    strat.subscribe_quote_ticks = MagicMock()
    strat.unsubscribe_quote_ticks = MagicMock()
    strat.submit_order = MagicMock()

    return strat


def test_mainnet_fails_fast_in_config():
    """Verify that AppConfig rejects BybitEnvironment.MAINNET at initialization."""
    with pytest.raises(ValueError, match="Safety violation: only BybitEnvironment.TESTNET is permitted"):
        AppConfig(
            api_key="key",
            api_secret="secret",
            environment=BybitEnvironment.MAINNET,
        )


def test_mainnet_fails_fast_in_strategy():
    """Verify that ShortStraddleStrategy rejects BybitEnvironment.MAINNET in on_start."""
    strat = create_test_strategy(environment=BybitEnvironment.MAINNET)
    with pytest.raises(ValueError, match="Safety violation: ShortStraddleStrategy requires BybitEnvironment.TESTNET"):
        strat.on_start()


def test_dry_run_mode_submits_no_orders():
    """Verify that execute_trades=False simulates execution without sending live orders."""
    strat = create_test_strategy(execute_trades=False)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    assert strat.straddle_status == StraddleExecutionStatus.DRY_RUN
    assert strat.submit_order.call_count == 0  # No live orders submitted

    report = strat.get_execution_report()
    assert report is not None
    assert report.is_dry_run is True
    assert report.call_leg.client_order_id == "DRYRUN-CALL-001"
    assert report.put_leg.client_order_id == "DRYRUN-PUT-001"
    assert report.call_leg.submitted_qty == 0.01
    assert report.put_leg.submitted_qty == 0.01


def test_valid_straddle_generates_sell_orders():
    """Verify that execute_trades=True creates and submits SELL orders for both legs."""
    strat = create_test_strategy(execute_trades=True, order_type="LIMIT", order_qty=0.01, limit_price=1500.0)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    assert strat.straddle_status == StraddleExecutionStatus.SUBMITTED
    assert strat.submit_order.call_count == 2

    # Inspect submitted orders
    call_order = strat.submit_order.call_args_list[0][0][0]
    put_order = strat.submit_order.call_args_list[1][0][0]

    assert call_order.side == OrderSide.SELL
    assert put_order.side == OrderSide.SELL
    assert call_order.quantity == Quantity.from_str("0.010")
    assert put_order.quantity == Quantity.from_str("0.010")
    assert call_order.price == Price.from_str("1500.0")
    assert put_order.price == Price.from_str("1500.0")


def test_mismatched_pair_rejected_before_execution():
    """Verify that mismatched strikes or expiries are rejected prior to execution."""
    strat = create_test_strategy(execute_trades=True)
    now = datetime.now(timezone.utc)
    call_meta = make_test_option_metadata("BTC-28MAR25-100000-C", "CALL", 100000.0, now + timedelta(days=2))
    # Mismatched strike (105000 vs 100000)
    put_meta = make_test_option_metadata("BTC-28MAR25-105000-P", "PUT", 105000.0, now + timedelta(days=2))

    strat.selected_straddle = ATMStraddle(
        spot_price=100000.0,
        expiry=call_meta.expiration_dt,
        strike=100000.0,
        call_instrument=call_meta,
        put_instrument=put_meta,
    )
    strat._setup_straddle_legs(strat.selected_straddle)

    with pytest.raises(ValueError, match="strike mismatch"):
        strat._trigger_straddle_execution()

    assert strat.submit_order.call_count == 0


def test_idempotency_prevents_duplicate_execution():
    """Verify that multiple triggers do not re-submit orders."""
    strat = create_test_strategy(execute_trades=True)
    strat._setup_straddle_legs(strat.selected_straddle)

    strat._trigger_straddle_execution()
    assert strat.submit_order.call_count == 2

    # Second invocation should be blocked by idempotency guard
    strat._trigger_straddle_execution()
    assert strat.submit_order.call_count == 2


def test_order_lifecycle_tracking():
    """Verify order lifecycle tracking: Submitted -> Accepted -> Filled."""
    strat = create_test_strategy(execute_trades=True)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    call_cid = ClientOrderId(strat.call_state.client_order_id)
    put_cid = ClientOrderId(strat.put_state.client_order_id)
    tid = TraderId("BTC-STRADDLE-001")
    sid = StrategyId("STRAT-001")
    aid = AccountId("BYBIT-TESTNET")

    # 1. OrderSubmitted
    strat.on_order_submitted(
        OrderSubmitted(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=InstrumentId.from_str(strat.call_state.instrument_id),
            client_order_id=call_cid,
            account_id=aid,
            event_id=UUID4(),
            ts_event=1000,
            ts_init=1000,
        )
    )
    assert strat.call_state.status == OrderStatus.SUBMITTED

    # 2. OrderAccepted
    strat.on_order_accepted(
        OrderAccepted(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=InstrumentId.from_str(strat.call_state.instrument_id),
            client_order_id=call_cid,
            venue_order_id=VenueOrderId("VENUE-CALL-123"),
            account_id=aid,
            event_id=UUID4(),
            ts_event=2000,
            ts_init=2000,
        )
    )
    assert strat.call_state.status == OrderStatus.ACCEPTED
    assert strat.call_state.venue_order_id == "VENUE-CALL-123"

    # 3. OrderFilled
    strat.on_order_filled(
        OrderFilled(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=InstrumentId.from_str(strat.call_state.instrument_id),
            client_order_id=call_cid,
            venue_order_id=VenueOrderId("VENUE-CALL-123"),
            account_id=aid,
            trade_id=TradeId("T-CALL-001"),
            position_id=None,
            order_side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            last_qty=Quantity.from_str("0.010"),
            last_px=Price.from_str("1500.0"),
            currency=Currency.from_str("USDT"),
            commission=Money.from_str("0.5 USDT"),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=3000,
            ts_init=3000,
        )
    )
    assert strat.call_state.status == OrderStatus.FILLED
    assert strat.call_state.filled_qty == 0.010
    assert strat.call_state.avg_fill_price == 1500.0


def test_order_rejection_handling():
    """Verify order rejection updates status, captures reason, and sets failure state."""
    strat = create_test_strategy(execute_trades=True)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    call_cid = ClientOrderId(strat.call_state.client_order_id)
    strat.on_order_rejected(
        OrderRejected(
            trader_id=TraderId("BTC-STRADDLE-001"),
            strategy_id=StrategyId("STRAT-001"),
            instrument_id=InstrumentId.from_str(strat.call_state.instrument_id),
            client_order_id=call_cid,
            account_id=AccountId("BYBIT-TESTNET"),
            reason="Insufficient margin on Bybit Testnet",
            event_id=UUID4(),
            ts_event=1000,
            ts_init=1000,
        )
    )

    assert strat.call_state.status == OrderStatus.REJECTED
    assert strat.call_state.rejection_reason == "Insufficient margin on Bybit Testnet"


def test_order_denied_handling():
    """Verify order denial updates status and captures rejection reason."""
    strat = create_test_strategy(execute_trades=True)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    call_cid = ClientOrderId(strat.call_state.client_order_id)
    strat.on_order_denied(
        OrderDenied(
            trader_id=TraderId("BTC-STRADDLE-001"),
            strategy_id=StrategyId("STRAT-001"),
            instrument_id=InstrumentId.from_str(strat.call_state.instrument_id),
            client_order_id=call_cid,
            reason="Order exceeds maximum position limit",
            event_id=UUID4(),
            ts_init=1000,
        )
    )

    assert strat.call_state.status == OrderStatus.DENIED
    assert strat.call_state.rejection_reason == "Order exceeds maximum position limit"


def test_partial_fill_tracking():
    """Verify volume-weighted average price calculation over multiple fills."""
    strat = create_test_strategy(execute_trades=True)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    call_cid = ClientOrderId(strat.call_state.client_order_id)
    tid = TraderId("BTC-STRADDLE-001")
    sid = StrategyId("STRAT-001")
    aid = AccountId("BYBIT-TESTNET")
    inst_id = InstrumentId.from_str(strat.call_state.instrument_id)

    # First partial fill: 0.004 @ 1000.0
    strat.on_order_filled(
        OrderFilled(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=inst_id,
            client_order_id=call_cid,
            venue_order_id=VenueOrderId("V-1"),
            account_id=aid,
            trade_id=TradeId("T-1"),
            position_id=None,
            order_side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            last_qty=Quantity.from_str("0.004"),
            last_px=Price.from_str("1000.0"),
            currency=Currency.from_str("USDT"),
            commission=Money.from_str("0.2 USDT"),
            liquidity_side=LiquiditySide.MAKER,
            event_id=UUID4(),
            ts_event=1000,
            ts_init=1000,
        )
    )
    assert strat.call_state.status == OrderStatus.PARTIALLY_FILLED
    assert strat.call_state.filled_qty == 0.004
    assert strat.call_state.avg_fill_price == 1000.0

    # Second partial fill: 0.006 @ 1200.0 (Total: 0.010, VWAP: (4.0 + 7.2) / 0.010 = 1120.0)
    strat.on_order_filled(
        OrderFilled(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=inst_id,
            client_order_id=call_cid,
            venue_order_id=VenueOrderId("V-1"),
            account_id=aid,
            trade_id=TradeId("T-2"),
            position_id=None,
            order_side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            last_qty=Quantity.from_str("0.006"),
            last_px=Price.from_str("1200.0"),
            currency=Currency.from_str("USDT"),
            commission=Money.from_str("0.3 USDT"),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=2000,
            ts_init=2000,
        )
    )
    assert strat.call_state.status == OrderStatus.FILLED
    assert strat.call_state.filled_qty == 0.010
    assert abs(strat.call_state.avg_fill_price - 1120.0) < 1e-6


def test_one_leg_failure_detection():
    """Verify that if one leg fills and the other is rejected, ONE_LEG_FAILED is flagged."""
    strat = create_test_strategy(execute_trades=True)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    call_cid = ClientOrderId(strat.call_state.client_order_id)
    put_cid = ClientOrderId(strat.put_state.client_order_id)
    tid = TraderId("BTC-STRADDLE-001")
    sid = StrategyId("STRAT-001")
    aid = AccountId("BYBIT-TESTNET")

    # 1. Call leg fills
    strat.on_order_filled(
        OrderFilled(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=InstrumentId.from_str(strat.call_state.instrument_id),
            client_order_id=call_cid,
            venue_order_id=VenueOrderId("V-CALL"),
            account_id=aid,
            trade_id=TradeId("T-CALL-001"),
            position_id=None,
            order_side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            last_qty=Quantity.from_str("0.010"),
            last_px=Price.from_str("1500.0"),
            currency=Currency.from_str("USDT"),
            commission=Money.from_str("0.5 USDT"),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=1000,
            ts_init=1000,
        )
    )
    assert strat.call_state.status == OrderStatus.FILLED

    # 2. Put leg is rejected
    strat.on_order_rejected(
        OrderRejected(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=InstrumentId.from_str(strat.put_state.instrument_id),
            client_order_id=put_cid,
            account_id=aid,
            reason="Contract expired or halted",
            event_id=UUID4(),
            ts_event=2000,
            ts_init=2000,
        )
    )
    assert strat.put_state.status == OrderStatus.REJECTED

    # Evaluate straddle status
    strat._evaluate_straddle_completion()
    assert strat.straddle_status == StraddleExecutionStatus.ONE_LEG_FAILED

    report = strat.get_execution_report()
    assert report is not None
    assert report.is_straddle_established is False


def test_straddle_established_with_short_positions():
    """Verify that when both legs fill and open SHORT positions, straddle is established."""
    strat = create_test_strategy(execute_trades=True)
    strat._setup_straddle_legs(strat.selected_straddle)
    strat._trigger_straddle_execution()

    call_cid = ClientOrderId(strat.call_state.client_order_id)
    put_cid = ClientOrderId(strat.put_state.client_order_id)
    tid = TraderId("BTC-STRADDLE-001")
    sid = StrategyId("STRAT-001")
    aid = AccountId("BYBIT-TESTNET")

    call_inst = InstrumentId.from_str(strat.call_state.instrument_id)
    put_inst = InstrumentId.from_str(strat.put_state.instrument_id)

    # 1. Fill Call leg
    strat.on_order_filled(
        OrderFilled(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=call_inst,
            client_order_id=call_cid,
            venue_order_id=VenueOrderId("V-CALL"),
            account_id=aid,
            trade_id=TradeId("T-CALL-001"),
            position_id=None,
            order_side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            last_qty=Quantity.from_str("0.010"),
            last_px=Price.from_str("1500.0"),
            currency=Currency.from_str("USDT"),
            commission=Money.from_str("0.5 USDT"),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=1000,
            ts_init=1000,
        )
    )

    # 2. Fill Put leg
    strat.on_order_filled(
        OrderFilled(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=put_inst,
            client_order_id=put_cid,
            venue_order_id=VenueOrderId("V-PUT"),
            account_id=aid,
            trade_id=TradeId("T-PUT-001"),
            position_id=None,
            order_side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            last_qty=Quantity.from_str("0.010"),
            last_px=Price.from_str("1450.0"),
            currency=Currency.from_str("USDT"),
            commission=Money.from_str("0.5 USDT"),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=2000,
            ts_init=2000,
        )
    )

    # 3. PositionOpened for Call (SHORT)
    strat.on_position_opened(
        PositionOpened(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=call_inst,
            position_id=PositionId("POS-CALL-001"),
            account_id=aid,
            opening_order_id=call_cid,
            entry=OrderSide.SELL,
            side=PositionSide.SHORT,
            signed_qty=-0.010,
            quantity=Quantity.from_str("0.010"),
            peak_qty=Quantity.from_str("0.010"),
            last_qty=Quantity.from_str("0.010"),
            last_px=Price.from_str("1500.0"),
            currency=Currency.from_str("USDT"),
            avg_px_open=1500.0,
            realized_pnl=Money.from_str("0.0 USDT"),
            event_id=UUID4(),
            ts_event=3000,
            ts_init=3000,
        )
    )

    # 4. PositionOpened for Put (SHORT)
    strat.on_position_opened(
        PositionOpened(
            trader_id=tid,
            strategy_id=sid,
            instrument_id=put_inst,
            position_id=PositionId("POS-PUT-001"),
            account_id=aid,
            opening_order_id=put_cid,
            entry=OrderSide.SELL,
            side=PositionSide.SHORT,
            signed_qty=-0.010,
            quantity=Quantity.from_str("0.010"),
            peak_qty=Quantity.from_str("0.010"),
            last_qty=Quantity.from_str("0.010"),
            last_px=Price.from_str("1450.0"),
            currency=Currency.from_str("USDT"),
            avg_px_open=1450.0,
            realized_pnl=Money.from_str("0.0 USDT"),
            event_id=UUID4(),
            ts_event=4000,
            ts_init=4000,
        )
    )

    assert strat.straddle_status == StraddleExecutionStatus.STRADDLE_ESTABLISHED

    report = strat.get_execution_report()
    assert report is not None
    assert report.is_straddle_established is True
    assert report.call_leg.position_side == "SHORT"
    assert report.put_leg.position_side == "SHORT"
    assert "Straddle Established:  YES" in report.summary()
