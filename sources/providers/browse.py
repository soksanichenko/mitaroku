"""Browse movies, TV shows and anime through TMDB and AniList."""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from itertools import zip_longest

from catalog.vocab import (
    ANILIST_COUNTRIES,
    BROWSE_COUNTRIES,
    GENRES,
    anilist_genre_name,
    tmdb_genre_filter,
)
from providers.anilist import AniListClient
from providers.http import ProviderError
from providers.tmdb import TmdbClient
from providers.types import HitPage, SearchHit

logger = logging.getLogger(__name__)

KINDS = ('movie', 'tv', 'anime')
SORTS = ('popularity', 'date', 'title', 'rating')
MAX_PAGE = 200

_FIRST_YEAR = 1950


def _first(params: Mapping[str, str], key: str) -> str:
    """Return the first value of a key, or an empty string.

    A Django ``QueryDict`` answers ``get`` with the last value of a repeated key,
    so it is asked for the whole list instead.
    """
    getlist = getattr(params, 'getlist', None)
    if getlist is not None:
        values = getlist(key)
        return values[0] if values else ''
    return params.get(key, '')


def _parse_page(value: str) -> int:
    """Return a positive page number clamped to ``MAX_PAGE``; 1 when invalid."""
    if not re.fullmatch('[0-9]+', value):
        return 1
    digits = value.lstrip('0')
    # A long number is far over the limit; it is not worth converting.
    if len(digits) > 6:
        return MAX_PAGE
    return min(int(digits or 0), MAX_PAGE) or 1


@dataclass(frozen=True)
class BrowseQuery:
    """What the Browse screen asks for.

    Attributes:
        kind: ``movie``, ``tv``, ``anime``, or None for all of them; the ``type``
            key of the URL.
        genre: A genre slug from ``catalog.vocab.GENRES``.
        country: An origin country from ``catalog.vocab.BROWSE_COUNTRIES``.
        year: Release (or first air) year.
        sort: One of ``SORTS``.
        page: 1-based page number, at most ``MAX_PAGE``.
    """

    kind: str | None = None
    genre: str | None = None
    country: str | None = None
    year: int | None = None
    sort: str = SORTS[0]
    page: int = 1

    @classmethod
    def parse(cls, params: Mapping[str, str]) -> 'BrowseQuery':
        """Build a query from untrusted request parameters.

        Every parameter that is missing or invalid falls back to its default; this
        never raises.

        Args:
            params: Query-string parameters; the first value of a key is used.

        Returns:
            A query holding only valid values.
        """
        kind = _first(params, 'type')
        genre = _first(params, 'genre')
        country = _first(params, 'country')
        year = _first(params, 'year')
        sort = _first(params, 'sort')
        return cls(
            kind=kind if kind in KINDS else None,
            genre=genre if genre in GENRES else None,
            country=(
                country
                if re.fullmatch('[A-Z]{2}', country) and country in BROWSE_COUNTRIES
                else None
            ),
            year=(
                int(year)
                if re.fullmatch('[0-9]{4}', year)
                and _FIRST_YEAR <= int(year) <= date.today().year + 1
                else None
            ),
            sort=sort if sort in SORTS else SORTS[0],
            page=_parse_page(_first(params, 'page')),
        )


@dataclass
class BrowseResults:
    """One page of merged Browse results.

    Attributes:
        hits: Movies, TV shows and anime, the sources interleaved so that none
            buries the others.
        has_more: Whether any source has a later page.
        failed: Names of the providers that could not answer; the page can say
            that the list is incomplete.
    """

    hits: list[SearchHit]
    has_more: bool
    failed: list[str]


def tmdb_covers_genre(query: BrowseQuery) -> bool:
    """Return whether TMDB can filter by the genre of a query, if it has one.

    A genre such as Sports has no TMDB id; asking TMDB anyway would list its most
    popular titles under that genre's name.
    """
    return not query.genre or bool(tmdb_genre_filter(query.genre))


def browse(
    query: BrowseQuery,
    language: str,
    *,
    tmdb: TmdbClient | None,
    anilist: AniListClient,
) -> BrowseResults:
    """Run a Browse query against TMDB and AniList.

    Japanese animation found by TMDB is dropped, because anime titles come from
    AniList. A source is skipped, without counting as a failure, when it cannot
    filter by the requested country or genre: it would list titles that do not
    match.

    Args:
        query: What to list; ``sort`` must be one of ``SORTS``.
        language: Site language for TMDB titles.
        tmdb: TMDB client, or None when no token is configured.
        anilist: AniList client.

    Returns:
        The merged hits, whether any source has a later page and the providers
        that failed.
    """
    kinds = KINDS if query.kind is None else (query.kind,)
    anilist_genre = anilist_genre_name(query.genre) if query.genre else None
    anime_covered = (not query.country or query.country in ANILIST_COUNTRIES) and (
        not query.genre or anilist_genre is not None
    )
    failed: list[str] = []
    pages: list[HitPage] = []
    for kind in kinds:
        provider = 'anilist' if kind == 'anime' else 'tmdb'
        if kind == 'anime' and not anime_covered:
            continue
        if provider == 'tmdb' and not tmdb_covers_genre(query):
            continue
        if provider == 'tmdb' and tmdb is None:
            if provider not in failed:
                failed.append(provider)
            continue
        try:
            if kind == 'anime':
                page = anilist.discover(
                    genre=anilist_genre,
                    country=query.country,
                    year=query.year,
                    sort=query.sort,
                    page=query.page,
                )
            else:
                page = tmdb.discover(
                    kind,
                    genre=query.genre,
                    country=query.country,
                    year=query.year,
                    sort=query.sort,
                    page=query.page,
                    language=language,
                )
                page = HitPage(
                    [h for h in page.hits if not h.anime_like],
                    page.total,
                    page.has_more,
                )
        except ProviderError as error:
            logger.warning('%s browse failed: %s', provider, error)
            if provider not in failed:
                failed.append(provider)
            continue
        pages.append(page)
    return BrowseResults(
        hits=[
            hit
            for group in zip_longest(*(page.hits for page in pages))
            for hit in group
            if hit is not None
        ],
        has_more=any(page.has_more for page in pages),
        failed=failed,
    )
