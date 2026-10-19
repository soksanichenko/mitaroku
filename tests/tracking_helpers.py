"""Builders shared by the tracking tests."""

from datetime import date, datetime, timedelta, timezone

from accounts.models import User
from catalog.models import Episode, Season, Title
from tracking.models import WatchEvent

TODAY = date(2026, 10, 19)


def make_show(
    name: str = 'Show',
    status: str = 'airing',
    dated: tuple[int | None, ...] = (-30, -23, -16, 7, None),
    extra: bool = True,
) -> tuple[Title, list[Episode], Episode | None]:
    """Create a TV title with a main season and, optionally, an extra season.

    Args:
        name: The original title.
        status: The ``Title.Status`` value.
        dated: One entry per main episode: days from ``TODAY`` or None (undated).
        extra: Whether to add season 2 (``is_extra``) with one episode aired
            100 days before ``TODAY``.

    Returns:
        The title, its main episodes in order and the extra episode (or None).
    """
    title = Title.objects.create(kind=Title.Kind.TV, original_title=name, status=status)
    season = Season.objects.create(
        title=title,
        number=1,
        kind=Season.Kind.TV,
        episode_count=len(dated),
        episode_source=Season.EpisodeSource.TMDB,
    )
    episodes = [
        Episode.objects.create(
            season=season,
            number=index,
            absolute_number=index,
            air_date=None if offset is None else TODAY + timedelta(days=offset),
        )
        for index, offset in enumerate(dated, start=1)
    ]
    extra_episode = None
    if extra:
        specials = Season.objects.create(
            title=title,
            number=2,
            kind=Season.Kind.SPECIAL,
            is_extra=True,
            episode_count=1,
            episode_source=Season.EpisodeSource.TMDB,
        )
        extra_episode = Episode.objects.create(
            season=specials, number=1, air_date=TODAY - timedelta(days=100)
        )
    return title, episodes, extra_episode


def make_movie(
    name: str = 'Movie', release_offset: int | None = -10, status: str = 'ended'
) -> Title:
    """Create a movie released ``release_offset`` days from ``TODAY``."""
    release = None if release_offset is None else TODAY + timedelta(days=release_offset)
    return Title.objects.create(
        kind=Title.Kind.MOVIE,
        original_title=name,
        status=status,
        release_date=release,
    )


def watch(
    user: User, *episodes: Episode, at: datetime | None = None
) -> list[WatchEvent]:
    """Create one ``WatchEvent`` per episode, bypassing the services.

    Args:
        user: Who watched.
        *episodes: The watched episodes.
        at: When; defaults to the current time.

    Returns:
        The created events.
    """
    when = at or datetime.now(timezone.utc)
    return [
        WatchEvent.objects.create(
            user=user, title=episode.season.title, episode=episode, watched_at=when
        )
        for episode in episodes
    ]


def first_episode(title: Title) -> Episode:
    """Return the first main episode that has aired as of ``TODAY``."""
    return (
        Episode.objects.filter(
            season__title=title, season__is_extra=False, air_date__lte=TODAY
        )
        .order_by('season__number', 'number')
        .first()
    )


def future_episode(title: Title) -> Episode:
    """Return the first main episode dated after ``TODAY``."""
    return (
        Episode.objects.filter(
            season__title=title, season__is_extra=False, air_date__gt=TODAY
        )
        .order_by('season__number', 'number')
        .first()
    )
