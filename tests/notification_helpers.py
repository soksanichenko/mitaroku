"""Helpers shared by the tests that build digests and messages."""

from datetime import UTC, date, datetime

from catalog.models import Title
from notifications.models import Notification, dedupe_key

Kind = Notification.Kind
NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
TODAY = date(2026, 5, 20)


def make_title(name: str = 'Show', kind: str = Title.Kind.TV) -> Title:
    """Create a title."""
    return Title.objects.create(kind=kind, original_title=name)


def notify(
    user,
    title: Title,
    kind: str = Kind.EPISODE_AIRED,
    day: date = TODAY,
    payload=None,
    created_at: datetime = NOW,
) -> Notification:
    """Create a notification row, as the scan would."""
    return Notification.objects.create(
        user=user,
        kind=kind,
        title=title,
        day=day,
        payload=payload if payload is not None else {},
        dedupe_key=dedupe_key(kind, title.pk, day),
        created_at=created_at,
    )


def load(user) -> list[Notification]:
    """Read the user's notifications with their titles, as the sender will."""
    return list(Notification.objects.for_user(user).select_related('title'))
