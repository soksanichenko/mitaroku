"""The shared genre, country and year vocabulary behind Browse and the title page.

TMDB and AniList name genres differently; both are folded into one set of slugs,
and each slug has a lazily translated label.
"""

from collections.abc import Iterable
from datetime import date

from babel import Locale
from django.utils.functional import Promise as StrPromise
from django.utils.translation import gettext_lazy as _

GENRES: dict[str, StrPromise] = {
    'action': _('Action'),
    'adventure': _('Adventure'),
    'animation': _('Animation'),
    'comedy': _('Comedy'),
    'crime': _('Crime'),
    'documentary': _('Documentary'),
    'drama': _('Drama'),
    'family': _('Family'),
    'fantasy': _('Fantasy'),
    'history': _('History'),
    'horror': _('Horror'),
    'kids': _('Kids'),
    'magical-girl': _('Magical girl'),
    'mecha': _('Mecha'),
    'music': _('Music'),
    'mystery': _('Mystery'),
    'news': _('News'),
    'psychological': _('Psychological'),
    'reality': _('Reality'),
    'romance': _('Romance'),
    'sci-fi': _('Science fiction'),
    'slice-of-life': _('Slice of life'),
    'soap': _('Soap'),
    'sports': _('Sports'),
    'supernatural': _('Supernatural'),
    'talk': _('Talk show'),
    'thriller': _('Thriller'),
    'war': _('War'),
    'western': _('Western'),
}

# Movie and TV genre ids share one table; a combined TV genre carries several slugs.
# 10770 (TV Movie) has no counterpart and is left out on purpose.
TMDB_GENRES: dict[int, tuple[str, ...]] = {
    28: ('action',),
    12: ('adventure',),
    16: ('animation',),
    35: ('comedy',),
    80: ('crime',),
    99: ('documentary',),
    18: ('drama',),
    10751: ('family',),
    14: ('fantasy',),
    36: ('history',),
    27: ('horror',),
    10402: ('music',),
    9648: ('mystery',),
    10749: ('romance',),
    878: ('sci-fi',),
    53: ('thriller',),
    10752: ('war',),
    37: ('western',),
    10759: ('action', 'adventure'),
    10762: ('kids',),
    10763: ('news',),
    10764: ('reality',),
    10765: ('sci-fi', 'fantasy'),
    10766: ('soap',),
    10767: ('talk',),
    10768: ('war',),
}

# AniList genres without a counterpart (Ecchi, Hentai, ...) are left out on purpose.
ANILIST_GENRES: dict[str, str] = {
    'Action': 'action',
    'Adventure': 'adventure',
    'Comedy': 'comedy',
    'Drama': 'drama',
    'Fantasy': 'fantasy',
    'Horror': 'horror',
    'Music': 'music',
    'Mystery': 'mystery',
    'Psychological': 'psychological',
    'Romance': 'romance',
    'Sports': 'sports',
    'Supernatural': 'supernatural',
    'Thriller': 'thriller',
    'Sci-Fi': 'sci-fi',
    'Slice of Life': 'slice-of-life',
    'Mahou Shoujo': 'magical-girl',
    'Mecha': 'mecha',
}

BROWSE_COUNTRIES: tuple[str, ...] = tuple(
    (
        'US GB JP KR CN FR DE IT ES RU CA AU IN BR MX '
        'SE DK NO FI IS PL TR TH HK TW UA NL BE IE NZ'
    ).split()
)

# The origin countries AniList can filter by.
ANILIST_COUNTRIES: frozenset[str] = frozenset({'JP', 'KR', 'CN', 'TW'})

_FIRST_BROWSE_YEAR = 1950

# CLDR's placeholder for "no such region"; it would read "Unknown Region".
_UNKNOWN_REGION = 'ZZ'


def _unique(slugs: Iterable[str]) -> list[str]:
    """Drop repeated slugs, keeping the first occurrence of each."""
    return list(dict.fromkeys(slugs))


def genres_from_tmdb(ids: Iterable[int]) -> list[str]:
    """Map TMDB genre ids to slugs in input order, dropping unknown ids.

    Args:
        ids: TMDB movie or TV genre ids.

    Returns:
        The slugs without duplicates.
    """
    return _unique(slug for genre_id in ids for slug in TMDB_GENRES.get(genre_id, ()))


def genres_from_anilist(names: Iterable[str]) -> list[str]:
    """Map AniList genre names to slugs in input order, dropping unknown names.

    Args:
        names: AniList genre names.

    Returns:
        The slugs without duplicates.
    """
    return _unique(ANILIST_GENRES[name] for name in names if name in ANILIST_GENRES)


def tmdb_genre_filter(slug: str) -> str:
    """Return the TMDB ``with_genres`` value matching any id of a slug.

    Args:
        slug: A genre slug.

    Returns:
        The ids joined by ``|`` (TMDB's "or"), or an empty string for an unknown slug.
    """
    return '|'.join(str(i) for i, slugs in TMDB_GENRES.items() if slug in slugs)


def anilist_genre_name(slug: str) -> str | None:
    """Return the AniList genre name of a slug, or ``None`` when AniList has none."""
    for name, mapped in ANILIST_GENRES.items():
        if mapped == slug:
            return name
    return None


def genre_label(slug: str) -> str:
    """Return the label of a genre in the active language, or the slug if unknown."""
    label = GENRES.get(slug)
    return str(label) if label is not None else slug


def country_name(code: str, language: str) -> str:
    """Return the name of a country in a language.

    Args:
        code: An ISO 3166-1 alpha-2 country code.
        language: A language code such as ``en`` or ``ru``.

    Returns:
        The territory name, or the code itself when it is unknown.
    """
    if code == _UNKNOWN_REGION:
        return code
    return Locale(language).territories.get(code, code)


def browse_years(today: date) -> list[int]:
    """Return the years offered by Browse, from the current one down to 1950."""
    return list(range(today.year, _FIRST_BROWSE_YEAR - 1, -1))
