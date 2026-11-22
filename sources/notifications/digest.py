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
class _Item:
    """One listed notification, ready for the templates."""

    row: Row
    name: str
    url: str


@dataclass(frozen=True)
class _Group:
    """The items of one local day under their heading."""

    heading: str
    items: list[_Item]


def _heading(day: date, today: date) -> str:
    """Return the heading of a day as the Notifications screen words it."""
    if day == today:
        return gettext('Today')
    if day == today - timedelta(days=1):
        return gettext('Yesterday')
    return short_date(day, 'weekday')


def _groups(
    notifications: list[Notification], rows: list[Row], language: str, today: date
) -> list[_Group]:
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
            _Item(
                row=row,
                name=display_name(row.title, language),
                url=absolute_url(reverse('title', args=[row.title.pk])),
            ),
        )
        for notification, row in zip(notifications, rows, strict=True)
    ]
    return [
        _Group(_heading(day, today), [item for _, item in pairs])
        for day, pairs in groupby(items, key=lambda pair: pair[0])
    ]


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
    profile = user.profile
    with translation.override(profile.interface_language):
        ordered = sorted(
            notifications, key=lambda n: (n.day, n.created_at, n.pk), reverse=True
        )
        shown = ordered[:MAX_ITEMS]
        rows = prepare_rows(shown, profile.metadata_language)
        hidden = len(ordered) - len(shown)
        unsubscribe_url = absolute_url(
            reverse('email_unsubscribe', args=[unsubscribe_token(user)])
        )
        more = (
            ngettext('and %(count)s more', 'and %(count)s more', hidden)
            % {'count': hidden}
            if hidden
            else ''
        )
        subject = ngettext(
            '%(count)s new notification',
            '%(count)s new notifications',
            len(ordered),
        ) % {'count': len(ordered)}
        context = {
            'language': translation.get_language(),
            'subject': subject,
            'groups': _groups(
                shown,
                rows,
                profile.metadata_language,
                local_day(now, profile.timezone),
            ),
            'more': more,
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
