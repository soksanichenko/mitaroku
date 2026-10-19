"""Tiny on-disk JSON cache so spike runs are repeatable and cheap."""

import json
from pathlib import Path


class DiskCache:
    """Store JSON-serialisable values as files under one directory."""

    def __init__(self, directory: Path) -> None:
        """Create the cache directory if needed.

        Args:
            directory: Where cache files live.
        """
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> object | None:
        """Return the cached value for ``key`` or None."""
        path = self._directory / f'{key}.json'
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding='utf-8'))

    def put(self, key: str, value: object) -> None:
        """Store ``value`` under ``key``."""
        path = self._directory / f'{key}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
