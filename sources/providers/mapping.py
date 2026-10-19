"""Load the Fribb/anime-lists dataset into the ``AnimeMapping`` table."""

import logging
from collections.abc import Iterable, Iterator

import httpx
from django.db import transaction
from providers.http import ProviderError
from providers.models import AnimeMapping

logger = logging.getLogger(__name__)

CHUNK = 2000
# The real dataset has about 40,000 entries; far fewer means a bad download.
MIN_ENTRIES = 10_000
UPDATE_FIELDS = [
    'mal_id',
    'entry_type',
    'tmdb_tv_id',
    'tmdb_movie_id',
    'imdb_id',
    'tmdb_season',
    'tmdb_episode_offset',
]


def download_dataset(url: str, client: httpx.Client) -> list[dict]:
    """Download the dataset.

    Args:
        url: Location of ``anime-list-full.json``.
        client: HTTP client to use.

    Returns:
        The parsed entries.

    Raises:
        ProviderError: The download failed or the file does not look like the
            dataset, so the previous data stays in place.
    """
    try:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        entries = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ProviderError(f'Fribb dataset download failed: {error}') from error
    if not isinstance(entries, list) or len(entries) < MIN_ENTRIES:
        raise ProviderError('Fribb dataset looks truncated, keeping the old data')
    return entries


def parse_entry(entry: dict) -> AnimeMapping | None:
    """Convert one dataset entry, or return None when it has no AniList id."""
    anilist_id = entry.get('anilist_id')
    if not anilist_id:
        return None
    tmdb = entry.get('themoviedb_id') or {}
    movie = tmdb.get('movie')
    imdb = entry.get('imdb_id') or []
    season = (entry.get('season') or {}).get('tmdb')
    offset = (entry.get('episode_offset') or {}).get('tmdb') or 0
    return AnimeMapping(
        anilist_id=anilist_id,
        mal_id=entry.get('mal_id'),
        entry_type=entry.get('type') or '',
        tmdb_tv_id=tmdb.get('tv'),
        tmdb_movie_id=(movie[0] if isinstance(movie, list) and movie else movie),
        imdb_id=imdb[0] if isinstance(imdb, list) and imdb else '',
        tmdb_season=season,
        tmdb_episode_offset=offset,
    )


def _chunks(rows: Iterable[AnimeMapping]) -> Iterator[list[AnimeMapping]]:
    """Yield the rows in chunks of ``CHUNK``."""
    chunk = []
    for row in rows:
        chunk.append(row)
        if len(chunk) == CHUNK:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def load_entries(entries: Iterable[dict]) -> int:
    """Upsert dataset entries by AniList id.

    Args:
        entries: Parsed dataset entries; those without an AniList id are skipped.

    Returns:
        The number of rows written.
    """
    rows = (row for row in map(parse_entry, entries) if row is not None)
    written = 0
    with transaction.atomic():
        for chunk in _chunks(rows):
            AnimeMapping.objects.bulk_create(
                chunk,
                update_conflicts=True,
                unique_fields=['anilist_id'],
                update_fields=UPDATE_FIELDS,
            )
            written += len(chunk)
    logger.info('Fribb mapping refreshed: %s rows', written)
    return written
