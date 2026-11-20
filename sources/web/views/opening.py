"""Open a title by its provider id, creating it in the background if needed."""

from catalog import tasks
from catalog.models import ExternalId
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET
from tracking.adding import EXTERNAL_ID, PROVIDER_KIND

POLL_TARGET = 'opening'
POLL_STATE = 'loading'
FAILED_STATE = 'error'


def _no_store(response: HttpResponse) -> HttpResponse:
    """Mark ``response`` as never to be cached: it depends on a running job."""
    patch_cache_control(response, private=True, no_store=True)
    return response


@require_GET
def open_title(request: HttpRequest, provider: str, external_id: int) -> HttpResponse:
    """Send the browser to a title, creating the title first when it is new.

    The browser supplies only a provider and an id, and the view reads and
    writes no user data: the title only enters the shared catalog. The page
    polls this URL while the job runs. A poll never queues a job, so it can
    tell that the claim is gone and the job failed.

    Args:
        request: The request.
        provider: ``tmdb_movie``, ``tmdb_tv`` or ``anilist``.
        external_id: The id at that provider, at most 10 digits.

    Returns:
        A redirect (an ``HX-Redirect`` for an HTMX request) when the title
        exists, otherwise the loading or the error state.
    """
    # "007" and "7" are the same provider id.
    normalised = str(external_id)
    if provider not in PROVIDER_KIND or not EXTERNAL_ID.fullmatch(normalised):
        raise Http404('Unknown provider or id')
    htmx = request.headers.get('HX-Request') == 'true'
    # Only the page's own poll (it targets #opening) must not queue a job: a link
    # that HTMX boosts or swaps in asks for the same URL and does need one.
    poll = htmx and request.headers.get('HX-Target') == POLL_TARGET
    link = ExternalId.objects.filter(provider=provider, external_id=normalised).first()
    if link is not None:
        target = reverse('title', args=[link.title_id])
        if htmx:
            response = HttpResponse(headers={'HX-Redirect': target})
            return _no_store(response)
        return _no_store(redirect(target))
    if poll:
        waiting = tasks.claimed(tasks.open_key(provider, normalised))
    else:
        # A queue that is down costs the job, not the page: that is the error state.
        waiting = tasks.enqueue_open_once(provider, normalised) or tasks.claimed(
            tasks.open_key(provider, normalised)
        )
    context = {
        'state': POLL_STATE if waiting else FAILED_STATE,
        'poll_url': reverse('open_title', args=[provider, normalised]),
    }
    return _no_store(render(request, 'web/opening.html', context))
