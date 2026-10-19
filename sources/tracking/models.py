"""Per-user tracking: library entries and watch events.

Every read of these models goes through ``objects.for_user(user)`` so that one
user's data cannot reach another user. They are deliberately not registered in
the admin.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class UserScopedManager(models.Manager):
    """Manager that scopes queries to a single user."""

    def for_user(self, user) -> models.QuerySet:
        """Return the rows that belong to ``user``.

        Args:
            user: The owner of the rows.

        Returns:
            A queryset filtered by ``user``.
        """
        return self.get_queryset().filter(user=user)


class LibraryEntry(models.Model):
    """A title in a user's library.

    "Not watching" is the absence of an entry. ``score`` is 0 (not rated) to 10
    and is shown as five stars. ``note`` is private to the user.
    """

    class Status(models.TextChoices):
        WATCHING = 'watching'
        PLANNED = 'planned'
        COMPLETED = 'completed'
        DROPPED = 'dropped'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='library_entries',
    )
    title = models.ForeignKey(
        'catalog.Title', on_delete=models.CASCADE, related_name='library_entries'
    )
    status = models.CharField(max_length=10, choices=Status, default=Status.WATCHING)
    score = models.PositiveSmallIntegerField(default=0)
    note = models.TextField(blank=True)
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'title'], name='unique_library_entry'
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0, score__lte=10),
                name='library_entry_score_range',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.user} / {self.title} [{self.status}]'


class WatchEvent(models.Model):
    """One viewing of an episode, or of a movie (``episode`` is empty).

    Progress is derived from these rows and a rewatch is an additional event.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='watch_events',
    )
    title = models.ForeignKey(
        'catalog.Title', on_delete=models.CASCADE, related_name='watch_events'
    )
    episode = models.ForeignKey(
        'catalog.Episode',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='watch_events',
    )
    watched_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'episode', 'watched_at'],
                condition=Q(episode__isnull=False),
                name='unique_episode_watch_event',
            ),
            models.UniqueConstraint(
                fields=['user', 'title', 'watched_at'],
                condition=Q(episode__isnull=True),
                name='unique_title_watch_event',
            ),
        ]
        indexes = [models.Index(fields=['user', 'title'], name='watchevent_user_title')]

    def __str__(self) -> str:
        return f'{self.user} / {self.episode or self.title} @ {self.watched_at}'


class AddRequest(models.Model):
    """A request to add a title that a background job completes.

    The browser sends only the provider, the id and the wanted status; the job
    builds the catalog title from the provider. ``error`` is ``provider`` (the
    provider failed) or ``timeout`` (the job never finished). Only one request
    per ``(user, provider, external_id)`` can be pending at a time.
    """

    class State(models.TextChoices):
        PENDING = 'pending'
        DONE = 'done'
        FAILED = 'failed'

    class Error(models.TextChoices):
        NONE = ''
        PROVIDER = 'provider'
        TIMEOUT = 'timeout'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='add_requests',
    )
    provider = models.CharField(max_length=12)
    external_id = models.CharField(max_length=10)
    status = models.CharField(
        max_length=10, choices=LibraryEntry.Status, default=LibraryEntry.Status.WATCHING
    )
    state = models.CharField(max_length=10, choices=State, default=State.PENDING)
    title = models.ForeignKey(
        'catalog.Title',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    error = models.CharField(max_length=10, choices=Error, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = UserScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'provider', 'external_id'],
                condition=Q(state='pending'),
                name='unique_pending_add_request',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.user} / {self.provider}:{self.external_id} [{self.state}]'
