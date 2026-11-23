"""The Telegram message: the digest as one chat message in Telegram's HTML."""

from datetime import datetime
from html import escape

from accounts.models import User
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext
from notifications.channel import absolute_url
from notifications.digest import Group, Item, count_line, layout, more_line
from notifications.models import Notification
from web.templatetags.web_extras import short_date

LIMIT = 4096


def _esc(value: str) -> str:
    """Escape ``value`` for Telegram's HTML, for text and attribute values alike."""
    return escape(value, quote=True)


def _item_line(item: Item) -> str:
    """Return the line of one item: the label, the linked name and the details."""
    row = item.row
    line = f'{_esc(row.label)}: <a href="{_esc(item.url)}">{_esc(item.name)}</a>'
    if row.text:
        line += f' — {_esc(row.text)}'
    if row.new:
        dates = short_date(row.new)
        if row.old:
            dates = f'{short_date(row.old)} → {dates}'
        line += f' ({_esc(dates)})'
    return line


def _render(head: str, groups: list[Group], dropped: int) -> str:
    """Join the head, the groups' lines and the closing lines into one text.

    Args:
        head: The bold count line.
        groups: The groups to list; the last one may hold fewer items.
        dropped: How many notifications are not listed; the "and N more" line
            is added when it is not zero.
    """
    sections = [head]
    for group in groups:
        lines = [f'<b>{_esc(group.heading)}</b>']
        lines.extend(_item_line(item) for item in group.items)
        sections.append('\n'.join(lines))
    closing = []
    if dropped:
        closing.append(_esc(more_line(dropped)))
    screen = absolute_url(reverse('notifications'))
    closing.append(f'<a href="{_esc(screen)}">{_esc(gettext("All notifications"))}</a>')
    sections.append('\n'.join(closing))
    return '\n\n'.join(sections)


def build_message(user: User, notifications: list[Notification], now: datetime) -> str:
    """Build the Telegram message of ``notifications`` for ``user``.

    Built under the interface language of the profile, with the title names in
    its metadata language, like the e-mail digest. Items are added newest first
    while the whole message, with its closing lines, still fits in ``LIMIT``; the
    "and N more" line counts the notifications the digest cap and the cut left out.

    Args:
        user: The recipient.
        notifications: Their notifications, with ``title`` loaded.
        now: The current moment, which decides "Today" and "Yesterday".

    Returns:
        The message in Telegram's HTML subset, at most ``LIMIT`` characters.
    """
    with translation.override(user.profile.interface_language):
        listed = layout(user, notifications, now)
        head = f'<b>{_esc(count_line(listed.count))}</b>'
        items = [(group, item) for group in listed.groups for item in group.items]
        kept: list[Group] = []
        text = _render(head, kept, listed.count)
        for number, (group, item) in enumerate(items, start=1):
            if kept and kept[-1].heading == group.heading:
                candidate = [*kept[:-1], Group(group.heading, [*kept[-1].items, item])]
            else:
                candidate = [*kept, Group(group.heading, [item])]
            dropped = listed.hidden + len(items) - number
            attempt = _render(head, candidate, dropped)
            if len(attempt) > LIMIT:
                break
            kept, text = candidate, attempt
        return text
