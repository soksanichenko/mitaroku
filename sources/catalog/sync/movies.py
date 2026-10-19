"""Sync a TMDB movie."""

import logging
from datetime import datetime

from catalog.models import Title
from catalog.sync.common import (
    Clients,
    link_external_id,
    require_tmdb,
    save_title_translation,
    schedule_next_sync,
    to_rating,
)
from providers.http import ProviderError

logger = logging.getLogger(__name__)

STATUS_BY_TMDB = {
    'Released': Title.Status.ENDED,
    'Canceled': Title.Status.CANCELED,
    'Rumored': Title.Status.UPCOMING,
    'Planned': Title.Status.UPCOMING,
    'In Production': Title.Status.UPCOMING,
    'Post Production': Title.Status.UPCOMING,
}


def sync_movie(title: Title, clients: Clients, now: datetime) -> None:
    """Refresh a movie title from TMDB.

    Args:
        title: A title of kind ``movie`` with a ``tmdb_movie`` external id.
        clients: The provider clients.
        now: Current time, stored as ``synced_at``.

    Raises:
        ProviderError: The id is missing, unknown to TMDB, or TMDB is down.
    """
    link = title.external_ids.filter(provider='tmdb_movie').first()
    if link is None:
        raise ProviderError(f'Title {title.pk} has no TMDB movie id')
    movie = require_tmdb(clients).movie(int(link.external_id))
    if movie is None:
        raise ProviderError(f'TMDB does not know movie {link.external_id}')
    title.original_title = movie.original_title or movie.title or title.original_title
    title.original_language = movie.original_language
    title.release_date = movie.release_date
    title.status = STATUS_BY_TMDB.get(movie.status, Title.Status.UNKNOWN)
    if (
        movie.release_date
        and movie.release_date > now.date()
        and title.status == (Title.Status.ENDED)
    ):
        title.status = Title.Status.UPCOMING
    title.runtime_minutes = movie.runtime
    title.poster_url = movie.poster_url
    title.backdrop_url = movie.backdrop_url
    title.rating = to_rating(movie.rating)
    title.rating_votes = movie.votes or None
    title.rating_source = 'tmdb' if movie.rating is not None else ''
    schedule_next_sync(title, now)
    title.save()
    if movie.imdb_id:
        link_external_id(title, 'imdb', movie.imdb_id)
    save_title_translation(title, 'en', movie.title, movie.overview)
