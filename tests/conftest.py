"""Shared fixtures."""

import pytest
from django.core.management import call_command

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
