"""Run the id-mapping and franchise spike and write results.json."""

import json
import os
from pathlib import Path

import fribb
import tmdb
import simkl
from anilist import AniListClient
from cache import DiskCache
from fixtures import FIXTURES
from franchise import build_franchise
from log import get_logger

logger = get_logger('run')
HERE = Path(__file__).parent
CACHE_DIR = HERE / '.cache'


def probe_simkl(client: simkl.SimklClient | None, simkl_id: int | None) -> dict | None:
    """Collect Simkl relation and episode-date numbers for one entry."""
    if client is None or simkl_id is None:
        return None
    detail = client.anime(simkl_id)
    episodes = client.episodes(simkl_id)
    dated = [e for e in episodes if e.get('date')]
    return {
        'relations': len(detail.get('relations') or []),
        'episodes': len(episodes),
        'episodes_with_date': len(dated),
    }


def probe_tmdb(client: tmdb.TmdbClient | None, mapped: tuple[str, int] | None) -> list | None:
    """Return TMDB season structure when the entry maps to a TV show."""
    if client is None or mapped is None or mapped[0] != 'tv':
        return None
    return client.tv_seasons(mapped[1])


def main() -> None:
    """Run every fixture and write ``results.json`` next to this file."""
    entries = fribb.load(CACHE_DIR / 'anime-list-full.json')
    by_mal = fribb.index_by_mal(entries)
    anilist = AniListClient(DiskCache(CACHE_DIR / 'anilist'))
    simkl_id = os.environ.get('SIMKL_CLIENT_ID')
    simkl_client = simkl.SimklClient(simkl_id) if simkl_id else None
    tmdb_token = os.environ.get('TMDB_TOKEN')
    tmdb_client = tmdb.TmdbClient(tmdb_token) if tmdb_token else None
    if simkl_client is None:
        logger.info('SIMKL_CLIENT_ID not set: Simkl probe skipped')
    if tmdb_client is None:
        logger.info('TMDB_TOKEN not set: TMDB probe skipped')

    results = []
    for fixture in FIXTURES:
        logger.info('== %s (MAL %s)', fixture.label, fixture.mal_id)
        root = anilist.media(mal_id=fixture.mal_id)
        if root is None:
            results.append({'label': fixture.label, 'mal_id': fixture.mal_id, 'anilist': None})
            continue
        franchise = build_franchise(anilist.node, root['id'])
        main_rows = []
        for node in franchise.main:
            mapping = by_mal.get(node.mal_id) if node.mal_id else None
            mapped_tmdb = fribb.tmdb_id(mapping) if mapping else None
            main_rows.append(
                {
                    'anilist_id': node.anilist_id,
                    'mal_id': node.mal_id,
                    'format': node.format,
                    'title': node.title,
                    'episodes': node.episodes,
                    'start': node.start,
                    'fribb': bool(mapping),
                    'simkl_id': mapping.get('simkl_id') if mapping else None,
                    'tmdb': mapped_tmdb,
                    'imdb': bool(mapping and mapping.get('imdb_id')),
                    'season': mapping.get('season') if mapping else None,
                    'episode_offset': mapping.get('episode_offset') if mapping else None,
                }
            )
        root_row = next((r for r in main_rows if r['anilist_id'] == root['id']), None)
        results.append(
            {
                'label': fixture.label,
                'mal_id': fixture.mal_id,
                'main': main_rows,
                'extras': len(franchise.extras),
                'airing_schedule': len((root.get('airingSchedule') or {}).get('nodes', [])),
                'simkl': probe_simkl(simkl_client, root_row['simkl_id'] if root_row else None),
                'tmdb_seasons': probe_tmdb(tmdb_client, root_row['tmdb'] if root_row else None),
            }
        )
    out = HERE / 'results.json'
    out.write_text(json.dumps(results, indent=1), encoding='utf-8')
    logger.info('Wrote %s', out)


if __name__ == '__main__':
    main()
