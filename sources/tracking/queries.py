"""Read side of tracking: progress, the library, upcoming releases, title pages.

Progress counts main (non-extra) seasons only. Counts are ``Subquery``
annotations keyed on the outer title because a join of episodes and watch events
would multiply rows. Every read of the tracking models goes through
``for_user``.
"""

from dataclasses import dataclass
from datetime import date, datetime

from catalog.models import (
    Episode,
    EpisodeTranslation,
    Season,
    Title,
    TitleTranslation,
)
from catalog.translations import FALLBACK_LANGUAGE
from django.db.models import (
    Count,
    Exists,
    IntegerField,
    Max,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from tracking.models import LibraryEntry, WatchEvent


def _percent(watched: int, total: int) -> int:
    """Return the percent watched, rounded down; 0 when there is nothing to watch."""
    return watched * 100 // total if total else 0


@dataclass(frozen=True)
class Progress:
    """Watched and total main episodes of a title."""

    watched: int
    total: int

    @property
    def percent(self) -> int:
        """The percent watched, rounded down (0 when ``total`` is 0)."""
        return _percent(self.watched, self.total)


@dataclass(frozen=True)
class LibraryRow:
    """A library entry with its progress and next episode."""

    entry: LibraryEntry
    watched: int
    total: int
    next: Episode | None
    last_watched_at: datetime | None

    @property
    def percent(self) -> int:
        """The percent watched, rounded down (0 when ``total`` is 0)."""
        return _percent(self.watched, self.total)


@dataclass(frozen=True)
class UpcomingItem:
    """The next release of a title: an episode of a series or a movie."""

    title: Title
    episode: Episode | None
    date: date | None


@dataclass(frozen=True)
class EpisodeRow:
    """An episode on the title page."""

    episode: Episode
    aired: bool
    views: int


@dataclass(frozen=True)
class GroupState:
    """A season on the title page with its episodes and counts."""

    season: Season
    rows: list[EpisodeRow]
    watched: int
    total: int
    aired_total: int


@dataclass(frozen=True)
class TitlePageState:
    """Everything the title page needs about the user's tracking of a title."""

    entry: LibraryEntry | None
    groups: list[GroupState]
    progress: Progress
    next_episode: Episode | None
    movie_views: int


def main_episodes(title: Title) -> QuerySet[Episode]:
    """Return the episodes of the main seasons of ``title`` in watching order."""
    return main_episodes_for([title])


def main_episodes_for(titles) -> QuerySet[Episode]:
    """Return the main-season episodes of several titles in watching order.

    Args:
        titles: Titles (or their ids).

    Returns:
        Episodes ordered by title, season number and episode number.
    """
    return Episode.objects.filter(
        season__title__in=titles, season__is_extra=False
    ).order_by('season__title', 'season__number', 'number')


def watched_episode_ids(user, title: Title) -> set[int]:
    """Return the ids of the episodes of ``title`` the user has watched.

    Extra seasons are included: they can be checked off.
    """
    return set(
        WatchEvent.objects.for_user(user)
        .filter(title=title, episode__isnull=False)
        .values_list('episode_id', flat=True)
    )


def _seen(user) -> Exists:
    """Return a condition on an ``Episode`` row: the user watched it."""
    return Exists(WatchEvent.objects.for_user(user).filter(episode=OuterRef('pk')))


def progress(user, title: Title) -> Progress:
    """Return how many main episodes of ``title`` the user has watched."""
    totals = main_episodes(title).aggregate(
        total=Count('pk'), watched=Count('pk', filter=Q(_seen(user)))
    )
    return Progress(watched=totals['watched'], total=totals['total'])


def next_episode(user, title: Title) -> Episode | None:
    """Return the first unwatched main episode (aired or not), with its season."""
    return main_episodes(title).filter(~_seen(user)).select_related('season').first()


def library_rows(
    user,
    *,
    kind: str | None = None,
    status: str | None = None,
    query: str = '',
    language: str = 'en',
) -> list[LibraryRow]:
    """Return the user's library with progress, in at most three queries.

    The queries fetch the entries with annotated counts, the next episodes in
    bulk and the translations for ``language`` and English in bulk.

    Args:
        user: The owner of the library.
        kind: Only titles of this ``Title.Kind``.
        status: Only entries with this ``LibraryEntry.Status``.
        query: Matches the original title or any translation name,
            case-insensitively.
        language: The metadata language whose translations are loaded.

    Returns:
        Rows from the most recently updated entry.
    """
    main = Episode.objects.filter(
        season__title=OuterRef('title'), season__is_extra=False
    )
    events = WatchEvent.objects.for_user(user).filter(title=OuterRef('title'))
    entries = (
        LibraryEntry.objects.for_user(user)
        .select_related('title')
        .annotate(
            total_count=Coalesce(
                Subquery(
                    main.order_by()
                    .values('season__title')
                    .annotate(n=Count('pk'))
                    .values('n'),
                    output_field=IntegerField(),
                ),
                0,
            ),
            watched_count=Coalesce(
                Subquery(
                    events.filter(episode__season__is_extra=False)
                    .order_by()
                    .values('title')
                    .annotate(n=Count('episode', distinct=True))
                    .values('n'),
                    output_field=IntegerField(),
                ),
                0,
            ),
            last_watched_at=Subquery(
                events.order_by()
                .values('title')
                .annotate(m=Max('watched_at'))
                .values('m')
            ),
            next_episode_id=Subquery(
                main.filter(~_seen(user))
                .order_by('season__number', 'number')
                .values('pk')[:1]
            ),
        )
        .prefetch_related(
            Prefetch(
                'title__translations',
                queryset=TitleTranslation.objects.filter(
                    language__in={language, FALLBACK_LANGUAGE}
                ),
            )
        )
        .order_by('-updated_at', '-pk')
    )
    if kind:
        entries = entries.filter(title__kind=kind)
    if status:
        entries = entries.filter(status=status)
    query = query.strip()
    if query:
        entries = entries.filter(
            Q(title__original_title__icontains=query)
            | Exists(
                TitleTranslation.objects.filter(
                    title=OuterRef('title'), name__icontains=query
                )
            )
        )
    entries = list(entries)
    episodes = Episode.objects.select_related('season').in_bulk(
        {entry.next_episode_id for entry in entries if entry.next_episode_id}
    )
    return [
        LibraryRow(
            entry=entry,
            watched=entry.watched_count,
            total=entry.total_count,
            next=episodes.get(entry.next_episode_id),
            last_watched_at=entry.last_watched_at,
        )
        for entry in entries
    ]


def continue_watching(
    user, *, today: date | None = None, language: str = 'en'
) -> list[LibraryRow]:
    """Return the Watching entries that have a next episode, best first.

    Entries whose next episode has aired come first, most recently watched
    first. The rest follow by the date of the next episode, undated last.

    Args:
        user: The owner of the library.
        today: The current date; defaults to ``timezone.localdate()``.
        language: The metadata language whose translations are loaded.

    Returns:
        The rows to continue.
    """
    today = today or timezone.localdate()
    rows = [
        row
        for row in library_rows(
            user, status=LibraryEntry.Status.WATCHING, language=language
        )
        if row.next
    ]
    aired, waiting = [], []
    for row in rows:
        has_aired = row.next.air_date and row.next.air_date <= today
        (aired if has_aired else waiting).append(row)
    aired.sort(key=lambda r: r.last_watched_at or r.entry.added_at, reverse=True)
    waiting.sort(key=lambda r: (r.next.air_date is None, r.next.air_date or today))
    return aired + waiting


def upcoming(user, today: date | None = None) -> list[UpcomingItem]:
    """Return the next release of every Watching or Planned title.

    A series gives its earliest unwatched main episode dated today or later, or,
    when it has none, an unwatched undated main episode if the title is airing or
    upcoming. A movie gives its release date when it is today or later, or no
    date yet if the title is upcoming, unless the user has already watched it.

    Args:
        user: The owner of the library.
        today: The current date; defaults to ``timezone.localdate()``.

    Returns:
        The items sorted by date, undated last.
    """
    today = today or timezone.localdate()
    main = Episode.objects.filter(
        season__title=OuterRef('title'), season__is_extra=False
    ).order_by('air_date', 'season__number', 'number')
    entries = list(
        LibraryEntry.objects.for_user(user)
        .filter(status__in=[LibraryEntry.Status.WATCHING, LibraryEntry.Status.PLANNED])
        .select_related('title')
        .annotate(
            dated_id=Subquery(
                main.filter(~_seen(user), air_date__gte=today).values('pk')[:1]
            ),
            undated_id=Subquery(
                main.filter(~_seen(user), air_date__isnull=True).values('pk')[:1]
            ),
            movie_seen=Exists(
                WatchEvent.objects.for_user(user).filter(
                    title=OuterRef('title'), episode__isnull=True
                )
            ),
        )
    )
    episodes = Episode.objects.select_related('season').in_bulk(
        {pk for entry in entries for pk in (entry.dated_id, entry.undated_id) if pk}
    )
    open_status = {Title.Status.AIRING, Title.Status.UPCOMING}
    items = []
    for entry in entries:
        title = entry.title
        if title.kind == Title.Kind.MOVIE:
            if entry.movie_seen:
                continue
            if title.release_date and title.release_date >= today:
                items.append(UpcomingItem(title, None, title.release_date))
            elif title.release_date is None and title.status == Title.Status.UPCOMING:
                items.append(UpcomingItem(title, None, None))
        elif entry.dated_id:
            episode = episodes[entry.dated_id]
            items.append(UpcomingItem(title, episode, episode.air_date))
        elif entry.undated_id and title.status in open_status:
            items.append(UpcomingItem(title, episodes[entry.undated_id], None))
    items.sort(key=lambda i: (i.date is None, i.date or today, i.title.original_title))
    return items


def title_page_state(
    user, title: Title, today: date | None = None, language: str = 'en'
) -> TitlePageState:
    """Return the user's tracking state of ``title`` in a constant number of queries.

    Args:
        user: The viewer.
        title: The title shown.
        today: The current date; defaults to ``timezone.localdate()``.
        language: The metadata language whose episode translations are loaded.

    Returns:
        The entry, one group per season, the main-season progress, the next
        episode and the number of times the user watched a movie.
    """
    today = today or timezone.localdate()
    entry = LibraryEntry.objects.for_user(user).filter(title=title).first()
    seasons = Season.objects.filter(title=title).prefetch_related(
        Prefetch(
            'episodes',
            queryset=Episode.objects.order_by('number').prefetch_related(
                Prefetch(
                    'translations',
                    queryset=EpisodeTranslation.objects.filter(
                        language__in={language, FALLBACK_LANGUAGE}
                    ),
                )
            ),
        )
    )
    views = {
        row['episode_id']: row['n']
        for row in WatchEvent.objects.for_user(user)
        .filter(title=title)
        .order_by()
        .values('episode_id')
        .annotate(n=Count('pk'))
    }
    groups = []
    for season in seasons:
        rows = [
            EpisodeRow(
                episode=episode,
                aired=bool(episode.air_date and episode.air_date <= today),
                views=views.get(episode.pk, 0),
            )
            for episode in season.episodes.all()
        ]
        groups.append(
            GroupState(
                season=season,
                rows=rows,
                watched=sum(1 for r in rows if r.views),
                total=len(rows),
                aired_total=sum(1 for r in rows if r.aired),
            )
        )
    main = [g for g in groups if not g.season.is_extra]
    next_up = next((r.episode for g in main for r in g.rows if not r.views), None)
    return TitlePageState(
        entry=entry,
        groups=groups,
        progress=Progress(
            watched=sum(g.watched for g in main), total=sum(g.total for g in main)
        ),
        next_episode=next_up,
        movie_views=views.get(None, 0),
    )
