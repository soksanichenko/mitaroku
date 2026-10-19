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
    costs no extra queries.
    """
    return localized_title(title, _metadata_language(context))[0]


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
