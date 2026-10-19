"""Group AniList entries into a franchise.

The main chain follows SEQUEL and PREQUEL links between anime entries. Side
stories, summaries, spin-offs, alternative versions and parents of a main entry
are listed as extras but are never walked, which keeps a franchise bounded.

A MOVIE or SPECIAL that has a PARENT edge is a compilation or side film of its
parent. It is an extra, yet the walk still passes through it, because AniList
sometimes links a season to the next one only via such a film. Known limit: a
compilation without a PARENT edge, such as AniList 192809 (the second Jujutsu
Kaisen compilation film, linked by ALTERNATIVE), is not recognised.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from providers.anilist import ANIME_FORMATS, AniListEdge, AniListMedia

MAIN_RELATIONS = {'SEQUEL', 'PREQUEL'}
EXTRA_RELATIONS = {'SIDE_STORY', 'SUMMARY', 'SPIN_OFF', 'ALTERNATIVE', 'PARENT'}


@dataclass
class Franchise:
    """A franchise split into the main chain and its extras, both chronological."""

    main: list[AniListMedia]
    extras: list[AniListMedia]


def is_compilation(media: AniListMedia) -> bool:
    """Return True for a MOVIE or SPECIAL that names a PARENT anime entry."""
    return media.format in ('MOVIE', 'SPECIAL') and any(
        edge.relation == 'PARENT' and edge.media_type == 'ANIME' for edge in media.edges
    )


def _walkable(edge: AniListEdge, source: AniListMedia) -> bool:
    """Return True for an edge the walk follows.

    That is a SEQUEL or PREQUEL to a known anime format, and also the PARENT
    edge of a compilation, so that a franchise found through a compilation film
    still reaches its seasons.
    """
    if edge.media_type != 'ANIME' or edge.format not in ANIME_FORMATS:
        return False
    if edge.relation in MAIN_RELATIONS:
        return True
    return edge.relation == 'PARENT' and is_compilation(source)


def _is_extra_edge(edge: AniListEdge) -> bool:
    """Return True for an edge that lists its target as an extra.

    Besides the extra relations this keeps sequels and prequels whose format is
    still null (freshly announced), instead of dropping them.
    """
    if edge.media_type != 'ANIME':
        return False
    if edge.relation in EXTRA_RELATIONS:
        return edge.format in ANIME_FORMATS or edge.format is None
    return edge.relation in MAIN_RELATIONS and edge.format is None


def load_franchise(
    fetch_many: Callable[[list[int]], Iterable[AniListMedia]], root_id: int
) -> Franchise:
    """Fetch and group the franchise around an entry.

    Args:
        fetch_many: Returns the entries for a list of AniList ids; ids it does
            not know are simply missing. ``AniListClient.media_many`` fits.
        root_id: AniList id of any entry of the franchise.

    Returns:
        The franchise. Unknown start dates sort last.
    """
    walked: dict[int, AniListMedia] = {}
    asked: set[int] = set()
    frontier = [root_id]
    while frontier:
        asked.update(frontier)
        found = list(fetch_many(frontier))
        walked.update({media.anilist_id: media for media in found})
        frontier = list(
            dict.fromkeys(
                edge.anilist_id
                for media in found
                for edge in media.edges
                if _walkable(edge, media) and edge.anilist_id not in asked
            )
        )
    main = [media for media in walked.values() if not is_compilation(media)]
    extra_ids = {
        edge.anilist_id
        for media in main
        for edge in media.edges
        if _is_extra_edge(edge) and edge.anilist_id not in walked
    }
    extras = [media for media in walked.values() if is_compilation(media)]
    if extra_ids:
        extras.extend(fetch_many(sorted(extra_ids)))
    main.sort(key=lambda media: media.sort_key)
    extras.sort(key=lambda media: media.sort_key)
    return Franchise(main=main, extras=extras)
