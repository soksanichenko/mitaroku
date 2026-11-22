"""Notifications view: what happened to the titles the user follows."""

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode, urlsplit

from catalog.models import Episode, EpisodeTranslation, Title, TitleTranslation
from catalog.translations import FALLBACK_LANGUAGE, localized_episode
from django.db.models import Prefetch, prefetch_related_objects
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.decorators.vary import vary_on_headers
from notifications.models import Notification
from notifications.moments import local_day
from web.templatetags.web_extras import episode_code

PAGE_SIZE = 30
# A page number this large is not a place anyone has reached; it bounds the work a
# plain (no JavaScript) request for "everything up to page N" can ask for.
MAX_PAGE = 1000

# The icon is the key of the SVG the row template draws.
KINDS = {
    Notification.Kind.EPISODE_AIRED: ('aired', _('New episode')),
    Notification.Kind.PREMIERE: ('premiere', _('Premiere')),
    Notification.Kind.MOVIE_RELEASED: ('released', _('Released')),
    Notification.Kind.RELEASE_ANNOUNCED: ('announced', _('Date announced')),
    Notification.Kind.RELEASE_CHANGED: ('changed', _('Date changed')),
}
EPISODE_KINDS = (Notification.Kind.EPISODE_AIRED, Notification.Kind.PREMIERE)


@dataclass(frozen=True)
class Row:
    """A notification prepared for display.

    Attributes:
        pk: The primary key of the notification, for its open and read forms.
        title: The title the notification is about.
        icon: Which kind icon to draw (``aired``, ``premiere``, ``released``,
            ``announced`` or ``changed``).
        label: The translated kind label.
        text: The line under the title: episode codes and name, or the release
            sentence; '' when there is nothing to say.
        old: The date a release moved away from, or None.
        new: The release date, or None.
        unread: Whether the notification has not been read.
    """

    pk: int
    title: Title
    icon: str
    label: str
    text: str
    old: date | None
    new: date | None
    unread: bool


@dataclass(frozen=True)
class Group:
    """The notifications of one local day.

    Attributes:
        day: The user's local day.
        key: The ISO date, used in element ids.
        label: ``Today`` or ``Yesterday`` (translated), or '' for an older day.
        rows: The rows, newest first.
        continued: True when the group continues the last day already on the
            page, so a Load more answer adds its rows to the existing list.
    """

    day: date
    key: str
    label: str
    rows: list[Row]
    continued: bool = False


def _list_url(params: dict[str, int]) -> str:
    """Return the URL of the list with the given query parameters."""
    url = reverse('notifications')
    return f'{url}?{urlencode(params)}' if params else url


def _page(value: str) -> int:
    """Return a 1-based page number; anything but a positive integer is 1."""
    if value.isascii() and value.isdecimal() and len(value) <= 6:
        return min(max(int(value), 1), MAX_PAGE)
    return 1


def _is_fragment_request(request: HttpRequest) -> bool:
    """Tell whether only a fragment is wanted.

    HTMX restoring a page from history asks with ``HX-Request`` too but needs
    the whole page.
    """
    return request.headers.get('HX-Request') == 'true' and not request.headers.get(
        'HX-History-Restore-Request'
    )


def _payload_date(payload: dict, key: str) -> date | None:
    """Return the ISO date stored under ``key``, or None when it is not one."""
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _payload_episodes(payload: dict) -> list[int]:
    """Return the episode ids of a payload, ignoring anything that is not an id."""
    ids = payload.get('episodes')
    if not isinstance(ids, list):
        return []
    return [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]


def _payload(notification: Notification) -> dict:
    """Return the payload when it is an object, else an empty one."""
    payload = notification.payload
    return payload if isinstance(payload, dict) else {}


def _episodes(notifications: list[Notification], language: str) -> dict[int, Episode]:
    """Load the episodes the payloads name, with the texts the tags read.

    Costs the same number of queries for any number of notifications.
    """
    ids = {
        episode_id
        for n in notifications
        if n.kind in EPISODE_KINDS
        for episode_id in _payload_episodes(_payload(n))
    }
    if not ids:
        return {}
    episodes = Episode.objects.filter(pk__in=ids).select_related('season')
    found = {episode.pk: episode for episode in episodes}
    prefetch_related_objects(
        list(found.values()),
        Prefetch(
            'translations',
            queryset=EpisodeTranslation.objects.filter(
                language__in={language, FALLBACK_LANGUAGE}
            ),
        ),
    )
    return found


def _text(notification: Notification, episodes: dict[int, Episode], language: str):
    """Return the text line of an episode or release notification."""
    kind = notification.kind
    if kind == Notification.Kind.MOVIE_RELEASED:
        return gettext('The movie was released.')
    if kind not in EPISODE_KINDS:
        return ''
    # An episode that was deleted since is skipped.
    shown = [
        episodes[i] for i in _payload_episodes(_payload(notification)) if i in episodes
    ]
    kind_of_title = notification.title.kind
    codes = [episode_code(episode, kind_of_title) for episode in shown]
    if len(shown) == 1:
        name = localized_episode(shown[0], language)[0]
        return f'{codes[0]} · {name}' if name else codes[0]
    return ', '.join(codes)


def _row(notification: Notification, episodes: dict[int, Episode], language: str):
    """Prepare one notification for display."""
    icon, label = KINDS[notification.kind]
    payload = _payload(notification)
    dates = notification.kind in (
        Notification.Kind.RELEASE_ANNOUNCED,
        Notification.Kind.RELEASE_CHANGED,
    )
    return Row(
        pk=notification.pk,
        title=notification.title,
        icon=icon,
        label=str(label),
        text=_text(notification, episodes, language),
        old=_payload_date(payload, 'from') if dates else None,
        new=_payload_date(payload, 'to') if dates else None,
        unread=notification.read_at is None,
    )


def _groups(
    notifications: list[Notification],
    rows: list[Row],
    today: date,
    after: date | None,
) -> list[Group]:
    """Group the rows by local day, keeping their order.

    Args:
        notifications: The notifications, in display order.
        rows: One prepared row for each, in the same order.
        today: The user's local day now.
        after: The day of the notification just before the first one, when the
            page continues an earlier one; its group is marked ``continued``.
    """
    labels = {today: gettext('Today'), today - timedelta(days=1): gettext('Yesterday')}
    groups: list[Group] = []
    for notification, row in zip(notifications, rows, strict=True):
        day = notification.day
        if not groups or groups[-1].day != day:
            groups.append(
                Group(
                    day=day,
                    key=day.isoformat(),
                    label=labels.get(day, ''),
                    rows=[],
                    continued=not groups and day == after,
                )
            )
        groups[-1].rows.append(row)
    return groups


@vary_on_headers('HX-Request', 'HX-History-Restore-Request')
def notifications_page(request: HttpRequest) -> HttpResponse:
    """Render the user's notifications, 30 to a page, grouped by local day.

    Query parameters: ``unread=1`` shows only unread notifications; ``page``
    (anything that is not a positive number is 1). An HTMX request for a page
    above 1 gets only that page's groups, to append to the list; a plain request
    shows every notification up to that page, so Load more works without
    JavaScript.

    Args:
        request: The request of the signed-in user.

    Returns:
        The page or the fragment.
    """
    user = request.user
    profile = user.profile
    unread_only = request.GET.get('unread') == '1'
    page = _page(request.GET.get('page', ''))
    append = _is_fragment_request(request) and page > 1

    query = Notification.objects.for_user(user)
    if unread_only:
        query = query.filter(read_at__isnull=True)
    query = query.select_related('title').order_by('-day', '-created_at', '-pk')
    # An appended page also reads the row before it, to know whether its first day
    # is the one the list already ends with. One more row tells if a page follows.
    start = (page - 1) * PAGE_SIZE if append else 0
    end = page * PAGE_SIZE
    found = list(query[max(start - 1, 0) : end + 1])
    after = None
    if append and start:
        after = found.pop(0).day if found else None
    more = len(found) > end - start
    notifications = found[: end - start]

    languages = {profile.metadata_language, FALLBACK_LANGUAGE}
    prefetch_related_objects(
        [n.title for n in notifications],
        Prefetch(
            'translations',
            queryset=TitleTranslation.objects.filter(language__in=languages),
        ),
    )
    episodes = _episodes(notifications, profile.metadata_language)
    rows = [_row(n, episodes, profile.metadata_language) for n in notifications]
    today = local_day(timezone.now(), profile.timezone)
    params = {'unread': 1} if unread_only else {}
    context = {
        'groups': _groups(notifications, rows, today, after),
        'unread_only': unread_only,
        'more_url': '',
        # What the "Mark as read" forms send back, to land on the same list.
        'next_url': _list_url({**params, 'page': page} if page > 1 else params),
    }
    if more:
        context['more_url'] = _list_url({**params, 'page': page + 1})
    if append:
        return render(request, 'web/_notifications_more.html', context)

    context['unread_total'] = (
        Notification.objects.for_user(user).filter(read_at__isnull=True).count()
    )
    # With nothing to list, say whether it is because the filter hides rows.
    context['hidden_by_filter'] = (
        unread_only
        and not notifications
        and Notification.objects.for_user(user).exists()
    )
    return render(request, 'web/notifications.html', context)


def _list_target(request: HttpRequest) -> str:
    """Return where to go back to after marking one notification read.

    The ``next`` field of the form names the list the user was on (with its
    ``unread`` and ``page``). It is used only when it is a local path to the list
    itself; anything else, such as another site or another page, is the plain list.
    """
    target = request.POST.get('next', '')
    parsed = urlsplit(target)
    if (
        target.startswith('/')
        and not target.startswith(('//', '/\\'))
        and not parsed.scheme
        and not parsed.netloc
        and parsed.path == reverse('notifications')
        and url_has_allowed_host_and_scheme(
            target,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return target
    return reverse('notifications')


def _mark_read(request: HttpRequest, pk: int) -> Notification:
    """Mark the requester's notification ``pk`` read; a row of another user is a 404.

    The first read time is kept: only a row without one is updated, in the same
    statement that checks it.
    """
    notification = get_object_or_404(Notification.objects.for_user(request.user), pk=pk)
    Notification.objects.for_user(request.user).filter(
        pk=notification.pk, read_at__isnull=True
    ).update(read_at=timezone.now())
    return notification


@require_POST
def notification_open(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Mark a notification read and go to its title (the main control of a row)."""
    notification = _mark_read(request, pk)
    return HttpResponseRedirect(
        reverse('title', args=[notification.title_id]), status=303
    )


@require_POST
def notification_read(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Mark one notification read and return to the list the user was on."""
    _mark_read(request, pk)
    return HttpResponseRedirect(_list_target(request), status=303)


@require_POST
def notifications_read_all(request: HttpRequest) -> HttpResponseRedirect:
    """Mark every unread notification of the requester read, in one statement."""
    Notification.objects.for_user(request.user).filter(read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return HttpResponseRedirect(reverse('notifications'), status=303)
