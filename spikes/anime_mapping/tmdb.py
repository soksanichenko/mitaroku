"""Minimal TMDB client for the TV season comparison."""

import httpx

BASE = 'https://api.themoviedb.org/3'


class TmdbClient:
    """Read TV show structure with a v4 read access token."""

    def __init__(self, token: str) -> None:
        """Create the client.

        Args:
            token: TMDB API read access token (Bearer).
        """
        self._http = httpx.Client(
            timeout=30, headers={'Authorization': f'Bearer {token}'}
        )

    def tv_seasons(self, tv_id: int) -> list[tuple[int, int]]:
        """Return ``(season_number, episode_count)`` for a TV show."""
        response = self._http.get(f'{BASE}/tv/{tv_id}')
        response.raise_for_status()
        return [
            (season['season_number'], season['episode_count'])
            for season in response.json().get('seasons', [])
        ]
