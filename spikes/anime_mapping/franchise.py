"""Group AniList entries into a franchise using sequel/prequel chains."""

from collections.abc import Callable
from dataclasses import dataclass, field

MAIN_RELATIONS = {'SEQUEL', 'PREQUEL'}
EXTRA_RELATIONS = {'SIDE_STORY', 'SUMMARY', 'SPIN_OFF', 'ALTERNATIVE', 'PARENT'}
ANIME_FORMATS = {'TV', 'TV_SHORT', 'MOVIE', 'SPECIAL', 'OVA', 'ONA'}


@dataclass
class Node:
    """One anime entry as returned by AniList.

    Attributes:
        anilist_id: AniList media id.
        mal_id: MyAnimeList id, or None when AniList has no mapping.
        format: AniList format (TV, MOVIE, OVA, ...).
        title: Romaji title.
        episodes: Episode count, or None while unknown.
        start: (year, month, day) with None for unknown parts.
        edges: List of (relation_type, anilist_id, mal_id, format, media_type).
    """

    anilist_id: int
    mal_id: int | None
    format: str | None
    title: str
    episodes: int | None
    start: tuple[int | None, int | None, int | None]
    edges: list[tuple[str, int, int | None, str | None, str]] = field(default_factory=list)


@dataclass
class Franchise:
    """A franchise split into the sequel/prequel chain and its side entries."""

    main: list[Node]
    extras: list[tuple[int, int | None, str | None, str]]


def node_from_media(media: dict) -> Node:
    """Convert an AniList ``Media`` object into a ``Node``.

    Args:
        media: Parsed ``data.Media`` object from the AniList response.

    Returns:
        The corresponding ``Node``.
    """
    start = media.get('startDate') or {}
    edges = [
        (
            edge['relationType'],
            edge['node']['id'],
            edge['node'].get('idMal'),
            edge['node'].get('format'),
            edge['node'].get('type', ''),
        )
        for edge in (media.get('relations') or {}).get('edges', [])
    ]
    return Node(
        anilist_id=media['id'],
        mal_id=media.get('idMal'),
        format=media.get('format'),
        title=(media.get('title') or {}).get('romaji') or '',
        episodes=media.get('episodes'),
        start=(start.get('year'), start.get('month'), start.get('day')),
        edges=edges,
    )


def _sort_key(node: Node) -> tuple[int, int, int]:
    """Sort chronologically, pushing unknown dates to the end."""
    year, month, day = node.start
    return (year or 9999, month or 12, day or 31)


def build_franchise(
    fetch: Callable[[int], Node | None], root_anilist_id: int
) -> Franchise:
    """Walk sequel/prequel links from a root entry.

    Side entries (side stories, summaries, spin-offs, alternatives, parents) of
    main entries are listed as extras but never expanded, which keeps a
    franchise bounded. Non-anime nodes (manga, music) are ignored.

    Args:
        fetch: Returns the ``Node`` for an AniList id, or None if unavailable.
        root_anilist_id: AniList id to start from.

    Returns:
        The franchise with ``main`` sorted by start date.
    """
    seen: set[int] = set()
    main: list[Node] = []
    queue = [root_anilist_id]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        node = fetch(current)
        if node is None:
            continue
        main.append(node)
        for relation, other_id, _mal, fmt, media_type in node.edges:
            if relation in MAIN_RELATIONS and media_type == 'ANIME' and fmt in ANIME_FORMATS:
                queue.append(other_id)
    main_ids = {node.anilist_id for node in main}
    extras: dict[int, tuple[int, int | None, str | None, str]] = {}
    for node in main:
        for relation, other_id, mal, fmt, media_type in node.edges:
            if (
                relation in EXTRA_RELATIONS
                and media_type == 'ANIME'
                and fmt in ANIME_FORMATS
                and other_id not in main_ids
            ):
                extras[other_id] = (other_id, mal, fmt, relation)
    main.sort(key=_sort_key)
    return Franchise(main=main, extras=list(extras.values()))
