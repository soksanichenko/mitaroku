"""Watch actions of the title page: episodes, seasons, the whole title, movies.

Every action is a POST for ``request.user`` that carries the wanted state
(``watched=1`` or ``watched=0``), never "toggle", so sending it twice changes
nothing. Each response is built from one ``title_page_state`` computation: a main
fragment for the htmx target plus out-of-band parts (``web/_watch_result.html``).
"""

from catalog.models import Episode, Season, Title
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from tracking import services
from tracking.models import LibraryEntry
from web.views.titles import page_context

NOT_READY = 'The title is not ready yet'
NOT_AIRED = 'The episode has not aired yet'


def _wanted(request: HttpRequest) -> bool | None:
    """Return the wanted state of the POST (``watched=1|0``), or None if invalid."""
    return {'1': True, '0': False}.get(request.POST.get('watched', ''))


def _bad_watched() -> HttpResponse:
    """Return the 400 answer for a missing or invalid ``watched``."""
    return HttpResponseBadRequest('watched must be 1 or 0')


def _status(user, title: Title) -> str | None:
    """Return the status of the user's entry for ``title``, or None without one."""
    return (
        LibraryEntry.objects.for_user(user)
        .filter(title=title)
        .values_list('status', flat=True)
        .first()
    )


def _respond(
    request: HttpRequest,
    title: Title,
    before: str | None,
    mode: str,
    season: Season | None = None,
    hint: bool = False,
) -> HttpResponse:
    """Render the result of an action from the freshly computed page state.

    Args:
        request: The request.
        title: The title acted on.
        before: The entry status before the action (None without an entry).
        mode: ``group`` (the season's fragment plus the out-of-band parts),
            ``episodes`` (the whole episode section) or ``movie`` (the movie block).
        season: The season whose group is the main fragment (``group`` mode).
        hint: Whether to arm the one-time "up to here" tip.

    Returns:
        The HTML fragments; the library panel is added out of band only when
        the status of the entry changed.
    """
    context = page_context(request, title)
    entry = context['entry']
    context.update(
        mode=mode, hint=hint, panel=(entry.status if entry else None) != before
    )
    if mode == 'group':
        groups = context['groups']
        context['group'] = next(g for g in groups if g.group.season.pk == season.pk)
        # "Up to here" also marks the earlier main seasons.
        context['earlier'] = [
            g
            for g in groups
            if not g.group.season.is_extra
            and not season.is_extra
            and g.group.season.number < season.number
        ]
    return render(request, 'web/_watch_result.html', context)


@require_POST
def episode_watch(request: HttpRequest, pk: int) -> HttpResponse:
    """Set one episode watched or unwatched, or add one more viewing.

    Args:
        request: The request with ``watched`` (``1`` or ``0``) and, with
            ``watched=1``, an optional ``again=1`` that records another viewing.
        pk: Primary key of the episode.

    Returns:
        The group fragment (``section#grp-<season pk>``) plus the out-of-band
        ``#title-progress``, ``#continue-bar``, ``#mark-up-to-hint`` (armed after
        a first mark) and, if the status changed, ``#library-panel``; 400 for a
        bad ``watched``, 409 for an episode that has not aired.
    """
    episode = get_object_or_404(Episode.objects.select_related('season__title'), pk=pk)
    watched = _wanted(request)
    if watched is None:
        return _bad_watched()
    title = episode.season.title
    if title.synced_at is None:
        return HttpResponse(NOT_READY, status=409)
    again = watched and request.POST.get('again') == '1'
    before = _status(request.user, title)
    try:
        services.set_episode_watched(request.user, episode, watched, again=again)
    except services.NotAired:
        return HttpResponse(NOT_AIRED, status=409)
    return _respond(
        request, title, before, 'group', episode.season, hint=watched and not again
    )


@require_POST
def episode_up_to(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark an episode and every earlier one as watched.

    Args:
        request: The request.
        pk: Primary key of the last episode to mark.

    Returns:
        The episode's group fragment, the earlier main seasons as out-of-band
        group fragments and the same out-of-band parts as ``episode_watch``
        (without the tip); 409 for an episode that has not aired.
    """
    episode = get_object_or_404(Episode.objects.select_related('season__title'), pk=pk)
    title = episode.season.title
    if title.synced_at is None:
        return HttpResponse(NOT_READY, status=409)
    if not services.is_aired(episode, timezone.localdate()):
        return HttpResponse(NOT_AIRED, status=409)
    before = _status(request.user, title)
    services.mark_up_to(request.user, episode)
    return _respond(request, title, before, 'group', episode.season)


@require_POST
def season_watch(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark every aired episode of a season watched, or clear the season.

    Args:
        request: The request with ``watched`` (``1`` or ``0``).
        pk: Primary key of the season.

    Returns:
        The season's group fragment plus the out-of-band parts of
        ``episode_watch``; 400 for a bad ``watched``.
    """
    season = get_object_or_404(Season.objects.select_related('title'), pk=pk)
    watched = _wanted(request)
    if watched is None:
        return _bad_watched()
    title = season.title
    if title.synced_at is None:
        return HttpResponse(NOT_READY, status=409)
    before = _status(request.user, title)
    services.set_season_watched(request.user, season, watched)
    return _respond(request, title, before, 'group', season)


@require_POST
def title_watch_all(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark every aired episode of the show watched, or clear all of them.

    Args:
        request: The request with ``watched`` (``1`` or ``0``).
        pk: Primary key of a series or anime title.

    Returns:
        The whole episode section (``section#episodes``, which holds the progress,
        the continue bar and the tip) plus ``#library-panel`` out of band when the
        status changed; 404 for a movie, 400 for a bad ``watched``.
    """
    title = get_object_or_404(Title.objects.exclude(kind=Title.Kind.MOVIE), pk=pk)
    watched = _wanted(request)
    if watched is None:
        return _bad_watched()
    if title.synced_at is None:
        return HttpResponse(NOT_READY, status=409)
    before = _status(request.user, title)
    services.set_title_watched(request.user, title, watched)
    return _respond(request, title, before, 'episodes')


@require_POST
def title_movie_watch(request: HttpRequest, pk: int) -> HttpResponse:
    """Set a movie watched or unwatched, or add one more viewing.

    Args:
        request: The request with ``watched`` (``1`` or ``0``) and, with
            ``watched=1``, an optional ``again=1``.
        pk: Primary key of a movie.

    Returns:
        The movie block (``section#movie-watch``) plus ``#library-panel`` out of
        band when the status changed (watching completes the movie); 404 for a
        title that is not a movie, 409 for a movie that is not released.
    """
    title = get_object_or_404(Title, pk=pk, kind=Title.Kind.MOVIE)
    watched = _wanted(request)
    if watched is None:
        return _bad_watched()
    if title.synced_at is None:
        return HttpResponse(NOT_READY, status=409)
    before = _status(request.user, title)
    try:
        services.set_movie_watched(
            request.user,
            title,
            watched,
            again=watched and request.POST.get('again') == '1',
        )
    except services.NotAired:
        return HttpResponse('The movie has not been released yet', status=409)
    return _respond(request, title, before, 'movie')


@require_POST
def title_continue(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark the next unwatched main episode as watched when it has aired.

    The Library cards post here too, from any page, so the response is made of
    parts that a page may or may not contain: the htmx target decides which one
    is swapped in and the out-of-band parts that find no target are ignored.

    Args:
        request: The request (no fields).
        pk: Primary key of a series or anime title.

    Returns:
        The group fragment of the marked episode (``section#grp-<season pk>``),
        then out of band ``#title-progress``, ``#continue-bar``,
        ``#mark-up-to-hint`` (armed) and, if the status changed,
        ``#library-panel``; 409 when there is no aired episode left to mark.
    """
    title = get_object_or_404(Title.objects.exclude(kind=Title.Kind.MOVIE), pk=pk)
    if title.synced_at is None:
        return HttpResponse(NOT_READY, status=409)
    before = _status(request.user, title)
    episode = services.mark_next(request.user, title)
    if episode is None:
        return HttpResponse('There is nothing to continue with', status=409)
    return _respond(request, title, before, 'group', episode.season, hint=True)
