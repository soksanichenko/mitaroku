"""Accounts app configuration."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Register the accounts app and its signals."""

    name = 'accounts'

    def ready(self) -> None:
        """Connect the profile-creation signal."""
        from accounts import signals  # noqa: F401
