"""Market data strategy for BTC reference price tracking and ATM Straddle pairing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy

from src.btc_straddle.instruments import (
    OptionMetadata,
    discover_btc_options,
    group_by_expiry,
)
from src.btc_straddle.straddle import (
    ATMStraddle,
    pair_atm_straddle,
    select_target_expiry,
)

# Optional API bridge — imported lazily so unit tests don't require fastapi/uvicorn
try:
    from src.btc_straddle import api as _api_bridge
    _API_AVAILABLE = True
except ImportError:
    _api_bridge = None  # type: ignore[assignment]
    _API_AVAILABLE = False


class OptionMarketDataConfig(StrategyConfig, kw_only=True):
    """Configuration for OptionMarketDataStrategy."""

    underlying_instrument_id: str = "BTCUSDT-LINEAR.BYBIT"
    tie_breaker: str = "lower"
    max_quote_logs: int = 10
    target_expiry_iso: str | None = None
    catalog: Any = None


class OptionMarketDataStrategy(Strategy):
    """
    NautilusTrader strategy responsible for:
    1. Discovering available BTC option contracts across all expiries.
    2. Subscribing to the live BTC reference/underlying price stream.
    3. Selecting the nearest unexpired target expiry.
    4. Calculating the nearest ATM strike to the reference price.
    5. Pairing and validating the matching ATM Call and Put options.
    6. Subscribing to live top-of-book quotes for the selected straddle pair.
    """

    def __init__(self, config: OptionMarketDataConfig) -> None:
        super().__init__(config)
        self._underlying_id: InstrumentId = InstrumentId.from_str(config.underlying_instrument_id)
        self._tie_breaker: str = config.tie_breaker
        self._max_quote_logs: int = config.max_quote_logs
        self._target_expiry_iso: str | None = config.target_expiry_iso
        self._catalog: Any = getattr(config, "catalog", None)

        # Discovered options cache
        self.discovered_options: list[OptionMetadata] = []
        self.subscribed_instrument_ids: set[InstrumentId] = set()

        # Reference price & selected straddle
        self.reference_price: float | None = None
        self.selected_straddle: ATMStraddle | None = None
        self.straddle_selected: bool = False

        # Quote tracking
        self.total_quotes_received: int = 0
        self.straddle_quotes_received: int = 0
        self.latest_quotes: dict[str, QuoteTick] = {}

    def on_start(self) -> None:
        """Invoked when the strategy starts. Discovers options and subscribes to BTC reference price."""
        self.log.info("OptionMarketDataStrategy starting: scanning cached instruments...")

        all_instruments = self.cache.instruments()
        self.discovered_options = discover_btc_options(all_instruments)

        if not self.discovered_options:
            self.log.warning(
                "No BTC option instruments found in cache. Ensure BybitDataClient loaded all instruments."
            )
            return

        # Group by expiry for reporting
        expiries = group_by_expiry(self.discovered_options)
        self.log.info(
            f"Discovered {len(self.discovered_options)} BTC option contracts across {len(expiries)} expiration dates."
        )

        for exp in list(expiries.keys())[:5]:
            opts = expiries[exp]
            calls = sum(1 for o in opts if o.is_call)
            puts = sum(1 for o in opts if o.is_put)
            strikes = [o.strike_price for o in opts]
            self.log.info(
                f"  Expiry {exp.strftime('%Y-%m-%d %H:%M UTC')}: {len(opts)} contracts ({calls} Calls, {puts} Puts) | Strikes: {min(strikes):g} - {max(strikes):g}"
            )

        # Push instrument discovery summary to the API bridge
        if _API_AVAILABLE:
            _api_bridge.update_state(
                node_status="RUNNING",
                is_connected=True,
                total_instruments_discovered=len(self.discovered_options),
                total_expiries=len(expiries),
            )

        # Subscribe to underlying BTC reference price
        self.log.info(f"Subscribing to BTC reference price feed: {self._underlying_id}...")
        self.subscribe_quote_ticks(self._underlying_id)
        self.subscribed_instrument_ids.add(self._underlying_id)

        # Persist underlying instrument definition to Data Catalog if enabled
        if self._catalog is not None:
            underlying_inst = self.cache.instrument(self._underlying_id)
            if underlying_inst is not None:
                self._catalog.record_instruments([underlying_inst])

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """
        Invoked when a quote tick event is received.

        Parameters
        ----------
        tick : QuoteTick
            The incoming quote tick event.
        """
        self.total_quotes_received += 1
        inst_id_str = str(tick.instrument_id)
        self.latest_quotes[inst_id_str] = tick

        # Persist quote tick to Data Catalog buffer if catalog is enabled
        if self._catalog is not None:
            self._catalog.record_quote(tick)

        # 1. Check if tick is for the BTC reference instrument
        if tick.instrument_id == self._underlying_id:
            bid = tick.bid_price.as_double()
            ask = tick.ask_price.as_double()
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
            elif bid > 0:
                mid = bid
            elif ask > 0:
                mid = ask
            else:
                return

            self.reference_price = mid

            # Push reference price to API bridge (throttled: every 10 ticks)
            if _API_AVAILABLE and self.total_quotes_received % 10 == 1:
                _api_bridge.update_state(
                    reference_price=mid,
                    reference_price_updated_at=datetime.now(timezone.utc).isoformat(),
                    total_quotes_received=self.total_quotes_received,
                )

            # Perform ATM Straddle pairing upon receiving the reference price
            if not self.straddle_selected and self.discovered_options:
                self._select_and_subscribe_straddle(mid)

        # 2. Check if tick is for one of the selected straddle legs
        elif self.selected_straddle is not None:
            call_id = self.selected_straddle.call_instrument.instrument_id
            put_id = self.selected_straddle.put_instrument.instrument_id

            if inst_id_str in (call_id, put_id):
                self.straddle_quotes_received += 1
                leg_name = "ATM CALL" if inst_id_str == call_id else "ATM PUT"

                # Push latest straddle quotes to API bridge
                if _API_AVAILABLE:
                    bid_val = tick.bid_price.as_double() or None
                    ask_val = tick.ask_price.as_double() or None
                    if inst_id_str == call_id:
                        _api_bridge.update_state(call_bid=bid_val, call_ask=ask_val)
                    else:
                        _api_bridge.update_state(put_bid=bid_val, put_ask=ask_val)

                if self.straddle_quotes_received <= self._max_quote_logs:
                    event_dt = datetime.fromtimestamp(tick.ts_event / 1e9, tz=timezone.utc)
                    self.log.info(
                        f"[{leg_name} QUOTE #{self.straddle_quotes_received}] {inst_id_str} | "
                        f"Bid: {tick.bid_price} (size: {tick.bid_size}) | "
                        f"Ask: {tick.ask_price} (size: {tick.ask_size}) | "
                        f"Time: {event_dt.strftime('%H:%M:%S.%f')[:-3]} UTC"
                    )
                    if self.straddle_quotes_received == self._max_quote_logs:
                        self.log.info(
                            f"Reached max straddle quote log limit ({self._max_quote_logs}). Further quotes will update state silently."
                        )

    def _select_and_subscribe_straddle(self, spot_price: float) -> None:
        """Calculate ATM strike, pair Call/Put, validate, and subscribe to their quotes."""
        try:
            target_expiry = None
            if self._target_expiry_iso:
                target_expiry = datetime.fromisoformat(self._target_expiry_iso)

            straddle = pair_atm_straddle(
                spot_price=spot_price,
                options=self.discovered_options,
                target_expiry=target_expiry,
                tie_breaker=self._tie_breaker,
            )

            self.selected_straddle = straddle
            self.straddle_selected = True

            self.log.info("==================================================")
            self.log.info("ATM STRADDLE PAIR SELECTED:")
            self.log.info(f"  BTC Reference Price: {straddle.spot_price:,.2f}")
            self.log.info(f"  Target Expiry:       {straddle.expiry.strftime('%Y-%m-%d %H:%M UTC')}")
            self.log.info(f"  Selected ATM Strike: {straddle.strike:,.2f}")
            self.log.info(f"  ATM Call Contract:   {straddle.call_instrument.instrument_id}")
            self.log.info(f"  ATM Put Contract:    {straddle.put_instrument.instrument_id}")
            self.log.info("  Straddle Validation: PASSED")
            self.log.info("==================================================")

            # Push ATM selection to API bridge
            if _API_AVAILABLE:
                _api_bridge.update_state(
                    atm_selected=True,
                    atm_strike=straddle.strike,
                    atm_expiry=straddle.expiry.isoformat(),
                    atm_call_id=straddle.call_instrument.instrument_id,
                    atm_put_id=straddle.put_instrument.instrument_id,
                    call_instrument_id=straddle.call_instrument.instrument_id,
                    put_instrument_id=straddle.put_instrument.instrument_id,
                )

            # Subscribe to Call and Put quote streams
            call_id = InstrumentId.from_str(straddle.call_instrument.instrument_id)
            put_id = InstrumentId.from_str(straddle.put_instrument.instrument_id)

            self.log.info(f"Subscribing to market data for ATM Call: {call_id}")
            self.subscribe_quote_ticks(call_id)
            self.subscribed_instrument_ids.add(call_id)

            self.log.info(f"Subscribing to market data for ATM Put:  {put_id}")
            self.subscribe_quote_ticks(put_id)
            self.subscribed_instrument_ids.add(put_id)

            # Persist selected ATM Call and Put instrument definitions to Data Catalog
            if self._catalog is not None:
                call_inst = self.cache.instrument(call_id)
                put_inst = self.cache.instrument(put_id)
                self._catalog.record_instruments([i for i in (call_inst, put_inst) if i is not None])

        except Exception as e:
            self.log.exception(f"Failed to pair ATM straddle: {e}")

    def on_stop(self) -> None:
        """Invoked when the strategy is stopping. Unsubscribes from all active feeds."""
        self.log.info(
            f"OptionMarketDataStrategy stopping. Total quotes: {self.total_quotes_received} "
            f"(Straddle quotes: {self.straddle_quotes_received})."
        )
        for inst_id in list(self.subscribed_instrument_ids):
            self.unsubscribe_quote_ticks(inst_id)
        self.subscribed_instrument_ids.clear()
        self.log.info("Unsubscribed from all market data feeds.")

        # Flush any remaining buffered quotes to the Data Catalog
        if self._catalog is not None:
            flushed = self._catalog.flush()
            if flushed > 0:
                self.log.info(f"Flushed {flushed} remaining quotes to Data Catalog on stop.")
