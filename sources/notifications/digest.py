"""The e-mail digest: one message that lists a user's new notifications."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import groupby

from accounts.models import User
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext, ngettext
from notifications.channel import absolute_url, effective_address
from notifications.display import Row, display_name, prepare_rows
from notifications.models import Notification
from notifications.moments import local_day
from notifications.tokens import unsubscribe_token
from web.templatetags.web_extras import short_date

MAX_ITEMS = 50


@dataclass(frozen=True)
class Item:
    """One listed notification, ready for the templates."""

    row: Row
    name: str
    url: str


@dataclass(frozen=True)
class Group:
    """The items of one local day under their heading."""

    heading: str
    items: list[Item]


@dataclass(frozen=True)
class Layout:
    """What a digest lists, whatever channel shows it.

    Attributes:
        count: How many notifications the digest is about.
        groups: The listed items, newest first, by local day.
        hidden: How many notifications the ``MAX_ITEMS`` cut left out.
    """

    count: int
    groups: list[Group]
    hidden: int


def _heading(day: date, today: date) -> str:
    """Return the heading of a day as the Notifications screen words it."""
    if day == today:
        return gettext('Today')
    if day == today - timedelta(days=1):
        return gettext('Yesterday')
    return short_date(day, 'weekday')


def _groups(
    notifications: list[Notification], rows: list[Row], language: str, today: date
) -> list[Group]:
    """Group the rows by local day, keeping their order.

    Args:
        notifications: The notifications, in display order.
        rows: One prepared row for each, in the same order.
        language: The metadata language the title names are shown in.
        today: The user's local day now.
    """
    items = [
        (
            notification.day,
            Item(
                row=row,
                name=display_name(row.title, language),
                url=absolute_url(reverse('title', args=[row.title.pk])),
            ),
        )
        for notification, row in zip(notifications, rows, strict=True)
    ]
    return [
        Group(_heading(day, today), [item for _, item in pairs])
        for day, pairs in groupby(items, key=lambda pair: pair[0])
    ]


def layout(user: User, notifications: list[Notification], now: datetime) -> Layout:
    """Order, cut and group ``notifications`` for ``user``'s digest.

    Must be called under the recipient's ``translation.override``: the labels,
    headings and fallback names are translated into the active language.

    Args:
        user: The recipient.
        notifications: Their notifications, with ``title`` loaded.
        now: The current moment, which decides "Today" and "Yesterday".

    Returns:
        At most ``MAX_ITEMS`` notifications, newest first, and the count of the
        ones that did not fit.
    """
    profile = user.profile
    ordered = sorted(
        notifications, key=lambda n: (n.day, n.created_at, n.pk), reverse=True
    )
    shown = ordered[:MAX_ITEMS]
    rows = prepare_rows(shown, profile.metadata_language)
    groups = _groups(
        shown, rows, profile.metadata_language, local_day(now, profile.timezone)
    )
    return Layout(count=len(ordered), groups=groups, hidden=len(ordered) - len(shown))


def count_line(count: int) -> str:
    """Return the "N new notifications" line in the active language."""
    return ngettext(
        '%(count)s new notification', '%(count)s new notifications', count
    ) % {'count': count}


def more_line(hidden: int) -> str:
    """Return the "and N more" line in the active language."""
    return ngettext('and %(count)s more', 'and %(count)s more', hidden) % {
        'count': hidden
    }


def build_digest(
    user: User, notifications: list[Notification], now: datetime
) -> EmailMultiAlternatives:
    """Build the digest of ``notifications`` for ``user``; nothing is sent.

    The whole message is built under the interface language of the profile and
    the title names are in its metadata language, as on the screen. The subject,
    the headers and every link come from fixed text, counts and signed tokens,
    never from title text.

    Args:
        user: The recipient; must have an address (``effective_address``).
        notifications: Their notifications, with ``title`` loaded.
        now: The current moment, which decides "Today" and "Yesterday".

    Returns:
        The message with a plain-text body and an HTML alternative; at most
        ``MAX_ITEMS`` notifications are listed, newest first.
    """
    with translation.override(user.profile.interface_language):
        listed = layout(user, notifications, now)
        unsubscribe_url = absolute_url(
            reverse('email_unsubscribe', args=[unsubscribe_token(user)])
        )
        subject = count_line(listed.count)
        context = {
            'language': translation.get_language(),
            'subject': subject,
            'groups': listed.groups,
            'more': more_line(listed.hidden) if listed.hidden else '',
            'screen_url': absolute_url(reverse('notifications')),
            'unsubscribe_url': unsubscribe_url,
        }
        text = render_to_string('notifications/email/digest.txt', context)
        html = render_to_string('notifications/email/digest.html', context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        to=[effective_address(user)],
        headers={
            'List-Unsubscribe': f'<{unsubscribe_url}>',
            'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
        },
    )
    message.attach_alternative(html, 'text/html')
    return message
