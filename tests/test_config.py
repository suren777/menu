import os
from unittest.mock import mock_open, patch

import pytest

from menu.config import ApplicationConfig, ConfigLoader, load_application_config


def os_environ_get_side_effect(key, default=None):
    if key == "CONFIG_FILE":
        return "test_config.yml"
    if key == "VAULT_ENABLED":
        return "false"
    return default


@patch("os.environ.get", side_effect=os_environ_get_side_effect)
def test_config_loader_load_from_env(mock_os_environ_get):
    with patch(
        "pathlib.Path.read_text", mock_open(read_data="service: test-service")
    ) as mock_file:
        loader = ConfigLoader()
        config = loader.load()
        assert config["service"] == "test-service"
        mock_file.assert_called_once_with(encoding="utf-8")


def test_config_loader_load_from_arg():
    with patch(
        "pathlib.Path.read_text", mock_open(read_data="service: test-service")
    ) as mock_file:
        loader = ConfigLoader()
        config = loader.load("test_config.yml")
        assert config["service"] == "test-service"
        mock_file.assert_called_once_with(encoding="utf-8")


def os_environ_get_side_effect_no_file(key, default=None):
    if key == "CONFIG_FILE":
        return None
    if key == "VAULT_ENABLED":
        return "false"
    return default


@patch("os.environ.get", side_effect=os_environ_get_side_effect_no_file)
def test_config_loader_no_file(mock_os_environ_get):
    loader = ConfigLoader()
    with pytest.raises(RuntimeError):
        loader.load()


def test_strtobool():
    loader = ConfigLoader()
    assert loader._strtobool("y")
    assert not loader._strtobool("n")
    with pytest.raises(ValueError):
        loader._strtobool("invalid")


def test_application_config():
    config = ApplicationConfig()
    assert config.service == "menu"
    assert config.host == "0.0.0.0"
    assert config.port == "8080"


@patch(
    "menu.config.ConfigLoader.load",
    return_value={"service": "menu", "host": "127.0.0.1", "port": "5000"},
)
def test_load_application_config(mock_load):
    config = load_application_config()
    assert config.service == "menu"
    assert config.host == "127.0.0.1"
    assert config.port == "5000"
    load_application_config.cache_clear()
