"""Compare ``build_franchise`` with the fork's rules on every fixture."""

from pathlib import Path

from anilist import AniListClient
from cache import DiskCache
from fixtures import FIXTURES
from fork_rules import fork_group
from franchise import build_franchise

CACHE_DIR = Path(__file__).parent / '.cache'


def main() -> None:
    """Print, per fixture, the entries on which the two groupings disagree."""
    client = AniListClient(DiskCache(CACHE_DIR / 'anilist'))
    print('| Title | ours main | fork main | only ours | only fork | extras ours/fork | skipped edges |')
    print('| --- | --- | --- | --- | --- | --- | --- |')
    for fixture in FIXTURES:
        root = client.media(mal_id=fixture.mal_id)
        if root is None:
            print(f'| {fixture.label} | not found | | | | | |')
            continue
        ours = build_franchise(client.node, root['id'])
        theirs = fork_group(client.node, root['id'])
        ours_main = {n.anilist_id for n in ours.main}
        ours_extra = {e[0] for e in ours.extras}
        print(
            f'| {fixture.label} | {len(ours_main)} | {len(theirs.main)} '
            f'| {sorted(ours_main - theirs.main)} | {sorted(theirs.main - ours_main)} '
            f'| {len(ours_extra)}/{len(theirs.extras)} | {len(theirs.warnings)} |'
        )


if __name__ == '__main__':
    main()
