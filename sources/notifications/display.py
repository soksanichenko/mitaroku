"""How a notification reads: the kind label, the text line and the dates.

Shared by the Notifications screen and the e-mail digest, so both say the same
thing.
"""

from dataclasses import dataclass
from datetime import date

from catalog.models import Episode, EpisodeTranslation, Title, TitleTranslation
from catalog.translations import FALLBACK_LANGUAGE, localized_episode, localized_title
from django.db.models import Prefetch, prefetch_related_objects
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from notifications.models import Notification
from web.templatetags.web_extras import episode_code

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


def prepare_rows(notifications: list[Notification], language: str) -> list[Row]:
    """Prepare notifications for display, in the order given.

    Prefetches what the rows and the title names need (the title translations in
    ``language`` and English, the named episodes), so the cost in queries does
    not depend on the number of notifications. The labels and sentences are
    translated into the language active at the call; outside a request the
    caller activates it.

    Args:
        notifications: The notifications, with ``title`` loaded.
        language: The metadata language of the reader.

    Returns:
        One row for each notification, in the same order.
    """
    prefetch_related_objects(
        [n.title for n in notifications],
        Prefetch(
            'translations',
            queryset=TitleTranslation.objects.filter(
                language__in={language, FALLBACK_LANGUAGE}
            ),
        ),
    )
    episodes = _episodes(notifications, language)
    return [_row(n, episodes, language) for n in notifications]


def display_name(title: Title, language: str) -> str:
    """Return the name of a title in ``language``; "Untitled" when it has none.

    Reads the prefetched translations, which ``prepare_rows`` loads.
    """
    return localized_title(title, language)[0] or gettext('Untitled')
