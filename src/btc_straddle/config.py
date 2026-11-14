"""Configuration management for NautilusTrader + Bybit Testnet."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
from nautilus_trader.adapters.bybit import BybitEnvironment


PLACEHOLDER_SECRETS = {
    "",
    "your_testnet_api_key",
    "your_testnet_api_key_here",
    "your_testnet_api_secret",
    "your_testnet_api_secret_here",
}


def mask_credential(val: str | None) -> str:
    """Mask a sensitive credential for safe logging."""
    if not val:
        return "<not set>"
    if len(val) <= 6:
        return "******"
    return f"{val[:3]}...{val[-3:]}"


@dataclass(frozen=True)
class AppConfig:
    """Validated configuration for the trading application."""

    api_key: str | None
    api_secret: str | None
    trader_id: str = "BTC-STRADDLE-001"
    log_level: str = "INFO"
    environment: BybitEnvironment = BybitEnvironment.TESTNET
    execute_trades: bool = False
    order_type: str = "LIMIT"
    order_quantity: float | None = None
    catalog_path: str = "data/catalog"
    enable_catalog: bool = True

    def __post_init__(self) -> None:
        """Validate safety invariants."""
        if self.environment != BybitEnvironment.TESTNET:
            raise ValueError(
                f"Safety violation: only BybitEnvironment.TESTNET is permitted (got {self.environment}). "
                "Mainnet execution is strictly disabled."
            )

    @property
    def has_api_credentials(self) -> bool:
        """Check whether valid, non-placeholder API credentials are provided."""
        return bool(
            self.api_key
            and self.api_secret
            and self.api_key.strip().lower() not in PLACEHOLDER_SECRETS
            and self.api_secret.strip().lower() not in PLACEHOLDER_SECRETS
        )

    def summary(self) -> dict[str, str]:
        """Return a safe summary dictionary with masked credentials."""
        return {
            "trader_id": self.trader_id,
            "environment": self.environment.name,
            "has_api_credentials": str(self.has_api_credentials),
            "api_key": mask_credential(self.api_key),
            "api_secret": mask_credential(self.api_secret),
            "log_level": self.log_level,
            "execute_trades": str(self.execute_trades),
            "order_type": self.order_type,
            "order_quantity": str(self.order_quantity) if self.order_quantity is not None else "<auto>",
            "catalog_path": self.catalog_path,
            "enable_catalog": str(self.enable_catalog),
        }


def load_config(env_path: Path | str | None = None) -> AppConfig:
    """
    Load and validate configuration from the environment and optional .env file.

    Parameters
    ----------
    env_path : Path | str | None, optional
        Path to custom .env file. Defaults to looking for .env in current directory.

    Returns
    -------
    AppConfig
        The validated application configuration.
    """
    if env_path:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    api_key = os.getenv("BYBIT_API_KEY", "").strip() or None
    api_secret = os.getenv("BYBIT_API_SECRET", "").strip() or None

    trader_id = os.getenv("TRADER_ID", "BTC-STRADDLE-001").strip()
    if "-" not in trader_id:
        raise ValueError(
            f"Invalid trader_id '{trader_id}'. NautilusTrader requires a hyphenated string like 'NAME-001'."
        )

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR"}
    if log_level not in valid_levels:
        log_level = "INFO"

    execute_trades_env = os.getenv("EXECUTE_TRADES", "false").strip().lower() in (
        "true",
        "1",
        "yes",
    )

    order_type_env = os.getenv("ORDER_TYPE", "LIMIT").strip().upper()
    if order_type_env not in {"LIMIT", "MARKET"}:
        order_type_env = "LIMIT"

    order_qty_raw = os.getenv("ORDER_QUANTITY", "").strip()
    order_qty = float(order_qty_raw) if order_qty_raw else None

    catalog_path_env = os.getenv("CATALOG_PATH", "data/catalog").strip()
    enable_catalog_env = os.getenv("ENABLE_CATALOG", "true").strip().lower() in (
        "true",
        "1",
        "yes",
    )

    return AppConfig(
        api_key=api_key,
        api_secret=api_secret,
        trader_id=trader_id,
        log_level=log_level,
        environment=BybitEnvironment.TESTNET,
        execute_trades=execute_trades_env,
        order_type=order_type_env,
        order_quantity=order_qty,
        catalog_path=catalog_path_env,
        enable_catalog=enable_catalog_env,
    )
