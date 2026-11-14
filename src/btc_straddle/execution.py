"""Short straddle order execution, order tracking, and position tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from nautilus_trader.adapters.bybit import BybitEnvironment
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.factories import OrderFactory
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import (
    OrderSide,
    OrderType,
    PositionSide,
    TimeInForce,
)
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderDenied,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.identifiers import (
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.config import StrategyConfig

from src.btc_straddle.instruments import OptionMetadata
from src.btc_straddle.market_data import (
    OptionMarketDataConfig,
    OptionMarketDataStrategy,
)
from src.btc_straddle.straddle import ATMStraddle, validate_straddle_pair

# Optional API bridge
try:
    from src.btc_straddle import api as _api_bridge
    _API_AVAILABLE = True
except ImportError:
    _api_bridge = None  # type: ignore[assignment]
    _API_AVAILABLE = False


class OrderStatus(str, Enum):
    """Lifecycle states of an individual option leg order."""

    NOT_SUBMITTED = "NOT_SUBMITTED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    DENIED = "DENIED"


class StraddleExecutionStatus(str, Enum):
    """Overall status of the short straddle pair execution."""

    NOT_STARTED = "NOT_STARTED"
    PAIR_SELECTED = "PAIR_SELECTED"
    DRY_RUN = "DRY_RUN"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    STRADDLE_ESTABLISHED = "STRADDLE_ESTABLISHED"
    ONE_LEG_FAILED = "ONE_LEG_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass
class LegExecutionState:
    """Detailed execution and position state for an individual straddle leg."""

    leg_name: str  # "CALL" or "PUT"
    instrument_id: str
    order_side: str = "SELL"
    order_type: str = "LIMIT"
    status: OrderStatus = OrderStatus.NOT_SUBMITTED

    client_order_id: str | None = None
    venue_order_id: str | None = None
    submitted_qty: float = 0.0
    filled_qty: float = 0.0
    cumulative_quote_volume: float = 0.0
    avg_fill_price: float = 0.0
    limit_price: float | None = None

    rejection_reason: str | None = None
    position_id: str | None = None
    position_side: str | None = None
    position_qty: float = 0.0

    submitted_time: datetime | None = None
    accepted_time: datetime | None = None
    last_fill_time: datetime | None = None

    def record_submission(
        self,
        client_order_id: str,
        qty: float,
        order_type: str = "LIMIT",
        limit_px: float | None = None,
        now: datetime | None = None,
    ) -> None:
        """Record order submission event."""
        self.client_order_id = client_order_id
        self.submitted_qty = qty
        self.order_type = order_type
        self.limit_price = limit_px
        self.status = OrderStatus.SUBMITTED
        self.submitted_time = now or datetime.now(timezone.utc)

    def record_acceptance(
        self,
        venue_order_id: str,
        now: datetime | None = None,
    ) -> None:
        """Record order acceptance / acknowledgment event."""
        self.venue_order_id = venue_order_id
        self.status = OrderStatus.ACCEPTED
        self.accepted_time = now or datetime.now(timezone.utc)

    def record_fill(
        self,
        qty: float,
        px: float,
        now: datetime | None = None,
    ) -> None:
        """Record execution / fill event with volume-weighted average price."""
        self.filled_qty += qty
        self.cumulative_quote_volume += qty * px
        if self.filled_qty > 0:
            self.avg_fill_price = self.cumulative_quote_volume / self.filled_qty

        if self.filled_qty >= self.submitted_qty - 1e-8:
            self.status = OrderStatus.FILLED
        else:
            self.status = OrderStatus.PARTIALLY_FILLED

        self.last_fill_time = now or datetime.now(timezone.utc)

    def record_rejection(self, reason: str) -> None:
        """Record order rejection event."""
        self.status = OrderStatus.REJECTED
        self.rejection_reason = reason

    def record_denial(self, reason: str) -> None:
        """Record order denial event."""
        self.status = OrderStatus.DENIED
        self.rejection_reason = reason

    def record_cancellation(self) -> None:
        """Record order cancellation event."""
        self.status = OrderStatus.CANCELED

    def record_position(
        self,
        position_id: str,
        side: str,
        qty: float,
    ) -> None:
        """Record resulting position event."""
        self.position_id = position_id
        self.position_side = side
        self.position_qty = qty


@dataclass(frozen=True)
class StraddleExecutionReport:
    """Immutable snapshot report of the short straddle execution."""

    status: StraddleExecutionStatus
    spot_price: float
    strike: float
    expiry: datetime
    call_leg: LegExecutionState
    put_leg: LegExecutionState
    is_dry_run: bool

    @property
    def is_straddle_established(self) -> bool:
        """Check if both legs are filled and open positions are verified short."""
        return (
            self.status == StraddleExecutionStatus.STRADDLE_ESTABLISHED
            and self.call_leg.status == OrderStatus.FILLED
            and self.put_leg.status == OrderStatus.FILLED
            and (self.is_dry_run or (self.call_leg.position_side == "SHORT" and self.put_leg.position_side == "SHORT"))
        )

    def summary(self) -> str:
        """Generate a formatted multi-line summary of the straddle execution."""
        lines = [
            "==================================================",
            f"SHORT STRADDLE EXECUTION REPORT ({'DRY-RUN / SIMULATED' if self.is_dry_run else 'LIVE TESTNET'})",
            "==================================================",
            f"Overall Status:        {self.status.value}",
            f"BTC Reference Price:   {self.spot_price:,.2f}",
            f"Target Expiry:         {self.expiry.strftime('%Y-%m-%d %H:%M UTC')}",
            f"ATM Strike:            {self.strike:,.2f}",
            "--------------------------------------------------",
            f"CALL LEG:              {self.call_leg.instrument_id}",
            f"  Order Side / Type:   {self.call_leg.order_side} {self.call_leg.order_type}",
            f"  Order Status:        {self.call_leg.status.value}",
            f"  Client Order ID:     {self.call_leg.client_order_id or '<none>'}",
            f"  Venue Order ID:      {self.call_leg.venue_order_id or '<none>'}",
            f"  Submitted Qty:       {self.call_leg.submitted_qty:g}",
            f"  Filled Qty:          {self.call_leg.filled_qty:g}",
            f"  Avg Fill Price:      {self.call_leg.avg_fill_price:,.2f}",
            f"  Position ID:         {self.call_leg.position_id or '<none>'}",
            f"  Position Side / Qty: {self.call_leg.position_side or '<none>'} ({self.call_leg.position_qty:g})",
        ]
        if self.call_leg.rejection_reason:
            lines.append(f"  Failure Reason:      {self.call_leg.rejection_reason}")

        lines.extend([
            "--------------------------------------------------",
            f"PUT LEG:               {self.put_leg.instrument_id}",
            f"  Order Side / Type:   {self.put_leg.order_side} {self.put_leg.order_type}",
            f"  Order Status:        {self.put_leg.status.value}",
            f"  Client Order ID:     {self.put_leg.client_order_id or '<none>'}",
            f"  Venue Order ID:      {self.put_leg.venue_order_id or '<none>'}",
            f"  Submitted Qty:       {self.put_leg.submitted_qty:g}",
            f"  Filled Qty:          {self.put_leg.filled_qty:g}",
            f"  Avg Fill Price:      {self.put_leg.avg_fill_price:,.2f}",
            f"  Position ID:         {self.put_leg.position_id or '<none>'}",
            f"  Position Side / Qty: {self.put_leg.position_side or '<none>'} ({self.put_leg.position_qty:g})",
        ])
        if self.put_leg.rejection_reason:
            lines.append(f"  Failure Reason:      {self.put_leg.rejection_reason}")

        lines.extend([
            "--------------------------------------------------",
            f"Straddle Established:  {'YES' if self.is_straddle_established else 'NO'}",
            "==================================================",
        ])
        return "\n".join(lines)


class ShortStraddleConfig(StrategyConfig, kw_only=True):
    """Configuration for ShortStraddleStrategy."""

    underlying_instrument_id: str = "BTCUSDT-LINEAR.BYBIT"
    tie_breaker: str = "lower"
    max_quote_logs: int = 10
    target_expiry_iso: str | None = None
    execute_trades: bool = False
    order_type: str = "LIMIT"  # "LIMIT" or "MARKET"
    order_quantity: float | None = None
    limit_price: float | None = None
    environment: BybitEnvironment = BybitEnvironment.TESTNET
    catalog: Any = None


class ShortStraddleStrategy(OptionMarketDataStrategy):
    """
    NautilusTrader strategy for Milestone 4: Short Straddle Order Execution & Position Tracking.

    Key responsibilities:
    1. Inherits instrument discovery, reference price streaming, and ATM strike pairing.
    2. Enforces safety guards: Bybit Testnet only, execute_trades control flag, and idempotency.
    3. Derives valid contract order quantities using instrument specifications.
    4. Submits SELL orders for both ATM Call and ATM Put legs (short straddle).
    5. Tracks the complete lifecycle:
       - OrderSubmitted
       - OrderAccepted
       - OrderFilled (partial and complete fills)
       - OrderRejected / OrderDenied / OrderCanceled
    6. Tracks resulting open positions (PositionOpened, PositionChanged, PositionClosed).
    7. Verifies that resulting positions are SHORT on both legs.
    8. Handles partial executions and single-leg failures safely without claiming false success.
    """

    def __init__(self, config: ShortStraddleConfig) -> None:
        super().__init__(
            OptionMarketDataConfig(
                underlying_instrument_id=config.underlying_instrument_id,
                tie_breaker=config.tie_breaker,
                max_quote_logs=config.max_quote_logs,
                target_expiry_iso=config.target_expiry_iso,
            )
        )
        self.execute_trades: bool = config.execute_trades
        self.order_type: str = config.order_type.upper()
        self.order_quantity_override: float | None = config.order_quantity
        self.limit_price_override: float | None = config.limit_price
        self.environment: BybitEnvironment = config.environment

        # Straddle execution state machine
        self.straddle_status: StraddleExecutionStatus = StraddleExecutionStatus.NOT_STARTED
        self.call_state: LegExecutionState | None = None
        self.put_state: LegExecutionState | None = None
        self._execution_initiated: bool = False

        # Order & Position maps for fast routing
        self._client_order_map: dict[str, str] = {}  # client_order_id_str -> "CALL" | "PUT"
        self._instrument_leg_map: dict[str, str] = {}  # instrument_id_str -> "CALL" | "PUT"

        # Test order factory fallback for offline unit testing
        self._test_order_factory: OrderFactory | None = None

    @property
    def order_factory(self) -> OrderFactory:
        """Return the active order factory, falling back to a test factory when not registered."""
        base_factory = super().order_factory
        if base_factory is not None:
            return base_factory
        if self._test_order_factory is None:
            self._test_order_factory = OrderFactory(
                trader_id=TraderId("BTC-STRADDLE-001"),
                strategy_id=StrategyId("STRAT-001"),
                clock=LiveClock(),
            )
        return self._test_order_factory

    def on_start(self) -> None:
        """Verify safety guards and start market data subscriptions."""
        if self.environment != BybitEnvironment.TESTNET:
            raise ValueError(
                f"Safety violation: ShortStraddleStrategy requires BybitEnvironment.TESTNET (got {self.environment}). "
                "Mainnet execution is strictly prohibited."
            )

        self.log.info("ShortStraddleStrategy starting...")
        self.log.info(f"  Environment:    {self.environment.name}")
        self.log.info(f"  Execute Trades: {self.execute_trades}")
        self.log.info(f"  Order Type:     {self.order_type}")
        if not self.execute_trades:
            self.log.info("  DRY-RUN MODE ACTIVE: No live orders will be sent to the venue.")

        super().on_start()

    def _select_and_subscribe_straddle(self, spot_price: float) -> None:
        """Override to pair straddle and trigger the two-leg execution workflow."""
        super()._select_and_subscribe_straddle(spot_price)

        if self.selected_straddle is not None and not self._execution_initiated:
            self._setup_straddle_legs(self.selected_straddle)
            self._trigger_straddle_execution()

    def _setup_straddle_legs(self, straddle: ATMStraddle) -> None:
        """Initialize leg execution states and route mappings."""
        call_id = straddle.call_instrument.instrument_id
        put_id = straddle.put_instrument.instrument_id

        self.call_state = LegExecutionState(
            leg_name="CALL",
            instrument_id=call_id,
            order_side="SELL",
            order_type=self.order_type,
        )
        self.put_state = LegExecutionState(
            leg_name="PUT",
            instrument_id=put_id,
            order_side="SELL",
            order_type=self.order_type,
        )

        self._instrument_leg_map[call_id] = "CALL"
        self._instrument_leg_map[put_id] = "PUT"
        self.straddle_status = StraddleExecutionStatus.PAIR_SELECTED

    def _determine_order_quantity(self, option_meta: OptionMetadata) -> Quantity:
        """Calculate the smallest valid order quantity for the contract."""
        if self.order_quantity_override is not None:
            qty_val = self.order_quantity_override
        else:
            # Query instrument from cache if available to get precise lot size
            inst = self.cache.instrument(InstrumentId.from_str(option_meta.instrument_id))
            if inst is not None and inst.min_quantity is not None:
                qty_val = float(inst.min_quantity)
            elif inst is not None and inst.size_increment is not None:
                qty_val = float(inst.size_increment)
            else:
                # Default smallest testnet option lot size (0.01 or 0.001 BTC)
                qty_val = 0.01

        precision = option_meta.size_precision
        return Quantity.from_str(f"{qty_val:.{precision}f}")

    def _determine_order_price(self, inst_id_str: str, option_meta: OptionMetadata) -> Price:
        """Determine the limit order price (best bid if available, or override/fallback)."""
        if self.limit_price_override is not None:
            px_val = self.limit_price_override
        else:
            latest_quote = self.latest_quotes.get(inst_id_str)
            if latest_quote is not None and latest_quote.bid_price.as_double() > 0:
                # Best bid crosses the spread for an immediate fill
                return latest_quote.bid_price
            else:
                # Fallback price when testnet orderbook is empty
                px_val = 100.0

        precision = option_meta.price_precision
        return Price.from_str(f"{px_val:.{precision}f}")

    def _trigger_straddle_execution(self) -> None:
        """Coordinate and submit the SELL orders for both Call and Put legs."""
        if self._execution_initiated:
            self.log.warning("Straddle execution already initiated. Skipping duplicate execution.")
            return

        if self.selected_straddle is None or self.call_state is None or self.put_state is None:
            self.log.error("Cannot execute straddle: pair not selected or initialized.")
            return

        # 1. Enforce environment check
        if self.environment != BybitEnvironment.TESTNET:
            raise ValueError("Execution aborted: Mainnet environment detected.")

        # 2. Validate pair integrity
        straddle = self.selected_straddle
        validate_straddle_pair(
            call=straddle.call_instrument,
            put=straddle.put_instrument,
            spot_price=straddle.spot_price,
        )

        # 3. Mark idempotency guard
        self._execution_initiated = True

        # 4. Calculate quantities and prices
        call_qty = self._determine_order_quantity(straddle.call_instrument)
        put_qty = self._determine_order_quantity(straddle.put_instrument)

        call_inst_id = InstrumentId.from_str(straddle.call_instrument.instrument_id)
        put_inst_id = InstrumentId.from_str(straddle.put_instrument.instrument_id)

        call_px = self._determine_order_price(str(call_inst_id), straddle.call_instrument)
        put_px = self._determine_order_price(str(put_inst_id), straddle.put_instrument)

        # 5. Handle DRY-RUN mode (execute_trades=False)
        if not self.execute_trades:
            self.straddle_status = StraddleExecutionStatus.DRY_RUN
            self.call_state.record_submission(
                client_order_id="DRYRUN-CALL-001",
                qty=call_qty.as_double(),
                order_type=self.order_type,
                limit_px=call_px.as_double() if self.order_type == "LIMIT" else None,
            )
            self.put_state.record_submission(
                client_order_id="DRYRUN-PUT-001",
                qty=put_qty.as_double(),
                order_type=self.order_type,
                limit_px=put_px.as_double() if self.order_type == "LIMIT" else None,
            )
            self.log.info("==================================================")
            self.log.info("DRY-RUN / SIMULATED STRADDLE SUBMISSION:")
            self.log.info(f"  SELL CALL: {call_inst_id} | Qty: {call_qty} | Type: {self.order_type} @ {call_px}")
            self.log.info(f"  SELL PUT:  {put_inst_id}  | Qty: {put_qty}  | Type: {self.order_type} @ {put_px}")
            self.log.info("  (Set EXECUTE_TRADES=true to submit live orders on Bybit Testnet)")
            self.log.info("==================================================")
            self._push_state_to_api()
            return

        # 6. Construct live orders via OrderFactory
        self.log.info("Constructing and submitting short straddle orders...")
        if self.order_type == "MARKET":
            call_order = self.order_factory.market(
                instrument_id=call_inst_id,
                order_side=OrderSide.SELL,
                quantity=call_qty,
                time_in_force=TimeInForce.GTC,
            )
            put_order = self.order_factory.market(
                instrument_id=put_inst_id,
                order_side=OrderSide.SELL,
                quantity=put_qty,
                time_in_force=TimeInForce.GTC,
            )
        else:
            call_order = self.order_factory.limit(
                instrument_id=call_inst_id,
                order_side=OrderSide.SELL,
                quantity=call_qty,
                price=call_px,
                time_in_force=TimeInForce.GTC,
            )
            put_order = self.order_factory.limit(
                instrument_id=put_inst_id,
                order_side=OrderSide.SELL,
                quantity=put_qty,
                price=put_px,
                time_in_force=TimeInForce.GTC,
            )

        # Register client order mappings
        call_cid = str(call_order.client_order_id)
        put_cid = str(put_order.client_order_id)
        self._client_order_map[call_cid] = "CALL"
        self._client_order_map[put_cid] = "PUT"

        self.call_state.record_submission(
            client_order_id=call_cid,
            qty=call_qty.as_double(),
            order_type=self.order_type,
            limit_px=call_px.as_double() if self.order_type == "LIMIT" else None,
        )
        self.put_state.record_submission(
            client_order_id=put_cid,
            qty=put_qty.as_double(),
            order_type=self.order_type,
            limit_px=put_px.as_double() if self.order_type == "LIMIT" else None,
        )

        self.straddle_status = StraddleExecutionStatus.SUBMITTED

        # Submit orders
        self.log.info(f"Submitting ATM CALL order: {call_order}")
        self.submit_order(call_order)

        self.log.info(f"Submitting ATM PUT order:  {put_order}")
        self.submit_order(put_order)
        self._push_state_to_api()

    # -------------------------------------------------------------------------
    # Order Event Callbacks
    # -------------------------------------------------------------------------

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        """Handle order submitted event from venue adapter."""
        leg = self._get_leg_by_order_id(str(event.client_order_id))
        if leg is None:
            return

        self.log.info(f"[{leg.leg_name} SUBMITTED] {leg.instrument_id} (ClientOrderId={event.client_order_id})")
        leg.status = OrderStatus.SUBMITTED
        self._push_state_to_api()

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """Handle order accepted / acknowledged event from venue."""
        leg = self._get_leg_by_order_id(str(event.client_order_id))
        if leg is None:
            return

        venue_oid = str(event.venue_order_id)
        leg.record_acceptance(venue_oid)
        self.log.info(
            f"[{leg.leg_name} ACCEPTED] {leg.instrument_id} | VenueOrderId: {venue_oid}"
        )

        # Check if both legs are accepted
        if (
            self.call_state
            and self.put_state
            and self.call_state.status in (OrderStatus.ACCEPTED, OrderStatus.FILLED)
            and self.put_state.status in (OrderStatus.ACCEPTED, OrderStatus.FILLED)
            and self.straddle_status == StraddleExecutionStatus.SUBMITTED
        ):
            self.straddle_status = StraddleExecutionStatus.ACCEPTED
        self._push_state_to_api()

    def on_order_rejected(self, event: OrderRejected) -> None:
        """Handle order rejection event from venue."""
        leg = self._get_leg_by_order_id(str(event.client_order_id))
        if leg is None:
            return

        reason = str(event.reason)
        leg.record_rejection(reason)
        self.log.error(f"[{leg.leg_name} REJECTED] {leg.instrument_id} | Reason: {reason}")
        self._evaluate_straddle_failure(leg)
        self._push_state_to_api()

    def on_order_denied(self, event: OrderDenied) -> None:
        """Handle order denied event (client pre-flight validation rejection)."""
        leg = self._get_leg_by_order_id(str(event.client_order_id))
        if leg is None:
            return

        reason = str(event.reason)
        leg.record_denial(reason)
        self.log.error(f"[{leg.leg_name} DENIED] {leg.instrument_id} | Reason: {reason}")
        self._evaluate_straddle_failure(leg)
        self._push_state_to_api()

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """Handle order canceled event."""
        leg = self._get_leg_by_order_id(str(event.client_order_id))
        if leg is None:
            return

        leg.record_cancellation()
        self.log.warning(f"[{leg.leg_name} CANCELED] {leg.instrument_id}")
        self._evaluate_straddle_failure(leg)
        self._push_state_to_api()

    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle order filled event."""
        leg = self._get_leg_by_order_id(str(event.client_order_id))
        if leg is None:
            return

        fill_qty = event.last_qty.as_double()
        fill_px = event.last_px.as_double()
        leg.record_fill(fill_qty, fill_px)

        self.log.info(
            f"[{leg.leg_name} FILL] {leg.instrument_id} | "
            f"Filled: {fill_qty:g} @ {fill_px:,.2f} | "
            f"Cumulative: {leg.filled_qty:g}/{leg.submitted_qty:g} | "
            f"Avg Price: {leg.avg_fill_price:,.2f}"
        )

        self._evaluate_straddle_completion()
        self._push_state_to_api()

    # -------------------------------------------------------------------------
    # Position Event Callbacks
    # -------------------------------------------------------------------------

    def on_position_opened(self, event: PositionOpened) -> None:
        """Handle position opened event. Verify SHORT position for straddle legs."""
        inst_id_str = str(event.instrument_id)
        leg = self._get_leg_by_instrument_id(inst_id_str)
        if leg is None:
            return

        side_str = event.side.name if hasattr(event.side, "name") else str(event.side)
        if side_str == "3" or event.side == PositionSide.SHORT:
            side_str = "SHORT"
        pos_id = str(event.position_id)
        qty = event.quantity.as_double()

        leg.record_position(position_id=pos_id, side=side_str, qty=qty)
        self.log.info(
            f"[{leg.leg_name} POSITION OPENED] {inst_id_str} | "
            f"Side: {side_str} | Qty: {qty:g} | Avg Open: {event.avg_px_open:,.2f} | "
            f"PositionId: {pos_id}"
        )

        if event.side != PositionSide.SHORT and side_str != "SHORT":
            self.log.error(
                f"Position anomaly: Expected SHORT position for {leg.leg_name} leg, got {event.side}!"
            )
        self._push_state_to_api()

    def on_position_changed(self, event: PositionChanged) -> None:
        """Handle position changed event."""
        inst_id_str = str(event.instrument_id)
        leg = self._get_leg_by_instrument_id(inst_id_str)
        if leg is None:
            return

        side_str = event.side.name if hasattr(event.side, "name") else str(event.side)
        if side_str == "3" or event.side == PositionSide.SHORT:
            side_str = "SHORT"
        qty = event.quantity.as_double()
        leg.record_position(position_id=str(event.position_id), side=side_str, qty=qty)
        self.log.info(f"[{leg.leg_name} POSITION CHANGED] {inst_id_str} | Qty: {qty:g}")
        self._push_state_to_api()

    def on_position_closed(self, event: PositionClosed) -> None:
        """Handle position closed event."""
        inst_id_str = str(event.instrument_id)
        leg = self._get_leg_by_instrument_id(inst_id_str)
        if leg is None:
            return

        self.log.info(f"[{leg.leg_name} POSITION CLOSED] {inst_id_str}")
        self._push_state_to_api()

    # -------------------------------------------------------------------------
    # Internal Evaluation & Helper Methods
    # -------------------------------------------------------------------------

    def _push_state_to_api(self) -> None:
        """Push full execution state snapshot to the API bridge."""
        if not _API_AVAILABLE or self.call_state is None or self.put_state is None:
            return
        straddle = self.selected_straddle
        _api_bridge.update_state(
            straddle_status=self.straddle_status.value,
            is_dry_run=not self.execute_trades,
            execute_trades=self.execute_trades,
            # ATM Straddle
            atm_selected=(straddle is not None),
            atm_strike=straddle.strike if straddle else None,
            atm_expiry=straddle.expiry.isoformat() if straddle else None,
            atm_call_id=straddle.call_instrument.instrument_id if straddle else None,
            atm_put_id=straddle.put_instrument.instrument_id if straddle else None,
            # Call leg
            call_order_status=self.call_state.status.value,
            call_instrument_id=self.call_state.instrument_id,
            call_client_order_id=self.call_state.client_order_id,
            call_venue_order_id=self.call_state.venue_order_id,
            call_submitted_qty=self.call_state.submitted_qty,
            call_filled_qty=self.call_state.filled_qty,
            call_avg_fill_price=self.call_state.avg_fill_price,
            call_limit_price=self.call_state.limit_price,
            call_rejection_reason=self.call_state.rejection_reason,
            call_position_id=self.call_state.position_id,
            call_position_side=self.call_state.position_side,
            call_position_qty=self.call_state.position_qty,
            # Put leg
            put_order_status=self.put_state.status.value,
            put_instrument_id=self.put_state.instrument_id,
            put_client_order_id=self.put_state.client_order_id,
            put_venue_order_id=self.put_state.venue_order_id,
            put_submitted_qty=self.put_state.submitted_qty,
            put_filled_qty=self.put_state.filled_qty,
            put_avg_fill_price=self.put_state.avg_fill_price,
            put_limit_price=self.put_state.limit_price,
            put_rejection_reason=self.put_state.rejection_reason,
            put_position_id=self.put_state.position_id,
            put_position_side=self.put_state.position_side,
            put_position_qty=self.put_state.position_qty,
            straddle_established=(self.straddle_status.value == "STRADDLE_ESTABLISHED"),
        )

    def _get_leg_by_order_id(self, client_order_id_str: str) -> LegExecutionState | None:
        """Find the corresponding LegExecutionState by client order ID."""
        leg_name = self._client_order_map.get(client_order_id_str)
        if leg_name == "CALL":
            return self.call_state
        elif leg_name == "PUT":
            return self.put_state
        return None

    def _get_leg_by_instrument_id(self, instrument_id_str: str) -> LegExecutionState | None:
        """Find the corresponding LegExecutionState by instrument ID."""
        leg_name = self._instrument_leg_map.get(instrument_id_str)
        if leg_name == "CALL":
            return self.call_state
        elif leg_name == "PUT":
            return self.put_state
        return None

    def _evaluate_straddle_completion(self) -> None:
        """Check whether both legs are filled and straddle is fully established."""
        if self.call_state is None or self.put_state is None:
            return

        call_filled = self.call_state.status == OrderStatus.FILLED
        put_filled = self.put_state.status == OrderStatus.FILLED

        if call_filled and put_filled:
            self.straddle_status = StraddleExecutionStatus.STRADDLE_ESTABLISHED
            self.log.info("==================================================")
            self.log.info("SUCCESS: SHORT STRADDLE FULLY ESTABLISHED!")
            self.log.info(
                f"  CALL: {self.call_state.filled_qty:g} filled @ avg {self.call_state.avg_fill_price:,.2f}"
            )
            self.log.info(
                f"  PUT:  {self.put_state.filled_qty:g} filled @ avg {self.put_state.avg_fill_price:,.2f}"
            )
            self.log.info("==================================================")
        elif call_filled or put_filled:
            other_leg = self.put_state if call_filled else self.call_state
            if other_leg.status in (OrderStatus.REJECTED, OrderStatus.DENIED, OrderStatus.CANCELED):
                self.straddle_status = StraddleExecutionStatus.ONE_LEG_FAILED
                self.log.error(
                    f"CRITICAL ONE-LEG FAILURE: One leg filled but other leg {other_leg.status.value}! "
                    "Short straddle is incomplete and exposed."
                )
            else:
                self.straddle_status = StraddleExecutionStatus.PARTIALLY_FILLED

    def _evaluate_straddle_failure(self, failed_leg: LegExecutionState) -> None:
        """Evaluate overall straddle state when one leg experiences a failure/cancellation."""
        if self.call_state is None or self.put_state is None:
            return

        other_leg = self.put_state if failed_leg.leg_name == "CALL" else self.call_state

        if other_leg.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            self.straddle_status = StraddleExecutionStatus.ONE_LEG_FAILED
            self.log.error(
                f"ALERT: ONE-LEG EXECUTION FAILURE! {failed_leg.leg_name} failed ({failed_leg.status.value}) "
                f"while {other_leg.leg_name} is already active/filled!"
            )
        elif other_leg.status in (OrderStatus.REJECTED, OrderStatus.DENIED, OrderStatus.CANCELED):
            self.straddle_status = StraddleExecutionStatus.EXECUTION_FAILED
            self.log.error("STRADDLE EXECUTION FAILED: Both option orders were rejected/canceled.")

    def get_execution_report(self) -> StraddleExecutionReport | None:
        """Generate an immutable snapshot report of the straddle execution."""
        if self.selected_straddle is None or self.call_state is None or self.put_state is None:
            return None

        return StraddleExecutionReport(
            status=self.straddle_status,
            spot_price=self.selected_straddle.spot_price,
            strike=self.selected_straddle.strike,
            expiry=self.selected_straddle.expiry,
            call_leg=self.call_state,
            put_leg=self.put_state,
            is_dry_run=not self.execute_trades,
        )
