"""BTC Option instrument discovery, metadata extraction, and filtering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.instruments import CryptoOption, Instrument


@dataclass(frozen=True)
class OptionMetadata:
    """Standardized metadata representation of an option contract."""

    instrument_id: str
    raw_symbol: str
    underlying: str
    option_kind: str  # "CALL" or "PUT"
    strike_price: float
    expiration_ns: int
    expiration_dt: datetime
    price_precision: int
    size_precision: int

    @property
    def is_call(self) -> bool:
        """Return True if this contract is a Call option."""
        return self.option_kind.upper() == "CALL"

    @property
    def is_put(self) -> bool:
        """Return True if this contract is a Put option."""
        return self.option_kind.upper() == "PUT"

    def summary(self) -> str:
        """Return a formatted single-line summary of the option."""
        return (
            f"[{self.option_kind}] Strike: {self.strike_price:g} | "
            f"Expiry: {self.expiration_dt.strftime('%Y-%m-%d %H:%M UTC')} | "
            f"ID: {self.instrument_id}"
        )


def is_btc_option(instrument: Instrument) -> bool:
    """
    Determine whether an instrument is a BTC option contract.

    Parameters
    ----------
    instrument : Instrument
        The NautilusTrader instrument to evaluate.

    Returns
    -------
    bool
        True if the instrument is a BTC option, False otherwise.
    """
    # 1. Must be a CryptoOption (or have option attributes)
    if not isinstance(instrument, CryptoOption):
        # Fallback check for dynamic PyO3 types
        if not hasattr(instrument, "option_kind") or not hasattr(instrument, "strike_price"):
            return False

    # 2. Underlying currency or asset must be BTC
    underlying_str = str(getattr(instrument, "underlying", "")).upper()
    if underlying_str != "BTC":
        return False

    return True


def extract_option_metadata(instrument: Instrument) -> OptionMetadata:
    """
    Extract structured OptionMetadata from a NautilusTrader option instrument.

    Parameters
    ----------
    instrument : Instrument
        The option instrument.

    Returns
    -------
    OptionMetadata
        The extracted metadata.
    """
    if not hasattr(instrument, "option_kind") or not hasattr(instrument, "strike_price"):
        raise ValueError(f"Instrument {instrument.id} is not an option contract.")

    # Extract option kind (CALL / PUT)
    option_kind_obj = getattr(instrument, "option_kind")
    if isinstance(option_kind_obj, OptionKind):
        option_kind_str = option_kind_obj.name
    elif hasattr(option_kind_obj, "name"):
        option_kind_str = option_kind_obj.name
    else:
        # Fallback if raw enum integer: 1=CALL, 2=PUT
        option_kind_str = "CALL" if int(option_kind_obj) == 1 else "PUT"

    # Extract strike price as float
    strike_obj = getattr(instrument, "strike_price")
    if hasattr(strike_obj, "as_double"):
        strike_val = strike_obj.as_double()
    else:
        strike_val = float(strike_obj)

    # Extract expiration timestamp
    expiration_ns = int(getattr(instrument, "expiration_ns"))
    expiration_dt = datetime.fromtimestamp(expiration_ns / 1e9, tz=timezone.utc)

    # Underlying
    underlying_str = str(getattr(instrument, "underlying", "BTC")).upper()

    return OptionMetadata(
        instrument_id=str(instrument.id),
        raw_symbol=str(instrument.raw_symbol),
        underlying=underlying_str,
        option_kind=option_kind_str,
        strike_price=strike_val,
        expiration_ns=expiration_ns,
        expiration_dt=expiration_dt,
        price_precision=getattr(instrument, "price_precision", 2),
        size_precision=getattr(instrument, "size_precision", 4),
    )


def discover_btc_options(instruments: Iterable[Instrument]) -> list[OptionMetadata]:
    """
    Discover and filter all BTC option instruments from a given collection.

    Parameters
    ----------
    instruments : Iterable[Instrument]
        Collection of instruments (e.g. from Cache or InstrumentProvider).

    Returns
    -------
    list[OptionMetadata]
        Sorted list of discovered BTC option metadata (sorted by expiry then strike).
    """
    discovered: list[OptionMetadata] = []

    for inst in instruments:
        if is_btc_option(inst):
            try:
                metadata = extract_option_metadata(inst)
                discovered.append(metadata)
            except Exception:
                continue

    # Sort deterministically by expiration datetime, strike price, and option kind
    discovered.sort(key=lambda m: (m.expiration_ns, m.strike_price, m.option_kind))
    return discovered


def group_by_expiry(options: Sequence[OptionMetadata]) -> dict[datetime, list[OptionMetadata]]:
    """
    Group option metadata by expiration datetime.

    Parameters
    ----------
    options : Sequence[OptionMetadata]
        List of option metadata.

    Returns
    -------
    dict[datetime, list[OptionMetadata]]
        Dictionary mapping expiration datetime to list of options for that expiry.
    """
    grouped: dict[datetime, list[OptionMetadata]] = {}
    for opt in options:
        grouped.setdefault(opt.expiration_dt, []).append(opt)
    return grouped

