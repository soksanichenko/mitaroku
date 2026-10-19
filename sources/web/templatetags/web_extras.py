"""Template tags and filters for the shell and the catalog components."""

from catalog.models import Episode, Title
from catalog.translations import (
    FALLBACK_LANGUAGE,
    localized_episode,
    localized_title,
)
from django import template
from django.contrib.auth.models import AbstractUser
from django.template.context import Context
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from tracking.models import LibraryEntry

register = template.Library()

# Choices of LibraryEntry.Status carry no translatable labels, so the UI wording
# lives here.
STATUS_LABELS = {
    LibraryEntry.Status.WATCHING: _('Watching'),
    LibraryEntry.Status.PLANNED: _('Plan to watch'),
    LibraryEntry.Status.COMPLETED: _('Completed'),
    LibraryEntry.Status.DROPPED: _('Dropped'),
}

RATING_SOURCES = {'tmdb': 'TMDB', 'anilist': 'AniList'}

KIND_LABELS = {
    Title.Kind.TV: _('Show'),
    Title.Kind.ANIME: _('Anime'),
    Title.Kind.MOVIE: _('Movie'),
}

# "Unknown" has no label: a tag that says nothing is not shown.
AIRING_LABELS = {
    Title.Status.UPCOMING: _('Upcoming'),
    Title.Status.AIRING: _('Airing'),
    Title.Status.ENDED: _('Ended'),
    Title.Status.CANCELED: _('Canceled'),
}


@register.filter
def initials(user: AbstractUser) -> str:
    """Return up to two uppercase initials for a user.

    Args:
        user: A user with ``get_full_name`` and ``get_username``.

    Returns:
        Initials from the full name, or the first two letters of the username.
    """
    name = (user.get_full_name() or user.get_username()).strip()
    words = name.split()
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper()


def _metadata_language(context: Context) -> str:
    """Return the metadata language of the signed-in user, or English."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return FALLBACK_LANGUAGE
    return user.profile.metadata_language


@register.simple_tag(takes_context=True)
def title_name(context: Context, title: Title) -> str:
    """Return the name of a title in the user's metadata language.

    Uses the prefetched translations when there are any, so a list of titles
    costs no extra queries. A title without any name is called "Untitled".
    """
    return localized_title(title, _metadata_language(context))[0] or gettext('Untitled')


@register.simple_tag(takes_context=True)
def title_overview(context: Context, title: Title) -> str:
    """Return the overview of a title in the user's metadata language."""
    return localized_title(title, _metadata_language(context))[1]


@register.simple_tag(takes_context=True)
def episode_name(context: Context, episode: Episode) -> str:
    """Return the name of an episode in the user's metadata language."""
    return localized_episode(episode, _metadata_language(context))[0]


@register.filter
def poster_hue(pk: int) -> int:
    """Return a stable hue from 0 to 359 for the placeholder tint of a title.

    The golden angle spreads consecutive ids over the colour wheel.
    """
    return int(pk * 137.508) % 360


@register.filter
def status_label(status: str) -> str:
    """Return the translated label of a ``LibraryEntry.Status`` value.

    Unknown values come back unchanged.
    """
    return str(STATUS_LABELS.get(status, status))


@register.filter
def rating_source_name(source: str) -> str:
    """Return the display name of a ``Title.rating_source`` value."""
    return RATING_SOURCES.get(source, source.upper())


@register.simple_tag
def status_options() -> list[tuple[str, str]]:
    """Return ``(value, label)`` for every status, in display order."""
    return [(value, str(label)) for value, label in STATUS_LABELS.items()]


@register.simple_tag
def score_choices() -> list[int]:
    """Return the scores a user can give: 0 (not rated) to 10."""
    return list(range(11))


@register.filter
def kind_label(kind: str) -> str:
    """Return the translated label of a ``Title.Kind`` value."""
    return str(KIND_LABELS.get(kind, kind))


@register.filter
def airing_label(status: str) -> str:
    """Return the translated label of a ``Title.Status`` value, or an empty string."""
    return str(AIRING_LABELS.get(status, ''))


@register.filter
def star_buttons(score: int) -> list[dict]:
    """Return the five star buttons of a score (0 to 10, two points a star).

    Each item has ``number`` (1 to 5), ``state`` (``full``, ``half`` or ``empty``;
    an odd score ends in a half star), ``pressed`` (a full star) and ``value``,
    what clicking posts: the star's score, or 0 for the star that already is the
    current whole rating, which clears it.
    """
    return [
        {
            'number': n,
            'state': 'full'
            if score >= 2 * n
            else 'half'
            if score == 2 * n - 1
            else 'empty',
            'pressed': score >= 2 * n,
            'value': 0 if score == 2 * n else 2 * n,
        }
        for n in range(1, 6)
    ]
