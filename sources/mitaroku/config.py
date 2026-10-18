"""Application configuration: a YAML file overridden by environment variables."""

import ipaddress
import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class DatabaseConfig(BaseModel):
    """PostgreSQL connection settings."""

    host: str = 'localhost'
    port: int = 5432
    name: str = 'mitaroku'
    user: str = 'mitaroku'
    password: SecretStr


class RedisConfig(BaseModel):
    """Redis connection settings (Huey broker and provider cache)."""

    host: str = 'localhost'
    port: int = 6379
    db: int = 0
    password: SecretStr | None = None


class AuthConfig(BaseModel):
    """How users are authenticated.

    Attributes:
        mode: ``portal`` trusts the SSO headers set by nginx; ``local`` uses
            Django accounts.
        trusted_proxies: Networks that may send the portal headers.
        portal_logout_url: Where the "Log out" menu item points in portal mode.
            Empty hides the item.
    """

    mode: Literal['portal', 'local'] = 'portal'
    trusted_proxies: list[str] = ['127.0.0.1/32', '::1/128']
    portal_logout_url: str = ''

    @field_validator('trusted_proxies')
    @classmethod
    def _valid_networks(cls, value: list[str]) -> list[str]:
        """Reject entries that are not IP networks."""
        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value


class Settings(BaseSettings):
    """All runtime settings.

    Values come from, highest priority first: environment variables prefixed
    ``MITAROKU_`` (nested keys joined with ``__``), then the YAML file named by
    ``MITAROKU_CONFIG`` (default ``config.yaml``). A missing file is ignored.
    """

    model_config = SettingsConfigDict(
        env_prefix='MITAROKU_', env_nested_delimiter='__', extra='forbid'
    )

    secret_key: SecretStr
    debug: bool = False
    allowed_hosts: list[str] = ['localhost', '127.0.0.1']
    csrf_trusted_origins: list[str] = []
    huey_immediate: bool = False
    auth: AuthConfig = AuthConfig()
    database: DatabaseConfig
    redis: RedisConfig = RedisConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Put the YAML file below the environment in priority."""
        yaml_source = YamlConfigSettingsSource(
            settings_cls, yaml_file=os.environ.get('MITAROKU_CONFIG', 'config.yaml')
        )
        return init_settings, env_settings, yaml_source


@lru_cache
def load_settings() -> Settings:
    """Load and cache the settings for the process."""
    return Settings()
