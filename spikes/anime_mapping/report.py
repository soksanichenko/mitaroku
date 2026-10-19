"""Render results.json as a markdown table for the spike report."""

import json
from pathlib import Path

HERE = Path(__file__).parent


def render(results: list[dict]) -> str:
    """Build the coverage table.

    Args:
        results: Parsed ``results.json``.

    Returns:
        A markdown table, one row per fixture.
    """
    header = (
        '| Title | Main entries | Extras | In Fribb | With Simkl id | With TMDB id '
        '| With IMDb id | AniList upcoming episodes | Simkl relations | '
        'Simkl episodes (dated) | TMDB seasons |\n'
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n'
    )
    rows = []
    for item in results:
        main = item.get('main')
        if not main:
            rows.append(f"| {item['label']} | not found on AniList |" + ' |' * 9)
            continue
        total = len(main)
        simkl = item.get('simkl')
        simkl_relations = simkl['relations'] if simkl else 'n/a'
        simkl_episodes = (
            f"{simkl['episodes']} ({simkl['episodes_with_date']})" if simkl else 'n/a'
        )
        seasons = item.get('tmdb_seasons')
        tmdb_seasons = len(seasons) if seasons else 'n/a'
        rows.append(
            f"| {item['label']} | {total} | {item['extras']} "
            f"| {sum(e['fribb'] for e in main)}/{total} "
            f"| {sum(1 for e in main if e['simkl_id'])}/{total} "
            f"| {sum(1 for e in main if e['tmdb'])}/{total} "
            f"| {sum(e['imdb'] for e in main)}/{total} "
            f"| {item['airing_schedule']} | {simkl_relations} "
            f"| {simkl_episodes} | {tmdb_seasons} |"
        )
    return header + '\n'.join(rows) + '\n'


def main() -> None:
    """Print the table for ``results.json`` to stdout."""
    results = json.loads((HERE / 'results.json').read_text(encoding='utf-8'))
    print(render(results))  # noqa: T201 - report output is the script's product


if __name__ == '__main__':
    main()
