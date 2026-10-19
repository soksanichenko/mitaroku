"""Sync a TMDB TV show with its seasons and episodes."""

import logging
from datetime import datetime

from catalog.models import Season, Title
from catalog.sync.common import (
    Clients,
    EpisodeRow,
    link_external_id,
    renumber_absolute,
    require_tmdb,
    save_title_translation,
    schedule_next_sync,
    to_rating,
    write_episodes,
)
from django.db import transaction
from providers.http import ProviderError

logger = logging.getLogger(__name__)

STATUS_BY_TMDB = {
    'Returning Series': Title.Status.AIRING,
    'Planned': Title.Status.UPCOMING,
    'In Production': Title.Status.UPCOMING,
    'Pilot': Title.Status.UPCOMING,
    'Ended': Title.Status.ENDED,
    'Canceled': Title.Status.CANCELED,
}


def sync_tv(title: Title, clients: Clients, now: datetime) -> None:
    """Refresh a TV title and all its seasons from TMDB.

    Season 0 (specials) is kept as an extra season. Seasons and episodes that
    TMDB no longer lists are left in place.

    Args:
        title: A title of kind ``tv`` with a ``tmdb_tv`` external id.
        clients: The provider clients.
        now: Current time, stored as ``synced_at``.

    Raises:
        ProviderError: The id is missing, unknown to TMDB, or TMDB is down.
    """
    link = title.external_ids.filter(provider='tmdb_tv').first()
    if link is None:
        raise ProviderError(f'Title {title.pk} has no TMDB TV id')
    tmdb = require_tmdb(clients)
    show = tmdb.tv(int(link.external_id))
    if show is None:
        raise ProviderError(f'TMDB does not know show {link.external_id}')
    seasons = tmdb.seasons(show)
    with transaction.atomic():
        title.original_title = show.original_name or show.name or title.original_title
        title.original_language = show.original_language
        title.release_date = show.first_air_date
        title.status = STATUS_BY_TMDB.get(show.status, Title.Status.UNKNOWN)
        title.poster_url = show.poster_url
        title.backdrop_url = show.backdrop_url
        title.rating = to_rating(show.rating)
        title.rating_votes = show.votes or None
        title.rating_source = 'tmdb' if show.rating is not None else ''
        schedule_next_sync(title, now)
        title.save()
        if show.imdb_id:
            link_external_id(title, 'imdb', show.imdb_id)
        save_title_translation(title, 'en', show.name, show.overview)
        for summary in show.seasons:
            tmdb_season = seasons.get(summary.number)
            if tmdb_season is None:
                continue
            dated = [e.air_date for e in tmdb_season.episodes if e.air_date]
            season, _ = Season.objects.update_or_create(
                title=title,
                number=summary.number,
                defaults={
                    'kind': Season.Kind.SPECIAL
                    if summary.number == 0
                    else Season.Kind.TV,
                    'name': summary.name,
                    'is_extra': summary.number == 0,
                    'tmdb_tv_id': show.tmdb_id,
                    'start_date': min(dated) if dated else None,
                    'episode_count': summary.episode_count,
                    'episode_source': Season.EpisodeSource.TMDB,
                },
            )
            write_episodes(
                season,
                [
                    EpisodeRow(
                        number=episode.number,
                        air_date=episode.air_date,
                        tmdb_season_number=episode.season_number,
                        tmdb_episode_number=episode.number,
                        name=episode.name,
                        overview=episode.overview,
                    )
                    for episode in tmdb_season.episodes
                ],
            )
        renumber_absolute(title)
