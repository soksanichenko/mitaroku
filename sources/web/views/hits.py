"""Provider hits prepared for display, shared by Search and Browse."""

from dataclasses import dataclass

from catalog.models import ExternalId
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from providers.types import SearchHit
from tracking.adding import PENDING_TIMEOUT
from tracking.models import AddRequest, LibraryEntry

# The keys are the ``SearchHit.kind`` values.
KIND_LABELS = {'tv': _('Show'), 'anime': _('Anime'), 'movie': _('Movie')}


@dataclass
class ResultRow:
    """One search hit prepared for display.

    Attributes:
        hit: The provider's search hit.
        poster: What the poster component reads (``pk``, ``poster_url``,
            ``rating``, ``rating_source``); a hit has no catalog title yet.
        kind_label: The translated kind: Show, Anime or Movie.
        title_id: The catalog title when the title is already known, else None.
        entry: The user's library entry for it, if any.
        pending: The user's add request for it that is still running, if any.
        href: The title page when the title is in the catalog, else the URL that
            opens (creates) it.
    """

    hit: SearchHit
    poster: dict
    kind_label: str
    title_id: int | None
    entry: LibraryEntry | None
    pending: AddRequest | None
    href: str

    @property
    def name(self) -> str:
        """The name to show: provider data may lack a title or both names."""
        return self.hit.title or self.hit.original_title or gettext('Untitled')

    @property
    def fields(self) -> dict[str, str]:
        """The hidden fields the add picker sends."""
        return {'provider': self.hit.provider, 'external_id': self.hit.external_id}


def _pending_requests(user, hits: list[SearchHit]) -> dict[tuple[str, str], AddRequest]:
    """Return the user's running add requests for the hits, in one query.

    A request older than the timeout belongs to a job that died; it is not shown
    as running (the next add expires it).
    """
    return {
        (request.provider, request.external_id): request
        for request in AddRequest.objects.for_user(user).filter(
            state=AddRequest.State.PENDING,
            created_at__gte=timezone.now() - PENDING_TIMEOUT,
            provider__in={h.provider for h in hits},
            external_id__in={h.external_id for h in hits},
        )
    }


def decorate(user, hits: list[SearchHit]) -> list[ResultRow]:
    """Attach the catalog title, the library entry and the running add request.

    Args:
        user: The signed-in user; only their library and requests are read.
        hits: Provider hits to prepare.

    Returns:
        One row per hit, in the same order. The queries do not depend on the
        number of hits.
    """
    links = ExternalId.objects.filter(
        provider__in={h.provider for h in hits},
        external_id__in={h.external_id for h in hits},
    ).values_list('provider', 'external_id', 'title_id')
    title_ids = {(provider, external_id): pk for provider, external_id, pk in links}
    entries = {
        entry.title_id: entry
        for entry in LibraryEntry.objects.for_user(user).filter(
            title_id__in=set(title_ids.values())
        )
    }
    pending = _pending_requests(user, hits)
    rows = []
    for hit in hits:
        title_id = title_ids.get((hit.provider, hit.external_id))
        rows.append(
            ResultRow(
                hit=hit,
                # Only the colour of the placeholder depends on the number.
                poster={
                    'pk': int(hit.external_id),
                    'poster_url': hit.poster_url,
                    'rating': hit.rating,
                    'rating_source': 'anilist' if hit.provider == 'anilist' else 'tmdb',
                },
                kind_label=str(KIND_LABELS[hit.kind]),
                title_id=title_id,
                entry=entries.get(title_id),
                pending=pending.get((hit.provider, hit.external_id)),
                href=(
                    reverse('title', args=[title_id])
                    if title_id is not None
                    else reverse(
                        'open_title', args=[hit.provider, int(hit.external_id)]
                    )
                ),
            )
        )
    return rows


def is_fragment_request(request: HttpRequest) -> bool:
    """Tell whether only the results block is wanted.

    HTMX restoring a page from history asks with ``HX-Request`` too but needs
    the whole page.
    """
    return request.headers.get('HX-Request') == 'true' and not request.headers.get(
        'HX-History-Restore-Request'
    )
