"""Search and add views."""

from dataclasses import dataclass
from urllib.parse import urlencode

from catalog.models import ExternalId
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.decorators.vary import vary_on_headers
from providers.search import search
from providers.types import SearchHit
from tracking.adding import MAX_PENDING, TooManyPending, expire_stale, request_add
from tracking.models import AddRequest, LibraryEntry
from web.clients import shared_clients
from web.templatetags.web_extras import rating_source_name

MIN_QUERY = 2
MAX_QUERY = 100
GROUP_LIMIT = 5
# Group order and headings; the keys are the ``SearchHit.kind`` values.
GROUPS = (('tv', _('Shows')), ('anime', _('Anime')), ('movie', _('Movies')))
KIND_LABELS = {'tv': _('Show'), 'anime': _('Anime'), 'movie': _('Movie')}


@dataclass
class ResultRow:
    """One search hit prepared for display.

    Attributes:
        hit: The provider's search hit.
        poster: What the poster component reads (``pk``, ``poster_url``,
            ``rating``); a hit has no catalog title yet.
        kind_label: The translated kind: Show, Anime or Movie.
        title_id: The catalog title when the title is already known, else None.
        entry: The user's library entry for it, if any.
    """

    hit: SearchHit
    poster: dict
    kind_label: str
    title_id: int | None
    entry: LibraryEntry | None

    @property
    def name(self) -> str:
        """The name to show: provider data may lack a title or both names."""
        return self.hit.title or self.hit.original_title or gettext('Untitled')

    @property
    def fields(self) -> dict[str, str]:
        """The hidden fields the add picker sends."""
        return {'provider': self.hit.provider, 'external_id': self.hit.external_id}


@dataclass
class ResultGroup:
    """The rows of one kind.

    Attributes:
        kind: ``tv``, ``anime`` or ``movie``.
        title: The translated group heading.
        count: How many hits the group has in all.
        rows: The rows shown.
        more_url: URL that shows the whole group, empty when nothing is hidden.
    """

    kind: str
    title: str
    count: int
    rows: list[ResultRow]
    more_url: str


def _search_url(**params: str) -> str:
    """Return the search URL with ``params`` as the query string."""
    return f'{reverse("search")}?{urlencode(params)}'


def _rows(user, hits: list[SearchHit]) -> list[ResultRow]:
    """Attach the catalog title and the user's library entry to every hit."""
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
                    'rating': None,
                },
                kind_label=str(KIND_LABELS[hit.kind]),
                title_id=title_id,
                entry=entries.get(title_id),
            )
        )
    return rows


def _groups(rows: list[ResultRow], query: str, expanded: str) -> list[ResultGroup]:
    """Group rows as Shows, Anime and Movies; empty groups are left out."""
    groups = []
    for kind, title in GROUPS:
        members = [row for row in rows if row.hit.kind == kind]
        if not members:
            continue
        hidden = kind != expanded and len(members) > GROUP_LIMIT
        groups.append(
            ResultGroup(
                kind=kind,
                title=str(title),
                count=len(members),
                rows=members[:GROUP_LIMIT] if hidden else members,
                more_url=_search_url(q=query, all=kind) if hidden else '',
            )
        )
    return groups


def _is_fragment_request(request: HttpRequest) -> bool:
    """Tell whether only the results block is wanted.

    HTMX restoring a page from history asks with ``HX-Request`` too but needs
    the whole page.
    """
    return request.headers.get('HX-Request') == 'true' and not request.headers.get(
        'HX-History-Restore-Request'
    )


@vary_on_headers('HX-Request', 'HX-History-Restore-Request')
def search_page(request: HttpRequest) -> HttpResponse:
    """Render the search page, or only its results block for an HTMX request.

    ``q`` is the query (at most 100 characters; fewer than 2 call no provider)
    and ``all`` one of ``tv``, ``anime`` or ``movie`` shows that whole group.
    """
    query = request.GET.get('q', '').strip()[:MAX_QUERY].strip()
    expanded = request.GET.get('all', '')
    if expanded not in KIND_LABELS:
        expanded = ''
    context: dict = {'q': query, 'state': 'idle'}
    if len(query) == 1:
        context['state'] = 'typing'
    elif query:
        clients = shared_clients()
        results = search(
            query,
            request.user.profile.metadata_language,
            tmdb=clients.tmdb,
            anilist=clients.anilist,
        )
        rows = _rows(request.user, results.hits)
        context['partial'] = bool(results.failed)
        context['failed_names'] = ', '.join(
            rating_source_name(name) for name in results.failed
        )
        if len(set(results.failed)) >= 2:
            context['state'] = 'error'
            context['retry_url'] = _search_url(q=query)
        elif not rows:
            context['state'] = 'none'
        else:
            context.update(
                state='results',
                total=len(rows),
                groups=_groups(rows, query, expanded),
            )
    template = 'web/_search_results.html' if _is_fragment_request(request) else None
    return render(request, template or 'web/search.html', context)


def _add_fragment(request: HttpRequest, **context) -> HttpResponse:
    """Render the control that replaces the add picker."""
    return render(request, 'web/_add_result.html', context)


def _failure_text(error: str) -> str:
    """Return the explanation shown for a failed request with ``error``."""
    if error == AddRequest.Error.TIMEOUT:
        return gettext('This took too long. Try again.')
    if error == AddRequest.Error.PROVIDER:
        return gettext("The metadata provider didn't respond. Try again.")
    return gettext('Something went wrong. Try again.')


def _request_fragment(request: HttpRequest, add_request: AddRequest) -> HttpResponse:
    """Render the control for an add request in whatever state it is."""
    if add_request.state == AddRequest.State.PENDING:
        return _add_fragment(request, state='pending', add_request=add_request)
    if add_request.state == AddRequest.State.DONE:
        return _add_fragment(
            request, state='added', title_id=add_request.title_id, entry=None
        )
    return _add_fragment(
        request,
        state='failed',
        text=_failure_text(add_request.error),
        provider=add_request.provider,
        external_id=add_request.external_id,
        status=add_request.status,
    )


@require_POST
def search_add(request: HttpRequest) -> HttpResponse:
    """Add a title to the library, or start adding it in the background.

    The browser sends only ``provider``, ``external_id`` and ``status``; any
    other field is ignored. Invalid values are a 400. Every handled outcome,
    including too many pending requests, is a 200 fragment.
    """
    provider = request.POST.get('provider', '')
    external_id = request.POST.get('external_id', '')
    status = request.POST.get('status', '')
    try:
        result = request_add(request.user, provider, external_id, status)
    except ValueError:
        return HttpResponseBadRequest('Invalid provider, id or status')
    except TooManyPending:
        return _add_fragment(
            request,
            state='failed',
            text=gettext('%(count)s titles are already being added. Try again soon.')
            % {'count': MAX_PENDING},
            provider=provider,
            external_id=external_id,
            status=status,
        )
    if isinstance(result, LibraryEntry):
        return _add_fragment(
            request, state='added', title_id=result.title_id, entry=result
        )
    return _request_fragment(request, result)


def add_status(request: HttpRequest, pk: int) -> HttpResponse:
    """Render the current state of one of the user's add requests.

    A pending request older than the timeout fails right here, so the page
    does not wait for the daily cleanup. Another user's request is a 404.
    """
    expire_stale(request.user, timezone.now())
    add_request = get_object_or_404(AddRequest.objects.for_user(request.user), pk=pk)
    return _request_fragment(request, add_request)
