import html as htmllib
import re

import pytest
from catalog.models import ExternalId, Title
from catalog.sync.common import Clients
from django.db import connection
from django.test.utils import CaptureQueriesContext
from fakes import FakeAniList
from providers.browse import BrowseQuery, BrowseResults
from providers.types import SearchHit
from redis.exceptions import ConnectionError as RedisConnectionError
from tracking.models import AddRequest, LibraryEntry
from web.views.hits import decorate

pytestmark = pytest.mark.django_db

unescape = htmllib.unescape

HTMX = {'HTTP_HX_REQUEST': 'true'}
FILTERED = '/browse/?type=tv&genre=drama&country=IS&year=2025'


class BrowseRecorder:
    """Stand-in for ``providers.browse.browse`` that records its calls.

    Attributes:
        calls: ``(query, language)`` of every call.
        result: What the next calls return.
        error: Raised by the next calls when set.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[BrowseQuery, str]] = []
        self.result = BrowseResults(hits=[], has_more=False, failed=[])
        self.error: Exception | None = None

    def __call__(self, query: BrowseQuery, language: str, **clients) -> BrowseResults:
        self.calls.append((query, language))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def patched_browse(monkeypatch) -> BrowseRecorder:
    """Replace the provider layer of the Browse view with a recorder."""
    recorder = BrowseRecorder()
    monkeypatch.setattr('web.views.browse.browse', recorder)
    monkeypatch.setattr(
        'web.views.browse.shared_clients', lambda: Clients(None, FakeAniList())
    )
    return recorder


def hit(name: str, number: int = 1, kind: str = 'movie', **kwargs) -> SearchHit:
    """Build a Browse hit of ``kind``."""
    provider = {'tv': 'tmdb_tv', 'anime': 'anilist', 'movie': 'tmdb_movie'}[kind]
    values = {
        'original_title': name,
        'year': 1999,
        'poster_url': '',
        'overview': '',
        **kwargs,
    }
    return SearchHit(provider, str(number), kind, name, **values)


def results(hits, *, has_more=False, failed=()) -> BrowseResults:
    """Build a ``BrowseResults`` of ``hits``."""
    return BrowseResults(hits=list(hits), has_more=has_more, failed=list(failed))


def get(client, portal_headers, url, **extra) -> str:
    """Return the body of ``url`` as text."""
    return client.get(url, **portal_headers, **extra).content.decode()


def chips(page: str) -> list[tuple[str, str]]:
    """Return ``(text, href)`` of the filter chips of a page."""
    found = re.findall(
        r'<a class="mt-br-chip"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', page, re.S
    )
    return [
        (' '.join(re.sub('<[^>]+>', ' ', text).split()), htmllib.unescape(href))
        for href, text in found
    ]


def test_browse_renders_cards_with_links(client, portal_headers, patched_browse):
    known = Title.objects.create(kind='movie', original_title='The Matrix')
    ExternalId.objects.create(title=known, provider='tmdb_movie', external_id='603')
    patched_browse.result = results([hit('The Matrix', 603), hit('Unknown', 999)])
    page = get(client, portal_headers, '/browse/')
    assert f'href="/titles/{known.pk}/"' in page
    assert 'href="/open/tmdb_movie/999/"' in page
    assert 'Unknown' in page and '1999 · Movie' in page


def test_browse_card_names_fall_back_to_the_original_title_then_untitled(
    client, portal_headers, patched_browse
):
    patched_browse.result = results(
        [hit('', 1, original_title='Original'), hit('', 2, original_title='')]
    )
    page = get(client, portal_headers, '/browse/')
    assert 'Original' in page and 'Untitled' in page


def test_browse_cards_show_the_rating_badge(client, portal_headers, patched_browse):
    patched_browse.result = results([hit('Rated', 1, rating=8.46)])
    page = get(client, portal_headers, '/browse/')
    assert 'mt-poster-badge' in page and '8.5' in page and 'TMDB' in page


def test_browse_shows_library_state_per_user(
    client, portal_headers, other_headers, patched_browse, alice, bob
):
    title = Title.objects.create(kind='movie', original_title='The Matrix')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    LibraryEntry.objects.create(user=alice, title=title, status='completed')
    patched_browse.result = results([hit('The Matrix', 603)])
    mine = get(client, portal_headers, '/browse/')
    theirs = get(client, other_headers, '/browse/')
    assert 'In your library: Completed' in mine
    assert 'hx-post="/search/add/"' not in mine
    assert 'In your library' not in theirs and 'hx-post="/search/add/"' in theirs
    assert 'name="external_id" value="603"' in theirs


def test_browse_shows_a_running_add_request(
    client, portal_headers, patched_browse, alice
):
    AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='7', status='planned'
    )
    patched_browse.result = results([hit('Pending', 7)])
    page = get(client, portal_headers, '/browse/')
    assert 'mt-add--pending' in page and 'hx-post="/search/add/"' not in page


def test_browse_heading_and_chips(client, portal_headers, patched_browse):
    page = get(client, portal_headers, FILTERED)
    heading = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S).group(1)
    assert ' '.join(heading.split()) == 'Shows · Drama · Iceland · 2025'
    found = chips(page)
    assert [text for text, _ in found] == [
        'Type: Shows',
        'Genre: Drama',
        'Country: Iceland',
        'Year: 2025',
    ]
    assert [href for _, href in found] == [
        '/browse/?genre=drama&country=IS&year=2025',
        '/browse/?type=tv&country=IS&year=2025',
        '/browse/?type=tv&genre=drama&year=2025',
        '/browse/?type=tv&genre=drama&country=IS',
    ]


def test_browse_heading_in_russian(client, portal_headers, patched_browse, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    patched_browse.result = results([hit('A', 1)], has_more=True)
    page = get(client, portal_headers, '/browse/?type=tv&genre=drama&country=IS')
    heading = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S).group(1)
    assert ' '.join(heading.split()) == 'Сериалы · Драма · Исландия'
    assert 'Показать ещё' in page and 'Показано 1<' in page and ' из ' not in page
    assert 'Добавить фильтр' in page and 'Сбросить всё' in page
    assert 'Browse' not in page
    for english in ('Load more', 'Showing', 'Add filter', 'Clear all'):
        assert english not in page


def test_browse_error_and_empty_states_in_russian(
    client, portal_headers, patched_browse, alice
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    assert 'Нет тайтлов с такими фильтрами' in get(client, portal_headers, FILTERED)
    patched_browse.result = results([], failed=['tmdb', 'anilist'])
    page = get(client, portal_headers, FILTERED)
    assert 'Нет ответа от: TMDB и AniList. Ваши фильтры сохранены.' in page
    assert 'Results may be incomplete' not in page


def test_the_kind_key_is_not_a_public_parameter(client, portal_headers, patched_browse):
    get(client, portal_headers, '/browse/?kind=tv')
    get(client, portal_headers, '/browse/?type=anime&kind=tv')
    assert [query.kind for query, _ in patched_browse.calls] == [None, 'anime']


def test_browse_heading_without_filters(client, portal_headers, patched_browse):
    page = get(client, portal_headers, '/browse/?sort=title')
    assert re.search(r'<h1[^>]*>\s*All titles\s*</h1>', page)
    assert chips(page) == []
    assert 'Clear all' not in page


def test_chips_and_clear_all_keep_the_sort(client, portal_headers, patched_browse):
    page = get(client, portal_headers, '/browse/?type=anime&sort=rating')
    assert chips(page) == [('Type: Anime', '/browse/?sort=rating')]
    assert 'href="/browse/?sort=rating"' in page
    assert 'Clear all' in page


def test_add_filter_offers_only_the_missing_facets(
    client, portal_headers, patched_browse
):
    page = get(client, portal_headers, '/browse/?type=tv&year=2025')
    assert 'Add filter' in page
    assert 'id="br-f-genre"' in page and 'id="br-f-country"' in page
    assert 'id="br-f-type"' not in page and 'id="br-f-year"' not in page
    assert '<option value="drama">Drama</option>' in page
    assert '<option value="IS">Iceland</option>' in page
    everything = get(client, portal_headers, FILTERED)
    assert 'Add filter' not in everything
    assert 'id="br-f-year"' in get(client, portal_headers, '/browse/?type=tv')


def test_sort_select_lists_the_four_orders(client, portal_headers, patched_browse):
    page = get(client, portal_headers, '/browse/?sort=rating')
    select = re.search(r'<select[^>]*name="sort".*?</select>', page, re.S).group()
    assert re.findall(r'<option value="(\w+)"', select) == [
        'popularity',
        'date',
        'title',
        'rating',
    ]
    assert '<option value="rating" selected>' in select


def test_the_form_is_wired_to_htmx_and_works_without_it(
    client, portal_headers, patched_browse
):
    page = get(client, portal_headers, FILTERED)
    form = re.search(r'<form id="browse-form"[^>]*>', page, re.S).group()
    assert 'method="get"' in form and 'action="/browse/"' in form
    assert 'hx-get="/browse/"' in form and 'hx-push-url="true"' in form
    assert 'hx-target="#browse-results"' in form
    assert 'hx-indicator' in form
    for name, value in (('type', 'tv'), ('genre', 'drama'), ('year', '2025')):
        assert f'<input type="hidden" name="{name}" value="{value}">' in page
    assert '<noscript>' in page


def test_browse_passes_the_query_and_language(
    client, portal_headers, patched_browse, alice
):
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    get(client, portal_headers, FILTERED + '&sort=rating&page=3')
    assert patched_browse.calls == [
        (
            BrowseQuery(
                kind='tv',
                genre='drama',
                country='IS',
                year=2025,
                sort='rating',
                page=3,
            ),
            'ru',
        )
    ]


def test_browse_ignores_invalid_parameters(client, portal_headers, patched_browse):
    response = client.get(
        '/browse/',
        {
            'year': 'abc',
            'genre': '<script>alert(1)</script>',
            'country': 'ZZ',
            'sort': 'x',
            'page': '99999999',
        },
        **portal_headers,
    )
    page = response.content.decode()
    assert response.status_code == 200
    assert patched_browse.calls == [(BrowseQuery(page=200), 'en')]
    assert 'alert(1)' not in page
    assert chips(page) == []


def test_browse_takes_the_first_value_of_a_repeated_key(
    client, portal_headers, patched_browse
):
    response = client.get(
        '/browse/?genre=drama&genre=<script>alert(1)</script>&type=tv&type=movie',
        **portal_headers,
    )
    page = response.content.decode()
    assert response.status_code == 200
    assert [query for query, _ in patched_browse.calls] == [
        BrowseQuery(kind='tv', genre='drama')
    ]
    # The language switcher may echo the query string, but only escaped.
    assert '<script>alert' not in page
    assert [text for text, _ in chips(page)] == ['Type: Shows', 'Genre: Drama']
    get(client, portal_headers, '/browse/?genre=<script>&genre=drama&type=x&type=tv')
    assert patched_browse.calls[-1][0] == BrowseQuery()


def test_browse_with_one_source_down(client, portal_headers, patched_browse):
    patched_browse.result = results([hit('Frieren', 1, kind='anime')], failed=['tmdb'])
    page = get(client, portal_headers, '/browse/')
    assert 'Frieren' in page
    assert 'Results may be incomplete: TMDB did not respond.' in page


def test_browse_error_keeps_the_filters(client, portal_headers, patched_browse):
    patched_browse.result = results([], failed=['tmdb', 'anilist'])
    response = client.get(FILTERED + '&sort=title', **portal_headers)
    page = response.content.decode()
    assert response.status_code == 200
    assert 'role="alert"' in page
    assert "TMDB and AniList didn't respond. Your filters are kept." in unescape(page)
    assert len(chips(page)) == 4
    assert (
        'hx-get="/browse/?type=tv&amp;genre=drama&amp;country=IS&amp;year=2025&amp;sort=title"'
        in page
    )
    assert 'hx-target="#browse-results"' in page
    assert 'No titles match' not in page and 'mt-br-grid' not in page


def test_browse_error_names_the_one_source_that_failed(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([], failed=['anilist'])
    page = get(client, portal_headers, '/browse/?type=anime')
    assert "AniList didn't respond. Your filters are kept." in unescape(page)


def test_browse_hides_the_count_on_an_error(client, portal_headers, patched_browse):
    patched_browse.result = results([hit('A', n) for n in range(1, 41)])
    assert '40 titles' in get(client, portal_headers, '/browse/')
    patched_browse.result = results([], failed=['tmdb', 'anilist'])
    assert 'titles</span>' not in get(client, portal_headers, '/browse/')


def test_browse_survives_a_redis_outage(client, portal_headers, patched_browse):
    patched_browse.error = RedisConnectionError('Redis is down')
    response = client.get(FILTERED, **portal_headers)
    page = response.content.decode()
    assert response.status_code == 200
    assert 'Your filters are kept' in unescape(page) and len(chips(page)) == 4


def test_browse_empty_state_offers_to_clear_filters(
    client, portal_headers, patched_browse
):
    page = get(client, portal_headers, FILTERED)
    assert 'No titles match these filters' in page
    assert 'Remove a filter, or start over.' in page
    assert re.search(r'<a [^>]*href="/browse/"[^>]*>\s*Clear filters', page)
    assert 'Your filters are kept' not in page


def test_browse_loading_skeleton_is_wired_to_the_indicator(
    client, portal_headers, patched_browse
):
    page = get(client, portal_headers, '/browse/')
    assert 'mt-state--loading' in page and 'Loading titles…' in page


def test_load_more_is_the_last_item_of_the_grid(client, portal_headers, patched_browse):
    patched_browse.result = results([hit('A', 1), hit('B', 2)], has_more=True)
    page = get(client, portal_headers, '/browse/?type=movie')
    grid = re.search(r'<ul[^>]*mt-br-grid.*?</ul>', page, re.S).group()
    items = re.findall(r'<li\b.*?</li>', grid, re.S)
    assert len(items) == 3 and 'Load more' in items[-1]
    link = re.search(r'<a [^>]*href="([^"]*)"[^>]*>', items[-1]).group()
    href = htmllib.unescape(re.search(r'href="([^"]*)"', link).group(1))
    assert href == '/browse/?type=movie&page=2&shown=2'
    assert f'hx-get="{htmllib.escape(href)}"' in link
    assert 'hx-swap="outerHTML"' in link
    assert 'Showing 2<' in page


def test_no_load_more_without_has_more(client, portal_headers, patched_browse):
    patched_browse.result = results([hit('A')], has_more=False)
    page = get(client, portal_headers, '/browse/')
    assert 'Load more' not in page and 'Showing' not in page
    assert '1 title<' in page


def test_load_more_returns_only_the_next_cards(client, portal_headers, patched_browse):
    patched_browse.result = results([hit('C', 3), hit('D', 4)], has_more=True)
    page = get(client, portal_headers, '/browse/?page=2&shown=2', **HTMX)
    assert '<html' not in page and '<form' not in page and '<h1' not in page
    assert page.count('<article class="mt-lcard mt-bcard">') == 2
    assert patched_browse.calls[0][0].page == 2
    assert page.rstrip().endswith('</li>')
    last = re.findall(r'<li\b.*?</li>', page, re.S)[-1]
    assert 'Load more' in last
    assert 'page=3' in htmllib.unescape(last) and 'shown=4' in htmllib.unescape(last)
    assert 'Showing 4<' in page


def test_load_more_ends_without_a_link_on_the_last_page(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([hit('E', 5)], has_more=False)
    page = get(client, portal_headers, '/browse/?page=2', **HTMX)
    assert 'Load more' not in page
    assert page.count('<article class="mt-lcard mt-bcard">') == 1


def test_load_more_failure_stays_in_the_grid(client, portal_headers, patched_browse):
    patched_browse.result = results([], failed=['tmdb'])
    page = get(client, portal_headers, '/browse/?page=2&shown=20', **HTMX)
    assert '<html' not in page and 'mt-bcard' not in page
    assert "Couldn't load more titles" in unescape(page)
    assert 'page=2' in htmllib.unescape(page) and 'Try again' in page
    # The retry keeps the number of cards loaded so far.
    link = re.search(r'<a [^>]*hx-get="([^"]*)"', page).group(1)
    assert unescape(link) == '/browse/?page=2&shown=20'


def test_fragment_request_returns_only_the_results(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([hit('A')], has_more=True)
    response = client.get(FILTERED, **portal_headers, **HTMX)
    page = response.content.decode()
    assert '<html' not in page and '<form id="browse-form"' not in page
    assert 'id="browse-results"' in page
    assert 'hx-swap-oob' in page
    heading = re.search(r'<h1 id="browse-heading"[^>]*>(.*?)</h1>', page, re.S)
    assert heading and 'Shows · Drama · Iceland · 2025' in heading.group(1)
    assert 'HX-Request' in response['Vary']
    assert 'HX-History-Restore-Request' in response['Vary']


def test_history_restore_gets_the_whole_page(client, portal_headers, patched_browse):
    page = get(
        client,
        portal_headers,
        FILTERED,
        **HTMX,
        HTTP_HX_HISTORY_RESTORE_REQUEST='true',
    )
    assert '<html' in page and '<form id="browse-form"' in page


def test_browse_requires_a_signed_in_member(client, patched_browse):
    assert client.get('/browse/').status_code == 401
    assert patched_browse.calls == []


def test_browse_query_count_is_constant(alice):
    def count(hits) -> int:
        with CaptureQueriesContext(connection) as queries:
            decorate(alice, hits)
        return len(queries)

    few = [hit(f'Few {n}', n, kind='tv') for n in range(1, 4)]
    many = [hit(f'Many {n}', n, kind='tv') for n in range(1, 31)]
    for number in range(1, 31, 2):
        title = Title.objects.create(kind='tv', original_title=f'T{number}')
        ExternalId.objects.create(
            title=title, provider='tmdb_tv', external_id=str(number)
        )
        LibraryEntry.objects.create(user=alice, title=title, status='planned')
    assert count(few) == count(many) == 3


def test_decorate_builds_the_href(alice):
    title = Title.objects.create(kind='tv', original_title='Known')
    ExternalId.objects.create(title=title, provider='tmdb_tv', external_id='5')
    known, unknown = decorate(
        alice, [hit('Known', 5, kind='tv'), hit('New', 6, kind='anime')]
    )
    assert known.href == f'/titles/{title.pk}/'
    assert unknown.href == '/open/anilist/6/'


def cards(page: str) -> int:
    """Return how many title cards a page or fragment holds."""
    return page.count('<article class="mt-lcard mt-bcard">')


def count_text(page: str) -> str:
    """Return the text of the title count of a page or fragment."""
    found = re.search(r'<span id="browse-count"[^>]*>(.*?)</span>', page, re.S)
    return ' '.join(re.sub('<[^>]+>', '', found.group(1)).split())


def test_a_page_whose_hits_were_all_dropped_still_offers_the_next_page(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([], has_more=True)
    page = get(client, portal_headers, '/browse/?type=movie')
    assert 'No titles match' not in page and cards(page) == 0
    grid = re.search(r'<ul[^>]*mt-br-grid.*?</ul>', page, re.S).group()
    items = re.findall(r'<li\b.*?</li>', grid, re.S)
    assert len(items) == 1 and 'Load more' in items[0]
    assert '/browse/?type=movie&page=2' in unescape(items[0])
    assert 'Showing' not in page and count_text(page) == ''


def test_an_emptied_page_ends_the_list_at_the_page_limit(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([], has_more=True)
    page = get(client, portal_headers, '/browse/?page=99999')
    assert 'No titles match these filters' in page and 'Load more' not in page


def test_an_emptied_appended_page_carries_the_next_load_more_item(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([], has_more=True)
    page = get(client, portal_headers, '/browse/?page=3&shown=40', **HTMX)
    assert cards(page) == 0 and 'No titles match' not in page
    items = re.findall(r'<li\b.*?</li>', page, re.S)
    assert len(items) == 1 and 'Load more' in items[0]
    assert '/browse/?page=4&shown=40' in unescape(items[0])
    assert 'Showing 40<' in page and count_text(page) == ''


def test_an_emptied_last_page_does_not_hide_a_failure(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([], has_more=True, failed=['tmdb'])
    page = get(client, portal_headers, '/browse/?page=3&shown=40', **HTMX)
    assert "Couldn't load more titles" in unescape(page)


def test_the_count_and_the_showing_line_are_claimed_only_for_a_complete_list(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([hit('A', n) for n in range(1, 4)], has_more=True)
    page = get(client, portal_headers, '/browse/')
    assert count_text(page) == '' and 'Showing 3<' in page
    assert re.search(r'Showing \d+ of', page) is None and '5000' not in page
    assert 'Load more' in page
    patched_browse.result = results([hit('A', n) for n in range(1, 4)])
    page = get(client, portal_headers, '/browse/')
    assert count_text(page) == '3 titles' and 'Showing' not in page
    assert 'Load more' not in page


def test_the_count_is_withheld_while_load_more_exists_in_a_fragment(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([hit('C', 3), hit('D', 4)], has_more=True)
    page = get(client, portal_headers, '/browse/?page=2&shown=2', **HTMX)
    assert count_text(page) == '' and 'hx-swap-oob' in page
    assert 'Showing 4<' in page and 'Showing 4 of' not in page
    patched_browse.result = results([hit('E', 5)], has_more=True)
    fresh = get(client, portal_headers, '/browse/?type=tv', **HTMX)
    assert count_text(fresh) == '' and 'Showing 1<' in fresh


def test_the_last_load_more_fills_in_the_exact_count(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([hit('E', 5), hit('F', 6)], has_more=False)
    page = get(client, portal_headers, '/browse/?page=2&shown=20', **HTMX)
    assert count_text(page) == '22 titles'
    assert re.search(r'<span id="browse-count"[^>]*hx-swap-oob="true"', page)
    assert 'Showing' not in page and 'Load more' not in page


def test_a_hand_edited_page_does_not_claim_a_count(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([hit('E', 5)], has_more=False)
    assert count_text(get(client, portal_headers, '/browse/?page=3')) == ''


@pytest.mark.parametrize(
    ('number', 'text'),
    [(1, '1 название'), (2, '2 названия'), (5, '5 названий'), (21, '21 название')],
)
def test_the_count_has_russian_plural_forms(
    client, portal_headers, patched_browse, alice, number, text
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    patched_browse.result = results([hit('A', n) for n in range(1, number + 1)])
    assert count_text(get(client, portal_headers, '/browse/')) == text


def test_a_failed_load_more_retries_with_the_cards_shown(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([], failed=['tmdb'])
    for shown, expected in (
        ('20', '/browse/?type=tv&page=2&shown=20'),
        ('junk', '/browse/?type=tv&page=2'),
    ):
        page = get(
            client, portal_headers, f'/browse/?type=tv&page=2&shown={shown}', **HTMX
        )
        assert unescape(re.search(r'hx-get="([^"]*)"', page).group(1)) == expected


HINT = 'Rating sort only lists titles with at least 100 votes.'


@pytest.mark.parametrize(
    ('url', 'shown'),
    [
        ('/browse/?type=tv&sort=rating', True),
        ('/browse/?type=movie&sort=rating', True),
        ('/browse/?sort=rating', True),
        ('/browse/?type=anime&sort=rating', False),
        ('/browse/?type=tv&sort=date', False),
        ('/browse/?type=tv', False),
        ('/browse/?type=movie&genre=sports&sort=rating', False),
    ],
)
def test_the_rating_hint_is_shown_only_when_tmdb_was_asked(
    client, portal_headers, patched_browse, url, shown
):
    assert (HINT in get(client, portal_headers, url)) is shown


def test_the_rating_hint_is_translated(client, portal_headers, patched_browse, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    page = get(client, portal_headers, '/browse/?type=tv&sort=rating')
    assert HINT not in page and 'не менее 100 голосов' in page


def test_the_rating_hint_is_only_for_the_empty_state(
    client, portal_headers, patched_browse
):
    patched_browse.result = results([hit('A')])
    assert HINT not in get(client, portal_headers, '/browse/?type=tv&sort=rating')
