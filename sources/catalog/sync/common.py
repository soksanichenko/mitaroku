"""Pieces shared by the movie, TV and anime sync."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from catalog.models import (
    Episode,
    EpisodeTranslation,
    ExternalId,
    Season,
    Title,
    TitleTranslation,
)
from providers.anilist import AniListClient
from providers.clients import get_anilist, get_tmdb
from providers.http import ProviderError
from providers.tmdb import TmdbClient

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

    Extras and specials get no absolute number.
    """
    counter = 0
    changed = []
    for season in title.seasons.order_by('number'):
        for episode in season.episodes.order_by('number'):
            if season.is_extra:
                wanted = None
            else:
                counter += 1
                wanted = counter
            if episode.absolute_number != wanted:
                episode.absolute_number = wanted
                changed.append(episode)
    Episode.objects.bulk_update(changed, ['absolute_number'], batch_size=500)
