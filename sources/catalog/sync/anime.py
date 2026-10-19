"""Sync an anime franchise: AniList structure, TMDB episodes behind the guard."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean

from catalog.episodes import EntrySpec, allocate_show
from catalog.franchise import Franchise, load_franchise
from catalog.models import EpisodeTranslation, Season, Title
from catalog.sync.common import (
    Clients,
    EpisodeRow,
    link_external_id,
    renumber_absolute,
    save_title_translation,
    schedule_next_sync,
    to_rating,
    write_episodes,
)
from django.db import transaction
from providers.anilist import AniListMedia
from providers.http import ProviderError
from providers.models import AnimeMapping
from providers.tmdb import TmdbEpisode

logger = logging.getLogger(__name__)

KIND_BY_FORMAT = {
    'TV': Season.Kind.TV,
    'TV_SHORT': Season.Kind.TV,
    'ONA': Season.Kind.ONA,
    'OVA': Season.Kind.OVA,
    'MOVIE': Season.Kind.MOVIE,
    'SPECIAL': Season.Kind.SPECIAL,
}
# AniList's airing times are timestamps; entries are dated by the day in Japan.
JST = timezone(timedelta(hours=9))
RUNNING = {'RELEASING', 'NOT_YET_RELEASED'}


def title_status(main: list[AniListMedia]) -> str:
    """Derive the franchise status from its main entries."""
    states = {media.status for media in main}
    if 'RELEASING' in states or 'HIATUS' in states:
        return Title.Status.AIRING
    if 'NOT_YET_RELEASED' in states:
        return Title.Status.UPCOMING
    if states == {'CANCELLED'}:
        return Title.Status.CANCELED
    if states and states <= {'FINISHED', 'CANCELLED'}:
        return Title.Status.ENDED
    return Title.Status.UNKNOWN


def resolve_tmdb_episodes(
    clients: Clients,
    entries: list[AniListMedia],
    mappings: dict[int, AnimeMapping],
) -> dict[int, list[TmdbEpisode]]:
    """Return the TMDB episodes the guard accepts, per AniList id.

    Entries the guard rejects, movies, and entries Fribb does not map to a TMDB
    show are absent from the result and go to the degraded mode.

    Raises:
        ProviderError: TMDB is down; the sync is retried rather than degraded,
            so a short outage does not wipe episode titles.
    """
    if clients.tmdb is None:
        return {}
    by_show: dict[int, list[EntrySpec]] = defaultdict(list)
    for media in entries:
        mapping = mappings.get(media.anilist_id)
        if media.format == 'MOVIE' or mapping is None or mapping.tmdb_tv_id is None:
            continue
        by_show[mapping.tmdb_tv_id].append(
            EntrySpec(
                anilist_id=media.anilist_id,
                format=media.format or '',
                start=media.start_date,
                expected=media.episodes,
                tmdb_season=mapping.tmdb_season,
                offset=mapping.tmdb_episode_offset,
                sort_key=media.sort_key,
            )
        )
    accepted: dict[int, list[TmdbEpisode]] = {}
    for tmdb_id, specs in by_show.items():
        show = clients.tmdb.tv(tmdb_id)
        if show is None:
            continue
        allocation = allocate_show(clients.tmdb.seasons(show), specs)
        accepted.update({k: v for k, v in allocation.items() if v is not None})
    return accepted


def degraded_rows(
    media: AniListMedia, is_extra: bool, clients: Clients
) -> list[EpisodeRow]:
    """Build episodes from AniList alone: counts, and dates where they are reliable.

    Per-episode dates for finished episodes are used only when the airing
    schedule has exactly one node per episode. Dates of episodes still to air
    are always used. Times are converted to Japan time to get the air date, as
    AniList's start dates are. No titles exist in this mode.
    """
    needs_schedule = (not is_extra) or media.status in RUNNING
    schedule = clients.anilist.schedule(media.anilist_id) if needs_schedule else None
    dates = {}
    if schedule is not None:
        # With a known count a partial schedule would date the wrong episodes;
        # with an unknown one every aired node is all there is.
        if media.episodes is None or len(schedule.all) == media.episodes:
            dates.update(schedule.all)
        dates.update(schedule.upcoming)
    if media.episodes is not None:
        numbers = range(1, media.episodes + 1)
    else:
        numbers = range(1, max(dates) + 1) if dates else range(0)
    return [
        EpisodeRow(
            number=n, air_date=dates[n].astimezone(JST).date() if n in dates else None
        )
        for n in numbers
    ]


def episode_plan(
    media: AniListMedia,
    is_extra: bool,
    accepted: dict[int, list[TmdbEpisode]],
    clients: Clients,
) -> tuple[str, list[EpisodeRow]]:
    """Return the episode source and rows for one entry."""
    if media.format == 'MOVIE':
        return Season.EpisodeSource.ANILIST, [EpisodeRow(1, media.start_date)]
    part = accepted.get(media.anilist_id)
    if part is not None:
        rows = [
            EpisodeRow(
                number=index,
                air_date=episode.air_date,
                tmdb_season_number=episode.season_number,
                tmdb_episode_number=episode.number,
                name=episode.name,
                overview=episode.overview,
            )
            for index, episode in enumerate(part, start=1)
        ]
        return Season.EpisodeSource.TMDB, rows
    rows = degraded_rows(media, is_extra, clients)
    return (Season.EpisodeSource.ANILIST if rows else Season.EpisodeSource.NONE), rows


def sync_anime(title: Title, clients: Clients, now: datetime) -> None:
    """Refresh an anime franchise.

    Args:
        title: A title of kind ``anime`` with at least one AniList external id.
        clients: The provider clients; TMDB may be missing (everything degrades).
        now: Current time, stored as ``synced_at``.

    Raises:
        ProviderError: AniList or TMDB is down, or AniList does not know the title.
    """
    root = title.external_ids.filter(provider='anilist').order_by('pk').first()
    if root is None:
        raise ProviderError(f'Title {title.pk} has no AniList id')
    franchise = load_franchise(clients.anilist.media_many, int(root.external_id))
    if not franchise.main:
        raise ProviderError(f'AniList does not know entry {root.external_id}')
    entries = franchise.main + franchise.extras
    mappings = AnimeMapping.objects.in_bulk([media.anilist_id for media in entries])
    if not mappings and not AnimeMapping.objects.exists():
        logger.warning('The Fribb mapping is empty; run "manage.py refresh_mapping"')
    accepted = resolve_tmdb_episodes(clients, entries, mappings)
    extra_ids = {media.anilist_id for media in franchise.extras}
    plans = {
        media.anilist_id: episode_plan(
            media, media.anilist_id in extra_ids, accepted, clients
        )
        for media in entries
    }
    with transaction.atomic():
        write_seasons(title, franchise, mappings, plans)
        renumber_absolute(title)
        update_title(title, franchise, mappings, now)


def write_seasons(
    title: Title,
    franchise: Franchise,
    mappings: dict[int, AnimeMapping],
    plans: dict[int, tuple[str, list[EpisodeRow]]],
) -> None:
    """Upsert one season per franchise entry, main entries first.

    Existing seasons are matched by AniList id and keep their rows (episodes and
    later watch events hang off them). To renumber without tripping the unique
    ``(title, number)`` constraint every season first gets a unique negative
    number (its position among the title's seasons, which always fits the
    ``number`` column). Seasons the franchise no longer contains end up after the others as
    extras.
    """
    existing = list(title.seasons.order_by('pk'))
    for position, season in enumerate(existing, start=1):
        season.number = -position
    Season.objects.bulk_update(existing, ['number'])
    kept = []
    ordered = [(m, False) for m in franchise.main] + [
        (m, True) for m in franchise.extras
    ]
    for position, (media, is_extra) in enumerate(ordered, start=1):
        source, rows = plans[media.anilist_id]
        mapping = mappings.get(media.anilist_id)
        season, _ = Season.objects.update_or_create(
            title=title,
            anilist_id=media.anilist_id,
            defaults={
                'number': position,
                'kind': KIND_BY_FORMAT.get(media.format or '', Season.Kind.SPECIAL),
                'name': media.english or media.romaji,
                'is_extra': is_extra or media.format is None,
                'tmdb_tv_id': mapping.tmdb_tv_id
                if mapping and source == Season.EpisodeSource.TMDB
                else None,
                'start_date': media.start_date,
                'episode_count': media.episodes,
                'episode_source': source,
            },
        )
        kept.append(season.pk)
        if source != Season.EpisodeSource.TMDB:
            # Titles from an earlier, now rejected, allocation must not linger.
            season.episodes.update(tmdb_season_number=None, tmdb_episode_number=None)
            EpisodeTranslation.objects.filter(episode__season=season).delete()
        write_episodes(season, rows)
    leftovers = list(title.seasons.exclude(pk__in=kept).order_by('pk'))
    for offset, season in enumerate(leftovers, start=len(ordered) + 1):
        season.number = offset
        season.is_extra = True
    Season.objects.bulk_update(leftovers, ['number', 'is_extra'])


def update_title(
    title: Title,
    franchise: Franchise,
    mappings: dict[int, AnimeMapping],
    now: datetime,
) -> None:
    """Fill the title from the franchise and link every provider id."""
    first = franchise.main[0]
    scores = [m.average_score for m in franchise.main if m.average_score is not None]
    title.original_title = first.romaji or first.english or title.original_title
    title.original_language = 'ja'
    title.release_date = first.start_date
    title.status = title_status(franchise.main)
    title.poster_url = first.cover_url
    title.backdrop_url = first.banner_url
    title.rating = to_rating(mean(scores) / 10) if scores else None
    title.rating_votes = None
    title.rating_source = 'anilist' if scores else ''
    schedule_next_sync(title, now)
    title.save()
    save_title_translation(title, 'en', first.english or first.romaji, '')
    for media in franchise.main + franchise.extras:
        link_external_id(title, 'anilist', media.anilist_id)
        if media.mal_id:
            link_external_id(title, 'mal', media.mal_id)
    for media in franchise.main:
        mapping = mappings.get(media.anilist_id)
        if mapping and mapping.tmdb_tv_id:
            link_external_id(title, 'tmdb_tv', mapping.tmdb_tv_id)
        if mapping and mapping.imdb_id:
            link_external_id(title, 'imdb', mapping.imdb_id)
