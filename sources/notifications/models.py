"""On-site notifications.

Every read goes through ``objects.for_user(user)``. The model is deliberately
not registered in the admin: it is user data.
"""

from datetime import date

from django.conf import settings
from django.db import models
from django.utils import timezone
from tracking.models import UserScopedManager


def dedupe_key(kind: str, title_id: int, day: date) -> str:
    """Return the key that makes one notification per user, kind, title and day.

    Args:
        kind: A ``Notification.Kind`` value.
        title_id: The primary key of the title.
        day: The user's local day of the event.

    Returns:
        The key, for example ``episode_aired:7:2026-11-21``.
    """
    return f'{kind}:{title_id}:{day.isoformat()}'


class Notification(models.Model):
    """One notification about a title for a user, grouped per local day.

    ``payload`` holds data, never display strings: the text is built when the
    page renders, in the viewer's language.
    """

    class Kind(models.TextChoices):
        EPISODE_AIRED = 'episode_aired'
        PREMIERE = 'premiere'
        MOVIE_RELEASED = 'movie_released'
        RELEASE_ANNOUNCED = 'release_announced'
        RELEASE_CHANGED = 'release_changed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    kind = models.CharField(max_length=20, choices=Kind)
    title = models.ForeignKey(
        'catalog.Title', on_delete=models.CASCADE, related_name='notifications'
    )
    day = models.DateField()
    payload = models.JSONField(default=dict)
    dedupe_key = models.CharField(max_length=80)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = UserScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'dedupe_key'], name='unique_notification_key'
            )
        ]
        indexes = [
            models.Index(
                fields=['user', 'read_at', '-created_at'],
                name='notification_unread_idx',
            )
        ]

    def __str__(self) -> str:
        """Return a readable label."""
        return f'{self.dedupe_key} for {self.user}'
