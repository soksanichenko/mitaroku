"""Builders shared by the tracking tests."""

from datetime import date, datetime, timedelta, timezone

from accounts.models import User
from catalog.models import Episode, EpisodeTranslation, Season, Title
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


def make_undated_show(
    status: str = 'ended',
    start: int | None = -2000,
    count: int = 3,
    main_undated: bool = False,
) -> tuple[Title, list[Episode], list[Episode]]:
    """Create a synced show with undated episodes whose season has a start date.

    By default the show has two aired main episodes and an extra OVA season of
    ``count`` undated episodes (a finished show whose OVA dates were never
    filled in). With ``main_undated`` the main season itself holds the ``count``
    undated episodes and the extra season is left out.

    Args:
        status: The ``Title.Status`` value.
        start: ``start_date`` of the season with the undated episodes, as days
            from ``TODAY``; None leaves it empty.
        count: How many undated episodes.
        main_undated: Make the main season the undated one.

    Returns:
        The title, its main episodes and the undated episodes (the main ones
        when ``main_undated``, else the OVA ones).
    """
    title = Title.objects.create(
        kind=Title.Kind.TV,
        original_title='Undated',
        status=status,
        synced_at=datetime.now(timezone.utc),
        next_sync_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    start_date = None if start is None else TODAY + timedelta(days=start)

    def season(number: int, kind: str, extra: bool, **fields) -> Season:
        return Season.objects.create(
            title=title,
            number=number,
            kind=kind,
            is_extra=extra,
            episode_source=Season.EpisodeSource.TMDB,
            **fields,
        )

    def episodes(parent: Season, dates: list[date | None]) -> list[Episode]:
        return [
            Episode.objects.create(
                season=parent,
                number=index,
                absolute_number=None if parent.is_extra else index,
                air_date=air_date,
            )
            for index, air_date in enumerate(dates, start=1)
        ]

    if main_undated:
        main = season(1, Season.Kind.TV, False, start_date=start_date)
        undated = episodes(main, [None] * count)
        return title, undated, undated
    main = season(1, Season.Kind.TV, False, start_date=TODAY - timedelta(days=3000))
    mains = episodes(main, [TODAY - timedelta(days=2990), TODAY - timedelta(days=2983)])
    ova = season(2, Season.Kind.OVA, True, start_date=start_date)
    return title, mains, episodes(ova, [None] * count)


def make_ova_first_anime(status: str = 'airing') -> Title:
    """Create a synced anime franchise whose first entry is an OVA.

    Entry 1 is the OVA (one episode, no TMDB numbers), entry 2 is TMDB season 1
    (3 episodes, absolute 1 to 3) and entry 3 is TMDB season 2 (2 episodes,
    absolute 4 and 5), so the franchise entry index is one ahead of the TMDB
    season. All episodes aired more than a month before ``TODAY``, except the
    last, which airs a week after it.
    """
    title = Title.objects.create(
        kind=Title.Kind.ANIME,
        original_title='Franchise',
        status=status,
        synced_at=datetime.now(timezone.utc),
        next_sync_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    absolute = 0
    for number, kind, tmdb_season, count in (
        (1, Season.Kind.OVA, None, 1),
        (2, Season.Kind.TV, 1, 3),
        (3, Season.Kind.TV, 2, 2),
    ):
        season = Season.objects.create(
            title=title,
            number=number,
            kind=kind,
            is_extra=kind == Season.Kind.OVA,
            episode_source=Season.EpisodeSource.TMDB,
        )
        for index in range(1, count + 1):
            absolute += 0 if season.is_extra else 1
            last = number == 3 and index == count
            Episode.objects.create(
                season=season,
                number=index,
                absolute_number=None if season.is_extra else absolute,
                tmdb_season_number=tmdb_season,
                tmdb_episode_number=None if tmdb_season is None else index,
                air_date=TODAY + timedelta(days=7 if last else -60 + absolute),
            )
    return title


EPISODE_NAMES = {
    'en': 'The Long Road Home, Part {n} of the Great Saga Chapter',
    'ru': 'Долгая дорога домой, часть {n} большой саги глава',
}


def make_long_show(
    seasons: int = 20,
    per_season: int = 60,
    name: str = 'Long',
    names: str | None = None,
) -> tuple[Title, list[Season]]:
    """Create a synced TV title whose episodes all aired before ``TODAY``.

    Args:
        seasons: How many main seasons.
        per_season: Episodes per season.
        name: The original title.
        names: A language of ``EPISODE_NAMES`` (``en`` or ``ru``): every episode
            gets a name of about 50 characters in it; None leaves them unnamed.

    Returns:
        The title and its seasons in order.
    """
    now = datetime.now(timezone.utc)
    title = Title.objects.create(
        kind=Title.Kind.TV,
        original_title=name,
        status='ended',
        synced_at=now,
        next_sync_at=now + timedelta(hours=1),
    )
    made = Season.objects.bulk_create(
        Season(
            title=title,
            number=number,
            kind=Season.Kind.TV,
            episode_count=per_season,
            episode_source=Season.EpisodeSource.TMDB,
        )
        for number in range(1, seasons + 1)
    )
    Episode.objects.bulk_create(
        Episode(
            season=season,
            number=n,
            absolute_number=(season.number - 1) * per_season + n,
            air_date=TODAY - timedelta(days=1000 - n),
        )
        for season in made
        for n in range(1, per_season + 1)
    )
    if names:
        EpisodeTranslation.objects.bulk_create(
            EpisodeTranslation(
                episode=episode,
                language=names,
                name=EPISODE_NAMES[names].format(n=episode.number)[:50],
            )
            for episode in Episode.objects.filter(season__title=title)
        )
    return title, made
