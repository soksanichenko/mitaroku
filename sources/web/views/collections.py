"""Collection: the movies of a TMDB franchise, in release order."""

import logging

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from providers.http import ProviderError
from redis.exceptions import RedisError
from tracking.adding import EXTERNAL_ID
from tracking.models import LibraryEntry
from web.clients import shared_clients
from web.views.hits import decorate

logger = logging.getLogger(__name__)


def collection_page(request: HttpRequest, tmdb_id: int) -> HttpResponse:
    """Render a TMDB collection with the user's progress through it.

    Args:
        request: The request.
        tmdb_id: The collection's id at TMDB, at most 10 digits.

    Returns:
        The page; the error state (status 200) when TMDB cannot be asked, and the
        empty state for a collection without movies.

    Raises:
        Http404: The id is not a TMDB id (0 is not), or TMDB does not know the collection.
    """
    if not EXTERNAL_ID.fullmatch(str(tmdb_id)) or tmdb_id == 0:
        raise Http404('Unknown collection')
    tmdb = shared_clients().tmdb
    context: dict = {'state': 'error'}
    if tmdb is not None:
        try:
            collection = tmdb.collection(
                tmdb_id, request.user.profile.metadata_language
            )
        except (ProviderError, RedisError) as error:
            # The provider layer keeps its rate limits and cache in Redis.
            logger.warning('Collection %s failed: %s', tmdb_id, type(error).__name__)
        else:
            if collection is None:
                raise Http404('Unknown collection')
            rows = decorate(request.user, collection.parts)
            context = {
                'state': 'results' if rows else 'empty',
                'collection': collection,
                'rows': rows,
                'total': len(rows),
                'completed': sum(
                    1
                    for row in rows
                    if row.entry and row.entry.status == LibraryEntry.Status.COMPLETED
                ),
            }
    return render(request, 'web/collection.html', context)
