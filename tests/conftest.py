"""Shared fixtures."""

import pytest

PORTAL = {
    'HTTP_X_DISCORD_USER_ID': '1001',
    'HTTP_X_DISCORD_EMAIL': 'kira@example.com',
    'HTTP_X_DISCORD_AVATAR_URL': 'https://cdn.example.com/a.png',
    'HTTP_X_DISCORD_ACCESS_LEVEL': 'member',
}


@pytest.fixture
def portal_headers() -> dict[str, str]:
    """Return a fresh copy of valid portal headers for a member."""
    return dict(PORTAL)
