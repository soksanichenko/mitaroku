"""Authentication backend for portal users."""

from accounts.models import User
from django.contrib.auth.backends import RemoteUserBackend
from django.http import HttpRequest


class PortalBackend(RemoteUserBackend):
    """Create users on first sight, keyed by the Discord user id."""

    create_unknown_user = True

    def configure_user(
        self, request: HttpRequest, user: User, created: bool = True
    ) -> User | None:
        """Record the Discord id for new users and refuse foreign accounts.

        An existing account is accepted only when its ``discord_id`` equals the
        portal id. Otherwise a local account whose username happens to be all
        digits would be taken over by a matching portal id.

        Args:
            request: The current request.
            user: The user found or created for the portal id.
            created: Whether the user was created by this login.

        Returns:
            The user, or None when the account is not a portal account.
        """
        if created:
            user.discord_id = user.get_username()
            user.set_unusable_password()
            user.save(update_fields=['discord_id', 'password'])
        elif user.discord_id != user.get_username():
            return None
        return user
