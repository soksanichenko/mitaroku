"""Probe AniList for what the degraded episode mode would rely on.

For the entries the TMDB guard could not resolve, ask for ``averageScore``,
``streamingEpisodes`` and the full ``airingSchedule`` (past episodes included).
"""

import json
import time
from pathlib import Path

import httpx
from log import get_logger

logger = get_logger('probe_anilist_extras')
HERE = Path(__file__).parent
URL = 'https://graphql.anilist.co'
QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id title { romaji } episodes averageScore
    streamingEpisodes { title }
    airingSchedule(perPage: 50) { nodes { episode airingAt } }
  }
}
"""
TITLES = (
    'Nekomonogatari (Kuro)', 'Monogatari Series: Second Season', 'Hanamonogatari',
    'Tsukimonogatari', 'Owarimonogatari (Ge)', 'Monogatari Series: Off & Monster Season',
    'Fate/stay night: Unlimited Blade Works', 'Fate/stay night: Unlimited Blade Works 2nd Season',
)


def main() -> None:
    """Print one line per unresolved entry with the AniList fields found."""
    results = json.loads((HERE / 'results.json').read_text(encoding='utf-8'))
    entries = [e for x in results for e in x.get('main') or [] if e['title'] in TITLES]
    http = httpx.Client(timeout=30)
    print('| Title | episodes | averageScore | streamingEpisodes | airingSchedule nodes |')
    print('| --- | --- | --- | --- | --- |')
    for entry in entries:
        response = http.post(URL, json={'query': QUERY, 'variables': {'id': entry['anilist_id']}})
        response.raise_for_status()
        media = response.json()['data']['Media']
        schedule = media['airingSchedule']['nodes']
        print(
            f'| {entry["title"][:40]} | {media["episodes"]} | {media["averageScore"]} '
            f'| {len(media["streamingEpisodes"])} | {len(schedule)} |'
        )
        time.sleep(1)


if __name__ == '__main__':
    main()
