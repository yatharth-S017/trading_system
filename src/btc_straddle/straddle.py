"""ATM Strike Selection, Call/Put Pairing, and Straddle Validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from src.btc_straddle.instruments import OptionMetadata, group_by_expiry


@dataclass(frozen=True)
class ATMStraddle:
    """Representation of a validated At-The-Money Call and Put straddle pair."""

    spot_price: float
    expiry: datetime
    strike: float
    call_instrument: OptionMetadata
    put_instrument: OptionMetadata

    def summary(self) -> str:
        """Return a formatted multi-line summary of the straddle pair."""
        return (
            f"ATM Straddle Pair:\n"
            f"  BTC Reference Price: {self.spot_price:,.2f}\n"
            f"  Target Expiry:       {self.expiry.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"  Selected ATM Strike: {self.strike:,.2f}\n"
            f"  ATM Call:            {self.call_instrument.instrument_id}\n"
            f"  ATM Put:             {self.put_instrument.instrument_id}"
        )


def select_atm_strike(
    spot_price: float,
    available_strikes: Sequence[float],
    tie_breaker: str = "lower",
) -> float:
    """
    Select the strike price closest to the reference/spot price.

    Parameters
    ----------
    spot_price : float
        The current underlying BTC reference price (> 0).
    available_strikes : Sequence[float]
        List or sequence of available strike prices (must not be empty).
    tie_breaker : str, default "lower"
        Tie-breaking rule when spot is equidistant between two strikes:
        - "lower": choose the smaller strike price (more conservative).
        - "higher": choose the larger strike price.

    Returns
    -------
    float
        The selected ATM strike price.

    Raises
    ------
    ValueError
        If spot_price <= 0, available_strikes is empty, or tie_breaker is invalid.
    """
    if spot_price <= 0:
        raise ValueError(f"Invalid spot price {spot_price}: must be positive (> 0).")

    if not available_strikes:
        raise ValueError("available_strikes list cannot be empty.")

    if tie_breaker not in {"lower", "higher"}:
        raise ValueError(f"Invalid tie_breaker '{tie_breaker}': must be 'lower' or 'higher'.")

    # Find minimum absolute distance from spot price
    min_dist = min(abs(strike - spot_price) for strike in available_strikes)

    # Collect all strikes that share this minimum distance (within floating tolerance)
    candidates = [
        strike
        for strike in available_strikes
        if abs(abs(strike - spot_price) - min_dist) < 1e-6
    ]

    if len(candidates) == 1:
        return candidates[0]

    # Deterministic tie-breaking rule
    if tie_breaker == "lower":
        return min(candidates)
    else:
        return max(candidates)


def select_target_expiry(
    options: Sequence[OptionMetadata],
    current_time: datetime | None = None,
) -> datetime:
    """
    Select the target expiration date for the short straddle.

    Rules applied:
    1. Filter out already expired options (expiration_dt <= current_time).
    2. Group options by expiration.
    3. Retain only expirations that contain at least one Call and at least one Put.
    4. Select the nearest unexpired expiration date.

    Parameters
    ----------
    options : Sequence[OptionMetadata]
        Collection of discovered option metadata.
    current_time : datetime | None, optional
        Current reference UTC time. Defaults to datetime.now(timezone.utc).

    Returns
    -------
    datetime
        The selected target expiration datetime.

    Raises
    ------
    ValueError
        If no valid future expirations with both Calls and Puts are found.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)

    # 1. Filter out expired options
    future_options = [opt for opt in options if opt.expiration_dt > current_time]
    if not future_options:
        raise ValueError("No future (unexpired) option contracts available.")

    # 2. Group by expiry
    expiries = group_by_expiry(future_options)

    # 3. Filter expiries that have both Call and Put contracts
    viable_expiries: list[datetime] = []
    for exp_dt, contracts in expiries.items():
        has_call = any(c.is_call for c in contracts)
        has_put = any(c.is_put for c in contracts)
        if has_call and has_put:
            viable_expiries.append(exp_dt)

    if not viable_expiries:
        raise ValueError("No unexpired expiration dates contain both Call and Put contracts.")

    # 4. Nearest expiry rule (simplest deterministic rule for short straddle)
    viable_expiries.sort()
    return viable_expiries[0]


def validate_straddle_pair(
    call: OptionMetadata,
    put: OptionMetadata,
    spot_price: float,
    current_time: datetime | None = None,
) -> None:
    """
    Validate that a Call and Put form a sound, identical-expiry, identical-strike straddle.

    Parameters
    ----------
    call : OptionMetadata
        The candidate Call option.
    put : OptionMetadata
        The candidate Put option.
    spot_price : float
        The current reference spot price.
    current_time : datetime | None, optional
        Current reference UTC time. Defaults to datetime.now(timezone.utc).

    Raises
    ------
    ValueError
        If any validation rule fails.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)

    if spot_price <= 0:
        raise ValueError(f"Straddle validation failed: spot price {spot_price} must be > 0.")

    if not call.is_call:
        raise ValueError(f"Straddle validation failed: {call.instrument_id} is not a CALL option.")

    if not put.is_put:
        raise ValueError(f"Straddle validation failed: {put.instrument_id} is not a PUT option.")

    if call.underlying != "BTC" or put.underlying != "BTC":
        raise ValueError(
            f"Straddle validation failed: underlying must be BTC (got call={call.underlying}, put={put.underlying})."
        )

    if call.expiration_dt != put.expiration_dt:
        raise ValueError(
            f"Straddle validation failed: expiration mismatch (call={call.expiration_dt}, put={put.expiration_dt})."
        )

    if abs(call.strike_price - put.strike_price) > 1e-6:
        raise ValueError(
            f"Straddle validation failed: strike mismatch (call={call.strike_price}, put={put.strike_price})."
        )

    if call.expiration_dt <= current_time:
        raise ValueError(f"Straddle validation failed: contract has expired ({call.expiration_dt} <= {current_time}).")


def pair_atm_straddle(
    spot_price: float,
    options: Sequence[OptionMetadata],
    target_expiry: datetime | None = None,
    current_time: datetime | None = None,
    tie_breaker: str = "lower",
) -> ATMStraddle:
    """
    Calculate the ATM strike and pair matching Call + Put contracts for the short straddle.

    Parameters
    ----------
    spot_price : float
        The current reference BTC price.
    options : Sequence[OptionMetadata]
        Available discovered options.
    target_expiry : datetime | None, optional
        Explicit target expiry to use. If None, automatically selects nearest unexpired expiry.
    current_time : datetime | None, optional
        Current UTC timestamp. Defaults to datetime.now(timezone.utc).
    tie_breaker : str, default "lower"
        Tie-breaker rule for equidistant strikes ("lower" or "higher").

    Returns
    -------
    ATMStraddle
        The validated ATM Call and Put pair.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)

    # 1. Determine target expiry
    if target_expiry is None:
        target_expiry = select_target_expiry(options, current_time=current_time)

    # 2. Filter contracts for target expiry
    contracts_for_expiry = [
        opt for opt in options
        if opt.expiration_dt == target_expiry and opt.expiration_dt > current_time
    ]

    # Map strikes to Call and Put contracts
    calls_by_strike: dict[float, OptionMetadata] = {}
    puts_by_strike: dict[float, OptionMetadata] = {}

    for opt in contracts_for_expiry:
        if opt.is_call:
            calls_by_strike[opt.strike_price] = opt
        elif opt.is_put:
            puts_by_strike[opt.strike_price] = opt

    # 3. Available strikes must have BOTH Call and Put
    common_strikes = sorted(set(calls_by_strike.keys()) & set(puts_by_strike.keys()))
    if not common_strikes:
        raise ValueError(
            f"No strike prices have both a Call and Put contract for expiry {target_expiry}."
        )

    # 4. Calculate ATM strike
    atm_strike = select_atm_strike(
        spot_price=spot_price,
        available_strikes=common_strikes,
        tie_breaker=tie_breaker,
    )

    selected_call = calls_by_strike[atm_strike]
    selected_put = puts_by_strike[atm_strike]

    # 5. Validate pair
    validate_straddle_pair(
        call=selected_call,
        put=selected_put,
        spot_price=spot_price,
        current_time=current_time,
    )

    return ATMStraddle(
        spot_price=spot_price,
        expiry=target_expiry,
        strike=atm_strike,
        call_instrument=selected_call,
        put_instrument=selected_put,
    )

