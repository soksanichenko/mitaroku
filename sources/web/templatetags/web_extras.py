"""Template tags and filters for the shell and the catalog components."""

from datetime import date

from catalog.models import Episode, Season, Title
from catalog.translations import (
    FALLBACK_LANGUAGE,
    localized_episode,
    localized_title,
)
from django import template
from django.contrib.auth.models import AbstractUser
from django.template.context import Context
from django.utils import dateformat, timezone
from django.utils.translation import gettext, pgettext, pgettext_lazy
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


# The month as it reads inside a date, abbreviated in the way of the language
# (Russian uses the genitive: "5 мая", "20 янв."). Django's own month names are
# capitalised in Russian and do not fit a date.
MONTHS = (
    pgettext_lazy('month in a date', 'Jan'),
    pgettext_lazy('month in a date', 'Feb'),
    pgettext_lazy('month in a date', 'Mar'),
    pgettext_lazy('month in a date', 'Apr'),
    pgettext_lazy('month in a date', 'May'),
    pgettext_lazy('month in a date', 'Jun'),
    pgettext_lazy('month in a date', 'Jul'),
    pgettext_lazy('month in a date', 'Aug'),
    pgettext_lazy('month in a date', 'Sep'),
    pgettext_lazy('month in a date', 'Oct'),
    pgettext_lazy('month in a date', 'Nov'),
    pgettext_lazy('month in a date', 'Dec'),
)


@register.filter
def short_date(value: date | None, weekday: str = '') -> str:
    """Return a date as month and day, with the year unless it is this year.

    The one format for dates in lists, cards and rows: "Jan 20" and "Jan 20, 2008"
    in English, "20 янв." and "20 янв. 2008 г." in Russian. The long form
    (``DATE_FORMAT``) is for the facts of a title page.

    Args:
        value: The date, or None.
        weekday: Pass ``weekday`` to put the abbreviated weekday in front.

    Returns:
        The text, or an empty string for None.
    """
    if value is None:
        return ''
    parts = {'month': str(MONTHS[value.month - 1]), 'day': value.day}
    if value.year == timezone.localdate().year:
        text = pgettext('date without the year', '%(month)s %(day)s') % parts
    else:
        parts['year'] = value.year
        text = pgettext('date with the year', '%(month)s %(day)s, %(year)s') % parts
    if weekday:
        return f'{dateformat.format(value, "D")}, {text}'
    return text


def episode_code(episode: Episode, kind: str) -> str:
    """Return the code of an episode: ``S1E3``, or ``E3`` when no season is known.

    An anime title is a franchise whose seasons are its entries, so its entry
    numbers shift when an OVA or a movie sits among them. The code of an anime
    episode is therefore the TMDB season and episode when it has both, else just
    the number inside its entry. A series uses its season number.

    Args:
        episode: The episode; ``episode.season`` is read for a series.
        kind: The ``Title.Kind`` of its title.
    """
    if kind == Title.Kind.ANIME:
        if (
            episode.tmdb_season_number is not None
            and episode.tmdb_episode_number is not None
        ):
            return f'S{episode.tmdb_season_number}E{episode.tmdb_episode_number}'
        return f'E{episode.number}'
    season: Season = episode.season
    return f'S{season.number}E{episode.number}'


register.filter('episode_code', episode_code)


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
def airing_label(status: str, kind: str = '') -> str:
    """Return the translated label of a ``Title.Status`` value, or an empty string.

    Args:
        status: A ``Title.Status`` value.
        kind: Optional ``Title.Kind`` value; a movie that has ended is "Released".
    """
    if kind == Title.Kind.MOVIE and status == Title.Status.ENDED:
        return str(_('Released'))
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
