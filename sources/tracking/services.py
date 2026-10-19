"""Write side of tracking: library status, score, note and watch events.

Every mutation runs in ``transaction.atomic()`` and locks the user's library
entry row (``get_or_create``, then ``select_for_update`` through ``for_user``),
so two simultaneous requests for one user and title cannot both pass the
"already watched" check. Only aired episodes can be watched; ``today`` and
``now`` are keyword arguments so that tests can fix the clock.
"""

from collections.abc import Iterable
from datetime import date, datetime

from catalog.models import Episode, Season, Title
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from tracking.models import LibraryEntry, WatchEvent
from tracking.queries import main_episodes, progress, watched_episode_ids

Status = LibraryEntry.Status

MAX_NOTE_LENGTH = 5000
MAX_SCORE = 10


class NotAired(ValueError):
    """Raised when something that has not been released is marked watched."""


class NotInLibrary(LookupError):
    """Raised when a score or note is set for a title not in the library."""


def is_aired(episode: Episode, today: date) -> bool:
    """Return whether ``episode`` has an air date that is not after ``today``."""
    return episode.air_date is not None and episode.air_date <= today


def _aired(episodes: Iterable[Episode], today: date) -> list[Episode]:
    """Return the episodes that have aired, in the given order."""
    return [episode for episode in episodes if is_aired(episode, today)]


def _lock_entry(user, title: Title) -> LibraryEntry:
    """Return the user's entry for ``title`` locked for update.

    A missing entry is created as Watching. Must run inside a transaction.
    """
    entry, _ = LibraryEntry.objects.get_or_create(
        user=user, title=title, defaults={'status': Status.WATCHING}
    )
    return LibraryEntry.objects.for_user(user).select_for_update().get(pk=entry.pk)


def _lock_existing_entry(user, title: Title) -> LibraryEntry | None:
    """Return the user's entry for ``title`` locked for update, or None.

    Used by actions that must not create an entry. Must run inside a
    transaction.
    """
    return (
        LibraryEntry.objects.for_user(user)
        .select_for_update()
        .filter(title=title)
        .first()
    )


def _after_watching(user, entry: LibraryEntry, title: Title) -> None:
    """Apply the status rules after viewings were added and save the entry.

    A Planned or Dropped entry becomes Watching. A Watching entry of an ended or
    canceled series with every main episode watched becomes Completed. Completed
    is left alone, so a rewatch does not reopen it.
    """
    if entry.status in (Status.PLANNED, Status.DROPPED):
        entry.status = Status.WATCHING
    if entry.status == Status.WATCHING and title.status in (
        Title.Status.ENDED,
        Title.Status.CANCELED,
    ):
        state = progress(user, title)
        if state.total and state.watched == state.total:
            entry.status = Status.COMPLETED
    entry.save(update_fields=['status', 'updated_at'])


def _add_viewings(user, title: Title, episodes: list[Episode], now: datetime) -> int:
    """Add one viewing for each of the (aired) episodes the user has not watched.

    Must run inside a transaction with the entry locked.
    """
    watched = watched_episode_ids(user, title)
    events = [
        WatchEvent(user=user, title=title, episode=episode, watched_at=now)
        for episode in episodes
        if episode.pk not in watched
    ]
    WatchEvent.objects.bulk_create(events)
    return len(events)


def _delete_latest_viewing(user, title: Title, episode: Episode | None) -> bool:
    """Delete the user's most recent viewing of ``episode`` (None: the movie)."""
    latest = (
        WatchEvent.objects.for_user(user)
        .filter(title=title, episode=episode)
        .order_by('-watched_at', '-pk')
        .first()
    )
    if latest is None:
        return False
    latest.delete()
    return True


def set_status(
    user,
    title: Title,
    status: str | None,
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> LibraryEntry | None:
    """Create or update the library entry, or remove it.

    Completed marks every aired main episode of a series watched, and adds a
    viewing to a released movie that has none. Removing the entry keeps the
    watch events, so adding the title again restores its progress.

    Args:
        user: The owner of the library.
        title: The title.
        status: A ``LibraryEntry.Status`` value, or None to remove the entry.
        now: The time of the viewings; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Returns:
        The entry, or None when it was removed.

    Raises:
        ValueError: If ``status`` is not a valid status.
    """
    if status is not None and status not in Status.values:
        raise ValueError(f'Unknown status: {status!r}')
    now = now or timezone.now()
    today = today or timezone.localdate()
    with transaction.atomic():
        if status is None:
            if _lock_existing_entry(user, title):
                LibraryEntry.objects.for_user(user).filter(title=title).delete()
            return None
        entry, _ = LibraryEntry.objects.get_or_create(
            user=user, title=title, defaults={'status': status}
        )
        entry = LibraryEntry.objects.for_user(user).select_for_update().get(pk=entry.pk)
        entry.status = status
        entry.save(update_fields=['status', 'updated_at'])
        if status == Status.COMPLETED:
            if title.kind == Title.Kind.MOVIE:
                released = title.release_date and title.release_date <= today
                seen = (
                    WatchEvent.objects.for_user(user)
                    .filter(title=title, episode__isnull=True)
                    .exists()
                )
                if released and not seen:
                    WatchEvent.objects.create(user=user, title=title, watched_at=now)
            else:
                _add_viewings(user, title, _aired(main_episodes(title), today), now)
        return entry


def set_score(user, title: Title, score: int) -> LibraryEntry:
    """Set the score of a library entry.

    Args:
        user: The owner of the library.
        title: The title.
        score: 0 (clears the score) to 10.

    Returns:
        The updated entry.

    Raises:
        ValueError: If ``score`` is outside 0 to 10.
        NotInLibrary: If the title is not in the user's library.
    """
    if not 0 <= score <= MAX_SCORE:
        raise ValueError(f'Score must be between 0 and {MAX_SCORE}')
    with transaction.atomic():
        entry = _lock_existing_entry(user, title)
        if entry is None:
            raise NotInLibrary(title.pk)
        entry.score = score
        entry.save(update_fields=['score', 'updated_at'])
        return entry


def set_note(user, title: Title, note: str) -> LibraryEntry:
    """Set the private note of a library entry.

    Args:
        user: The owner of the library.
        title: The title.
        note: The text, at most 5000 characters.

    Returns:
        The updated entry.

    Raises:
        ValueError: If the note is longer than 5000 characters.
        NotInLibrary: If the title is not in the user's library.
    """
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f'The note is longer than {MAX_NOTE_LENGTH} characters')
    with transaction.atomic():
        entry = _lock_existing_entry(user, title)
        if entry is None:
            raise NotInLibrary(title.pk)
        entry.note = note
        entry.save(update_fields=['note', 'updated_at'])
        return entry


def set_episode_watched(
    user,
    episode: Episode,
    watched: bool,
    *,
    again: bool = False,
    now: datetime | None = None,
    today: date | None = None,
) -> None:
    """Mark one episode watched or unwatched.

    Watching is idempotent: an episode that already has a viewing gets none
    unless ``again`` is set, which records one more. Unwatching deletes the
    most recent viewing. Watching makes a missing, Planned or Dropped entry
    Watching.

    Args:
        user: The viewer.
        episode: The episode.
        watched: The wanted state.
        again: Record one more viewing of an already watched episode.
        now: The time of the viewing; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Raises:
        NotAired: If the episode has not aired and ``watched`` is true.
    """
    title = episode.season.title
    if not watched:
        with transaction.atomic():
            _lock_existing_entry(user, title)
            _delete_latest_viewing(user, title, episode)
        return
    now = now or timezone.now()
    today = today or timezone.localdate()
    if not is_aired(episode, today):
        raise NotAired(episode.pk)
    with transaction.atomic():
        entry = _lock_entry(user, title)
        seen = WatchEvent.objects.for_user(user).filter(episode=episode).exists()
        if again or not seen:
            WatchEvent.objects.create(
                user=user, title=title, episode=episode, watched_at=now
            )
            _after_watching(user, entry, title)


def mark_episodes(
    user,
    title: Title,
    episodes: Iterable[Episode],
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> int:
    """Add a viewing for each aired episode the user has not watched.

    Args:
        user: The viewer.
        title: The title the episodes belong to.
        episodes: The candidate episodes; unaired ones are skipped.
        now: The time of the viewings; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Returns:
        The number of viewings added.
    """
    now = now or timezone.now()
    today = today or timezone.localdate()
    aired = _aired(episodes, today)
    if not aired:
        return 0
    with transaction.atomic():
        entry = _lock_entry(user, title)
        added = _add_viewings(user, title, aired, now)
        if added:
            _after_watching(user, entry, title)
        return added


def mark_up_to(
    user,
    episode: Episode,
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> int:
    """Mark the episode and every earlier one in its group as watched.

    The group of a main episode is all earlier main seasons plus its own season
    up to the episode; the group of an extra episode is its own season.

    Args:
        user: The viewer.
        episode: The last episode to mark.
        now: The time of the viewings; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Returns:
        The number of viewings added.
    """
    season = episode.season
    title = season.title
    if season.is_extra:
        group = Episode.objects.filter(season=season, number__lte=episode.number)
    else:
        group = main_episodes(title).filter(
            Q(season__number__lt=season.number)
            | Q(season=season, number__lte=episode.number)
        )
    return mark_episodes(user, title, group, now=now, today=today)


def set_season_watched(
    user,
    season: Season,
    watched: bool,
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> int:
    """Mark a whole season watched or unwatched.

    Args:
        user: The viewer.
        season: The season (extras included).
        watched: The wanted state.
        now: The time of the viewings; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Returns:
        With ``watched`` true, the number of viewings added (unaired episodes are
        skipped); otherwise the number of the user's events deleted.
    """
    title = season.title
    if watched:
        return mark_episodes(user, title, season.episodes.all(), now=now, today=today)
    with transaction.atomic():
        _lock_existing_entry(user, title)
        deleted, _ = (
            WatchEvent.objects.for_user(user).filter(episode__season=season).delete()
        )
        return deleted


def set_title_watched(
    user,
    title: Title,
    watched: bool,
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> int:
    """Mark every season of a title, extras included, watched or unwatched.

    A movie is delegated to ``set_movie_watched``.

    Args:
        user: The viewer.
        title: The title.
        watched: The wanted state.
        now: The time of the viewings; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Returns:
        What ``set_season_watched`` would return summed over the seasons; for a
        movie 1 when a viewing was added or deleted, else 0.

    Raises:
        NotAired: If the title is a movie that has not been released and
            ``watched`` is true.
    """
    now = now or timezone.now()
    today = today or timezone.localdate()
    if title.kind == Title.Kind.MOVIE:
        return _set_movie_watched(user, title, watched, False, now, today)
    if watched:
        episodes = Episode.objects.filter(season__title=title).order_by(
            'season__number', 'number'
        )
        return mark_episodes(user, title, episodes, now=now, today=today)
    with transaction.atomic():
        _lock_existing_entry(user, title)
        deleted, _ = (
            WatchEvent.objects.for_user(user)
            .filter(title=title, episode__isnull=False)
            .delete()
        )
        return deleted


def _set_movie_watched(
    user, title: Title, watched: bool, again: bool, now: datetime, today: date
) -> int:
    """Implement ``set_movie_watched``; return 1 when an event changed, else 0."""
    if not watched:
        with transaction.atomic():
            _lock_existing_entry(user, title)
            return int(_delete_latest_viewing(user, title, None))
    if title.release_date is None or title.release_date > today:
        raise NotAired(title.pk)
    with transaction.atomic():
        entry = _lock_entry(user, title)
        seen = (
            WatchEvent.objects.for_user(user)
            .filter(title=title, episode__isnull=True)
            .exists()
        )
        added = again or not seen
        if added:
            WatchEvent.objects.create(user=user, title=title, watched_at=now)
        if entry.status != Status.COMPLETED:
            entry.status = Status.COMPLETED
            entry.save(update_fields=['status', 'updated_at'])
        return int(added)


def set_movie_watched(
    user,
    title: Title,
    watched: bool,
    *,
    again: bool = False,
    now: datetime | None = None,
    today: date | None = None,
) -> None:
    """Mark a movie watched or unwatched.

    Watching sets the entry to Completed, creating it if needed, and records a
    viewing unless there is one already and ``again`` is not set. Unwatching
    deletes the latest viewing and leaves the status alone.

    Args:
        user: The viewer.
        title: The movie.
        watched: The wanted state.
        again: Record one more viewing of an already watched movie.
        now: The time of the viewing; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Raises:
        NotAired: If the movie has no release date or it is after ``today``,
            and ``watched`` is true.
    """
    _set_movie_watched(
        user,
        title,
        watched,
        again,
        now or timezone.now(),
        today or timezone.localdate(),
    )


def mark_next(
    user,
    title: Title,
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> Episode | None:
    """Mark the first unwatched aired main episode as watched.

    Args:
        user: The viewer.
        title: The series.
        now: The time of the viewing; defaults to ``timezone.now()``.
        today: The current date; defaults to ``timezone.localdate()``.

    Returns:
        The episode marked, or None when every aired main episode is watched.
    """
    now = now or timezone.now()
    today = today or timezone.localdate()
    aired = main_episodes(title).filter(air_date__lte=today).select_related('season')
    if not aired.exists():
        return None
    with transaction.atomic():
        entry = _lock_entry(user, title)
        episode = aired.exclude(pk__in=watched_episode_ids(user, title)).first()
        if episode is not None:
            _add_viewings(user, title, [episode], now)
            _after_watching(user, entry, title)
        return episode
