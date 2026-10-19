"""Title page views: the page, its library panel actions and the retry button."""

from dataclasses import dataclass
from datetime import date, timedelta

from catalog import tasks
from catalog.models import Episode, Season, Title
from catalog.translations import FALLBACK_LANGUAGE
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from tracking import queries, services
from tracking.models import LibraryEntry

# A title that has not synced this long after it was created gets a retry button.
SYNC_TIMEOUT = timedelta(minutes=15)
NOTE_MAX_LENGTH = 5000
REMOVE = 'remove'

SYNCING = 'syncing'
STUCK = 'stuck'
READY = 'ready'

# Anime entries that are not a numbered season are named by their kind.
SEASON_KIND_LABELS = {
    Season.Kind.OVA: _('OVA'),
    Season.Kind.ONA: _('ONA'),
    Season.Kind.MOVIE: _('Movie'),
    Season.Kind.SPECIAL: _('Special'),
}


@dataclass(frozen=True)
class RowView:
    """An episode row: the tracking state plus how to number it.

    Attributes:
        row: The episode with its aired flag and viewing count.
        code: The main number, such as ``S1E3``, ``4`` (absolute) or ``OVA 1``.
        code2: The season code shown small beside an absolute number, or ''.
        check: ``watched``, ``unwatched`` or ``unaired``.
        days: Days until a dated episode airs (0 once it has aired or when undated).
    """

    row: queries.EpisodeRow
    code: str
    code2: str
    check: str
    days: int


@dataclass(frozen=True)
class GroupView:
    """A season (a franchise entry for anime) of the episode list.

    Attributes:
        group: The season with its counts.
        rows: The episode rows.
        label: Heading and chip text.
        tag: A kind tag for single-episode anime entries, or ''.
        check: ``watched``, ``partly``, ``unwatched`` or ``unaired``.
        note: A one-line remark about missing episode data, or ''.
        open: Whether the accordion starts open.
    """

    group: queries.GroupState
    rows: list[RowView]
    label: str
    tag: str
    check: str
    note: str
    open: bool


def _numbering(title: Title, season: Season, episode: Episode) -> tuple[str, str]:
    """Return the main and the small number of an episode row."""
    if season.kind == Season.Kind.SPECIAL:
        return gettext('SP %(number)s') % {'number': episode.number}, ''
    if season.kind == Season.Kind.OVA:
        return gettext('OVA %(number)s') % {'number': episode.number}, ''
    if season.kind == Season.Kind.MOVIE:
        return gettext('Movie'), ''
    code = f'S{season.number}E{episode.number}'
    continued = (
        title.kind == Title.Kind.ANIME
        and not season.is_extra
        and episode.absolute_number not in (None, episode.number)
    )
    if continued:
        return str(episode.absolute_number), code
    return code, ''


def _label(title: Title, season: Season) -> str:
    """Return the heading of a season, or of an anime franchise entry."""
    if season.kind == Season.Kind.SPECIAL and title.kind == Title.Kind.TV:
        return gettext('Specials')
    if title.kind == Title.Kind.ANIME and season.name:
        return season.name
    if season.kind in (Season.Kind.TV, Season.Kind.ONA):
        return gettext('Season %(number)s') % {'number': season.number}
    return str(SEASON_KIND_LABELS[season.kind])


def _group_check(group: queries.GroupState) -> str:
    """Return the state icon of a group: all aired episodes watched, some, or none."""
    if group.aired_total == 0:
        return 'unaired'
    if group.watched >= group.aired_total:
        return 'watched'
    return 'partly' if group.watched else 'unwatched'


def _note(season: Season, rows: list[RowView]) -> str:
    """Return the remark for a season whose episode data is missing."""
    if season.episode_source == Season.EpisodeSource.ANILIST:
        return gettext('Episode titles are not available for this entry')
    if season.episode_source == Season.EpisodeSource.NONE and not rows:
        return gettext('Episodes are not known yet')
    return ''


def _group_views(
    title: Title, state: queries.TitlePageState, today: date
) -> list[GroupView]:
    """Prepare the episode list: one view per season, with the first to open."""
    opening = state.next_episode.season_id if state.next_episode else None
    if not any(g.season.pk == opening for g in state.groups):
        opening = state.groups[0].season.pk if state.groups else None
    views = []
    for group in state.groups:
        season = group.season
        rows = []
        for row in group.rows:
            code, code2 = _numbering(title, season, row.episode)
            airs = row.episode.air_date
            rows.append(
                RowView(
                    row=row,
                    code=code,
                    code2=code2,
                    check='watched'
                    if row.views
                    else 'unwatched'
                    if row.aired
                    else 'unaired',
                    days=(airs - today).days if airs and not row.aired else 0,
                )
            )
        single = title.kind == Title.Kind.ANIME and len(rows) == 1
        kind_tag = season.kind not in (Season.Kind.TV, Season.Kind.ONA)
        views.append(
            GroupView(
                group=group,
                rows=rows,
                label=_label(title, season),
                tag=str(SEASON_KIND_LABELS[season.kind]) if single and kind_tag else '',
                check=_group_check(group),
                note=_note(season, rows),
                open=season.pk == opening,
            )
        )
    return views


def _sync_state(title: Title) -> str:
    """Return whether the page can show content, is waiting for it, or gave up."""
    if title.synced_at is not None:
        return READY
    waiting = timezone.now() - title.created_at < SYNC_TIMEOUT
    if waiting or tasks.claimed(tasks.refresh_key(title)):
        return SYNCING
    return STUCK


def _context(request: HttpRequest, title: Title) -> dict:
    """Build the context of the page body for the signed-in user."""
    context = {'title': title, 'sync_state': _sync_state(title), 'entry': None}
    if context['sync_state'] != READY:
        return context
    today = timezone.localdate()
    language = request.user.profile.metadata_language
    state = queries.title_page_state(request.user, title, today, language)
    context.update(entry=state.entry, progress=state.progress)
    if title.kind != Title.Kind.MOVIE:
        groups = _group_views(title, state, today)
        context.update(
            has_episodes=True,
            groups=groups,
            multi=len(groups) > 1,
            open_ids=[g.group.season.pk for g in groups if g.open],
            chips=groups if title.kind == Title.Kind.ANIME and len(groups) > 1 else [],
        )
    return context


def _queue_background_jobs(request: HttpRequest, title: Title) -> None:
    """Ask for a sync and for the user's language; both are best effort."""
    tasks.enqueue_sync_once(title)
    language = request.user.profile.metadata_language
    translated = any(t.language == language for t in title.translations.all())
    if title.synced_at and language != FALLBACK_LANGUAGE and not translated:
        tasks.enqueue_language_once(title, language)


def title_page(request: HttpRequest, pk: int) -> HttpResponse:
    """Render the title page.

    Args:
        request: The request.
        pk: Primary key of the title.

    Returns:
        The page, or 404 for an unknown title.
    """
    title = get_object_or_404(Title.objects.prefetch_related('translations'), pk=pk)
    _queue_background_jobs(request, title)
    return render(request, 'web/title.html', _context(request, title))


@require_POST
def title_refresh(request: HttpRequest, pk: int) -> HttpResponse:
    """Queue a forced sync (at most one a minute) and return the page body.

    Args:
        request: The request.
        pk: Primary key of the title.

    Returns:
        The body of the page: the syncing block while the sync is pending.
    """
    title = get_object_or_404(Title.objects.prefetch_related('translations'), pk=pk)
    tasks.enqueue_refresh_once(title)
    return render(request, 'web/_title_body.html', _context(request, title))


def _panel(request: HttpRequest, title: Title, entry: LibraryEntry | None):
    """Render the library panel for ``entry``."""
    context = {'title': title, 'entry': entry}
    return render(request, 'web/_library_panel.html', context)


@require_POST
def title_status(request: HttpRequest, pk: int) -> HttpResponse:
    """Set the status of the user's entry, or remove it with ``status=remove``.

    Completing a series marks its episodes, so the response carries the episode
    list too (an out-of-band swap).

    Args:
        request: The request with ``status``.
        pk: Primary key of the title.

    Returns:
        The library panel; 400 for an unknown status.
    """
    title = get_object_or_404(Title.objects.prefetch_related('translations'), pk=pk)
    status = request.POST.get('status', '')
    if status != REMOVE and status not in LibraryEntry.Status.values:
        return HttpResponseBadRequest('Unknown status')
    entry = services.set_status(
        request.user, title, None if status == REMOVE else status
    )
    context = _context(request, title)
    context.update(entry=entry, oob_episodes=context.get('has_episodes', False))
    return render(request, 'web/_library_panel.html', context)


@require_POST
def title_score(request: HttpRequest, pk: int) -> HttpResponse:
    """Rate the title from 0 (not rated) to 10.

    Args:
        request: The request with ``score``.
        pk: Primary key of the title.

    Returns:
        The library panel; 400 for a bad score, 409 when the title is not in the
        library.
    """
    title = get_object_or_404(Title, pk=pk)
    value = request.POST.get('score', '')
    if not (value.isascii() and value.isdecimal() and int(value) <= 10):
        return HttpResponseBadRequest('The score must be from 0 to 10')
    try:
        entry = services.set_score(request.user, title, int(value))
    except services.NotInLibrary:
        return HttpResponse('The title is not in your library', status=409)
    return _panel(request, title, entry)


@require_POST
def title_note(request: HttpRequest, pk: int) -> HttpResponse:
    """Save the user's private note.

    Args:
        request: The request with ``note``.
        pk: Primary key of the title.

    Returns:
        The library panel; 400 for a missing or too long note, 409 when the title
        is not in the library.
    """
    title = get_object_or_404(Title, pk=pk)
    note = request.POST.get('note')
    # Browsers send line breaks as CRLF but count them as one character.
    note = note.replace('\r\n', '\n') if note is not None else None
    if note is None or len(note) > NOTE_MAX_LENGTH:
        return HttpResponseBadRequest('The note is missing or too long')
    try:
        entry = services.set_note(request.user, title, note)
    except services.NotInLibrary:
        return HttpResponse('The title is not in your library', status=409)
    return _panel(request, title, entry)
