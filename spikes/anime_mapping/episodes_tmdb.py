"""Variant 1: build anime episode lists from TMDB alone and score them.

Two allocations are compared against the AniList start date of each entry:
``concat`` glues every non-special TMDB season into one stream and slices it by
the AniList episode counts; ``fribb`` uses the Fribb season and offset.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
import simkl
from cache import DiskCache

HERE = Path(__file__).parent
BASE = 'https://api.themoviedb.org/3'


def allocate_concat(flat: list[dict], counts: list[int | None]) -> list[list[dict]]:
    """Slice one episode stream by consecutive entry counts.

    Args:
        flat: Episodes in watching order.
        counts: Episode count per entry in release order; None takes the rest.

    Returns:
        One list of episodes per entry (shorter when the stream runs out).
    """
    parts = []
    pos = 0
    for count in counts:
        end = len(flat) if count is None else pos + count
        parts.append(flat[pos:end])
        pos = min(end, len(flat))
    return parts


def allocate_fribb(
    seasons: dict[int, list[dict]], season: int | None, offset: int, count: int | None
) -> list[dict]:
    """Take ``count`` episodes of a TMDB season starting after ``offset``."""
    if season is None or season not in seasons:
        return []
    episodes = seasons[season][offset:]
    return episodes if count is None else episodes[:count]


def date_matches(air_date: str | None, start: tuple, days: int = 14) -> bool:
    """Return True when ``air_date`` is within ``days`` of a (y, m, d) start."""
    if not air_date or None in start:
        return False
    return abs(date.fromisoformat(air_date) - date(*start)) <= timedelta(days=days)


class Tmdb:
    """Cached TMDB season fetcher."""

    def __init__(self, token: str, cache: DiskCache) -> None:
        self._http = httpx.Client(timeout=30, headers={'Authorization': f'Bearer {token}'})
        self._cache = cache

    def seasons(self, tv_id: int) -> dict[int, list[dict]]:
        """Return ``{season_number: episodes}`` for seasons 1 and up."""
        key = f'tmdb_tv_{tv_id}'
        cached = self._cache.get(key)
        if cached is None:
            show = self._http.get(f'{BASE}/tv/{tv_id}').json()
            cached = {}
            for s in show.get('seasons', []):
                if s['season_number'] < 1:
                    continue
                resp = self._http.get(f'{BASE}/tv/{tv_id}/season/{s["season_number"]}')
                cached[str(s['season_number'])] = resp.json().get('episodes', [])
            self._cache.put(key, cached)
        return {int(k): v for k, v in cached.items()}


def main() -> None:
    """Score both allocations (and Simkl) for every TV/ONA entry in results.json."""
    results = json.loads((HERE / 'results.json').read_text(encoding='utf-8'))
    cache = DiskCache(HERE / '.cache' / 'variant1')
    tmdb = Tmdb(os.environ['TMDB_TOKEN'], cache)
    sk = simkl.SimklClient(os.environ['SIMKL_CLIENT_ID'])
    print('| Title | entries | concat ok | fribb ok | simkl ok | notes |')
    print('| --- | --- | --- | --- | --- | --- |')
    totals = {'n': 0, 'concat': 0, 'fribb': 0, 'simkl': 0}
    for fixture in results:
        groups: dict[int, list[dict]] = {}
        for entry in fixture['main']:
            if entry['format'] in ('TV', 'ONA') and entry['tmdb'] and entry['tmdb'][0] == 'tv':
                groups.setdefault(entry['tmdb'][1], []).append(entry)
        n = c_ok = f_ok = s_ok = 0
        notes = []
        for tv_id, entries in groups.items():
            seasons = tmdb.seasons(tv_id)
            flat = [e for s in sorted(seasons) for e in seasons[s]]
            entries.sort(key=lambda e: tuple(x or 0 for x in e['start']))
            concat = allocate_concat(flat, [e['episodes'] for e in entries])
            for entry, part in zip(entries, concat):
                n += 1
                first = part[0].get('air_date') if part else None
                c_ok += date_matches(first, tuple(entry['start']))
                off = ((entry['episode_offset'] or {}).get('tmdb')) or 0
                fpart = allocate_fribb(
                    seasons, (entry['season'] or {}).get('tmdb'), off, entry['episodes']
                )
                ffirst = fpart[0].get('air_date') if fpart else None
                f_ok += date_matches(ffirst, tuple(entry['start']))
                eps = sk.episodes(entry['simkl_id']) if entry['simkl_id'] else []
                real = [e for e in eps if e.get('type') == 'episode' and e.get('date')]
                sfirst = real[0]['date'][:10] if real else None
                s_ok += date_matches(sfirst, tuple(entry['start']))
                if not date_matches(first, tuple(entry['start'])):
                    notes.append(f'{entry["title"][:28]} (concat first={first})')
        totals['n'] += n
        totals['concat'] += c_ok
        totals['fribb'] += f_ok
        totals['simkl'] += s_ok
        print(f'| {fixture["label"]} | {n} | {c_ok} | {f_ok} | {s_ok} | {"; ".join(notes)} |')
    print(f'| TOTAL | {totals["n"]} | {totals["concat"]} | {totals["fribb"]} | {totals["simkl"]} | |')


if __name__ == '__main__':
    main()
