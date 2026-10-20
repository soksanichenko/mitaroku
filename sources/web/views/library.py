"""Library views: the user's titles with filters, sorting and a "Continue" strip."""

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlencode

from catalog.models import EpisodeTranslation, Title
from catalog.translations import FALLBACK_LANGUAGE, localized_episode, localized_title
from django.db.models import Prefetch, prefetch_related_objects
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.vary import vary_on_headers
from tracking import queries
from tracking.models import LibraryEntry
from web.templatetags.web_extras import STATUS_LABELS, episode_code

PAGE_SIZE = 60
MAX_QUERY = 100
CONTINUE_LIMIT = 20
ALL = 'all'
DEFAULT_SORT = 'recent'
DEFAULT_VIEW = 'grid'

# The keys of TABS are ``Title.Kind`` values.
TABS = (
    (ALL, _('All')),
    (Title.Kind.TV, _('Shows')),
    (Title.Kind.ANIME, _('Anime')),
    (Title.Kind.MOVIE, _('Movies')),
)
STATUSES = (
    (ALL, _('All statuses')),
    *((value, label) for value, label in STATUS_LABELS.items()),
)
SORTS = (
    ('recent', _('Recently watched')),
    ('title', _('Title')),
    ('rating', _('Rating')),
    ('next', _('Next episode date')),
)
VIEWS = ('grid', 'list')

# Sort keys for what is missing: before every real moment (never watched, and the
# newest-first order puts it last) and after every real date (no next episode date).
NEVER = datetime.min.replace(tzinfo=UTC)
NO_DATE = date.max


@dataclass(frozen=True)
class Item:
    """A library row prepared for display.

    Attributes:
        row: The entry with its progress and next episode.
        name: The title in the user's metadata language (never empty).
        code: The code of the next episode, such as ``S1E3``, or ''.
        next_state: ``aired`` (the next episode can be watched), ``dated`` (it
            airs on a known day later), ``undated`` or ``none`` (nothing left).
    """

    row: queries.LibraryRow
    name: str
    code: str
    next_state: str


@dataclass(frozen=True)
class ContinueCard:
    """A card of the "Continue watching" strip.

    Attributes:
        row: The Watching entry with its progress and next episode.
        name: The title in the user's metadata language (never empty).
        code: The code of the next episode, such as ``S1E3``.
        episode_name: The name of the next episode ('' when unknown).
        aired: Whether the next episode can be marked now (see ``queries.has_aired``).
        today: Whether it aired today (it is dated today).
    """

    row: queries.LibraryRow
    name: str
    code: str
    episode_name: str
    aired: bool
    today: bool


def _choice(value: str, allowed: Collection[str], default: str) -> str:
    """Return ``value`` when it is one of ``allowed``, else ``default``."""
    return value if value in allowed else default


def _page(value: str) -> int:
    """Return a 1-based page count; anything but a positive integer is 1."""
    if value.isascii() and value.isdecimal() and len(value) <= 6:
        return max(int(value), 1)
    return 1


def _in_tab(item: Item, tab: str) -> bool:
    """Tell whether the title of ``item`` is of the kind the tab shows."""
    return tab in (ALL, item.row.entry.title.kind)


def _in_status(item: Item, status: str) -> bool:
    """Tell whether the entry of ``item`` has the status the chip shows."""
    return status in (ALL, item.row.entry.status)


def _name(row: queries.LibraryRow, language: str) -> str:
    """Return the title of a row in ``language``, or "Untitled"."""
    return localized_title(row.entry.title, language)[0] or gettext('Untitled')


def _items(rows: list[queries.LibraryRow], language: str, today: date) -> list[Item]:
    """Prepare the rows for display."""
    items = []
    for row in rows:
        episode = row.next
        title = row.entry.title
        if episode is None:
            state = 'none'
        elif queries.episode_aired(episode, title, today):
            state = 'aired'
        elif episode.air_date is None:
            state = 'undated'
        else:
            state = 'dated'
        items.append(
            Item(
                row=row,
                name=_name(row, language),
                code=episode_code(episode, title.kind) if episode else '',
                next_state=state,
            )
        )
    return items


def _rating(item: Item):
    """Return a sort key: rated titles by rating, best first; unrated last."""
    rating = item.row.entry.title.rating
    return (rating is None, -rating if rating is not None else 0)


def _next_date(item: Item):
    """Return a sort key: the next air date; no episode or no date comes last."""
    episode = item.row.next
    return episode.air_date if episode and episode.air_date else NO_DATE


def _sorted(items: list[Item], sort: str) -> list[Item]:
    """Return ``items`` in the order of ``sort``.

    ``recent`` is the last viewing, newest first, then the date the title was
    added, newest first; ``title`` is case-insensitive; ``rating`` puts unrated
    titles last and ``next`` the titles without a dated next episode. Ties go by
    title (every sort is stable, so the title order is the base of them all).
    """
    ordered = sorted(items, key=lambda i: (i.name.casefold(), i.row.entry.pk))
    if sort == 'recent':
        ordered.sort(key=lambda i: i.row.entry.added_at, reverse=True)
        ordered.sort(key=lambda i: i.row.last_watched_at or NEVER, reverse=True)
    elif sort == 'rating':
        ordered.sort(key=_rating)
    elif sort == 'next':
        ordered.sort(key=_next_date)
    return ordered


def _choices(options, counts: Counter, selected: str) -> list[dict]:
    """Return radio choices with their labels, counts and checked flag."""
    return [
        {
            'value': str(value),
            'label': label,
            'count': counts[str(value)],
            'checked': str(value) == selected,
        }
        for value, label in options
    ]


def _library_url(**params: str | int) -> str:
    """Return the library URL with ``params`` as the query string."""
    return f'{reverse("library")}?{urlencode(params)}'


def _is_fragment_request(request: HttpRequest) -> bool:
    """Tell whether only a fragment is wanted.

    HTMX restoring a page from history asks with ``HX-Request`` too but needs
    the whole page.
    """
    return request.headers.get('HX-Request') == 'true' and not request.headers.get(
        'HX-History-Restore-Request'
    )


def _continue_cards(user, language: str, today: date) -> list[ContinueCard]:
    """Return the cards of the "Continue watching" strip."""
    rows = queries.continue_watching(user, today=today, language=language)
    rows = rows[:CONTINUE_LIMIT]
    prefetch_related_objects(
        [row.next for row in rows],
        Prefetch(
            'translations',
            queryset=EpisodeTranslation.objects.filter(
                language__in={language, FALLBACK_LANGUAGE}
            ),
        ),
    )
    return [
        ContinueCard(
            row=row,
            name=_name(row, language),
            code=episode_code(row.next, row.entry.title.kind),
            episode_name=localized_episode(row.next, language)[0],
            aired=queries.episode_aired(row.next, row.entry.title, today),
            today=row.next.air_date == today,
        )
        for row in rows
    ]


@vary_on_headers('HX-Request', 'HX-History-Restore-Request')
def library(request: HttpRequest) -> HttpResponse:
    """Render the library page, or only the part of it that HTMX asks for.

    Query parameters (unknown values fall back to the defaults): ``tab``
    (``all``, ``tv``, ``anime``, ``movie``), ``status`` (``all`` or a status),
    ``q`` (at most 100 characters), ``sort`` (``recent``, ``title``, ``rating``,
    ``next``), ``view`` (``grid``, ``list``), ``page`` (60 titles a page) and
    ``pages`` (how many pages the page has loaded, sent by the refresh).

    An HTMX request gets only the results block, with the first ``pages`` pages.
    With ``page`` above 1 it gets just the titles of that page, to append to the
    list. A plain request with a
    ``page`` shows every title up to that page, so "Show more" works without
    JavaScript. ``fragment=continue`` asks HTMX for the "Continue watching" strip
    alone.

    Args:
        request: The request of the signed-in user.

    Returns:
        The page or the fragment.
    """
    params = request.GET
    tab = _choice(params.get('tab', ''), {str(v) for v, _l in TABS}, ALL)
    status = _choice(params.get('status', ''), {str(v) for v, _l in STATUSES}, ALL)
    query = params.get('q', '').strip()[:MAX_QUERY].strip()
    sort = _choice(params.get('sort', ''), {v for v, _l in SORTS}, DEFAULT_SORT)
    view = _choice(params.get('view', ''), VIEWS, DEFAULT_VIEW)
    page = _page(params.get('page', ''))
    pages = _page(params.get('pages', ''))
    fragment = _is_fragment_request(request)

    user = request.user
    language = user.profile.metadata_language
    today = timezone.localdate()
    if fragment and params.get('fragment') == 'continue':
        context = {'cards': _continue_cards(user, language, today)}
        return render(request, 'web/_continue_watching.html', context)

    total = LibraryEntry.objects.for_user(user).count()
    rows = queries.library_rows(user, query=query, language=language)
    items = _items(rows, language, today)
    # Each chip row counts the titles that match every other filter.
    in_status = [i for i in items if _in_status(i, status)]
    in_tab = [i for i in items if _in_tab(i, tab)]
    tab_counts = Counter(i.row.entry.title.kind for i in in_status)
    tab_counts[ALL] = len(in_status)
    status_counts = Counter(i.row.entry.status for i in in_tab)
    status_counts[ALL] = len(in_tab)
    shown = _sorted([i for i in in_status if _in_tab(i, tab)], sort)

    # A refresh sends ``pages`` (how many pages the user has loaded) and gets all of
    # them again; "Show more" (HTMX, ``page`` above 1) gets only its own page; any
    # other request starts from page 1 or, without HTMX, shows every page up to
    # ``page``. The count never exceeds the pages that exist.
    last = max(1, -(-len(shown) // PAGE_SIZE))
    append = fragment and page > 1
    loaded = min(page if append else max(page, pages), last)
    end = (page if append else loaded) * PAGE_SIZE
    start = end - PAGE_SIZE if append else 0
    context = {
        'total': total,
        'items': shown[start:end],
        'append': append,
        'oob': fragment,
        'pages': loaded,
        'more_url': _library_url(
            tab=tab, status=status, q=query, sort=sort, view=view, page=loaded + 1
        )
        if len(shown) > end
        else '',
        'q': query,
        'sort': sort,
        'view': view,
        'tabs': _choices(TABS, tab_counts, tab),
        'statuses': _choices(STATUSES, status_counts, status),
        'sorts': SORTS,
    }
    if fragment:
        return render(request, 'web/_library_results.html', context)
    context['cards'] = _continue_cards(user, language, today)
    return render(request, 'web/library.html', context)
