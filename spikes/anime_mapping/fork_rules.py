"""The Yamtrack fork's relation rules, re-expressed over AniList relation types.

The fork walks MAL relations (``anime_series_builder._discover_group``). AniList
relation types derive from MAL, so the mapping is: sequel/prequel -> SEQUEL and
PREQUEL; side_story/summary -> SIDE_STORY and SUMMARY; parent_story/full_story ->
PARENT; alternative_version/spin_off -> ALTERNATIVE and SPIN_OFF.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from franchise import ANIME_FORMATS, Node

MAIN = {'SEQUEL', 'PREQUEL'}
EXTRA = {'SIDE_STORY', 'SUMMARY'}
PARENT = {'PARENT'}
DUBIOUS = {'ALTERNATIVE', 'SPIN_OFF'}


@dataclass
class ForkGroup:
    """Result of the fork's traversal: main ids, extra ids, and warnings."""

    main: set[int] = field(default_factory=set)
    extras: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def fork_group(fetch: Callable[[int], Node | None], root_anilist_id: int) -> ForkGroup:
    """Walk the relation graph the way the fork's ``_discover_group`` does.

    Unlike ``build_franchise``, extras are expanded too (their sequel and
    prequel edges are followed), which is how the fork behaves.

    Args:
        fetch: Returns the ``Node`` for an AniList id, or None if unavailable.
        root_anilist_id: AniList id to start from.

    Returns:
        The ids classified as main or extra, plus warnings for skipped edges.
    """
    is_extra: dict[int, bool] = {}
    warnings: list[str] = []
    visited = {root_anilist_id}
    queue: list[tuple[int, bool]] = [(root_anilist_id, False)]
    while queue:
        current, extra = queue.pop(0)
        node = fetch(current)
        if node is None:
            continue
        is_extra[current] = is_extra.get(current, False) or extra
        for relation, other, _mal, fmt, media_type in node.edges:
            if media_type != 'ANIME' or fmt not in ANIME_FORMATS:
                continue
            if relation in DUBIOUS:
                warnings.append(f'{node.anilist_id} -{relation}-> {other}')
                continue
            target_extra = relation in EXTRA
            if relation in PARENT:
                is_extra[current] = True
            if (relation in MAIN or target_extra or relation in PARENT) and other not in visited:
                visited.add(other)
                queue.append((other, target_extra))
    return ForkGroup(
        main={i for i, extra in is_extra.items() if not extra},
        extras={i for i, extra in is_extra.items() if extra},
        warnings=warnings,
    )
