"""Fill the catalog from the providers."""

import logging
from datetime import datetime

from catalog.franchise import load_franchise
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


def find_franchise_title(hit: SearchHit, clients: Clients | None) -> Title | None:
    """Return the title that already holds another entry of the hit's franchise.

    A sequel announced after the franchise was last synced is not linked yet,
    but its siblings are. Only the franchise's anchors count (the main chain and
    its compilations): a remake or spin-off that the franchise merely lists, or
    a side story shared with an alternative version, does not make a match. This
    is a network call, so it runs before any write.

    Raises:
        ProviderError: AniList is down.
    """
    anilist = (clients or default_clients()).anilist
    franchise = load_franchise(anilist.media_many, int(hit.external_id))
    ids = [str(media.anilist_id) for media in franchise.anchors]
    link = (
        ExternalId.objects.select_related('title')
        .filter(provider='anilist', external_id__in=ids)
        .order_by('pk')
        .first()
    )
    return link.title if link else None


def get_or_create_title(
    hit: SearchHit, clients: Clients | None = None
) -> tuple[Title, bool]:
    """Return the title for a search hit, creating a bare one when it is new.

    The provider id decides: adding any entry of an anime franchise that is
    already in the catalog returns the existing franchise title, even when that
    entry itself has not been linked by a sync yet.

    Args:
        hit: A search result.
        clients: Provider clients for the franchise lookup of an anime hit; the
            configured ones when omitted.

    Returns:
        ``(title, created)``. A created title has only the data of the search
        result until it is synced.

    Raises:
        ProviderError: AniList failed during the franchise lookup.
    """
    lookup = {'provider': hit.provider, 'external_id': hit.external_id}
    link = ExternalId.objects.select_related('title').filter(**lookup).first()
    if link is not None:
        return link.title, False
    if hit.provider == 'anilist':
        existing = find_franchise_title(hit, clients)
        if existing is not None:
            return existing, False
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
