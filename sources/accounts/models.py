"""User and profile models."""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone as dj_timezone


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
    timezone = models.CharField(max_length=64, default='UTC')
    notify_since = models.DateTimeField(default=dj_timezone.now)
    email_enabled = models.BooleanField(default=False)
    email_since = models.DateTimeField(null=True, blank=True)
    notify_email = models.EmailField(blank=True)
    pending_email = models.EmailField(blank=True)
    telegram_enabled = models.BooleanField(default=False)
    telegram_since = models.DateTimeField(null=True, blank=True)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True, unique=True)
    telegram_link_hash = models.CharField(max_length=64, blank=True, db_index=True)
    telegram_link_expires = models.DateTimeField(null=True, blank=True)
    telegram_blocked = models.BooleanField(default=False)

    def __str__(self) -> str:
        """Return a readable label for the admin."""
        return f'Profile of {self.user}'
