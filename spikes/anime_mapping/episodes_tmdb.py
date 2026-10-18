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


def accepts(
    part: list[dict], expected: int | None, start: tuple, days: int = 2
) -> bool:
    """Return True when an allocation passes the guard.

    The first episode must air within ``days`` of the AniList start date, and the
    allocation must have exactly ``expected`` episodes when that is known. The
    length check catches off-by-one shifts that a date window alone lets through.
    """
    if not part:
        return False
    if expected is not None and len(part) != expected:
        return False
    return date_matches(part[0].get('air_date'), start, days)


def first_accepted(
    candidates: list[list[dict]], expected: int | None, start: tuple, days: int = 2
) -> int | None:
    """Return the index of the first candidate that passes ``accepts``."""
    for index, part in enumerate(candidates):
        if accepts(part, expected, start, days):
            return index
    return None


class Tmdb:
    """Cached TMDB season fetcher."""

    def __init__(self, token: str, cache: DiskCache) -> None:
        self._http = httpx.Client(timeout=30, headers={'Authorization': f'Bearer {token}'})
        self._cache = cache

    def seasons(self, tv_id: int) -> dict[int, list[dict]]:
        """Return ``{season_number: episodes}``, season 0 (specials) included."""
        key = f'tmdb_tv_all_{tv_id}'
        cached = self._cache.get(key)
        if cached is None:
            response = self._http.get(f'{BASE}/tv/{tv_id}')
            response.raise_for_status()
            show = response.json()
            cached = {}
            for s in show.get('seasons', []):
                resp = self._http.get(f'{BASE}/tv/{tv_id}/season/{s["season_number"]}')
                resp.raise_for_status()
                cached[str(s['season_number'])] = resp.json().get('episodes', [])
            self._cache.put(key, cached)
        return {int(k): v for k, v in cached.items()}


def main() -> None:
    """Score the allocations for every TV/ONA entry in ``results.json``.

    Columns: ``concat`` and ``fribb`` count the loose check (first air date within
    14 days, the original measure); ``hybrid`` is the guard (Fribb, then concat,
    each accepted only by ``accepts``: first episode within 2 days and the
    allocation length equal to the AniList count); ``simkl`` applies the same
    guard to the Simkl episode list; ``fit`` counts Fribb season/offset pairs that
    fit inside the TMDB season length.
    """
    results = json.loads((HERE / 'results.json').read_text(encoding='utf-8'))
    cache = DiskCache(HERE / '.cache' / 'variant1')
    tmdb = Tmdb(os.environ['TMDB_TOKEN'], cache)
    sk = simkl.SimklClient(os.environ['SIMKL_CLIENT_ID'])
    keys = ('n', 'concat14', 'fribb14', 'hybrid', 'simkl', 'fit', 'fit_known')
    totals = dict.fromkeys(keys, 0)
    print('| Title | entries | concat (14d) | fribb (14d) | hybrid (guard) | simkl (guard) | fribb fits season | unresolved by hybrid |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- |')
    for fixture in results:
        groups: dict[int, list[dict]] = {}
        for entry in fixture.get('main') or []:
            if entry['format'] in ('TV', 'ONA') and entry['tmdb'] and entry['tmdb'][0] == 'tv':
                groups.setdefault(entry['tmdb'][1], []).append(entry)
        row = dict.fromkeys(keys, 0)
        unresolved = []
        for tv_id, entries in groups.items():
            seasons = tmdb.seasons(tv_id)
            flat = [e for s in sorted(seasons) if s >= 1 for e in seasons[s]]
            entries.sort(key=lambda e: tuple(x or 0 for x in e['start']))
            concat = allocate_concat(flat, [e['episodes'] for e in entries])
            for entry, cpart in zip(entries, concat):
                start = tuple(entry['start'])
                expected = entry['episodes']
                season = (entry['season'] or {}).get('tmdb')
                offset = ((entry['episode_offset'] or {}).get('tmdb')) or 0
                fpart = allocate_fribb(seasons, season, offset, expected)
                row['n'] += 1
                row['concat14'] += bool(cpart) and date_matches(cpart[0].get('air_date'), start)
                row['fribb14'] += bool(fpart) and date_matches(fpart[0].get('air_date'), start)
                if expected and season in seasons:
                    row['fit_known'] += 1
                    row['fit'] += offset + expected <= len(seasons[season])
                if first_accepted([fpart, cpart], expected, start) is not None:
                    row['hybrid'] += 1
                else:
                    unresolved.append(entry['title'][:26])
                eps = cache.get(f'simkl_{entry["simkl_id"]}') if entry['simkl_id'] else []
                if eps is None:
                    eps = sk.episodes(entry['simkl_id'])
                    cache.put(f'simkl_{entry["simkl_id"]}', eps)
                real = [e for e in eps if e.get('type') == 'episode']
                spart = [{'air_date': (e.get('date') or '')[:10] or None} for e in real]
                row['simkl'] += accepts(spart, expected, start)
        for key in keys:
            totals[key] += row[key]
        print(
            f'| {fixture["label"]} | {row["n"]} | {row["concat14"]} | {row["fribb14"]} '
            f'| {row["hybrid"]} | {row["simkl"]} | {row["fit"]}/{row["fit_known"]} '
            f'| {"; ".join(unresolved)} |'
        )
    print(
        f'| TOTAL | {totals["n"]} | {totals["concat14"]} | {totals["fribb14"]} '
        f'| {totals["hybrid"]} | {totals["simkl"]} | {totals["fit"]}/{totals["fit_known"]} | |'
    )


if __name__ == '__main__':
    main()
