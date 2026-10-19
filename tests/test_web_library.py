import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from catalog.models import Episode, EpisodeTranslation, Season, Title, TitleTranslation
from django.db import connection
from django.test.utils import CaptureQueriesContext
from tracking.models import LibraryEntry
from tracking_helpers import TODAY, first_episode, make_movie, make_show, watch

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
NAME = re.compile(r'class="mt-(?:lcard|lrow)-name">([^<]*)<')


def library(client, headers, query: str = '', **extra) -> str:
    """Return the HTML of ``/library/`` with ``query`` as the query string."""
    response = client.get(f'/library/{query}', **headers, **extra)
    assert response.status_code == 200
    return response.content.decode()


def names(html: str) -> list[str]:
    """Return the names of the titles listed, in order."""
    return NAME.findall(html)


def track(user, title, status: str = 'watching', **fields) -> LibraryEntry:
    """Put ``title`` into the library of ``user``."""
    return LibraryEntry.objects.create(user=user, title=title, status=status, **fields)


def make_title(name: str, kind: str = 'tv', **fields) -> Title:
    """Create a title without seasons."""
    return Title.objects.create(kind=kind, original_title=name, **fields)


def tag(html: str, element_id: str) -> str:
    """Return the opening tag of the element with ``element_id`` (empty if absent)."""
    found = re.search(rf'<[a-z0-9]+[^>]*\bid="{re.escape(element_id)}"[^>]*>', html)
    return found.group() if found else ''


def assert_unique_ids(html: str) -> None:
    """Assert that no DOM id appears twice in an HTML fragment."""
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def at(day: int) -> datetime:
    """Return a moment on day ``day`` of October 2026."""
    return datetime(2026, 10, day, 12, 0, tzinfo=UTC)


def make_listed(count: int, prefix: str = 'T') -> list[Title]:
    """Create ``count`` titles named ``T00``, ``T01``, ... (sortable by title)."""
    Title.objects.bulk_create(
        Title(kind='tv', original_title=f'{prefix}{n:03d}') for n in range(count)
    )
    return list(Title.objects.filter(original_title__startswith=prefix).order_by('pk'))


def test_library_with_an_announced_title(client, portal_headers, alice, today):
    announced = make_title('Announced', 'anime', status=Title.Status.UPCOMING)
    track(alice, announced)
    html = library(client, portal_headers)
    assert names(html) == ['Announced']
    assert 'aria-valuenow="0"' in html
    assert '0/0' in html
    assert 'No upcoming episodes' in html
    list_html = library(client, portal_headers, '?view=list')
    assert names(list_html) == ['Announced']
    assert '0/0' in list_html


def test_an_untitled_title_uses_the_fallback(client, portal_headers, alice, today):
    track(alice, make_title(''))
    assert names(library(client, portal_headers)) == ['Untitled']


def test_library_shows_only_my_titles(client, portal_headers, alice, bob, today):
    track(alice, make_title('Mine'))
    other = make_title('Theirs')
    track(bob, other)
    html = library(client, portal_headers)
    assert names(html) == ['Mine']
    assert 'Theirs' not in html
    assert f'/titles/{other.pk}/' not in html
    assert '1 title' in html


def test_empty_library_shows_onboarding_with_a_find_a_title_link(
    client, portal_headers, alice, today
):
    html = library(client, portal_headers)
    assert 'Your library is empty' in html
    assert re.search(r'<a[^>]*href="/search/"[^>]*>\s*Find a title', html)
    assert 'id="library-filters"' not in html
    assert 'id="continue-watching"' not in html
    assert 'No titles match' not in html
    assert 'Check off episodes' in html


def test_filters_without_matches_show_the_no_match_state(
    client, portal_headers, alice, today
):
    track(alice, make_title('Alpha'))
    html = library(client, portal_headers, '?q=zzz')
    assert 'No titles match' in html
    assert re.search(r'<a[^>]*href="/library/"[^>]*>\s*Clear filters', html)
    assert 'Your library is empty' not in html
    assert 'id="library-filters"' in html


def test_tabs_status_and_search_filter_the_results(
    client, portal_headers, alice, today
):
    track(alice, make_title('Show A', 'tv'), 'watching')
    track(alice, make_title('Show B', 'tv'), 'planned')
    track(alice, make_title('Anime A', 'anime'), 'watching')
    track(alice, make_title('Film A', 'movie'), 'completed')
    assert set(names(library(client, portal_headers))) == {
        'Show A',
        'Show B',
        'Anime A',
        'Film A',
    }
    assert set(names(library(client, portal_headers, '?tab=tv'))) == {
        'Show A',
        'Show B',
    }
    assert names(library(client, portal_headers, '?tab=anime')) == ['Anime A']
    assert names(library(client, portal_headers, '?tab=movie')) == ['Film A']
    assert set(names(library(client, portal_headers, '?status=watching'))) == {
        'Show A',
        'Anime A',
    }
    assert names(library(client, portal_headers, '?tab=tv&status=planned')) == [
        'Show B'
    ]
    assert names(library(client, portal_headers, '?q=film')) == ['Film A']
    assert set(names(library(client, portal_headers, '?q=ANIME&tab=all'))) == {
        'Anime A'
    }
    assert names(library(client, portal_headers, '?q=show&status=watching')) == [
        'Show A'
    ]


def test_search_matches_translated_names(client, portal_headers, alice, today):
    title = make_title('Sousou no Frieren', 'anime')
    TitleTranslation.objects.create(title=title, language='en', name='Frieren')
    track(alice, title)
    track(alice, make_title('Other'))
    # The translation is what the user sees; the original title matches too.
    assert names(library(client, portal_headers, '?q=frier')) == ['Frieren']
    assert names(library(client, portal_headers, '?q=sousou')) == ['Frieren']


def test_counts_follow_the_other_filters(client, portal_headers, alice, today):
    track(alice, make_title('Show A', 'tv'), 'watching')
    track(alice, make_title('Show B', 'tv'), 'planned')
    track(alice, make_title('Anime A', 'anime'), 'watching')
    html = library(client, portal_headers, '?status=watching')
    counts = dict(re.findall(r'id="count-(tab-\w+|status-\w+)"[^>]*>(\d+)<', html))
    assert counts['tab-all'] == '2'
    assert counts['tab-tv'] == '1'
    assert counts['tab-anime'] == '1'
    assert counts['tab-movie'] == '0'
    # The status chips count the rows of the selected tab.
    assert counts['status-all'] == '3'
    assert counts['status-planned'] == '1'
    assert counts['status-watching'] == '2'
    html = library(client, portal_headers, '?tab=tv')
    counts = dict(re.findall(r'id="count-(tab-\w+|status-\w+)"[^>]*>(\d+)<', html))
    assert counts['status-all'] == '2'
    assert counts['status-watching'] == '1'
    assert counts['tab-all'] == '3'


def test_sort_recent_is_last_watched_then_added(client, portal_headers, alice, today):
    old, mid, new, fresh_a, fresh_b = (make_show(n, extra=False) for n in 'ABCDE')
    for (title, _, _), added in zip(
        (old, mid, new, fresh_a, fresh_b), (1, 2, 3, 4, 5), strict=True
    ):
        entry = track(alice, title)
        LibraryEntry.objects.filter(pk=entry.pk).update(added_at=at(added))
    watch(alice, old[1][0], at=at(10))
    watch(alice, mid[1][0], at=at(12))
    watch(alice, new[1][0], at=at(11))
    # D and E were never watched: the later added comes first, after the watched.
    assert names(library(client, portal_headers)) == ['B', 'C', 'A', 'E', 'D']
    assert names(library(client, portal_headers, '?sort=recent')) == [
        'B',
        'C',
        'A',
        'E',
        'D',
    ]


def test_sort_title_is_case_insensitive_and_localized(
    client, portal_headers, alice, today
):
    for name in ('banana', 'Apple', 'cherry'):
        track(alice, make_title(name))
    zebra = make_title('Zebra')
    TitleTranslation.objects.create(title=zebra, language='en', name='Aardvark')
    track(alice, zebra)
    assert names(library(client, portal_headers, '?sort=title')) == [
        'Aardvark',
        'Apple',
        'banana',
        'cherry',
    ]
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    TitleTranslation.objects.create(title=zebra, language='ru', name='Арбуз')
    assert names(library(client, portal_headers, '?sort=title')) == [
        'Apple',
        'banana',
        'cherry',
        'Арбуз',
    ]


def test_sort_rating_puts_unrated_last(client, portal_headers, alice, today):
    for name, rating in (('Low', '5.5'), ('None', None), ('High', '9.1'), ('Mid', '7')):
        track(
            alice,
            make_title(name, rating=None if rating is None else Decimal(rating)),
        )
    assert names(library(client, portal_headers, '?sort=rating')) == [
        'High',
        'Mid',
        'Low',
        'None',
    ]


def test_sort_next_orders_by_the_next_air_date(client, portal_headers, alice, today):
    soon, _, _ = make_show('Soon', dated=(3, 9), extra=False)
    sooner, _, _ = make_show('Sooner', dated=(2, 9), extra=False)
    aired, _, _ = make_show('Aired', dated=(-5, 4), extra=False)
    undated, _, _ = make_show('Undated', dated=(None,), extra=False)
    finished, finished_eps, _ = make_show('Finished', dated=(-9,), extra=False)
    movie = make_movie('Film')
    for title in (soon, sooner, aired, undated, finished, movie):
        track(alice, title)
    watch(alice, *finished_eps)
    # Undated, finished and movie rows have no date to sort by: they come last.
    assert names(library(client, portal_headers, '?sort=next')) == [
        'Aired',
        'Sooner',
        'Soon',
        'Film',
        'Finished',
        'Undated',
    ]


def test_unknown_filter_values_fall_back_to_defaults(
    client, portal_headers, alice, today
):
    track(alice, make_title('Show A', 'tv'), 'watching')
    track(alice, make_title('Film A', 'movie'), 'planned')
    html = library(
        client, portal_headers, '?tab=nope&status=nope&sort=nope&view=nope&page=nope'
    )
    assert set(names(html)) == {'Show A', 'Film A'}
    assert 'mt-lgrid' in html
    default = library(client, portal_headers)
    # The CSRF token in the body tag differs between renders.
    assert html.split('<main')[1] == default.split('<main')[1]
    assert set(names(library(client, portal_headers, '?page=-3'))) == {
        'Show A',
        'Film A',
    }
    assert set(names(library(client, portal_headers, '?page=0'))) == {
        'Show A',
        'Film A',
    }


def test_the_query_is_capped_at_100_characters(client, portal_headers, alice, today):
    track(alice, make_title('x' * 100))
    html = library(client, portal_headers, '?q=' + 'x' * 100 + 'y' * 50)
    assert names(html) == ['x' * 100]
    assert f'value="{"x" * 100}"' in html


def test_the_filter_form_works_without_javascript(client, portal_headers, alice, today):
    track(alice, make_title('Show A'))
    html = library(client, portal_headers, '?tab=tv&status=planned&q=ab&sort=title')
    form = re.search(r'<form[^>]*id="library-filters"[^>]*>', html).group()
    assert 'method="get"' in form
    assert 'action="/library/"' in form
    assert 'hx-get="/library/"' in form
    assert (
        "hx-trigger=\"change[target.name!='q'], input delay:300ms from:#library-q"
        in form
    )
    assert 'hx-replace-url="true"' in form
    assert 'hx-target="#library-results"' in form
    assert 'hx-push-url' not in form
    assert re.search(r'<noscript>\s*<button[^>]*type="submit"', html)
    assert re.search(r'name="tab" value="tv"[^>]*checked', html)
    assert re.search(r'name="status" value="planned"[^>]*checked', html)
    assert re.search(r'<option value="title" selected>', html)
    assert re.search(r'name="q"[^>]*value="ab"', html)
    assert re.search(r'name="view" value="grid"[^>]*checked', html)
    # Every control belongs to the one form.
    assert html[html.index('<div id="library"') :].count('<form') == 1


def test_htmx_request_returns_only_the_results_fragment(
    client, portal_headers, alice, today
):
    track(alice, make_title('Show A'))
    response = client.get('/library/?tab=tv', **portal_headers, **HTMX)
    html = response.content.decode()
    assert response.status_code == 200
    assert '<html' not in html
    assert 'id="library-results"' in html
    assert names(html) == ['Show A']
    assert 'id="continue-watching"' not in html
    assert 'id="library-filters"' not in html
    assert 'HX-Request' in response['Vary']
    assert 'HX-History-Restore-Request' in response['Vary']
    assert_unique_ids(html)


def test_a_history_restore_request_gets_the_whole_page(
    client, portal_headers, alice, today
):
    track(alice, make_title('Show A'))
    html = library(
        client, portal_headers, **HTMX, HTTP_HX_HISTORY_RESTORE_REQUEST='true'
    )
    assert '<html' in html
    assert 'id="library-filters"' in html


def test_the_fragment_updates_the_counts_and_the_total_out_of_band(
    client, portal_headers, alice, today
):
    track(alice, make_title('Show A', 'tv'))
    track(alice, make_title('Film A', 'movie'))
    html = client.get('/library/?tab=tv', **portal_headers, **HTMX).content.decode()
    assert re.search(r'id="count-tab-movie"[^>]*hx-swap-oob="true"[^>]*>1<', html)
    assert re.search(r'id="count-status-all"[^>]*hx-swap-oob="true"[^>]*>1<', html)
    assert re.search(r'id="library-total"[^>]*hx-swap-oob="true"[^>]*>2 titles<', html)
    full = library(client, portal_headers)
    assert 'hx-swap-oob' not in full
    assert_unique_ids(full)


def test_continue_watching_lists_watching_titles_and_enables_aired_next(
    client, portal_headers, alice, synced_show
):
    other, _, _ = make_show('Planned show')
    track(alice, synced_show, 'watching')
    track(alice, other, 'planned')
    track(alice, make_movie('Film'), 'watching')
    watch(alice, first_episode(synced_show))
    html = library(client, portal_headers)
    strip = re.search(r'<section id="continue-watching".*?</section>', html, re.S)
    assert strip
    strip = strip.group()
    assert strip.count('mt-cw-card') == 1
    assert 'Planned show' not in strip
    assert 'Film' not in strip
    second = Episode.objects.get(season__title=synced_show, season__number=1, number=2)
    button = re.search(r'<button[^>]*hx-post="/titles/\d+/continue/"[^>]*>', strip)
    assert button
    button = button.group()
    assert f'hx-post="/titles/{synced_show.pk}/continue/"' in button
    assert f'hx-vals=\'{{"episode": "{second.pk}"}}\'' in button
    assert 'hx-swap="none"' in button
    assert f'id="cw-btn-{synced_show.pk}"' in button
    assert 'Mark S1E2 of Show as watched' in button
    assert 'S1E2' in strip
    assert 'Aired' in strip
    assert 'Aired Sep 26' in strip
    assert '1/5' in strip


def test_continue_card_without_an_aired_next_episode_has_no_button(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, 'watching')
    main = Episode.objects.filter(season__title=synced_show, season__is_extra=False)
    watch(alice, *main.filter(air_date__lte=TODAY))
    strip = re.search(
        r'<section id="continue-watching".*?</section>',
        library(client, portal_headers),
        re.S,
    ).group()
    assert 'mt-cw-card' in strip
    assert 'Next: S1E4 · Oct 26' in strip
    assert 'hx-post' not in strip
    assert '<button' not in strip


def test_continue_card_with_an_undated_next_episode(
    client, portal_headers, alice, today
):
    title, episodes, _ = make_show('Undated', dated=(-5, None), extra=False)
    track(alice, title)
    watch(alice, episodes[0])
    strip = re.search(
        r'<section id="continue-watching".*?</section>',
        library(client, portal_headers),
        re.S,
    ).group()
    assert 'Next: S1E2 · date not announced' in strip
    assert 'hx-post' not in strip


def test_continue_card_names_the_episode_in_the_metadata_language(
    client, portal_headers, alice, today
):
    title, episodes, _ = make_show('Named', dated=(-5, -4), extra=False)
    EpisodeTranslation.objects.create(
        episode=episodes[0], language='en', name='The Journey'
    )
    EpisodeTranslation.objects.create(
        episode=episodes[0], language='ru', name='Путешествие'
    )
    track(alice, title)
    assert 'S1E1 · The Journey' in library(client, portal_headers)
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    assert 'S1E1 · Путешествие' in library(client, portal_headers)


def test_the_continue_fragment_is_requested_on_its_own(
    client, portal_headers, alice, synced_show
):
    track(alice, synced_show, 'watching')
    response = client.get('/library/?fragment=continue', **portal_headers, **HTMX)
    html = response.content.decode()
    assert '<html' not in html
    assert 'id="continue-watching"' in html
    assert 'mt-cw-card' in html
    assert 'id="library-results"' not in html
    assert 'HX-Request' in response['Vary']
    # Without HTMX the parameter is ignored and the whole page comes back.
    assert '<html' in library(client, portal_headers, '?fragment=continue')


def test_the_continue_strip_refreshes_itself_and_stays_when_empty(
    client, portal_headers, alice, today
):
    track(alice, make_title('Planned'), 'planned')
    html = client.get('/library/?fragment=continue', **portal_headers, **HTMX)
    html = html.content.decode()
    strip = tag(html, 'continue-watching')
    assert 'hx-get="/library/?fragment=continue"' in strip
    assert 'library-changed from:body' in strip
    assert 'mt-cw-card' not in html
    assert 'Continue watching' not in html


def test_the_page_refreshes_after_a_card_post(client, portal_headers, alice, today):
    track(alice, make_title('Show A'))
    html = library(client, portal_headers)
    assert 'library-changed from:body' in tag(html, 'library-filters')
    root = tag(html, 'library')
    assert 'htmx:after-request' in root
    assert 'library-changed' in root
    assert 'htmx:response-error' in root
    assert 'htmx:send-error' in root


def test_pagination_appends_the_next_page(client, portal_headers, alice, today):
    for title in make_listed(65):
        track(alice, title)
    first = library(client, portal_headers, '?sort=title')
    assert len(names(first)) == 60
    assert names(first)[0] == 'T000'
    assert names(first)[-1] == 'T059'
    more = tag(first, 'library-more')
    assert more
    button = re.search(r'<a[^>]*page=2[^>]*>', first).group()
    assert 'hx-get="/library/?' in button
    assert 'sort=title' in button
    assert 'page=2' in button
    assert 'hx-target="#library-list"' in button
    assert 'hx-swap="beforeend"' in button
    assert 'hx-push-url' not in button
    second = client.get(
        '/library/?sort=title&page=2', **portal_headers, **HTMX
    ).content.decode()
    assert names(second) == [f'T{n:03d}' for n in range(60, 65)]
    assert '<html' not in second
    assert 'id="library-results"' not in second
    # The button is replaced out of band; there is no further page.
    assert re.search(r'id="library-more"[^>]*hx-swap-oob="true"', second)
    assert 'page=3' not in second
    assert 'Show more' not in second
    assert_unique_ids(second)


def test_the_page_parameter_without_javascript_shows_everything_so_far(
    client, portal_headers, alice, today
):
    for title in make_listed(65):
        track(alice, title)
    html = library(client, portal_headers, '?sort=title&page=2')
    assert len(names(html)) == 65
    assert 'Show more' not in html
    assert len(names(library(client, portal_headers, '?sort=title&page=99'))) == 65


def test_no_show_more_for_exactly_one_page(client, portal_headers, alice, today):
    for title in make_listed(60):
        track(alice, title)
    html = library(client, portal_headers)
    assert len(names(html)) == 60
    assert 'Show more' not in html


def test_list_pages_append_table_rows(client, portal_headers, alice, today):
    for title in make_listed(62):
        track(alice, title)
    first = library(client, portal_headers, '?sort=title&view=list')
    assert len(names(first)) == 60
    assert 'id="library-list"' in first
    assert 'view=list' in re.search(r'<a[^>]*page=2[^>]*>', first).group()
    second = client.get(
        '/library/?sort=title&view=list&page=2', **portal_headers, **HTMX
    ).content.decode()
    assert names(second) == ['T060', 'T061']
    assert second.count('<tr') == 2
    assert '<template>' in second
    assert_unique_ids(second)


def test_library_query_count_does_not_grow_with_titles(
    client, portal_headers, alice, today
):
    title, episodes, _ = make_show('First', extra=False)
    track(alice, title)
    watch(alice, episodes[0])
    library(client, portal_headers)  # warm up the profile and the session

    def measure() -> dict[str, int]:
        counts = {}
        for view in ('grid', 'list'):
            with CaptureQueriesContext(connection) as queries:
                library(client, portal_headers, f'?view={view}&sort=title')
            counts[view] = len(queries)
        with CaptureQueriesContext(connection) as queries:
            client.get('/library/?view=grid', **portal_headers, **HTMX)
        counts['fragment'] = len(queries)
        return counts

    one = measure()
    for n in range(70):
        many_title, many_eps, _ = make_show(f'Many {n}', extra=False)
        track(alice, many_title, 'watching')
        watch(alice, many_eps[0])
        TitleTranslation.objects.create(
            title=many_title, language='en', name=f'Many {n}'
        )
    assert measure() == one
    assert one['grid'] <= 14
    assert one['fragment'] <= 10


def test_the_grid_card_shows_progress_and_the_next_episode(
    client, portal_headers, alice, today
):
    title, episodes, _ = make_show('Card', dated=(-20, -10, 5, None), extra=False)
    Title.objects.filter(pk=title.pk).update(
        poster_url='https://img.example.com/p.jpg',
        rating=Decimal('8.2'),
        rating_source='tmdb',
        release_date=TODAY.replace(year=2024),
    )
    track(alice, title)
    watch(alice, episodes[0], episodes[1])
    html = library(client, portal_headers)
    card = re.search(r'<article class="mt-lcard".*?</article>', html, re.S).group()
    assert 'class="mt-lcard-name">Card<' in card
    assert 'aria-valuenow="2"' in card
    assert 'aria-valuemax="4"' in card
    assert '2/4' in card
    assert 'Next S1E3 · Oct 24' in card
    assert '2024 · Show' in card
    assert 'Airing' in card
    assert 'src="https://img.example.com/p.jpg"' in card
    assert 'TMDB rating 8.2 out of 10' in card
    assert f'href="/titles/{title.pk}/"' in card
    assert 'Watching' in card
    assert f'hx-post="/titles/{title.pk}/status/"' in card
    assert 'hx-swap="none"' in card
    assert 'Your watched episodes are kept' in card


def test_the_card_next_line_states(client, portal_headers, alice, today):
    aired, aired_eps, _ = make_show('Aired next', dated=(-5, -4), extra=False)
    undated, undated_eps, _ = make_show('Undated next', dated=(-5, None), extra=False)
    done, done_eps, _ = make_show('Done', dated=(-5,), extra=False)
    for title in (aired, undated, done):
        track(alice, title)
    watch(alice, aired_eps[0], undated_eps[0], done_eps[0])
    cards = {
        re.search(r'mt-lcard-name">([^<]*)<', card).group(1): card
        for card in re.findall(
            r'<article class="mt-lcard".*?</article>',
            library(client, portal_headers),
            re.S,
        )
    }
    assert 'S1E2 available' in cards['Aired next']
    assert 'Next: date not announced' in cards['Undated next']
    assert 'No upcoming episodes' in cards['Done']


def test_movies_show_no_progress_bar_and_the_release_date(
    client, portal_headers, alice, today
):
    seen = make_movie('Seen film', release_offset=-40)
    unseen = make_movie('Unseen film', release_offset=-10)
    Title.objects.filter(pk=unseen.pk).update(runtime_minutes=136)
    track(alice, seen, 'completed')
    track(alice, unseen, 'planned')
    html = library(client, portal_headers, '?tab=movie&sort=title')
    assert names(html) == ['Seen film', 'Unseen film']
    assert 'role="progressbar"' not in html
    assert 'Sep 9, 2026' in html
    assert 'Oct 9, 2026 · 136 min' in html
    rows = library(client, portal_headers, '?tab=movie&sort=title&view=list')
    seen_row, unseen_row = re.findall(r'<tr class="mt-lrow".*?</tr>', rows, re.S)
    assert 'Watched' in seen_row
    assert 'Watched' not in unseen_row
    assert '—' in unseen_row
    assert '0/0' not in rows


def test_grid_and_list_views(client, portal_headers, alice, today):
    title, _, _ = make_show('Row', dated=(-5, 7), extra=False)
    Title.objects.filter(pk=title.pk).update(release_date=TODAY.replace(year=2023))
    track(alice, title)
    grid = library(client, portal_headers, '?view=grid')
    assert 'mt-lgrid' in grid
    assert '<table' not in grid
    assert re.search(r'name="view" value="grid"[^>]*checked', grid)
    listing = library(client, portal_headers, '?view=list')
    assert '<table' in listing
    assert 'mt-lgrid' not in listing
    assert re.search(r'name="view" value="list"[^>]*checked', listing)
    headers = re.findall(r'<th[^>]*scope="col"[^>]*>([^<]*)<', listing)
    assert headers == ['Title', 'Type', 'Progress', 'Next', 'Status']
    assert 'class="mt-lrow-name">Row<' in listing
    assert '0/2' in listing
    assert 'S1E1 available' in listing
    assert re.search(r'mt-col-wide[^>]*>\s*Show\s*<', listing)
    assert f'hx-post="/titles/{title.pk}/status/"' in listing
    assert_unique_ids(listing)


def test_titles_are_escaped(client, portal_headers, alice, today):
    track(alice, make_title('<script>alert(1)</script>'))
    translated = make_title('Plain')
    TitleTranslation.objects.create(title=translated, language='en', name='<b>x</b>')
    track(alice, translated)
    html = library(client, portal_headers, '?q=%3Cscript')
    assert '<script>alert' not in html
    assert '&lt;script&gt;' in html
    assert 'value="&lt;script"' in html
    assert '<b>x</b>' not in library(client, portal_headers)
    assert '&lt;b&gt;x&lt;/b&gt;' in library(client, portal_headers)
    assert_unique_ids(html)


def test_the_states_have_their_markup(client, portal_headers, alice, today):
    track(alice, make_title('Show A'))
    html = library(client, portal_headers)
    loading = tag(html, 'library-loading')
    assert 'role="status"' in html
    assert 'aria-busy="true"' in html
    assert 'hx-indicator="#library-loading"' in tag(html, 'library-filters')
    assert 'class="mt-lib-loading' in loading
    assert 'Loading your library' in html
    error = re.search(r'<div[^>]*x-show="failed"[^>]*>', html).group()
    assert 'role="alert"' in html
    assert error
    assert 'Couldn’t load your library' in html
    assert 'Try again' in html
    assert 'x-data' in tag(html, 'library')


def test_library_is_translated_to_russian(client, portal_headers, alice, today):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    title, episodes, _ = make_show('Рус', dated=(-5, -4), extra=False)
    track(alice, title)
    watch(alice, episodes[0])
    html = library(client, portal_headers)
    for text in (
        'Продолжить просмотр',
        'Все статусы',
        'Сериалы',
        'Фильмы',
        'Искать в библиотеке',
        'Сортировка',
        'Недавно просмотренные',
        'Вид списком',
        'Вид сеткой',
        'Не удалось загрузить библиотеку',
        'Загружаем библиотеку',
        '1 название',
    ):
        assert text in html, text
    empty = client.get('/library/?q=zzz', **portal_headers).content.decode()
    assert 'Ничего не найдено' in empty
    assert 'Сбросить фильтры' in empty


def test_russian_onboarding(client, portal_headers, alice, today):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    html = library(client, portal_headers)
    assert 'Ваша библиотека пуста' in html
    assert 'Найти тайтл' in html
    assert 'Отмечайте серии' in html


def test_anonymous_requests_are_refused(client):
    assert client.get('/library/').status_code == 401


def test_a_status_filter_value_of_another_user_leaks_nothing(
    client, portal_headers, other_headers, alice, bob, today
):
    track(alice, make_title('Mine'), 'watching')
    track(bob, make_title('Hers'), 'watching')
    assert names(library(client, other_headers)) == ['Hers']
    assert names(library(client, portal_headers, '?q=hers')) == []


def test_extra_seasons_do_not_change_the_card_progress(
    client, portal_headers, alice, today
):
    title, episodes, extra = make_show('Extras', dated=(-5, -4))
    track(alice, title)
    watch(alice, extra)
    html = library(client, portal_headers)
    assert '0/2' in html
    assert Season.objects.filter(title=title, is_extra=True).exists()
