"""Browse: movies, shows and anime by type, genre, country and year."""

import logging
import re
from dataclasses import dataclass, replace
from datetime import date
from urllib.parse import urlencode

from catalog.vocab import (
    BROWSE_COUNTRIES,
    GENRES,
    browse_years,
    country_name,
    genre_label,
)
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.text import get_text_list
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.vary import vary_on_headers
from providers.browse import (
    MAX_PAGE,
    SORTS,
    BrowseQuery,
    BrowseResults,
    browse,
    tmdb_covers_genre,
)
from redis.exceptions import RedisError
from web.clients import shared_clients
from web.templatetags.web_extras import interface_language, rating_source_name
from web.views.hits import decorate, is_fragment_request
from web.views.search import GROUPS

logger = logging.getLogger(__name__)

SORT_LABELS = {
    'popularity': _('Popularity'),
    'date': _('Release date'),
    'title': _('Title'),
    'rating': _('Rating'),
}
FACET_LABELS = {
    'type': _('Type'),
    'genre': _('Genre'),
    'country': _('Country'),
    'year': _('Year'),
}
# The kinds as the plural headings of Search ("Shows"), in the order of the facet.
KIND_HEADINGS = dict(GROUPS)
# More than this many loaded cards is not a number a page can honestly show.
MAX_SHOWN = 100_000


@dataclass(frozen=True)
class Chip:
    """An active filter that a link removes.

    Attributes:
        facet: The facet label, such as "Genre".
        value: The label of the chosen value, such as "Drama".
        remove_url: The Browse URL without this filter.
    """

    facet: str
    value: str
    remove_url: str


@dataclass(frozen=True)
class Facet:
    """A filter that is not set yet and can be added.

    Attributes:
        name: The query-string key, which is also the select's name.
        label: The translated label.
        options: ``(value, label)`` pairs.
    """

    name: str
    label: str
    options: list[tuple[str, str]]


def browse_url(query: BrowseQuery, *, shown: int = 0) -> str:
    """Return the Browse URL of a query, leaving out what is the default.

    Args:
        query: The query to link to.
        shown: How many cards the visitor has loaded already; only "Load more"
            carries it, to tell "Showing N" how many there are.

    Returns:
        The path with a query string, or the bare path for the default query.
    """
    params = {
        key: value
        for key, value in (
            ('type', query.kind),
            ('genre', query.genre),
            ('country', query.country),
            ('year', query.year),
            ('sort', query.sort if query.sort != SORTS[0] else None),
            ('page', query.page if query.page > 1 else None),
            ('shown', shown or None),
        )
        if value is not None
    }
    url = reverse('browse')
    return f'{url}?{urlencode(params)}' if params else url


def _shown(value: str) -> int:
    """Return how many cards the visitor reports to have loaded; 0 when invalid."""
    if not re.fullmatch('[0-9]{1,6}', value):
        return 0
    return min(int(value), MAX_SHOWN)


def _heading(chips: list[Chip]) -> str:
    """Return the active filters joined by dots, or "All titles"."""
    return ' · '.join(chip.value for chip in chips) or gettext('All titles')


def _chips(query: BrowseQuery, language: str) -> list[Chip]:
    """Return one chip per active filter, in the order of the heading."""
    values = (
        ('type', str(KIND_HEADINGS[query.kind]) if query.kind else None),
        ('genre', genre_label(query.genre) if query.genre else None),
        ('country', country_name(query.country, language) if query.country else None),
        ('year', str(query.year) if query.year else None),
    )
    removals = {
        'type': replace(query, kind=None, page=1),
        'genre': replace(query, genre=None, page=1),
        'country': replace(query, country=None, page=1),
        'year': replace(query, year=None, page=1),
    }
    return [
        Chip(str(FACET_LABELS[name]), value, browse_url(removals[name]))
        for name, value in values
        if value is not None
    ]


def browse_heading(query: BrowseQuery) -> str:
    """Return the heading Browse shows for a query, such as ``Shows · Drama``.

    Args:
        query: The filters; ``sort`` and ``page`` do not matter.

    Returns:
        The active filters in the interface language, joined by dots, or "All
        titles" for none.
    """
    return _heading(_chips(query, interface_language()))


def _facets(query: BrowseQuery, language: str) -> list[Facet]:
    """Return the filters the query does not set yet, with their options."""
    facets = []
    if not query.kind:
        facets.append(
            Facet(
                'type',
                str(FACET_LABELS['type']),
                [(kind, str(label)) for kind, label in GROUPS],
            )
        )
    if not query.genre:
        facets.append(
            Facet(
                'genre',
                str(FACET_LABELS['genre']),
                sorted(
                    ((slug, genre_label(slug)) for slug in GENRES),
                    key=lambda option: option[1].casefold(),
                ),
            )
        )
    if not query.country:
        facets.append(
            Facet(
                'country',
                str(FACET_LABELS['country']),
                sorted(
                    ((code, country_name(code, language)) for code in BROWSE_COUNTRIES),
                    key=lambda option: option[1].casefold(),
                ),
            )
        )
    if not query.year:
        facets.append(
            Facet(
                'year',
                str(FACET_LABELS['year']),
                [(str(year), str(year)) for year in browse_years(date.today())],
            )
        )
    return facets


@vary_on_headers('HX-Request', 'HX-History-Restore-Request')
def browse_page(request: HttpRequest) -> HttpResponse:
    """Render Browse, or only the part that changed for an HTMX request.

    The query string holds the filters (``type``, ``genre``, ``country``,
    ``year``), ``sort`` and ``page``; a value that is not valid is ignored. An
    HTMX request for a later page answers with just the next cards and the next
    "Load more" item.
    """
    query = BrowseQuery.parse(request.GET)
    clients = shared_clients()
    try:
        results = browse(
            query,
            request.user.profile.metadata_language,
            tmdb=clients.tmdb,
            anilist=clients.anilist,
        )
    except RedisError as error:
        # The provider layer keeps its rate limits and cache in Redis.
        logger.warning('Browse failed: %s', type(error).__name__)
        results = BrowseResults(hits=[], has_more=False, failed=['tmdb', 'anilist'])
    rows = decorate(request.user, results.hits)
    language = interface_language()
    chips = _chips(query, language)
    # The cards of the earlier pages are not known here: "Load more" carries
    # their number. The providers' totals are estimates, so none is shown.
    previous = _shown(request.GET.get('shown', ''))
    shown = previous + len(rows)
    fragment = is_fragment_request(request)
    append = fragment and query.page > 1
    more_url = (
        browse_url(replace(query, page=query.page + 1), shown=shown)
        if results.has_more and query.page < MAX_PAGE
        else ''
    )
    failed_names = get_text_list(
        [rating_source_name(name) for name in results.failed], gettext('and')
    )
    context: dict = {
        'query': query,
        'heading': _heading(chips),
        'chips': chips,
        'facets': _facets(query, language),
        'sorts': [(value, str(SORT_LABELS[value])) for value in SORTS],
        'clear_url': browse_url(BrowseQuery(sort=query.sort)),
        'retry_url': browse_url(query, shown=previous if append else 0),
        'failed_names': failed_names,
        'append': append,
        'oob': fragment and not append,
        'state': 'results',
    }
    if not rows and results.failed:
        context['state'] = 'error'
    elif not rows and not more_url:
        context['state'] = 'empty'
        # TMDB lists only the titles with enough votes when sorting by rating.
        context['rating_hint'] = (
            query.sort == 'rating'
            and query.kind != 'anime'
            and tmdb_covers_genre(query)
        )
    else:
        # A page can lose every hit to the filters after paging: it still has a
        # next page, so "Load more" stays.
        context.update(
            rows=rows,
            shown=shown,
            # A number is exact only for a list that has ended. A hand-edited page
            # does not know the cards before it.
            count=(
                shown
                if rows and not results.has_more and (query.page == 1 or previous)
                else 0
            ),
            partial=bool(results.failed),
            more_url=more_url,
        )
    template = 'web/_browse_results.html' if fragment else 'web/browse.html'
    return render(request, template, context)
