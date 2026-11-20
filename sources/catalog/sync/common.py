"""Pieces shared by the movie, TV and anime sync."""

import logging
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import islice

from catalog.models import (
    Episode,
    EpisodeTranslation,
    ExternalId,
    Season,
    Title,
    TitleDetails,
    TitleTranslation,
)
from providers.anilist import AniListClient
from providers.clients import get_anilist, get_tmdb
from providers.http import ProviderError
from providers.tmdb import TmdbClient, TmdbVideo
from providers.types import SearchHit

logger = logging.getLogger(__name__)

# How soon a title is looked at again, by status. Airing and announced titles
# change often; finished ones only get a slow check for corrections.
RESYNC_AFTER = {
    Title.Status.AIRING: timedelta(hours=6),
    Title.Status.UPCOMING: timedelta(hours=24),
    Title.Status.ENDED: timedelta(days=14),
    Title.Status.CANCELED: timedelta(days=14),
    Title.Status.UNKNOWN: timedelta(days=1),
}
# A YouTube video id; anything else never reaches an embed URL.
TRAILER_KEY = re.compile(r'[A-Za-z0-9_-]{6,20}')
MAX_TRAILERS = 8
MAX_SIMILAR = 12
# Official videos first, then by how much of a trailer they are.
TRAILER_KINDS = ('Trailer', 'Teaser', 'Clip')


@dataclass
class Clients:
    """The provider clients a sync needs. ``tmdb`` is None without a token."""

    tmdb: TmdbClient | None
    anilist: AniListClient


@dataclass(frozen=True)
class EpisodeRow:
    """One episode to write, from whichever provider it came from."""

    number: int
    air_date: date | None
    tmdb_season_number: int | None = None
    tmdb_episode_number: int | None = None
    name: str = ''
    overview: str = ''


def default_clients() -> Clients:
    """Build the clients from the configuration; TMDB is optional."""
    try:
        tmdb = get_tmdb()
    except ProviderError as error:
        logger.warning('%s', error)
        tmdb = None
    return Clients(tmdb=tmdb, anilist=get_anilist())


def require_tmdb(clients: Clients) -> TmdbClient:
    """Return the TMDB client, or fail when no token is configured."""
    if clients.tmdb is None:
        raise ProviderError('TMDB is not configured: set providers.tmdb_token')
    return clients.tmdb


def to_rating(value: float | None) -> Decimal | None:
    """Round a provider rating to one decimal place."""
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def schedule_next_sync(title: Title, now: datetime) -> None:
    """Set ``synced_at`` and ``next_sync_at`` according to the title's status."""
    title.synced_at = now
    title.next_sync_at = now + RESYNC_AFTER[Title.Status(title.status)]


def link_external_id(title: Title, provider: str, external_id: object) -> bool:
    """Attach a provider id to a title.

    Returns:
        False when the id already belongs to another title (it is left there).
    """
    link, _ = ExternalId.objects.get_or_create(
        provider=provider, external_id=str(external_id), defaults={'title': title}
    )
    if link.title_id != title.pk:
        logger.warning(
            'Not linking %s:%s to title %s: it belongs to title %s',
            provider,
            external_id,
            title.pk,
            link.title_id,
        )
        return False
    return True


def save_title_translation(
    title: Title, language: str, name: str, overview: str
) -> None:
    """Store a title translation unless both texts are empty."""
    if name or overview:
        TitleTranslation.objects.update_or_create(
            title=title,
            language=language,
            defaults={'name': name, 'overview': overview},
        )


def trailer_dicts(videos: Iterable[TmdbVideo]) -> list[dict]:
    """Pick the videos worth showing, as ``TitleDetails.trailers`` entries.

    Args:
        videos: The videos TMDB lists for a title.

    Returns:
        At most ``MAX_TRAILERS`` YouTube videos with a valid key: official ones
        first, then trailers before teasers before clips, then other kinds.
    """
    usable = [
        video
        for video in videos
        if video.site == 'YouTube' and TRAILER_KEY.fullmatch(video.key)
    ]

    def rank(video: TmdbVideo) -> tuple[bool, int]:
        kind = video.kind
        return (
            not video.official,
            TRAILER_KINDS.index(kind) if kind in TRAILER_KINDS else len(TRAILER_KINDS),
        )

    return [
        {
            'key': video.key,
            'name': video.name,
            'kind': video.kind,
            'language': video.language,
            'official': video.official,
        }
        for video in sorted(usable, key=rank)[:MAX_TRAILERS]
    ]


def similar_dicts(
    hits: Iterable[SearchHit], exclude: Collection[tuple[str, str]] = ()
) -> list[dict]:
    """Turn recommendations into ``TitleDetails.similar`` entries.

    Args:
        hits: The recommended titles.
        exclude: ``(provider, external_id)`` pairs to leave out, such as the
            title's own ids.

    Returns:
        At most ``MAX_SIMILAR`` entries, without excluded and ``anime_like``
        hits (the AniList side covers those).
    """
    wanted = (
        hit
        for hit in hits
        if not hit.anime_like and (hit.provider, hit.external_id) not in exclude
    )
    return [
        {
            'provider': hit.provider,
            'external_id': hit.external_id,
            'kind': hit.kind,
            'title': hit.title,
            'original_title': hit.original_title,
            'year': hit.year,
            'poster_url': hit.poster_url,
            'rating': hit.rating,
        }
        for hit in islice(wanted, MAX_SIMILAR)
    ]


def write_details(title: Title, **fields: object) -> TitleDetails:
    """Create or update the details row of a title with exactly these fields."""
    details, _ = TitleDetails.objects.update_or_create(title=title, defaults=fields)
    return details


def write_episodes(
    season: Season, rows: list[EpisodeRow], language: str = 'en'
) -> None:
    """Upsert the episodes of a season and their translations.

    Episodes are never deleted, because watch events will refer to them.

    Args:
        season: The season to write into.
        rows: The episodes as the provider reports them.
        language: Language of the names and overviews in ``rows``.
    """
    if not rows:
        return
    Episode.objects.bulk_create(
        [
            Episode(
                season=season,
                number=row.number,
                air_date=row.air_date,
                tmdb_season_number=row.tmdb_season_number,
                tmdb_episode_number=row.tmdb_episode_number,
            )
            for row in rows
        ],
        update_conflicts=True,
        unique_fields=['season', 'number'],
        update_fields=['air_date', 'tmdb_season_number', 'tmdb_episode_number'],
    )
    texts = [row for row in rows if row.name or row.overview]
    if not texts:
        return
    ids = dict(
        season.episodes.filter(number__in=[row.number for row in texts]).values_list(
            'number', 'pk'
        )
    )
    EpisodeTranslation.objects.bulk_create(
        [
            EpisodeTranslation(
                episode_id=ids[row.number],
                language=language,
                name=row.name,
                overview=row.overview,
            )
            for row in texts
        ],
        update_conflicts=True,
        unique_fields=['episode', 'language'],
        update_fields=['name', 'overview'],
    )


def renumber_absolute(title: Title) -> None:
    """Number the episodes of the main seasons 1, 2, 3 ... across the title.

    Extras and specials get no absolute number, and neither do rows beyond the
    known episode count of their season (anime only: TMDB numbers may have
    gaps): those are left over from an earlier, larger guess and would
    otherwise be counted twice.
    """
    counter = 0
    changed = []
    for season in title.seasons.order_by('number'):
        for episode in season.episodes.order_by('number'):
            beyond = (
                season.anilist_id is not None
                and season.episode_count is not None
                and episode.number > season.episode_count
            )
            if season.is_extra or beyond:
                wanted = None
            else:
                counter += 1
                wanted = counter
            if episode.absolute_number != wanted:
                episode.absolute_number = wanted
                changed.append(episode)
    Episode.objects.bulk_update(changed, ['absolute_number'], batch_size=500)
