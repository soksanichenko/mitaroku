"""The scanner that finds releases that became due in each user's zone."""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from catalog.models import Episode, Season, Title
from notifications.models import Notification, dedupe_key
from notifications.moments import due_moment, local_day
from tracking.models import LibraryEntry, WatchEvent

logger = logging.getLogger(__name__)

# A stopped worker catches up within this look-back; the unique key makes the
# overlap between two runs harmless.
WINDOW = timedelta(hours=48)

Kind = Notification.Kind
# (user id, kind, title id, local day) -> episode ids; empty for a movie.
Groups = dict[tuple[int, str, int, date], set[int]]


def _candidates(
    now: datetime,
) -> tuple[list[tuple[int, date, int, str, bool, int]], list[tuple[int, date]]]:
    """Return the episodes and movies whose date could be due now in any zone.

    Zones span UTC-12 to UTC+14, so the date range is widened by a day on each
    side; the exact check happens per user.

    Args:
        now: The current moment.

    Returns:
        Episode tuples ``(id, air_date, number, season kind, is_extra, title id)``
        and movie tuples ``(title id, release_date)``.
    """
    low = (now - WINDOW).date() - timedelta(days=1)
    high = now.date() + timedelta(days=1)
    episodes = list(
        Episode.objects.filter(air_date__range=(low, high)).values_list(
            'pk',
            'air_date',
            'number',
            'season__kind',
            'season__is_extra',
            'season__title_id',
        )
    )
    movies = list(
        Title.objects.filter(
            kind=Title.Kind.MOVIE, release_date__range=(low, high)
        ).values_list('pk', 'release_date')
    )
    return episodes, movies


def _rows(now: datetime) -> Groups:
    """Return the notification rows that are due for every user, per Decision 5.

    Args:
        now: The current moment.

    Returns:
        The episode ids (empty for movies) per ``(user, kind, title, day)``.
    """
    episodes, movies = _candidates(now)
    title_ids = {row[5] for row in episodes} | {row[0] for row in movies}
    if not title_ids:
        return {}
    # System job: it deliberately spans all users, so it does not use for_user.
    entries = list(
        LibraryEntry.objects.filter(
            title_id__in=title_ids,
            status__in=(LibraryEntry.Status.WATCHING, LibraryEntry.Status.PLANNED),
        ).select_related('user__profile')
    )
    # System job again: watched pairs of every user for the candidate episodes.
    watched = set(
        WatchEvent.objects.filter(
            episode_id__in=[row[0] for row in episodes],
            user_id__in={entry.user_id for entry in entries},
        ).values_list('user_id', 'episode_id')
    )
    by_title: dict[int, list[LibraryEntry]] = defaultdict(list)
    for entry in entries:
        by_title[entry.title_id].append(entry)

    groups: Groups = defaultdict(set)

    def add(entry: LibraryEntry, kind: str, day: date, episode_id: int | None) -> None:
        zone = entry.user.profile.timezone
        due = due_moment(day, zone)
        if not (now - WINDOW < due <= now):
            return
        if due <= max(entry.added_at, entry.user.profile.notify_since):
            return
        ids = groups[(entry.user_id, kind, entry.title_id, local_day(due, zone))]
        if episode_id is not None:
            ids.add(episode_id)

    for episode_id, air_date, number, season_kind, is_extra, title_id in episodes:
        for entry in by_title[title_id]:
            if (entry.user_id, episode_id) in watched:
                continue
            if entry.status == LibraryEntry.Status.WATCHING:
                add(entry, Kind.EPISODE_AIRED, air_date, episode_id)
            elif number == 1 and season_kind == Season.Kind.TV and not is_extra:
                add(entry, Kind.PREMIERE, air_date, episode_id)
    for title_id, release_date in movies:
        for entry in by_title[title_id]:
            add(entry, Kind.MOVIE_RELEASED, release_date, None)
    return groups


def scan_due(now: datetime) -> int:
    """Write a notification for every release that became due for a user.

    A release is due at 09:00 in the user's zone on its date, counts for 48
    hours and only when it is later than both the library entry and the
    profile's ``notify_since``. A later event of the same user, kind, title and
    local day extends the existing row and marks it unread; an event the row
    already holds changes nothing. The number of queries does not depend on the
    number of users or titles.

    Args:
        now: The current moment (the clock is never read here).

    Returns:
        The number of rows created or changed.
    """
    groups = _rows(now)
    if not groups:
        return 0
    # System job: existing rows of all users, matched by their unique key.
    keys = {dedupe_key(kind, title_id, day) for _, kind, title_id, day in groups}
    existing = {
        (row.user_id, row.dedupe_key): row
        for row in Notification.objects.filter(
            user_id__in={user_id for user_id, *_ in groups}, dedupe_key__in=keys
        )
    }
    created: list[Notification] = []
    changed: list[Notification] = []
    for (user_id, kind, title_id, day), ids in groups.items():
        key = dedupe_key(kind, title_id, day)
        row = existing.get((user_id, key))
        if row is None:
            created.append(
                Notification(
                    user_id=user_id,
                    kind=kind,
                    title_id=title_id,
                    day=day,
                    dedupe_key=key,
                    payload={'episodes': sorted(ids)} if ids else {},
                    created_at=now,
                )
            )
            continue
        known = set(row.payload.get('episodes', []))
        if ids <= known:
            continue
        row.payload = {'episodes': sorted(known | ids)}
        row.read_at = None
        changed.append(row)
    # A concurrent run may have inserted the same key meanwhile: the unique
    # constraint makes that insert a no-op.
    Notification.objects.bulk_create(created, ignore_conflicts=True)
    if changed:
        Notification.objects.bulk_update(changed, ['payload', 'read_at'])
    logger.debug('Scan: %d created, %d changed', len(created), len(changed))
    return len(created) + len(changed)
