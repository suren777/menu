import os
import enum
from functools import lru_cache
from functools import cached_property
from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel as PyBaseModel

class Environment(str, enum.Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"

    DEV = "development"
    PROD = "production"

    LOCAL = "local"
    TEST = "test"


class BaseModel(PyBaseModel):
    class Config:
        allow_mutation = False
        keep_untouched = (cached_property,)

class AppConfig(dict[str, Any]):
    pass


class ConfigLoader:
    def __init__(self, vault_enabled: bool | None = None) -> None:
        self.vault_enabled = (
            vault_enabled
            if vault_enabled is not None
            else self._strtobool(os.environ.get("VAULT_ENABLED", "false"))
        )

    def load(self, config_file: str | None = None) -> AppConfig:
        if not (path := config_file or os.environ.get("CONFIG_FILE")):
            raise RuntimeError("CONFIG_FILE is not supplied")

        raw_config = Path(path).read_text(encoding="utf-8")
        config = yaml.safe_load(raw_config)
        return AppConfig(**config)

    @staticmethod
    def _strtobool(value: str) -> bool:
        value = value.lower()
        if value in ("y", "yes", "t", "true", "on", "1"):
            return True
        elif value in ("n", "no", "f", "false", "off", "0"):
            return False
        else:
            raise ValueError(f"invalid truth value {value!r}")




class ApplicationConfig(BaseModel):
    service: str = "menu"
    host: str = "0.0.0.0"
    port: str = "8080"


 

@lru_cache
def load_application_config() -> ApplicationConfig:
    return ApplicationConfig(**ConfigLoader().load())