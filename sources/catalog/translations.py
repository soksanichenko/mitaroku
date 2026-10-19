"""Read and lazily fill translations of titles and episodes.

Only the English text is stored when a title is synced. Another language is
fetched from TMDB the first time someone needs it and then kept. Empty values
are never stored. The lookup falls back from the user's language to the
original title and then to English.
"""

import logging

from catalog.models import Episode, EpisodeTranslation, Season, Title, TitleTranslation
from providers.tmdb import TmdbClient

logger = logging.getLogger(__name__)

FALLBACK_LANGUAGE = 'en'


def _first(*values: str) -> str:
    """Return the first non-empty value, or an empty string."""
    return next((value for value in values if value), '')


def localized_title(title: Title, language: str) -> tuple[str, str]:
    """Return ``(name, overview)`` of a title with the fallback chain applied.

    The name chain is the user's language, the original title, English. The
    overview has no original, so it is the user's language, then English.

    Args:
        title: The title.
        language: The user's metadata language.

    Returns:
        The name and the overview (possibly empty).
    """
    texts = {t.language: t for t in title.translations.all()}
    own = texts.get(language)
    english = texts.get(FALLBACK_LANGUAGE)
    name = _first(
        own.name if own else '',
        title.original_title,
        english.name if english else '',
    )
    overview = _first(own.overview if own else '', english.overview if english else '')
    return name, overview


def localized_episode(episode: Episode, language: str) -> tuple[str, str]:
    """Return ``(name, overview)`` of an episode: the user's language, then English.

    Both are empty for episodes without TMDB data (the degraded mode).
    """
    texts = {t.language: t for t in episode.translations.all()}
    own = texts.get(language)
    english = texts.get(FALLBACK_LANGUAGE)
    return (
        _first(own.name if own else '', english.name if english else ''),
        _first(own.overview if own else '', english.overview if english else ''),
    )


def ensure_title_translation(title: Title, language: str, tmdb: TmdbClient) -> bool:
    """Make sure a title has a translation in ``language``, fetching it if needed.

    Args:
        title: The title; it needs a TMDB id (anime get one when Fribb maps them).
        language: Site language to fetch.
        tmdb: The TMDB client.

    Returns:
        True when the title has a stored translation in that language afterwards.
    """
    if title.translations.filter(language=language).exists():
        return True
    provider = 'tmdb_movie' if title.kind == Title.Kind.MOVIE else 'tmdb_tv'
    link = title.external_ids.filter(provider=provider).order_by('pk').first()
    if link is None:
        return False
    if provider == 'tmdb_movie':
        item = tmdb.movie(int(link.external_id), language)
        name, overview = (item.title, item.overview) if item else ('', '')
    else:
        item = tmdb.tv(int(link.external_id), language)
        name, overview = (item.name, item.overview) if item else ('', '')
    if not (name or overview):
        return False
    TitleTranslation.objects.update_or_create(
        title=title, language=language, defaults={'name': name, 'overview': overview}
    )
    return True


def ensure_episode_translations(season: Season, language: str, tmdb: TmdbClient) -> int:
    """Fetch the episode texts of a season in ``language`` where they are missing.

    Only seasons whose episodes came from TMDB are filled; a degraded season has
    no titles in any language.

    Args:
        season: The season.
        language: Site language to fetch.
        tmdb: The TMDB client.

    Returns:
        The number of translations stored.
    """
    if season.episode_source != Season.EpisodeSource.TMDB or not season.tmdb_tv_id:
        return 0
    missing = list(
        season.episodes.filter(tmdb_season_number__isnull=False).exclude(
            translations__language=language
        )
    )
    stored = 0
    for number in sorted({e.tmdb_season_number for e in missing}):
        tmdb_season = tmdb.season(season.tmdb_tv_id, number, language)
        if tmdb_season is None:
            continue
        texts = {e.number: e for e in tmdb_season.episodes if e.name or e.overview}
        for episode in missing:
            text = texts.get(episode.tmdb_episode_number)
            if episode.tmdb_season_number == number and text is not None:
                EpisodeTranslation.objects.update_or_create(
                    episode=episode,
                    language=language,
                    defaults={'name': text.name, 'overview': text.overview},
                )
                stored += 1
    return stored
