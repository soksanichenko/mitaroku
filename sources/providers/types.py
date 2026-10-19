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
