"""Load the Fribb/anime-lists id mapping dataset."""

import json
from pathlib import Path

import httpx

from log import get_logger

logger = get_logger(__name__)

URL = 'https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json'


def load(path: Path) -> list[dict]:
    """Download the dataset once and return its entries.

    Args:
        path: Local file to cache the dataset in.

    Returns:
        The parsed list of mapping entries.
    """
    if not path.exists():
        logger.info('Downloading %s', URL)
        response = httpx.get(URL, timeout=120, follow_redirects=True)
        response.raise_for_status()
        path.write_bytes(response.content)
    return json.loads(path.read_text(encoding='utf-8'))


def index_by_mal(entries: list[dict]) -> dict[int, dict]:
    """Index entries by ``mal_id``; the first entry wins on duplicates."""
    result: dict[int, dict] = {}
    for entry in entries:
        mal_id = entry.get('mal_id')
        if mal_id is not None and mal_id not in result:
            result[mal_id] = entry
    return result


def tmdb_id(entry: dict) -> tuple[str, int] | None:
    """Return ``('tv'|'movie', id)`` for the first TMDB id, or None."""
    tmdb = entry.get('themoviedb_id')
    if not tmdb:
        return None
    for kind in ('tv', 'movie'):
        value = tmdb.get(kind)
        if value:
            return kind, value[0] if isinstance(value, list) else value
    return None
