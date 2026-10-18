"""Authentication backend for portal users."""

from django.contrib.auth.backends import RemoteUserBackend


class PortalBackend(RemoteUserBackend):
    """Create users on first sight, keyed by the Discord user id."""

    create_unknown_user = True

    def configure_user(self, request, user, created=True):
        """Record the Discord id and disable password login for new users."""
        if created:
            user.discord_id = user.get_username()
            user.set_unusable_password()
            user.save(update_fields=['discord_id', 'password'])
        return user
