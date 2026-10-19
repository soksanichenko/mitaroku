"""Minimal AniList GraphQL client (no API key needed)."""

import time

import httpx

from cache import DiskCache
from franchise import Node, node_from_media
from log import get_logger

logger = get_logger(__name__)

URL = 'https://graphql.anilist.co'
QUERY = """
query ($id: Int, $idMal: Int) {
  Media(id: $id, idMal: $idMal, type: ANIME) {
    id idMal format status episodes
    title { romaji }
    startDate { year month day }
    nextAiringEpisode { episode airingAt }
    airingSchedule(notYetAired: true, perPage: 50) { nodes { episode airingAt } }
    relations { edges { relationType node { id idMal format type } } }
  }
}
"""


class AniListClient:
    """Fetch anime by AniList or MAL id, politely and with caching."""

    def __init__(self, cache: DiskCache, min_interval: float = 0.8) -> None:
        """Create the client.

        Args:
            cache: Disk cache for raw ``Media`` objects.
            min_interval: Minimum seconds between network requests.
        """
        self._cache = cache
        self._min_interval = min_interval
        self._last_request = 0.0
        self._http = httpx.Client(timeout=30)

    def media(self, *, anilist_id: int | None = None, mal_id: int | None = None) -> dict | None:
        """Return the raw ``Media`` object, or None when AniList has no match.

        Args:
            anilist_id: AniList id to look up.
            mal_id: MyAnimeList id to look up (used when ``anilist_id`` is None).

        Returns:
            The ``Media`` dict, or None if the entry does not exist.
        """
        key = f'anilist-{anilist_id}' if anilist_id else f'anilist-mal-{mal_id}'
        cached = self._cache.get(key)
        if cached is not None:
            return cached or None
        variables = {'id': anilist_id} if anilist_id else {'idMal': mal_id}
        media = self._request(variables)
        self._cache.put(key, media or {})
        return media

    def node(self, anilist_id: int) -> Node | None:
        """Return a franchise ``Node`` for an AniList id."""
        media = self.media(anilist_id=anilist_id)
        return node_from_media(media) if media else None

    def _request(self, variables: dict) -> dict | None:
        """POST the query, respecting the rate limit and ``Retry-After``."""
        for attempt in range(5):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            response = self._http.post(URL, json={'query': QUERY, 'variables': variables})
            if response.status_code == 429:
                delay = int(response.headers.get('Retry-After', '60'))
                logger.info('AniList rate limit hit, sleeping %s s', delay)
                time.sleep(delay)
                continue
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()['data']['Media']
        raise RuntimeError('AniList rate limit not cleared after 5 attempts')
