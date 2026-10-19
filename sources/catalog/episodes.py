"""Decide which TMDB episodes belong to which AniList entry.

TMDB lists one show where AniList has many cours, and the Fribb dataset that
bridges them has known errors. So an allocation is accepted only when it passes
a guard: the first episode airs within two days of the AniList start date, and
the number of episodes equals the AniList count when that is known. Anything
that fails the guard falls back to the degraded mode (see ``catalog.sync``).
"""

from dataclasses import dataclass
from datetime import date

from providers.tmdb import TmdbEpisode, TmdbSeason

GUARD_DAYS = 2
# Formats whose episodes continue each other inside one TMDB show.
STREAM_FORMATS = {'TV', 'TV_SHORT', 'ONA'}


@dataclass(frozen=True)
class EntrySpec:
    """What the allocation needs to know about one AniList entry.

    Attributes:
        anilist_id: AniList id of the entry.
        format: AniList format (TV, ONA, OVA, SPECIAL, ...).
        start: Full AniList start date, or None when any part is unknown.
        expected: AniList episode count, or None while the show is running.
        tmdb_season: TMDB season the Fribb dataset gives, or None.
        offset: Episodes of that season that belong to earlier entries.
        sort_key: AniList chronological key (unknown dates last).
    """

    anilist_id: int
    format: str
    start: date | None
    expected: int | None
    tmdb_season: int | None
    offset: int
    sort_key: tuple[int, int, int]


def passes_guard(
    part: list[TmdbEpisode], expected: int | None, start: date | None
) -> bool:
    """Return True when an allocation is trustworthy.

    Args:
        part: The episodes allocated to an entry.
        expected: AniList episode count, or None when unknown.
        start: AniList start date, or None when not fully known.

    Returns:
        True when the first episode airs within ``GUARD_DAYS`` of ``start`` and
        the length equals ``expected`` (when known).
    """
    if not part or start is None or part[0].air_date is None:
        return False
    if expected is not None and len(part) != expected:
        return False
    return abs((part[0].air_date - start).days) <= GUARD_DAYS


def fribb_part(seasons: dict[int, TmdbSeason], spec: EntrySpec) -> list[TmdbEpisode]:
    """Take the entry's episodes from the TMDB season the Fribb dataset names."""
    if spec.tmdb_season is None or spec.tmdb_season not in seasons:
        return []
    episodes = seasons[spec.tmdb_season].episodes[spec.offset :]
    return episodes if spec.expected is None else episodes[: spec.expected]


def concatenated_parts(
    seasons: dict[int, TmdbSeason], specs: list[EntrySpec]
) -> dict[int, list[TmdbEpisode]]:
    """Slice all regular seasons, glued together, by the entries' counts.

    Args:
        seasons: TMDB seasons of one show; season 0 (specials) is not part of
            the stream.
        specs: The stream entries in release order.

    Returns:
        ``{anilist_id: episodes}``. An entry without a known count takes the rest.
    """
    stream = [
        episode
        for number in sorted(seasons)
        if number >= 1
        for episode in seasons[number].episodes
    ]
    parts = {}
    position = 0
    for spec in specs:
        end = len(stream) if spec.expected is None else position + spec.expected
        parts[spec.anilist_id] = stream[position:end]
        position = min(end, len(stream))
    return parts


def allocate_show(
    seasons: dict[int, TmdbSeason], specs: list[EntrySpec]
) -> dict[int, list[TmdbEpisode] | None]:
    """Allocate the episodes of one TMDB show to the AniList entries mapped to it.

    For each entry the Fribb season and offset is tried first, then (for TV, ONA
    and TV_SHORT entries) the concatenated stream; the first candidate that
    passes the guard wins.

    Args:
        seasons: All TMDB seasons of the show, season 0 included.
        specs: The entries Fribb maps to this show.

    Returns:
        ``{anilist_id: episodes}``, with None for entries no candidate satisfied.
    """
    ordered = sorted(specs, key=lambda spec: spec.sort_key)
    stream = concatenated_parts(
        seasons, [spec for spec in ordered if spec.format in STREAM_FORMATS]
    )
    result: dict[int, list[TmdbEpisode] | None] = {}
    for spec in ordered:
        candidates = [fribb_part(seasons, spec)]
        if spec.format in STREAM_FORMATS:
            candidates.append(stream[spec.anilist_id])
        result[spec.anilist_id] = next(
            (
                part
                for part in candidates
                if passes_guard(part, spec.expected, spec.start)
            ),
            None,
        )
    return result
