"""Notifications view: what happened to the titles the user follows."""

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode, urlsplit

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.views.decorators.http import require_POST
from django.views.decorators.vary import vary_on_headers
from notifications.display import Row, prepare_rows
from notifications.models import Notification
from notifications.moments import local_day

PAGE_SIZE = 30
# A page number this large is not a place anyone has reached; it bounds the work a
# plain (no JavaScript) request for "everything up to page N" can ask for.
MAX_PAGE = 1000


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

    rows = prepare_rows(notifications, profile.metadata_language)
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
