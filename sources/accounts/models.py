"""User and profile models."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """A mitaroku user.

    Attributes:
        discord_id: Discord user id supplied by the SSO portal; the stable key
            for portal users. Local accounts leave it empty.
        avatar_url: Avatar URL supplied by the SSO portal.
    """

    discord_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    avatar_url = models.URLField(blank=True)


class Profile(models.Model):
    """Per-user preferences."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile'
    )
    interface_language = models.CharField(
        max_length=5, choices=settings.LANGUAGES, default='en'
    )
    metadata_language = models.CharField(
        max_length=5, choices=settings.LANGUAGES, default='en'
    )

    def __str__(self) -> str:
        """Return a readable label for the admin."""
        return f'Profile of {self.user}'
