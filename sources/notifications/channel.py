"""Helpers shared by the notification channels."""

from accounts.models import User
from django.conf import settings


def email_configured() -> bool:
    """Return whether the operator configured outgoing email."""
    return settings.EMAIL_ENABLED


def telegram_configured() -> bool:
    """Return whether the operator configured the Telegram bot."""
    return settings.TELEGRAM_ENABLED


def effective_address(user: User) -> str:
    """Return the address mail for ``user`` goes to, or an empty string.

    The address the user confirmed wins; otherwise the account address, which
    the SSO portal or the operator supplies, is trusted.
    """
    return user.profile.notify_email.strip() or user.email.strip()


def absolute_url(path: str) -> str:
    """Return the public URL of ``path``, which starts with ``/``."""
    return settings.PUBLIC_URL + path
