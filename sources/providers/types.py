"""Types shared by the provider clients."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    """One search result. Search results are shown but never stored.

    Attributes:
        provider: ``ExternalId`` provider value: ``tmdb_movie``, ``tmdb_tv`` or
            ``anilist``.
        external_id: The id at that provider, as a string.
        kind: ``movie``, ``tv`` or ``anime``: the ``Title`` kind it would create.
        title: Title in the requested language.
        original_title: Title in the original language.
        year: Release or first air year, when known.
        poster_url: Poster image URL, empty when there is none.
        overview: Short description, possibly empty.
        anime_like: TMDB hit that is animation from Japan; the AniList search
            covers these, so the merged search drops them.
        rating: Average vote from 0 to 10, None when nobody voted.
    """

    provider: str
    external_id: str
    kind: str
    title: str
    original_title: str
    year: int | None
    poster_url: str
    overview: str
    anime_like: bool = False
    rating: float | None = None


@dataclass(frozen=True)
class HitPage:
    """One page of browse results.

    Attributes:
        hits: The hits of the page.
        total: How many results the whole listing has.
        has_more: Whether a later page exists.
    """

    hits: list[SearchHit]
    total: int
    has_more: bool
