"""Factories that wire the provider clients to Django settings."""

import httpx
from django.conf import settings
from mitaroku.config import load_settings
from providers.anilist import AniListClient
from providers.http import ProviderError, ProviderHttp
from providers.tmdb import TmdbClient
from redis import Redis

TIMEOUT = 30.0


def connect_redis() -> Redis:
    """Return a Redis connection that decodes responses to ``str``."""
    return Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )


def user_agent() -> str:
    """Return the ``User-Agent`` header, naming the operator when configured."""
    contact = load_settings().providers.contact
    return f'mitaroku/0.1 ({contact})' if contact else 'mitaroku/0.1'


def get_tmdb() -> TmdbClient:
    """Return a TMDB client.

    Raises:
        ProviderError: No TMDB token is configured.
    """
    config = load_settings().providers
    if config.tmdb_token is None:
        raise ProviderError('TMDB is not configured: set providers.tmdb_token')
    client = httpx.Client(
        timeout=TIMEOUT,
        headers={
            'Authorization': f'Bearer {config.tmdb_token.get_secret_value()}',
            'User-Agent': user_agent(),
        },
    )
    http = ProviderHttp(
        'tmdb',
        client=client,
        redis=connect_redis(),
        min_interval=config.tmdb_min_interval,
    )
    return TmdbClient(http)


def get_anilist() -> AniListClient:
    """Return an AniList client; it needs no key."""
    config = load_settings().providers
    client = httpx.Client(timeout=TIMEOUT, headers={'User-Agent': user_agent()})
    http = ProviderHttp(
        'anilist',
        client=client,
        redis=connect_redis(),
        min_interval=config.anilist_min_interval,
    )
    return AniListClient(http)
