"""Unit tests for NautilusTrader TradingNode setup with Bybit Testnet."""

from src.btc_straddle.config import AppConfig
from src.btc_straddle.node import create_trading_node


def test_create_node_public_mode():
    """Verify that TradingNode builds cleanly in public data mode without credentials."""
    config = AppConfig(
        api_key=None,
        api_secret=None,
        trader_id="TEST-NODE-001",
        log_level="INFO",
    )
    node = create_trading_node(config)
    try:
        assert node.is_built() is True
        assert str(node.trader_id) == "TEST-NODE-001"
    finally:
        node.dispose()


def test_create_node_authenticated_mode():
    """Verify that TradingNode builds cleanly with credentials (data + exec clients)."""
    config = AppConfig(
        api_key="test_api_key_12345",
        api_secret="test_api_secret_67890",
        trader_id="TEST-NODE-002",
        log_level="INFO",
        execute_trades=True,
    )
    node = create_trading_node(config)
    try:
        assert node.is_built() is True
        assert str(node.trader_id) == "TEST-NODE-002"
    finally:
        node.dispose()

