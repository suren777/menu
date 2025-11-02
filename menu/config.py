"""This module contains the configuration for the application."""
import enum
import os
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel as PydanticBaseModel


class Environment(str, enum.Enum):
    """The environment in which the application is running."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"

    DEV = "development"
    PROD = "production"

    LOCAL = "local"
    TEST = "test"


class BaseModel(PydanticBaseModel):
    """Custom base model for the application."""

    class Config:
        """Configuration for the custom base model."""

        allow_mutation = False
        keep_untouched = (cached_property,)


class AppConfig(dict[str, Any]):
    """The application configuration."""


class ConfigLoader:
    """Loads the configuration for the application."""

    def __init__(self, vault_enabled: bool | None = None) -> None:
        """Initialize the config loader."""
        self.vault_enabled = (
            vault_enabled
            if vault_enabled is not None
            else self._strtobool(os.environ.get("VAULT_ENABLED", "false"))
        )

    def load(self, config_file: str | None = None) -> AppConfig:
        """Load the configuration from a file."""
        if not (path := config_file or os.environ.get("CONFIG_FILE")):
            raise RuntimeError("CONFIG_FILE is not supplied")

        raw_config = Path(path).read_text(encoding="utf-8")
        config = yaml.safe_load(raw_config)
        return AppConfig(config)

    @staticmethod
    def _strtobool(value: str) -> bool:
        """Convert a string to a boolean."""
        value = value.lower()
        if value in ("y", "yes", "t", "true", "on", "1"):
            return True
        if value in ("n", "no", "f", "false", "off", "0"):
            return False

        raise ValueError(f"invalid truth value {value!r}")


class ApplicationConfig(BaseModel):
    """The application configuration."""

    service: str = "menu"
    host: str = "0.0.0.0"
    port: str = "8080"


@lru_cache
def load_application_config() -> ApplicationConfig:
    """Load the application configuration."""
    return ApplicationConfig(**ConfigLoader().load())
