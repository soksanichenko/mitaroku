"""Fill the catalog from the providers."""

import logging
from datetime import datetime

from catalog.models import ExternalId, Title
from catalog.sync.anime import sync_anime
from catalog.sync.common import Clients, default_clients
from catalog.sync.movies import sync_movie
from catalog.sync.tv import sync_tv
from django.db import IntegrityError, transaction
from django.utils import timezone
from providers.types import SearchHit

logger = logging.getLogger(__name__)

SYNC_BY_KIND = {
    Title.Kind.MOVIE: sync_movie,
    Title.Kind.TV: sync_tv,
    Title.Kind.ANIME: sync_anime,
}


def sync_title(
    title: Title, clients: Clients | None = None, now: datetime | None = None
) -> None:
    """Refresh a title and everything under it from the providers.

    Args:
        title: The title to refresh.
        clients: Provider clients; the configured ones when omitted.
        now: Current time, for tests.

    Raises:
        ProviderError: A provider failed; callers retry later.
    """
    SYNC_BY_KIND[Title.Kind(title.kind)](
        title, clients or default_clients(), now or timezone.now()
    )


def get_or_create_title(hit: SearchHit) -> tuple[Title, bool]:
    """Return the title for a search hit, creating a bare one when it is new.

    The provider id decides: adding any entry of an anime franchise that is
    already in the catalog returns the existing franchise title.

    Args:
        hit: A search result.

    Returns:
        ``(title, created)``. A created title has only the data of the search
        result until it is synced.
    """
    lookup = {'provider': hit.provider, 'external_id': hit.external_id}
    link = ExternalId.objects.select_related('title').filter(**lookup).first()
    if link is not None:
        return link.title, False
    try:
        with transaction.atomic():
            title = Title.objects.create(
                kind=hit.kind,
                original_title=hit.original_title or hit.title,
                poster_url=hit.poster_url,
            )
            ExternalId.objects.create(title=title, **lookup)
    except IntegrityError:
        # A concurrent request created it first.
        return ExternalId.objects.select_related('title').get(**lookup).title, False
    return title, True
