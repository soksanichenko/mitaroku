import re
from datetime import timedelta

import pytest
from catalog.models import Title
from django.template.loader import render_to_string
from django.utils import timezone
from providers.search import SearchResults
from providers.types import SearchHit
from tracking.models import AddRequest, LibraryEntry

pytestmark = pytest.mark.django_db

# 5 queries are the session, the user, the admin group check and the profile; the
# lookup of library entries adds 2 however many hits there are.
NUM_SEARCH_QUERIES = 7
NUM_POLL_QUERIES = 7  # 5 for the sign-in, then the expiry update and the read
ADD = {'provider': 'tmdb_tv', 'external_id': '1396', 'status': 'planned'}


def hit(kind: str, name: str, number: int = 1, **kwargs) -> SearchHit:
    """Build a search hit of ``kind`` (``tv``, ``anime`` or ``movie``)."""
    provider = {'tv': 'tmdb_tv', 'anime': 'anilist', 'movie': 'tmdb_movie'}[kind]
    values = {
        'original_title': name,
        'year': 2023,
        'poster_url': '',
        'overview': '',
        **kwargs,
    }
    return SearchHit(provider, str(number), kind, name, **values)


def anime_hit(name: str) -> SearchHit:
    return hit('anime', name)


def pending(user, number: int = 1396, **kwargs) -> AddRequest:
    """Create a pending add request of ``user``."""
    return AddRequest.objects.create(
        user=user,
        provider='tmdb_tv',
        external_id=str(number),
        status='planned',
        **kwargs,
    )


def test_short_query_calls_no_provider(client, portal_headers, patched_search):
    client.get('/search/?q=a', **portal_headers)
    client.get('/search/?q=%20a%20', **portal_headers)
    client.get('/search/', **portal_headers)
    assert patched_search.calls == []


def test_idle_and_typing_states_have_their_hints(
    client, portal_headers, patched_search
):
    idle = client.get('/search/', **portal_headers).content.decode()
    typing = client.get('/search/?q=a', **portal_headers).content.decode()
    assert 'Type a title' in idle
    assert 'Type at least 2 characters' in typing
    assert 'Type at least 2 characters' not in idle


def test_search_input_is_wired_to_htmx(client, portal_headers, patched_search):
    html = client.get('/search/?q=frieren', **portal_headers).content.decode()
    field = re.search(r'<input id="mt-q"[^>]*>', html, re.S).group()
    assert 'hx-get="/search/"' in field
    assert 'hx-trigger="input changed delay:400ms"' in field
    assert 'hx-target="#search-results"' in field
    assert 'hx-replace-url="true"' in field
    assert 'hx-push-url' not in field
    assert 'value="frieren"' in field
    assert 'id="search-results"' in html


def test_query_goes_to_the_provider_in_the_metadata_language(
    client, portal_headers, patched_search, alice
):
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    client.get('/search/?q=%20frieren%20', **portal_headers)
    assert patched_search.calls == [('frieren', 'ru')]


def test_query_is_capped_at_100_characters(client, portal_headers, patched_search):
    client.get('/search/?q=' + 'x' * 300, **portal_headers)
    assert patched_search.calls == [('x' * 100, 'en')]


def test_search_with_one_provider_down(client, portal_headers, patched_search):
    patched_search.result = SearchResults(hits=[anime_hit('Frieren')], failed=['tmdb'])
    html = client.get('/search/?q=frieren', **portal_headers).content.decode()
    assert 'Frieren' in html and 'TMDB' in html and 'incomplete' in html.lower()


def test_search_with_one_provider_down_and_no_hits_says_both(
    client, portal_headers, patched_search
):
    patched_search.result = SearchResults(failed=['anilist'])
    html = client.get('/search/?q=frieren', **portal_headers).content.decode()
    assert 'No results for' in html and 'AniList' in html


def test_search_with_both_providers_down_shows_the_error_state(
    client, portal_headers, patched_search
):
    patched_search.result = SearchResults(failed=['tmdb', 'anilist'])
    response = client.get('/search/?q=frieren', **portal_headers)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'role="alert"' in html
    assert 'hx-get="/search/?q=frieren"' in html
    assert 'No results for' not in html


def test_no_results(client, portal_headers, patched_search):
    html = client.get('/search/?q=zzzz', **portal_headers).content.decode()
    assert 'No results for' in html and 'zzzz' in html


def test_results_are_grouped_and_limited_to_five_per_group(
    client, portal_headers, patched_search
):
    patched_search.result = SearchResults(
        hits=[hit('tv', f'Show {n}', n) for n in range(1, 8)]
        + [hit('movie', 'Film', 50), hit('anime', 'Cartoon', 60)]
    )
    html = client.get('/search/?q=show', **portal_headers).content.decode()
    assert html.index('>Shows ') < html.index('>Anime ') < html.index('>Movies ')
    assert 'Show 5' in html and 'Show 6' not in html
    assert 'Show all 7' in html
    assert 'Show all 1' not in html
    assert 'href="/search/?q=show&amp;all=tv"' in html
    full = client.get('/search/?q=show&all=tv', **portal_headers).content.decode()
    assert 'Show 7' in full and 'Show all 7' not in full


def test_empty_groups_are_hidden(client, portal_headers, patched_search):
    patched_search.result = SearchResults(hits=[hit('movie', 'Film')])
    html = client.get('/search/?q=film', **portal_headers).content.decode()
    assert '>Movies ' in html and '>Shows ' not in html and '>Anime ' not in html


def test_all_parameter_is_whitelisted(client, portal_headers, patched_search):
    patched_search.result = SearchResults(
        hits=[hit('tv', f'S{n}', n) for n in range(7)]
    )
    for value in ('bogus', 'TV', 'anime', '<script>'):
        html = client.get(
            '/search/', {'q': 'show', 'all': value}, **portal_headers
        ).content.decode()
        names = re.findall(r'class="mt-result-name">(?:<a[^>]*>)?([^<]+)', html)
        assert 'S0' in names and 'S6' not in names and '<script>' not in html


def test_row_shows_name_original_year_and_kind(client, portal_headers, patched_search):
    patched_search.result = SearchResults(
        hits=[
            hit('anime', 'Frieren', original_title='Sousou no Frieren', year=2023),
            hit('tv', 'Same', 2, original_title='Same', year=None),
        ]
    )
    html = client.get('/search/?q=fr', **portal_headers).content.decode()
    assert 'Sousou no Frieren' in html and '2023' in html
    assert html.count('>Same<') == 1


def test_hit_text_is_escaped(client, portal_headers, patched_search):
    patched_search.result = SearchResults(
        hits=[hit('tv', '<b>bold</b>', overview='<script>alert(1)</script>')]
    )
    html = client.get('/search/?q=bold', **portal_headers).content.decode()
    assert '<b>bold</b>' not in html and '<script>alert(1)' not in html
    assert '&lt;b&gt;bold&lt;/b&gt;' in html and '&amp;lt;' not in html


def test_hits_not_in_the_library_get_the_add_picker(
    client, portal_headers, patched_search
):
    patched_search.result = SearchResults(hits=[hit('tv', 'Breaking Bad', 1396)])
    html = client.get('/search/?q=breaking', **portal_headers).content.decode()
    assert 'hx-post="/search/add/"' in html
    assert 'name="provider" value="tmdb_tv"' in html
    assert 'name="external_id" value="1396"' in html
    assert 'aria-label="Add Breaking Bad to library"' in html


def test_hits_already_in_the_library_show_their_status(
    client, portal_headers, patched_search, alice, bob, synced_movie
):
    LibraryEntry.objects.create(user=alice, title=synced_movie, status='completed')
    patched_search.result = SearchResults(
        hits=[hit('movie', 'The Matrix', 603), hit('tv', 'Other', 9)]
    )
    html = client.get('/search/?q=matrix', **portal_headers).content.decode()
    assert 'aria-label="In your library: Completed"' in html
    assert f'href="/titles/{synced_movie.pk}/"' in html
    assert 'aria-label="Add Other to library"' in html
    assert 'Add The Matrix' not in html
    assert 'name="external_id" value="603"' not in html


def test_another_users_library_does_not_show(
    client, portal_headers, patched_search, bob, synced_movie
):
    LibraryEntry.objects.create(user=bob, title=synced_movie, status='completed')
    patched_search.result = SearchResults(hits=[hit('movie', 'The Matrix', 603)])
    html = client.get('/search/?q=matrix', **portal_headers).content.decode()
    assert 'In your library' not in html
    assert 'name="external_id" value="603"' in html


def test_htmx_request_returns_only_the_results_fragment(
    client, portal_headers, patched_search
):
    patched_search.result = SearchResults(hits=[hit('tv', 'Breaking Bad', 1396)])
    response = client.get(
        '/search/?q=breaking', HTTP_HX_REQUEST='true', **portal_headers
    )
    html = response.content.decode()
    assert 'Breaking Bad' in html
    assert '<html' not in html and 'mt-header' not in html
    assert 'id="search-results"' in html
    vary = [name.strip() for name in response['Vary'].split(',')]
    assert 'HX-Request' in vary and 'HX-History-Restore-Request' in vary


def test_history_restore_gets_the_full_page(client, portal_headers, patched_search):
    response = client.get(
        '/search/?q=breaking',
        HTTP_HX_REQUEST='true',
        HTTP_HX_HISTORY_RESTORE_REQUEST='true',
        **portal_headers,
    )
    assert '<html' in response.content.decode()


def test_full_page_for_a_normal_request(client, portal_headers, patched_search):
    html = client.get('/search/?q=breaking', **portal_headers).content.decode()
    assert '<html' in html and 'mt-header' in html
    assert 'id="search-loading"' in html


def test_add_button_returns_a_polling_fragment(client, portal_headers, queued, alice):
    response = client.post('/search/add/', ADD, **portal_headers)
    assert response.status_code == 200
    html = response.content.decode()
    request = AddRequest.objects.get(user=alice)
    assert 'hx-trigger="load delay:2s"' in html
    assert f'hx-get="/add/{request.pk}/"' in html
    assert 'hx-swap="outerHTML"' in html
    assert 'role="status"' in html


def test_add_of_a_synced_title_returns_the_open_link(
    client, portal_headers, alice, synced_movie
):
    data = {'provider': 'tmdb_movie', 'external_id': '603', 'status': 'planned'}
    response = client.post('/search/add/', data, **portal_headers)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'Plan to watch' in html
    assert f'href="/titles/{synced_movie.pk}/"' in html and 'Open' in html
    assert LibraryEntry.objects.for_user(alice).get().title == synced_movie
    assert 'hx-trigger' not in html


def test_too_many_pending_is_a_normal_fragment(client, portal_headers, queued, alice):
    for number in range(1, 6):
        pending(alice, number)
    response = client.post('/search/add/', ADD, **portal_headers)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'already being added' in html
    assert 'hx-trigger="load' not in html
    assert AddRequest.objects.for_user(alice).count() == 5


def test_forged_values_are_rejected(client, portal_headers):
    for data in (
        {'provider': 'imdb', 'external_id': '1', 'status': 'planned'},
        {'provider': 'tmdb_tv', 'external_id': 'x', 'status': 'planned'},
        {'provider': 'tmdb_tv', 'external_id': '1', 'status': 'hacked'},
        {'provider': 'tmdb_tv', 'external_id': '1'},
        {'external_id': '1', 'status': 'planned'},
        {},
    ):
        assert client.post('/search/add/', data, **portal_headers).status_code == 400
    assert not Title.objects.exists()
    assert not AddRequest.objects.exists()


def test_extra_fields_from_the_browser_are_ignored(
    client, portal_headers, queued, alice
):
    data = {**ADD, 'title': 'Hacked', 'original_title': 'Hacked', 'user': '99'}
    assert client.post('/search/add/', data, **portal_headers).status_code == 200
    assert not Title.objects.filter(original_title='Hacked').exists()
    assert AddRequest.objects.get().user == alice


def test_add_only_accepts_post(client, portal_headers):
    assert client.get('/search/add/', **portal_headers).status_code == 405


def test_add_status_is_visible_only_to_its_owner(
    client, portal_headers, other_headers, alice, bob
):
    request = pending(bob)
    assert client.get(f'/add/{request.pk}/', **portal_headers).status_code == 404
    assert client.get(f'/add/{request.pk}/', **other_headers).status_code == 200
    assert client.get('/add/99999/', **portal_headers).status_code == 404


def test_add_status_of_a_pending_request_keeps_polling(client, portal_headers, alice):
    request = pending(alice)
    html = client.get(f'/add/{request.pk}/', **portal_headers).content.decode()
    assert 'hx-trigger="load delay:2s"' in html
    assert f'hx-get="/add/{request.pk}/"' in html


def test_add_status_fragment_for_done_and_failed(
    client, portal_headers, alice, synced_movie
):
    done = pending(alice, state='done', title=synced_movie)
    html = client.get(f'/add/{done.pk}/', **portal_headers).content.decode()
    assert 'Added' in html and f'href="/titles/{synced_movie.pk}/"' in html
    assert 'hx-trigger' not in html

    failed = pending(alice, 1399, state='failed', error='provider')
    html = client.get(f'/add/{failed.pk}/', **portal_headers).content.decode()
    assert "Couldn't add this title" in html
    assert 'hx-post="/search/add/"' in html
    assert 'name="external_id" value="1399"' in html
    assert 'name="status" value="planned"' in html
    assert 'hx-trigger="load' not in html


@pytest.mark.parametrize('error', ['', 'provider', 'unexpected'])
def test_failed_request_with_any_error_code_renders(
    client, portal_headers, alice, error
):
    failed = pending(alice, state='failed', error=error)
    response = client.get(f'/add/{failed.pk}/', **portal_headers)
    assert response.status_code == 200
    assert "Couldn't add this title" in response.content.decode()


def test_a_stale_pending_request_is_shown_as_timed_out(client, portal_headers, alice):
    request = pending(alice)
    AddRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(hours=1, minutes=1)
    )
    html = client.get(f'/add/{request.pk}/', **portal_headers).content.decode()
    assert "Couldn't add this title" in html and 'took too long' in html
    assert 'hx-trigger="load' not in html
    request.refresh_from_db()
    assert (request.state, request.error) == ('failed', 'timeout')


def test_a_recent_pending_request_is_not_timed_out(client, portal_headers, alice):
    request = pending(alice)
    AddRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(minutes=59)
    )
    client.get(f'/add/{request.pk}/', **portal_headers)
    request.refresh_from_db()
    assert request.state == 'pending'


def test_viewing_the_status_never_times_out_another_users_request(
    client, portal_headers, alice, bob
):
    request = pending(bob)
    AddRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(hours=2)
    )
    client.get(f'/add/{request.pk}/', **portal_headers)
    request.refresh_from_db()
    assert request.state == 'pending'


def test_search_requires_login(client):
    assert client.get('/search/?q=matrix').status_code == 401
    assert client.get('/search/', HTTP_HX_REQUEST='true').status_code == 401
    assert client.post('/search/add/', ADD).status_code == 401
    assert client.get('/add/1/').status_code == 401


def test_search_page_is_translated_to_russian(
    client, portal_headers, patched_search, alice
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    patched_search.result = SearchResults(
        hits=[hit('tv', 'Breaking Bad', 1396), hit('movie', 'Film', 2)],
        failed=['anilist'],
    )
    html = client.get('/search/?q=breaking', **portal_headers).content.decode()
    assert 'Сериалы' in html and 'Фильмы' in html
    assert 'Добавить' in html
    assert 'Shows' not in html and 'incomplete' not in html.lower()
    empty = client.get('/search/', **portal_headers).content.decode()
    assert 'Введите название' in empty
    typing = client.get('/search/?q=a', **portal_headers).content.decode()
    assert 'Введите не менее 2 символов' in typing


@pytest.mark.parametrize(
    'names',
    [('', 'Original'), (None, 'Original'), ('', ''), (None, None)],
    ids=['empty-title', 'none-title', 'both-empty', 'both-none'],
)
def test_a_hit_without_a_title_does_not_break_the_page(
    client, portal_headers, patched_search, names
):
    title, original = names
    bad = SearchHit('tmdb_tv', '7', 'tv', title, original, 2020, '', '')
    patched_search.result = SearchResults(hits=[bad, hit('tv', 'Fine', 8)])
    response = client.get('/search/?q=anything', **portal_headers)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'Fine' in html
    expected = original or 'Untitled'
    assert f'aria-label="Add {expected} to library"' in html
    assert f'>{expected}<' in html


def test_the_picker_without_a_title_or_a_name_renders_a_generic_label():
    html = render_to_string(
        'web/components/_status_picker.html',
        {'entry': None, 'action': '/search/add/', 'display_name': ''},
    )
    assert 'aria-label="Add Untitled to library"' in html


def test_the_library_lookup_does_not_grow_with_the_hits(
    client,
    portal_headers,
    patched_search,
    alice,
    synced_movie,
    django_assert_num_queries,
):
    LibraryEntry.objects.create(user=alice, title=synced_movie, status='planned')
    hits = [hit('tv', f'Show {n}', n) for n in range(1, 12)]
    patched_search.result = SearchResults(hits=[*hits, hit('movie', 'Matrix', 603)])
    client.get('/search/?q=warm', **portal_headers)  # session, profile, user
    with django_assert_num_queries(NUM_SEARCH_QUERIES):
        client.get('/search/?q=show', **portal_headers)


def test_one_poll_of_add_status_costs_a_few_queries(
    client, portal_headers, alice, django_assert_max_num_queries
):
    request = pending(alice)
    client.get(f'/add/{request.pk}/', **portal_headers)
    with django_assert_max_num_queries(NUM_POLL_QUERIES):
        client.get(f'/add/{request.pk}/', **portal_headers)


def test_the_too_many_pending_fragment_carries_the_retry_fields(
    client, portal_headers, queued, alice
):
    for number in range(1, 6):
        pending(alice, number)
    data = {'provider': 'tmdb_movie', 'external_id': '603', 'status': 'dropped'}
    html = client.post('/search/add/', data, **portal_headers).content.decode()
    assert '5 titles are already being added' in html
    assert 'name="provider" value="tmdb_movie"' in html
    assert 'name="external_id" value="603"' in html
    assert 'name="status" value="dropped"' in html
    assert 'hx-post="/search/add/"' in html and 'hx-include="closest .mt-add"' in html


def test_an_untitled_hit_is_translated(client, portal_headers, patched_search, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    bad = SearchHit('tmdb_tv', '7', 'tv', '', '', None, '', '')
    patched_search.result = SearchResults(hits=[bad])
    html = client.get('/search/?q=anything', **portal_headers).content.decode()
    assert 'Без названия' in html
