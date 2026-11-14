"""
Level 2 – Automated Risk Management for BTC Short Straddle.

This module implements continuous stop-loss (+1%) and take-profit (2%) monitoring
and execution for open BTC short straddle positions using NautilusTrader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import (
    OrderSide,
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
    PositionClosed,
)
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from src.btc_straddle.execution import (
    OrderStatus,
    ShortStraddleConfig,
    ShortStraddleStrategy,
    StraddleExecutionStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Data Models
# ---------------------------------------------------------------------------

class RiskState(str, Enum):
    """Lifecycle state of automated risk management."""

    PENDING = "PENDING"                    # Awaiting straddle entry / fill
    MONITORING = "MONITORING"              # Position open, actively monitoring buyback premium
    EXIT_IN_PROGRESS = "EXIT_IN_PROGRESS"  # Stop-loss or take-profit triggered, exit orders active
    EXIT_COMPLETED = "EXIT_COMPLETED"      # Both legs fully closed
    EXIT_FAILED = "EXIT_FAILED"            # Exit orders rejected / canceled without fill


class ExitReason(str, Enum):
    """Reason for triggering straddle exit."""

    NONE = "NONE"
    STOP_LOSS = "STOP_LOSS"        # Buyback cost >= entry * 1.01 (+1% loss)
    TAKE_PROFIT = "TAKE_PROFIT"    # Buyback cost <= entry * 0.98 (2% profit)


@dataclass
class LegRiskState:
    """Detailed risk and exit tracking state for an individual straddle leg."""

    leg_name: str                      # "CALL" or "PUT"
    instrument_id: str
    position_qty: float = 0.0          # Current open short quantity
    entry_price: float = 0.0           # Execution price per unit
    exit_submitted_qty: float = 0.0    # Quantity requested in exit order
    exit_filled_qty: float = 0.0       # Quantity filled on exit
    exit_avg_price: float = 0.0        # Average fill price on exit
    exit_order_id: str | None = None   # ClientOrderId for the exit order
    exit_order_status: OrderStatus = OrderStatus.NOT_SUBMITTED
    last_ask: float | None = None      # Latest ask price for buyback valuation
    rejection_reason: str | None = None

    @property
    def remaining_qty(self) -> float:
        """Return remaining open position quantity still requiring closure."""
        return max(0.0, self.position_qty - self.exit_filled_qty)

    @property
    def is_closed(self) -> bool:
        """Return True only when remaining quantity is effectively zero."""
        return self.remaining_qty <= 1e-8


@dataclass(frozen=True)
class RiskConfig:
    """Configuration parameters for straddle risk management."""

    stop_loss_pct: float = 0.01    # +1% stop loss on combined entry premium
    take_profit_pct: float = 0.02  # 2% take profit on combined entry premium


# ---------------------------------------------------------------------------
# Core Risk Management Engine (Isolated, pure-logic & state manager)
# ---------------------------------------------------------------------------

class StraddleRiskManager:
    """
    Manages premium calculations, thresholds, exit triggers, and fill tracking
    for a two-leg short straddle.

    Parameters
    ----------
    call_instrument_id : str
        Instrument ID of the short Call leg.
    put_instrument_id : str
        Instrument ID of the short Put leg.
    call_qty : float
        Initial position quantity for the Call leg.
    put_qty : float
        Initial position quantity for the Put leg.
    call_entry_price : float
        Fill / entry price per unit for the Call leg.
    put_entry_price : float
        Fill / entry price per unit for the Put leg.
    config : RiskConfig, optional
        Risk threshold configuration. Defaults to 1% stop-loss, 2% take-profit.
    """

    def __init__(
        self,
        call_instrument_id: str,
        put_instrument_id: str,
        call_qty: float,
        put_qty: float,
        call_entry_price: float,
        put_entry_price: float,
        config: RiskConfig | None = None,
    ) -> None:
        self.config = config or RiskConfig()

        self.call_leg = LegRiskState(
            leg_name="CALL",
            instrument_id=call_instrument_id,
            position_qty=call_qty,
            entry_price=call_entry_price,
        )
        self.put_leg = LegRiskState(
            leg_name="PUT",
            instrument_id=put_instrument_id,
            position_qty=put_qty,
            entry_price=put_entry_price,
        )

        # Combined entry premium and risk thresholds
        self.combined_entry_premium = self._calculate_combined_entry_premium()
        self.stop_loss_threshold = self.combined_entry_premium * (1.0 + self.config.stop_loss_pct)
        self.take_profit_threshold = self.combined_entry_premium * (1.0 - self.config.take_profit_pct)

        # State tracking
        self.state = RiskState.MONITORING
        self.exit_reason = ExitReason.NONE
        self.last_combined_buyback: float | None = None
        self.trigger_time: datetime | None = None

        logger.info(
            f"RiskManager initialized: Combined Entry=${self.combined_entry_premium:,.2f} | "
            f"Stop-Loss Threshold=${self.stop_loss_threshold:,.2f} (+{self.config.stop_loss_pct*100:g}%) | "
            f"Take-Profit Threshold=${self.take_profit_threshold:,.2f} (-{self.config.take_profit_pct*100:g}%)"
        )

    def _calculate_combined_entry_premium(self) -> float:
        """Calculate initial combined premium received from selling both legs."""
        call_premium = self.call_leg.entry_price * self.call_leg.position_qty
        put_premium = self.put_leg.entry_price * self.put_leg.position_qty
        return call_premium + put_premium

    def update_quote(self, instrument_id: str, ask_price: float | None) -> None:
        """
        Update the latest available ask price for a leg.

        Parameters
        ----------
        instrument_id : str
            The instrument ID of the quote update.
        ask_price : float | None
            The top-of-book ask price.
        """
        if instrument_id == self.call_leg.instrument_id:
            self.call_leg.last_ask = ask_price if (ask_price is not None and ask_price > 0) else None
        elif instrument_id == self.put_leg.instrument_id:
            self.put_leg.last_ask = ask_price if (ask_price is not None and ask_price > 0) else None

    def calculate_combined_buyback_premium(self) -> float | None:
        """
        Calculate the total premium required to buy back both open short option positions
        using current ASK prices.

        Returns
        -------
        float | None
            Combined buyback cost, or None if either ask price or quantity is invalid.
        """
        # Validate that quotes exist for both legs
        if self.call_leg.last_ask is None or self.put_leg.last_ask is None:
            return None

        # Validate non-negative quantities
        if self.call_leg.remaining_qty < 0 or self.put_leg.remaining_qty < 0:
            return None

        call_buyback = self.call_leg.last_ask * self.call_leg.remaining_qty
        put_buyback = self.put_leg.last_ask * self.put_leg.remaining_qty
        return call_buyback + put_buyback

    def evaluate_risk(self) -> ExitReason | None:
        """
        Evaluate current combined buyback premium against stop-loss and take-profit thresholds.

        Returns
        -------
        ExitReason | None
            The triggered exit reason, or None if within the hold range or already exiting.
        """
        # Guard: Do not re-evaluate or trigger duplicate exits once an exit is in progress or completed
        if self.state != RiskState.MONITORING:
            return None

        combined_buyback = self.calculate_combined_buyback_premium()
        if combined_buyback is None:
            return None

        self.last_combined_buyback = combined_buyback

        # Stop-Loss rule: Combined Buyback Premium >= Combined Entry Premium * 1.01
        if combined_buyback >= self.stop_loss_threshold - 1e-9:
            self.state = RiskState.EXIT_IN_PROGRESS
            self.exit_reason = ExitReason.STOP_LOSS
            self.trigger_time = datetime.now(timezone.utc)
            logger.warning(
                f"🚨 STOP LOSS TRIGGERED! Combined Buyback=${combined_buyback:,.2f} >= "
                f"Threshold=${self.stop_loss_threshold:,.2f} (+{self.config.stop_loss_pct*100:g}% loss). "
                "Initiating simultaneous buyback of both legs."
            )
            return ExitReason.STOP_LOSS

        # Take-Profit rule: Combined Buyback Premium <= Combined Entry Premium * 0.98
        elif combined_buyback <= self.take_profit_threshold + 1e-9:
            self.state = RiskState.EXIT_IN_PROGRESS
            self.exit_reason = ExitReason.TAKE_PROFIT
            self.trigger_time = datetime.now(timezone.utc)
            logger.info(
                f"🎯 TAKE PROFIT TRIGGERED! Combined Buyback=${combined_buyback:,.2f} <= "
                f"Threshold=${self.take_profit_threshold:,.2f} (-{self.config.take_profit_pct*100:g}% cost). "
                "Initiating simultaneous buyback of both legs."
            )
            return ExitReason.TAKE_PROFIT

        return None

    def record_exit_submission(self, leg_name: str, order_id: str, qty: float) -> None:
        """Record the submission of an exit buy order for a specific leg."""
        leg = self.call_leg if leg_name == "CALL" else self.put_leg
        leg.exit_order_id = order_id
        leg.exit_submitted_qty = qty
        leg.exit_order_status = OrderStatus.SUBMITTED

    def record_exit_fill(self, leg_name: str, fill_qty: float, fill_px: float) -> None:
        """
        Record fill for a leg's exit order and update completion status.

        Parameters
        ----------
        leg_name : str
            "CALL" or "PUT".
        fill_qty : float
            Quantity filled in this execution event.
        fill_px : float
            Execution price of this fill event.
        """
        leg = self.call_leg if leg_name == "CALL" else self.put_leg

        # Update average fill price
        prev_value = leg.exit_avg_price * leg.exit_filled_qty
        new_value = prev_value + (fill_px * fill_qty)
        leg.exit_filled_qty += fill_qty
        if leg.exit_filled_qty > 0:
            leg.exit_avg_price = new_value / leg.exit_filled_qty

        if leg.is_closed:
            leg.exit_order_status = OrderStatus.FILLED
            logger.info(f"DataCatalog/Risk: [{leg_name} EXIT COMPLETED] Closed {leg.exit_filled_qty:g} units.")
        else:
            leg.exit_order_status = OrderStatus.PARTIALLY_FILLED
            logger.warning(
                f"DataCatalog/Risk: [{leg_name} PARTIAL FILL] Filled: {leg.exit_filled_qty:g}/"
                f"{leg.position_qty:g} | Remaining open: {leg.remaining_qty:g}"
            )

        # Check straddle-wide completion
        if self.call_leg.is_closed and self.put_leg.is_closed:
            self.state = RiskState.EXIT_COMPLETED
            logger.info(
                "==================================================\n"
                f"✓ STRADDLE RISK EXIT FULLY COMPLETED ({self.exit_reason.value})\n"
                f"  CALL closed: {self.call_leg.exit_filled_qty:g} @ avg {self.call_leg.exit_avg_price:,.2f}\n"
                f"  PUT closed:  {self.put_leg.exit_filled_qty:g} @ avg {self.put_leg.exit_avg_price:,.2f}\n"
                "=================================================="
            )

    def record_exit_rejection(self, leg_name: str, reason: str) -> None:
        """Record order rejection for a leg exit order without corrupting position state."""
        leg = self.call_leg if leg_name == "CALL" else self.put_leg
        leg.exit_order_status = OrderStatus.REJECTED
        leg.rejection_reason = reason
        logger.error(f"[{leg_name} EXIT REJECTED] {leg.instrument_id} | Reason: {reason} | Position remains OPEN.")

    def record_exit_cancellation(self, leg_name: str) -> None:
        """Record order cancellation for a leg exit order."""
        leg = self.call_leg if leg_name == "CALL" else self.put_leg
        leg.exit_order_status = OrderStatus.CANCELED
        logger.warning(
            f"[{leg_name} EXIT CANCELED] Remaining open position: {leg.remaining_qty:g} units."
        )


# ---------------------------------------------------------------------------
# Level 2 Strategy: RiskManagedStraddleStrategy
# ---------------------------------------------------------------------------

class RiskManagedStraddleConfig(ShortStraddleConfig, kw_only=True):
    """Configuration for Level 2 automated risk management strategy."""

    stop_loss_pct: float = 0.01    # +1% stop loss
    take_profit_pct: float = 0.02  # 2% take profit


class RiskManagedStraddleStrategy(ShortStraddleStrategy):
    """
    Level 2 Short Straddle strategy with automated risk management.

    Extends Level 1 by continuously monitoring the combined buyback premium
    (using ASK prices) and submitting simultaneous BUY orders when Stop-Loss
    (+1%) or Take-Profit (2%) thresholds are crossed.
    """

    def __init__(self, config: RiskManagedStraddleConfig) -> None:
        super().__init__(config)
        self.risk_config = RiskConfig(
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
        )
        self.risk_manager: StraddleRiskManager | None = None
        self._exit_order_map: dict[str, str] = {}  # client_order_id -> "CALL" | "PUT"
        self._exit_in_flight: bool = False

    def _evaluate_straddle_completion(self) -> None:
        """Override to initialize Level 2 risk monitoring once short straddle is established."""
        super()._evaluate_straddle_completion()

        # If straddle is established and risk manager not yet created, initialize it
        if (
            self.straddle_status == StraddleExecutionStatus.STRADDLE_ESTABLISHED
            and self.risk_manager is None
            and self.call_state is not None
            and self.put_state is not None
        ):
            self.risk_manager = StraddleRiskManager(
                call_instrument_id=self.call_state.instrument_id,
                put_instrument_id=self.put_state.instrument_id,
                call_qty=self.call_state.filled_qty,
                put_qty=self.put_state.filled_qty,
                call_entry_price=self.call_state.avg_fill_price,
                put_entry_price=self.put_state.avg_fill_price,
                config=self.risk_config,
            )
            self.log.info("Level 2 Automated Risk Management activated and actively monitoring.")

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Process incoming quote tick, update leg ask prices, and evaluate risk triggers."""
        # 1. Level 1 quote processing and persistence
        super().on_quote_tick(tick)

        # 2. Level 2 automated risk monitoring
        if self.risk_manager is None:
            return

        inst_id_str = str(tick.instrument_id)
        ask_val = tick.ask_price.as_double() if tick.ask_price is not None else None

        # Update ask price for the corresponding leg
        self.risk_manager.update_quote(inst_id_str, ask_val)

        # Evaluate risk conditions
        exit_reason = self.risk_manager.evaluate_risk()
        if exit_reason is not None and not self._exit_in_flight:
            self._trigger_straddle_exit(exit_reason)

    def _trigger_straddle_exit(self, reason: ExitReason) -> None:
        """
        Submit simultaneous BUY orders to close both short option positions.

        Parameters
        ----------
        reason : ExitReason
            STOP_LOSS or TAKE_PROFIT.
        """
        if self._exit_in_flight or self.risk_manager is None:
            return

        self._exit_in_flight = True
        self.log.info("==================================================")
        self.log.info(f"LEVEL 2 RISK TRIGGERED: {reason.value}")
        self.log.info("Executing simultaneous BUY orders to close both legs.")
        self.log.info("==================================================")

        call_leg = self.risk_manager.call_leg
        put_leg = self.risk_manager.put_leg

        # In dry-run mode (or when testing without private testnet execution credentials), simulate exit
        if not self.execute_trades:
            self.log.info("DRY-RUN MODE: Simulating straddle buyback execution...")
            call_cid = "DRYRUN-EXIT-CALL-001"
            put_cid = "DRYRUN-EXIT-PUT-001"

            self.risk_manager.record_exit_submission("CALL", call_cid, call_leg.remaining_qty)
            self.risk_manager.record_exit_submission("PUT", put_cid, put_leg.remaining_qty)

            # Record simulated fills at the current ask prices
            call_fill_px = call_leg.last_ask or call_leg.entry_price
            put_fill_px = put_leg.last_ask or put_leg.entry_price

            self.risk_manager.record_exit_fill("CALL", call_leg.remaining_qty, call_fill_px)
            self.risk_manager.record_exit_fill("PUT", put_leg.remaining_qty, put_fill_px)
            return

        # LIVE EXECUTION MODE (When valid Bybit Testnet API credentials are provided)
        call_id = InstrumentId.from_str(call_leg.instrument_id)
        put_id = InstrumentId.from_str(put_leg.instrument_id)

        call_qty = Quantity.from_str(f"{call_leg.remaining_qty:g}")
        put_qty = Quantity.from_str(f"{put_leg.remaining_qty:g}")

        # Construct BUY orders to cover short positions
        if self.order_type == "MARKET":
            call_order = self.order_factory.market(
                instrument_id=call_id,
                order_side=OrderSide.BUY,
                quantity=call_qty,
                time_in_force=TimeInForce.GTC,
            )
            put_order = self.order_factory.market(
                instrument_id=put_id,
                order_side=OrderSide.BUY,
                quantity=put_qty,
                time_in_force=TimeInForce.GTC,
            )
        else:
            # For LIMIT buy order, use current ask price to cross spread and fill immediately
            call_px = Price.from_str(f"{call_leg.last_ask:.2f}") if call_leg.last_ask else Price.from_str("100.00")
            put_px = Price.from_str(f"{put_leg.last_ask:.2f}") if put_leg.last_ask else Price.from_str("100.00")

            call_order = self.order_factory.limit(
                instrument_id=call_id,
                order_side=OrderSide.BUY,
                quantity=call_qty,
                price=call_px,
                time_in_force=TimeInForce.GTC,
            )
            put_order = self.order_factory.limit(
                instrument_id=put_id,
                order_side=OrderSide.BUY,
                quantity=put_qty,
                price=put_px,
                time_in_force=TimeInForce.GTC,
            )

        call_cid = str(call_order.client_order_id)
        put_cid = str(put_order.client_order_id)
        self._exit_order_map[call_cid] = "CALL"
        self._exit_order_map[put_cid] = "PUT"

        self.risk_manager.record_exit_submission("CALL", call_cid, call_leg.remaining_qty)
        self.risk_manager.record_exit_submission("PUT", put_cid, put_leg.remaining_qty)

        self.log.info(f"Submitting Call Buyback Order: {call_order}")
        self.submit_order(call_order)

        self.log.info(f"Submitting Put Buyback Order:  {put_order}")
        self.submit_order(put_order)

    # -------------------------------------------------------------------------
    # Exit Order Event Handlers
    # -------------------------------------------------------------------------

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        """Handle order submitted event for entry or exit orders."""
        cid = str(event.client_order_id)
        if cid in self._exit_order_map and self.risk_manager is not None:
            leg_name = self._exit_order_map[cid]
            self.log.info(f"[{leg_name} EXIT SUBMITTED] ClientOrderId: {cid}")
            return
        super().on_order_submitted(event)

    def on_order_accepted(self, event: OrderAccepted) -> None:
        """Handle order accepted event for entry or exit orders."""
        cid = str(event.client_order_id)
        if cid in self._exit_order_map and self.risk_manager is not None:
            leg_name = self._exit_order_map[cid]
            leg = self.risk_manager.call_leg if leg_name == "CALL" else self.risk_manager.put_leg
            leg.exit_order_status = OrderStatus.ACCEPTED
            self.log.info(f"[{leg_name} EXIT ACCEPTED] VenueOrderId: {event.venue_order_id}")
            return
        super().on_order_accepted(event)

    def on_order_rejected(self, event: OrderRejected) -> None:
        """Handle order rejected event for entry or exit orders."""
        cid = str(event.client_order_id)
        if cid in self._exit_order_map and self.risk_manager is not None:
            leg_name = self._exit_order_map[cid]
            self.risk_manager.record_exit_rejection(leg_name, str(event.reason))
            return
        super().on_order_rejected(event)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """Handle order canceled event for entry or exit orders."""
        cid = str(event.client_order_id)
        if cid in self._exit_order_map and self.risk_manager is not None:
            leg_name = self._exit_order_map[cid]
            self.risk_manager.record_exit_cancellation(leg_name)
            return
        super().on_order_canceled(event)

    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle order filled event for entry or exit orders."""
        cid = str(event.client_order_id)
        if cid in self._exit_order_map and self.risk_manager is not None:
            leg_name = self._exit_order_map[cid]
            qty = event.last_qty.as_double()
            px = event.last_px.as_double()
            self.risk_manager.record_exit_fill(leg_name, qty, px)
            return
        super().on_order_filled(event)
