"""Search across TMDB and AniList."""

import logging
from dataclasses import dataclass, field
from itertools import zip_longest

from providers.anilist import AniListClient
from providers.http import ProviderError
from providers.tmdb import TmdbClient
from providers.types import SearchHit

logger = logging.getLogger(__name__)


@dataclass
class SearchResults:
    """Merged search results.

    Attributes:
        hits: Movies, TV shows and anime, the two providers interleaved so that
            neither buries the other.
        failed: Names of the providers that could not answer; the page can say
            that the list is incomplete.
    """

    hits: list[SearchHit] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def search(
    query: str,
    language: str,
    *,
    tmdb: TmdbClient | None,
    anilist: AniListClient,
) -> SearchResults:
    """Search both providers.

    Japanese animation found by TMDB is dropped, because AniList covers it and
    anime titles come from AniList (they are franchises there, not TMDB shows).

    Args:
        query: Text typed by the user.
        language: Site language for TMDB titles.
        tmdb: TMDB client, or None when no token is configured.
        anilist: AniList client.

    Returns:
        The merged hits and the providers that failed.
    """
    results = SearchResults()
    tmdb_hits: list[SearchHit] = []
    anilist_hits: list[SearchHit] = []
    if tmdb is None:
        results.failed.append('tmdb')
    else:
        try:
            tmdb_hits = [h for h in tmdb.search(query, language) if not h.anime_like]
        except ProviderError as error:
            logger.warning('TMDB search failed: %s', error)
            results.failed.append('tmdb')
    try:
        anilist_hits = anilist.search(query)
    except ProviderError as error:
        logger.warning('AniList search failed: %s', error)
        results.failed.append('anilist')
    results.hits = [
        hit
        for pair in zip_longest(tmdb_hits, anilist_hits)
        for hit in pair
        if hit is not None
    ]
    return results
