"""Factories that wire the provider clients to Django settings."""

from django.conf import settings
from redis import Redis


def connect_redis() -> Redis:
    """Return a Redis connection that decodes responses to ``str``."""
    return Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )
