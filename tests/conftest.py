"""Shared fixtures."""

import uuid

import httpx
import pytest
from django.core.management import call_command
from providers.clients import connect_redis
from providers.http import ProviderHttp

PORTAL = {
    'HTTP_X_DISCORD_USER_ID': '1001',
    'HTTP_X_DISCORD_EMAIL': 'kira@example.com',
    'HTTP_X_DISCORD_AVATAR_URL': 'https://cdn.example.com/a.png',
    'HTTP_X_DISCORD_ACCESS_LEVEL': 'member',
}


@pytest.fixture(scope='session', autouse=True)
def compiled_messages() -> None:
    """Compile the .po files so translations can be asserted."""
    call_command('compilemessages', verbosity=0)


@pytest.fixture
def portal_headers() -> dict[str, str]:
    """Return a fresh copy of valid portal headers for a member."""
    return dict(PORTAL)


@pytest.fixture
def redis_client():
    """Return a Redis connection and delete the keys of this test afterwards."""
    client = connect_redis()
    yield client
    for key in client.scan_iter('test:*'):
        client.delete(key)


@pytest.fixture
def redis_prefix() -> str:
    """Return a Redis key prefix unique to the test."""
    return f'test:{uuid.uuid4().hex}:'


@pytest.fixture
def provider_http(redis_client, redis_prefix):
    """Return a factory for a ``ProviderHttp`` over a mock transport."""

    def build(handler, name: str = 'test', **kwargs) -> ProviderHttp:
        return ProviderHttp(
            name,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            redis=redis_client,
            prefix=redis_prefix,
            sleep=lambda seconds: None,
            **kwargs,
        )

    return build
