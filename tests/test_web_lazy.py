"""Lazy episode groups: collapsed groups render their header and load rows on demand."""

import re
from datetime import timedelta

import pytest
from catalog.models import Episode, Season
from django.db import connection
from django.test.utils import CaptureQueriesContext
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import TODAY, make_long_show, watch
from web.views import titles

pytestmark = pytest.mark.django_db

PAGE_LIMIT = 150_000


@pytest.fixture
def long_show(today):
    """Return a synced 20 x 60 series (every episode aired) and its seasons."""
    return make_long_show()


def htmx(client, headers, method, path, **data):
    """Send an HTMX request."""
    return getattr(client, method)(path, data, HTTP_HX_REQUEST='true', **headers)


def page(client, headers, title) -> str:
    """Return the HTML of the title page."""
    response = client.get(f'/titles/{title.pk}/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def rows_of(html: str, season: Season) -> str:
    """Return the ``<ul>`` of the rows of ``season`` in ``html`` ('' if none)."""
    start = html.find(f'id="grp-{season.pk}-rows"')
    assert start != -1
    found = re.compile(r'<ul class="mt-eps">.*?</ul>', re.S).search(html, start)
    next_group = html.find('<section class="mt-grp"', start)
    if not found or (next_group != -1 and found.start() > next_group):
        return ''
    return found.group()


def container(html: str, season: Season) -> str:
    """Return the opening tag of the rows container of ``season``."""
    return re.search(rf'<div id="grp-{season.pk}-rows"[^>]*>', html).group()


def header(html: str, season: Season) -> str:
    """Return the toggle button of ``season`` with its count."""
    return re.search(
        rf'<button[^>]*aria-controls="grp-{season.pk}-rows".*?</button>', html, re.S
    ).group()


def test_a_collapsed_long_group_renders_only_its_header(
    client, portal_headers, alice, long_show
):
    title, seasons = long_show
    html = page(client, portal_headers, title)
    assert len(html.encode()) < PAGE_LIMIT
    assert html.count('<li class="mt-ep ') == 60  # the open first group only
    assert rows_of(html, seasons[0])
    lazy = seasons[5]
    assert rows_of(html, lazy) == ''
    tag = container(html, lazy)
    assert 'data-lazy' in tag and 'x-cloak' in tag
    assert f'hx-get="/seasons/{lazy.pk}/rows/"' in tag
    assert 'hx-swap="innerHTML"' in tag
    assert 'x-show="has(%d)"' % lazy.pk in tag
    assert 'data-lazy' not in container(html, seasons[0])
    assert f'id="ep-{Episode.objects.filter(season=lazy).first().pk}"' not in html
    assert html.count('data-lazy') >= 19
    assert 'Loading episodes…' in html
    assert 'aria-busy' not in tag  # only while a request runs
    # the toggle still names and controls its rows
    toggle = header(html, lazy)
    assert f'aria-controls="grp-{lazy.pk}-rows"' in toggle
    assert 'aria-expanded="false"' in toggle


def test_the_threshold_is_a_named_constant(client, portal_headers, alice, today):
    assert titles.LAZY_ROW_LIMIT == 10
    title, seasons = make_long_show(seasons=3, per_season=titles.LAZY_ROW_LIMIT)
    other = Season.objects.create(
        title=title,
        number=4,
        kind=Season.Kind.TV,
        episode_count=11,
        episode_source=Season.EpisodeSource.TMDB,
    )
    Episode.objects.bulk_create(
        Episode(season=other, number=n, air_date=TODAY - timedelta(days=n))
        for n in range(1, titles.LAZY_ROW_LIMIT + 2)
    )
    html = page(client, portal_headers, title)
    for season in seasons:
        assert 'data-lazy' not in container(html, season)
        assert rows_of(html, season)
    assert 'data-lazy' in container(html, other)
    assert rows_of(html, other) == ''


def test_lazy_headers_keep_the_counts_and_the_group_check(
    client, portal_headers, alice, long_show
):
    title, seasons = long_show
    episodes = list(Episode.objects.filter(season=seasons[6]).order_by('number'))
    watch(alice, *episodes[:7])
    watch(alice, *Episode.objects.filter(season=seasons[7]))
    html = page(client, portal_headers, title)
    assert '7/60' in header(html, seasons[6])
    assert '60/60' in header(html, seasons[7])
    assert '0/60' in header(html, seasons[8])
    partly = re.search(rf'<span[^>]*id="check-grp-{seasons[6].pk}"[^>]*>', html).group()
    assert 'mt-check--partly' in partly
    done = re.search(rf'<span[^>]*id="check-grp-{seasons[7].pk}"[^>]*>', html).group()
    assert 'mt-check--watched' in done
    assert f'id="mark-grp-{seasons[6].pk}"' in html


def test_the_group_of_the_next_episode_opens_with_rows(
    client, portal_headers, alice, long_show
):
    title, seasons = long_show
    watch(alice, *Episode.objects.filter(season__in=seasons[:4]))
    html = page(client, portal_headers, title)
    assert rows_of(html, seasons[4])
    assert 'data-lazy' not in container(html, seasons[4])
    assert rows_of(html, seasons[0]) == ''
    assert 'data-lazy' in container(html, seasons[0])


def test_season_rows_equal_the_rows_of_the_page(
    client, portal_headers, alice, long_show, monkeypatch
):
    title, seasons = long_show
    watch(alice, *Episode.objects.filter(season=seasons[3]).order_by('number')[:5])
    lazy = page(client, portal_headers, title)
    assert rows_of(lazy, seasons[3]) == ''
    monkeypatch.setattr(titles, 'LAZY_ROW_LIMIT', 1000)
    eager = page(client, portal_headers, title)
    expected = rows_of(eager, seasons[3])
    assert expected.count('<li ') == 60
    response = client.get(f'/seasons/{seasons[3].pk}/rows/', **portal_headers)
    assert response.status_code == 200
    body = response.content.decode()
    assert rows_of(f'<div id="grp-{seasons[3].pk}-rows">{body}', seasons[3]) == expected
    assert body.count('mt-check--watched') == 5
    assert (
        f'id="check-ep-{Episode.objects.filter(season=seasons[3]).first().pk}"' in body
    )
    assert 'id="more-ep-' in body
    assert '<section' not in body and 'id="grp-' not in body
    # one group: the size of the open group of the page, a fraction of the page
    open_group = rows_of(eager, seasons[0])
    assert abs(len(body.encode()) - len(open_group.encode())) < 2000


def test_season_rows_are_for_the_signed_in_user_only(
    client, portal_headers, other_headers, alice, bob, long_show
):
    title, seasons = long_show
    watch(alice, *Episode.objects.filter(season=seasons[2]))
    LibraryEntry.objects.create(user=alice, title=title, note='alice secret')
    mine = client.get(f'/seasons/{seasons[2].pk}/rows/', **portal_headers)
    assert mine.content.decode().count('mt-check--watched') == 60
    assert not WatchEvent.objects.for_user(bob).exists()
    theirs = client.get(f'/seasons/{seasons[2].pk}/rows/', **other_headers)
    body = theirs.content.decode()
    assert theirs.status_code == 200
    assert 'mt-check--watched' not in body
    assert body.count('mt-check--unwatched') == 60
    assert 'alice secret' not in body
    assert '×' not in body


def test_season_rows_headers_and_methods(client, portal_headers, alice, long_show):
    _, seasons = long_show
    url = f'/seasons/{seasons[1].pk}/rows/'
    response = client.get(url, **portal_headers)
    assert response.status_code == 200
    control = response['Cache-Control']
    assert 'private' in control and 'no-store' in control
    for method in ('post', 'put', 'delete', 'head'):
        assert getattr(client, method)(url, **portal_headers).status_code == 405
    assert client.get('/seasons/999999/rows/', **portal_headers).status_code == 404


def test_season_rows_need_a_login(client, long_show):
    _, seasons = long_show
    assert client.get(f'/seasons/{seasons[1].pk}/rows/').status_code == 401


def test_season_rows_of_a_title_that_is_not_synced_is_409(
    client, portal_headers, alice, long_show
):
    title, seasons = long_show
    type(title).objects.filter(pk=title.pk).update(synced_at=None)
    url = f'/seasons/{seasons[1].pk}/rows/'
    assert client.get(url, **portal_headers).status_code == 409


def test_season_rows_cost_constant_queries(client, portal_headers, alice, long_show):
    _, seasons = long_show
    url = f'/seasons/{seasons[1].pk}/rows/'
    assert client.get(url, **portal_headers).status_code == 200  # warms up the profile
    with CaptureQueriesContext(connection) as small:
        client.get(url, **portal_headers)
    extra = Season.objects.create(
        title=seasons[0].title, number=30, kind=Season.Kind.TV, episode_count=60
    )
    Episode.objects.bulk_create(
        Episode(season=extra, number=n, air_date=TODAY - timedelta(days=n))
        for n in range(1, 200)
    )
    with CaptureQueriesContext(connection) as large:
        client.get(url, **portal_headers)
    assert len(large) == len(small)
    with CaptureQueriesContext(connection) as whole:
        client.get(f'/titles/{seasons[0].title_id}/', **portal_headers)
    assert len(small) <= len(whole)


def test_an_episode_action_answers_with_its_group_rows(
    client, portal_headers, alice, long_show
):
    title, seasons = long_show
    episode = Episode.objects.filter(season=seasons[9], number=3).get()
    html = htmx(
        client, portal_headers, 'post', f'/episodes/{episode.pk}/watch/', watched='1'
    ).content.decode()
    assert rows_of(html, seasons[9]).count('<li ') == 60
    assert 'data-lazy' not in container(html, seasons[9])
    # an answer is no first paint: a cloaked row would refuse the focus htmx restores
    assert 'x-cloak' not in container(html, seasons[9])
    assert len(html.encode()) < PAGE_LIMIT
    season = htmx(
        client, portal_headers, 'post', f'/seasons/{seasons[11].pk}/watch/', watched='1'
    ).content.decode()
    assert rows_of(season, seasons[11]).count('mt-check--watched') == 60
    assert 'data-lazy' not in container(season, seasons[11])
    assert '60/60' in header(season, seasons[11])


def test_up_to_sends_the_earlier_groups_lazy_with_their_counts(
    client, portal_headers, alice, long_show
):
    title, seasons = long_show
    episode = Episode.objects.filter(season=seasons[10], number=5).get()
    response = htmx(client, portal_headers, 'post', f'/episodes/{episode.pk}/up-to/')
    html = response.content.decode()
    assert len(html.encode()) < PAGE_LIMIT
    assert rows_of(html, seasons[10]).count('<li ') == 60
    for earlier in seasons[:10]:
        assert (
            'hx-swap-oob="true"'
            in re.search(
                rf'<section class="mt-grp" id="grp-{earlier.pk}"[^>]*>', html
            ).group()
        )
        assert rows_of(html, earlier) == ''
        assert 'data-lazy' in container(html, earlier)
        assert '60/60' in header(html, earlier)
    assert '5/60' in header(html, seasons[10])
    assert f'id="grp-{seasons[11].pk}"' not in html
    assert WatchEvent.objects.for_user(alice).count() == 605


def test_watch_all_and_completing_are_small(client, portal_headers, alice, long_show):
    title, seasons = long_show
    response = htmx(
        client, portal_headers, 'post', f'/titles/{title.pk}/watch-all/', watched='1'
    )
    html = response.content.decode()
    assert len(html.encode()) < PAGE_LIMIT
    assert 'id="episodes"' in html
    assert '60/60' in header(html, seasons[9])
    assert rows_of(html, seasons[9]) == ''
    WatchEvent.objects.for_user(alice).delete()
    LibraryEntry.objects.for_user(alice).delete()
    status = htmx(
        client,
        portal_headers,
        'post',
        f'/titles/{title.pk}/status/',
        status='completed',
    ).content.decode()
    assert len(status.encode()) < PAGE_LIMIT
    assert 'id="episodes"' in status and 'hx-swap-oob="true"' in status
    assert '60/60' in header(status, seasons[15])
    assert rows_of(status, seasons[15]) == ''


def anilist_show(today, rows=12):
    """Return a title with an AniList season of ``rows`` episodes and a bare one."""
    title, seasons = make_long_show(seasons=3, per_season=12, name='Degraded')
    Season.objects.filter(pk=seasons[1].pk).update(
        episode_source=Season.EpisodeSource.ANILIST
    )
    Season.objects.filter(pk=seasons[2].pk).update(
        episode_source=Season.EpisodeSource.NONE
    )
    Episode.objects.filter(season=seasons[2]).delete()
    return title, seasons


def test_a_degraded_anilist_group_keeps_its_note_in_the_rows(
    client, portal_headers, alice, today
):
    title, seasons = anilist_show(today)
    note = 'Episode titles are not available for this entry'
    html = page(client, portal_headers, title)
    assert 'data-lazy' in container(html, seasons[1])
    assert note not in html
    body = client.get(f'/seasons/{seasons[1].pk}/rows/', **portal_headers)
    text = body.content.decode()
    assert f'<p class="mt-ep-note">{note}</p>' in text
    assert text.count('<li ') == 12


def test_a_degraded_none_group_has_no_rows_and_is_never_lazy(
    client, portal_headers, alice, today
):
    title, seasons = anilist_show(today)
    html = page(client, portal_headers, title)
    assert 'data-lazy' not in container(html, seasons[2])
    assert 'Episodes are not known yet' in html
    text = client.get(
        f'/seasons/{seasons[2].pk}/rows/', **portal_headers
    ).content.decode()
    assert 'Episodes are not known yet' in text
    assert '<li ' not in text


def test_the_lazy_markup_has_loading_and_error_states(
    client, portal_headers, alice, long_show
):
    title, seasons = long_show
    html = page(client, portal_headers, title)
    tag = container(html, seasons[4])
    assert 'hx-trigger="mt-load-rows"' in tag
    assert 'hx-target="this"' in tag
    assert 'class="mt-lazy-loading"' in html and 'role="status"' in html
    assert 'class="mt-lazy-error"' in html and 'role="alert"' in html
    assert 'Couldn’t load the episodes' in html
    # the Alpine scope loads only containers that are still lazy
    assert "hasAttribute('data-lazy')" in html


def test_the_lazy_states_are_translated(client, portal_headers, alice, long_show):
    title, _ = long_show
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    html = page(client, portal_headers, title)
    assert 'Загрузка серий…' in html
    assert 'Не удалось загрузить серии' in html
    assert 'Loading episodes' not in html
