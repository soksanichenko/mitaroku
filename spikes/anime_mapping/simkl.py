"""Minimal Simkl catalog client (needs a free client_id)."""

import httpx

BASE = 'https://api.simkl.com'


class SimklClient:
    """Read-only calls to the cached Simkl catalog endpoints."""

    def __init__(self, client_id: str) -> None:
        """Create the client.

        Args:
            client_id: Simkl application client id.
        """
        self._params = {
            'client_id': client_id,
            'app-name': 'mitaroku-spike',
            'app-version': '0.1',
        }
        self._http = httpx.Client(
            timeout=30, headers={'User-Agent': 'mitaroku-spike/0.1'}
        )

    def anime(self, simkl_id: int) -> dict:
        """Return the full anime record including ``relations``."""
        response = self._http.get(
            f'{BASE}/anime/{simkl_id}', params={**self._params, 'extended': 'full'}
        )
        response.raise_for_status()
        return response.json()

    def episodes(self, simkl_id: int) -> list[dict]:
        """Return the episode list with air dates."""
        response = self._http.get(
            f'{BASE}/anime/episodes/{simkl_id}', params=self._params
        )
        response.raise_for_status()
        return response.json()
