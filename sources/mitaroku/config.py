"""Application configuration: a YAML file overridden by environment variables."""

import ipaddress
import os
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, SecretStr, field_validator, model_validator
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


class ProvidersConfig(BaseModel):
    """Settings for the external metadata providers.

    Attributes:
        tmdb_token: TMDB API read access token (Bearer). Without it the TMDB
            client refuses to start; AniList and Fribb need no key.
        contact: Address or URL sent in the ``User-Agent`` so providers can reach
            the operator. Empty sends the bare application name.
        tmdb_min_interval: Minimum seconds between TMDB requests.
        anilist_min_interval: Minimum seconds between AniList requests.
        fribb_url: Where the Fribb/anime-lists dataset is downloaded from.
    """

    tmdb_token: SecretStr | None = None
    contact: str = ''
    tmdb_min_interval: float = 0.05
    anilist_min_interval: float = 2.0
    fribb_url: str = (
        'https://raw.githubusercontent.com/Fribb/anime-lists/master/'
        'anime-list-full.json'
    )

    @field_validator('tmdb_token', mode='before')
    @classmethod
    def _blank_token_is_none(cls, value: object) -> object:
        """Treat an empty token (such as an unset ``${VAR}``) as not configured."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


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


class EmailConfig(BaseModel):
    """Outgoing SMTP settings for the email notification channel.

    Attributes:
        host: SMTP server. Empty turns the email channel off.
        port: SMTP port.
        user: SMTP login. Empty sends without authentication.
        password: SMTP password; a blank value counts as not set.
        starttls: Upgrade the connection with STARTTLS (usually port 587).
        ssl: Use implicit TLS from the first byte (usually port 465). It takes
            precedence over a ``starttls`` left at its default, which is then
            ignored; setting both to true explicitly is rejected.
        from_address: The sender address; required once ``host`` is set.
    """

    host: str = ''
    port: int = 587
    user: str = ''
    password: SecretStr | None = None
    starttls: bool = True
    ssl: bool = False
    from_address: str = ''

    @field_validator('password', mode='before')
    @classmethod
    def _blank_password_is_none(cls, value: object) -> object:
        """Treat an empty password (such as an unset ``${VAR}``) as not set."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode='after')
    def _host_needs_a_sender(self) -> 'EmailConfig':
        """Refuse a configured server that has no sender address."""
        if self.host and not self.from_address:
            raise ValueError('email.from_address is required when email.host is set')
        return self

    @model_validator(mode='after')
    def _ssl_and_starttls_are_exclusive(self) -> 'EmailConfig':
        """Refuse an explicit ``starttls`` next to ``ssl``.

        ``starttls`` defaults to true, so only a value that was actually given
        counts as a conflict.
        """
        if self.ssl and self.starttls and 'starttls' in self.model_fields_set:
            raise ValueError('email.ssl and email.starttls cannot both be true')
        return self


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
    providers: ProvidersConfig = ProvidersConfig()
    email: EmailConfig = EmailConfig()
    public_url: str = ''

    @field_validator('public_url')
    @classmethod
    def _http_without_trailing_slash(cls, value: str) -> str:
        """Accept an empty value or an http(s) URL, and drop trailing slashes."""
        value = value.strip().rstrip('/')
        if value and not value.startswith(('http://', 'https://')):
            raise ValueError('public_url must start with http:// or https://')
        return value

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


def mailers_setting(email: EmailConfig) -> dict[str, dict[str, Any]]:
    """Return Django's ``MAILERS`` setting for the given SMTP block.

    The ``default`` mailer always exists so code and tests that send mail find
    one; with no host configured it points at localhost and nothing sends it.

    Args:
        email: The ``email`` block of the settings.

    Returns:
        A mapping with the ``default`` SMTP mailer.
    """
    return {
        'default': {
            'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'OPTIONS': {
                'host': email.host or 'localhost',
                'port': email.port,
                'username': email.user,
                'password': email.password.get_secret_value() if email.password else '',
                'use_tls': email.starttls and not email.ssl,
                'use_ssl': email.ssl,
                'timeout': 15,
            },
        }
    }


@lru_cache
def load_settings() -> Settings:
    """Load and cache the settings for the process."""
    return Settings()
