"""Upcoming view: the next episode or premiere of every title the user follows."""

from dataclasses import dataclass
from datetime import date, timedelta

from catalog.models import EpisodeTranslation, TitleTranslation
from catalog.translations import FALLBACK_LANGUAGE
from django.db.models import Prefetch, prefetch_related_objects
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from tracking import queries

# Days from today up to which a date belongs to "This week".
WEEK_DAYS = 7


@dataclass(frozen=True)
class Group:
    """A block of releases that share a distance from today.

    Attributes:
        key: Identifier used in the DOM (``today``, ``tomorrow``, ``week``,
            ``later`` or ``tba``).
        heading: The translated heading.
        date: The date the heading stands for (today and tomorrow), or None.
        note: A translated sub-line shown instead of a date, or ''.
        items: The releases, in the order of ``tracking.queries.upcoming``.
    """

    key: str
    heading: str
    date: date | None
    note: str
    items: list[queries.UpcomingItem]


def _bucket(item: queries.UpcomingItem, today: date) -> str:
    """Return the key of the group ``item`` belongs to."""
    if item.date is None:
        return 'tba'
    days = (item.date - today).days
    if days <= 0:
        return 'today'
    if days == 1:
        return 'tomorrow'
    return 'week' if days < WEEK_DAYS else 'later'


def _groups(items: list[queries.UpcomingItem], today: date) -> list[Group]:
    """Split the releases into groups, leaving out the empty ones."""
    blueprint = (
        ('today', _('Today'), today, ''),
        ('tomorrow', _('Tomorrow'), today + timedelta(days=1), ''),
        ('week', _('This week'), None, ''),
        ('later', _('Later'), None, ''),
        ('tba', _('Date not announced'), None, _('No date yet')),
    )
    buckets: dict[str, list[queries.UpcomingItem]] = {row[0]: [] for row in blueprint}
    for item in items:
        buckets[_bucket(item, today)].append(item)
    return [
        Group(key, heading, day, note, buckets[key])
        for key, heading, day, note in blueprint
        if buckets[key]
    ]


def upcoming(request: HttpRequest) -> HttpResponse:
    """Render the releases to come, grouped by how far away they are."""
    today = timezone.localdate()
    items = queries.upcoming(request.user, today=today)
    # The names are looked up by the template tags; load the texts in two queries.
    languages = [request.user.profile.metadata_language, FALLBACK_LANGUAGE]
    prefetch_related_objects(
        [item.title for item in items],
        Prefetch(
            'translations',
            queryset=TitleTranslation.objects.filter(language__in=languages),
        ),
    )
    prefetch_related_objects(
        [item.episode for item in items if item.episode],
        Prefetch(
            'translations',
            queryset=EpisodeTranslation.objects.filter(language__in=languages),
        ),
    )
    return render(
        request,
        'web/upcoming.html',
        {'groups': _groups(items, today), 'today': today},
    )
