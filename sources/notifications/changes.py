"""Notifications for a changed ``Title.release_date``, written by the sync."""

import logging
from datetime import date, datetime

from catalog.models import Title
from notifications.models import Notification, dedupe_key
from notifications.moments import local_day
from tracking.models import LibraryEntry

logger = logging.getLogger(__name__)

Kind = Notification.Kind


def record_release_change(
    title: Title, old_date: date | None, was_synced: bool, now: datetime
) -> int:
    """Notify the users who plan a title that its release date was set or moved.

    Nothing is written for the first sync of a title (a baseline), when the date
    is unchanged or removed, when a new date is already past, or when a date
    moves between two past dates. A repeat on the same local day keeps the
    original ``from``, replaces ``to`` and marks the row unread; a repeat that
    changes nothing leaves the row alone, and one that moves the date back to
    ``from`` deletes the row.

    Args:
        title: The synced title, already holding the new date.
        old_date: The title's release date before the sync.
        was_synced: Whether the title had been synced before this run.
        now: The current moment (the clock is never read here).

    Returns:
        The number of rows created or changed.
    """
    new_date = title.release_date
    if not was_synced or new_date is None or new_date == old_date:
        return 0
    today = now.date()
    if old_date is None:
        if new_date <= today:
            return 0
        kind = Kind.RELEASE_ANNOUNCED
        payload = {'to': new_date.isoformat()}
    else:
        if max(old_date, new_date) <= today:
            return 0
        kind = Kind.RELEASE_CHANGED
        payload = {'from': old_date.isoformat(), 'to': new_date.isoformat()}
    # System job: it deliberately spans all users, so it does not use for_user.
    entries = list(
        LibraryEntry.objects.filter(
            title=title, status=LibraryEntry.Status.PLANNED
        ).select_related('user__profile')
    )
    if not entries:
        return 0
    days = {
        entry.user_id: local_day(now, entry.user.profile.timezone) for entry in entries
    }
    keys = {dedupe_key(kind, title.pk, day) for day in days.values()}
    # System job again: existing rows of those users, matched by their unique key.
    existing = {
        (row.user_id, row.dedupe_key): row
        for row in Notification.objects.filter(user_id__in=days, dedupe_key__in=keys)
    }
    created: list[Notification] = []
    changed: list[Notification] = []
    undone: list[int] = []
    for user_id, day in days.items():
        key = dedupe_key(kind, title.pk, day)
        row = existing.get((user_id, key))
        if row is None:
            created.append(
                Notification(
                    user_id=user_id,
                    kind=kind,
                    title=title,
                    day=day,
                    dedupe_key=key,
                    payload=payload,
                    created_at=now,
                )
            )
            continue
        # The first move of the day fixes "from"; later ones only move "to".
        kept = {'from': row.payload['from']} if 'from' in row.payload else {}
        merged = {**payload, **kept}
        if merged == row.payload:
            continue
        if merged.get('from') == merged['to']:
            # Moved back to where it was the same day: net unchanged, so no row.
            undone.append(row.pk)
            continue
        row.payload = merged
        row.read_at = None
        changed.append(row)
    # A concurrent sync may have inserted the same key meanwhile: the unique
    # constraint makes that insert a no-op.
    Notification.objects.bulk_create(created, ignore_conflicts=True)
    if changed:
        Notification.objects.bulk_update(changed, ['payload', 'read_at'])
    if undone:
        Notification.objects.filter(pk__in=undone).delete()
    logger.debug(
        'Release change: %d created, %d changed, %d undone',
        len(created),
        len(changed),
        len(undone),
    )
    return len(created) + len(changed) + len(undone)
