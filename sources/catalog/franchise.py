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

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from providers.anilist import ANIME_FORMATS, AniListEdge, AniListMedia

MAIN_RELATIONS = {'SEQUEL', 'PREQUEL'}
EXTRA_RELATIONS = {'SIDE_STORY', 'SUMMARY', 'SPIN_OFF', 'ALTERNATIVE', 'PARENT'}
# An extra found only through these is a work of its own that merely relates to
# the franchise (a remake, a spin-off series): it is listed, but never claimed.
DETACHED_RELATIONS = {'SPIN_OFF', 'ALTERNATIVE'}


@dataclass
class Franchise:
    """A franchise split into the main chain and its extras, both chronological.

    ``detached`` holds the AniList ids of the extras that are only related to
    the franchise (see ``DETACHED_RELATIONS``). They are shown as extra seasons
    but are not the franchise's own, so they must not decide which title a
    provider id belongs to.
    """

    main: list[AniListMedia]
    extras: list[AniListMedia]
    detached: set[int] = field(default_factory=set)

    @property
    def own(self) -> list[AniListMedia]:
        """The entries that belong to this franchise: the main chain and its extras
        except the detached ones."""
        return self.main + [m for m in self.extras if m.anilist_id not in self.detached]

    @property
    def anchors(self) -> list[AniListMedia]:
        """The entries that identify this franchise: the main chain and the
        compilations. Side stories and other extras can be shared with an
        alternative version of the franchise, so they never decide a match."""
        return self.main + [m for m in self.extras if is_compilation(m)]


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
    found_by: dict[int, set[str]] = defaultdict(set)
    for media in main:
        for edge in media.edges:
            if _is_extra_edge(edge) and edge.anilist_id not in walked:
                found_by[edge.anilist_id].add(edge.relation)
    fetched = list(fetch_many(sorted(found_by))) if found_by else []
    extras = [media for media in walked.values() if is_compilation(media)] + fetched
    main_ids = {media.anilist_id for media in main}
    # The extra's own edges back to the main chain tell what it really is: the
    # reverse of a spin-off is a plain PARENT edge, which says nothing by itself.
    for media in fetched:
        found_by[media.anilist_id].update(
            edge.relation for edge in media.edges if edge.anilist_id in main_ids
        )
    detached = set()
    for extra_id, relations in found_by.items():
        informative = relations - {'PARENT'}
        if informative and informative <= DETACHED_RELATIONS:
            detached.add(extra_id)
    main.sort(key=lambda media: media.sort_key)
    extras.sort(key=lambda media: media.sort_key)
    return Franchise(main=main, extras=extras, detached=detached)
