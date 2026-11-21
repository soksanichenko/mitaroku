"""Search and add views."""

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.decorators.vary import vary_on_headers
from providers.search import SearchResults, search
from redis.exceptions import RedisError
from tracking.adding import (
    MAX_PENDING,
    TooManyPending,
    expire_stale,
    request_add,
)
from tracking.models import AddRequest, LibraryEntry
from web.clients import shared_clients
from web.templatetags.web_extras import rating_source_name
from web.views.hits import KIND_LABELS, ResultRow, decorate, is_fragment_request

logger = logging.getLogger(__name__)

MIN_QUERY = 2
MAX_QUERY = 100
GROUP_LIMIT = 5
# Group order and headings; the keys are the ``SearchHit.kind`` values.
GROUPS = (('tv', _('Shows')), ('anime', _('Anime')), ('movie', _('Movies')))


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
        try:
            results = search(
                query,
                request.user.profile.metadata_language,
                tmdb=clients.tmdb,
                anilist=clients.anilist,
            )
        except RedisError as error:
            # The provider layer keeps its rate limits and cache in Redis.
            logger.warning('Search failed: %s', type(error).__name__)
            results = SearchResults(hits=[], failed=['tmdb', 'anilist'])
        rows = decorate(request.user, results.hits)
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
    template = 'web/_search_results.html' if is_fragment_request(request) else None
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
    if error == AddRequest.Error.UNAVAILABLE:
        return gettext('The service is busy, try again in a moment.')
    return gettext('Something went wrong. Try again.')


def _request_fragment(request: HttpRequest, add_request: AddRequest) -> HttpResponse:
    """Render the control for an add request in whatever state it is."""
    if add_request.state == AddRequest.State.PENDING:
        return _add_fragment(request, state='pending', add_request=add_request)
    if add_request.state == AddRequest.State.DONE:
        entry = (
            LibraryEntry.objects.for_user(request.user)
            .filter(title_id=add_request.title_id)
            .first()
        )
        return _add_fragment(
            request, state='added', title_id=add_request.title_id, entry=entry
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
