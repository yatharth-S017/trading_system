"""NautilusTrader TradingNode setup with Bybit Testnet integration."""

from __future__ import annotations

import asyncio
import logging
from nautilus_trader.adapters.bybit import (
    BybitDataClientConfig,
    BybitEnvironment,
    BybitExecClientConfig,
    BybitLiveDataClientFactory,
    BybitLiveExecClientFactory,
    BybitProductType,
)
from nautilus_trader.common.config import InstrumentProviderConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode, TradingNodeConfig
from nautilus_trader.model.identifiers import TraderId

from src.btc_straddle.config import AppConfig

logger = logging.getLogger(__name__)


def create_trading_node(
    config: AppConfig,
    loop: asyncio.AbstractEventLoop | None = None,
) -> TradingNode:
    """
    Build and return a configured NautilusTrader TradingNode for Bybit Testnet.

    Parameters
    ----------
    config : AppConfig
        The application configuration containing credentials and node settings.
    loop : asyncio.AbstractEventLoop | None, optional
        The event loop to attach to the node. If None, a new event loop is created.

    Returns
    -------
    TradingNode
        The built TradingNode instance ready to connect and run.
    """
    if config.environment != BybitEnvironment.TESTNET:
        raise ValueError(
            f"Safety violation: only BybitEnvironment.TESTNET is permitted (got {config.environment}). "
            "Mainnet execution is strictly disabled."
        )

    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 1. Configure the Bybit Data Client (handles WebSocket market data & REST instrument metadata)
    data_client_config = BybitDataClientConfig(
        environment=BybitEnvironment.TESTNET,
        product_types=(BybitProductType.OPTION, BybitProductType.LINEAR),
        instrument_provider=InstrumentProviderConfig(load_all=True),
        api_key=config.api_key if config.has_api_credentials else None,
        api_secret=config.api_secret if config.has_api_credentials else None,
    )

    data_clients = {"BYBIT": data_client_config}
    exec_clients = {}

    # 2. Configure the Bybit Execution Client (handles private accounts, orders, positions)
    if config.has_api_credentials and config.execute_trades:
        logger.info("Configuring Bybit Execution Client with Testnet credentials...")
        exec_client_config = BybitExecClientConfig(
            environment=BybitEnvironment.TESTNET,
            product_types=(BybitProductType.OPTION, BybitProductType.LINEAR),
            api_key=config.api_key,
            api_secret=config.api_secret,
        )
        exec_clients["BYBIT"] = exec_client_config
    else:
        if not config.execute_trades:
            logger.info("Dry-run mode: Execution client omitted. Market data and ATM selection active.")
        else:
            logger.warning(
                "No Bybit API credentials provided. Initializing in public market data mode only. "
                "Private trade execution and account management require BYBIT_API_KEY and BYBIT_API_SECRET."
            )

    # 3. Configure the TradingNode
    logging_config = LoggingConfig(log_level=config.log_level)

    node_config = TradingNodeConfig(
        trader_id=TraderId(config.trader_id),
        logging=logging_config,
        data_clients=data_clients,
        exec_clients=exec_clients,
    )

    # 4. Instantiate node and register Bybit client factories
    node = TradingNode(config=node_config, loop=loop)
    node.add_data_client_factory("BYBIT", BybitLiveDataClientFactory)

    if config.has_api_credentials and config.execute_trades:
        node.add_exec_client_factory("BYBIT", BybitLiveExecClientFactory)

    # 5. Build node components
    logger.info("Building TradingNode components...")
    node.build()
    logger.info("TradingNode built successfully.")

    return node
