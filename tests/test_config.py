"""Unit tests for configuration loading and validation."""

import os
from unittest.mock import patch
import pytest
from nautilus_trader.adapters.bybit import BybitEnvironment

from src.btc_straddle.config import AppConfig, load_config, mask_credential


def test_mask_credential():
    """Verify that credentials are correctly masked and never leaked in full."""
    assert mask_credential(None) == "<not set>"
    assert mask_credential("") == "<not set>"
    assert mask_credential("123") == "******"
    assert mask_credential("abcdef123456") == "abc...456"


def test_default_config_missing_keys():
    """Verify default behavior when environment has no keys set."""
    with patch.dict(os.environ, {}, clear=True), patch("src.btc_straddle.config.load_dotenv"):
        config = load_config()
        assert config.api_key is None
        assert config.api_secret is None
        assert config.has_api_credentials is False
        assert config.trader_id == "BTC-STRADDLE-001"
        assert config.environment == BybitEnvironment.TESTNET


def test_config_with_placeholders():
    """Verify that placeholder values from .env.example are detected as invalid credentials."""
    env = {
        "BYBIT_API_KEY": "your_testnet_api_key_here",
        "BYBIT_API_SECRET": "your_testnet_api_secret_here",
    }
    with patch.dict(os.environ, env, clear=True), patch("src.btc_straddle.config.load_dotenv"):
        config = load_config()
        assert config.has_api_credentials is False


def test_config_with_valid_credentials():
    """Verify that real/valid credential strings are recognized."""
    env = {
        "BYBIT_API_KEY": "valid_key_12345",
        "BYBIT_API_SECRET": "valid_secret_67890",
        "TRADER_ID": "CUSTOM-002",
        "LOG_LEVEL": "DEBUG",
    }
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
        assert config.has_api_credentials is True
        assert config.api_key == "valid_key_12345"
        assert config.api_secret == "valid_secret_67890"
        assert config.trader_id == "CUSTOM-002"
        assert config.log_level == "DEBUG"
        assert config.environment == BybitEnvironment.TESTNET


def test_invalid_trader_id():
    """Verify that a trader_id without a hyphen raises a ValueError."""
    env = {"TRADER_ID": "INVALIDTRADERID"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="Invalid trader_id"):
            load_config()


def test_summary_does_not_reveal_secrets():
    """Verify that summary() masks keys and secrets."""
    config = AppConfig(
        api_key="my_super_secret_key",
        api_secret="my_super_secret_secret",
        trader_id="BTC-STRADDLE-001",
    )
    summary = config.summary()
    assert "my_super_secret_key" not in summary["api_key"]
    assert "my_super_secret_secret" not in summary["api_secret"]
    assert summary["api_key"] == "my_...key"
    assert summary["environment"].upper() == "TESTNET"
