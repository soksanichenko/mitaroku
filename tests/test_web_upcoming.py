import re
from datetime import UTC, datetime

import pytest
from catalog.models import EpisodeTranslation, TitleTranslation
from django.db import connection
from django.test.utils import CaptureQueriesContext
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import make_movie, make_show, watch

pytestmark = pytest.mark.django_db

SECTION = re.compile(
    r'<section[^>]*aria-labelledby="up-([a-z]+)"[^>]*>(.*?)</section>', re.S
)
NAME = re.compile(r'class="mt-up-name"[^>]*>([^<]*)<')


def upcoming(client, headers) -> str:
    """Return the HTML of ``/upcoming/``."""
    response = client.get('/upcoming/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def groups(html: str) -> dict[str, list[str]]:
    """Return the names of the titles of every group shown, by group key."""
    return {key: NAME.findall(body) for key, body in SECTION.findall(html)}


def track(user, title, status: str = 'watching') -> LibraryEntry:
    """Put ``title`` into the library of ``user``."""
    return LibraryEntry.objects.create(user=user, title=title, status=status)


def show_airing(name: str, offset: int | None, **fields):
    """Make a show whose only unwatched main episode is ``offset`` days away."""
    title, episodes, _ = make_show(name, dated=(offset,), extra=False, **fields)
    return title, episodes[0]


def test_upcoming_without_dates(client, portal_headers, alice, today):
    title, _ = show_airing('Undated', None)
    track(alice, title)
    response = client.get('/upcoming/', **portal_headers)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'Date not announced' in html
    assert 'No date yet' in html
    assert groups(html) == {'tba': ['Undated']}


def test_groups_by_distance(client, portal_headers, alice, today):
    for name, offset in (
        ('Now', 0),
        ('Next day', 1),
        ('Soon', 2),
        ('Edge of the week', 6),
        ('Later one', 7),
        ('Far away', 20),
        ('Unknown', None),
    ):
        track(alice, show_airing(name, offset)[0])
    html = upcoming(client, portal_headers)
    assert groups(html) == {
        'today': ['Now'],
        'tomorrow': ['Next day'],
        'week': ['Soon', 'Edge of the week'],
        'later': ['Later one', 'Far away'],
        'tba': ['Unknown'],
    }
    headings = re.findall(r'<h2 id="up-[a-z]+"[^>]*>\s*([^<]*?)\s*<', html)
    assert headings == [
        'Today',
        'Tomorrow',
        'This week',
        'Later',
        'Date not announced',
    ]


def test_empty_groups_are_not_shown(client, portal_headers, alice, today):
    track(alice, show_airing('Soon', 3)[0])
    html = upcoming(client, portal_headers)
    assert list(groups(html)) == ['week']
    for heading in ('Today', 'Tomorrow', 'Later', 'Date not announced'):
        assert f'>{heading}<' not in html.replace('\n', '')


def test_the_group_lines_give_the_dates(client, portal_headers, alice, today):
    track(alice, show_airing('Now', 0)[0])
    track(alice, show_airing('Next day', 1)[0])
    html = upcoming(client, portal_headers)
    sections = dict(SECTION.findall(html))
    # 2026-10-19 is a Monday.
    assert '<span class="mt-up-sub">Mon, Oct 19</span>' in sections['today']
    assert '<span class="mt-up-sub">Tue, Oct 20</span>' in sections['tomorrow']
    assert 'mt-up-today' in sections['today']
    assert 'mt-up-today' not in sections['tomorrow']


def test_an_item_shows_the_episode_the_date_and_the_status(
    client, portal_headers, alice, today
):
    title, episodes, _ = make_show('Journey', dated=(-9, -2, 5), extra=False)
    EpisodeTranslation.objects.create(
        episode=episodes[2], language='en', name='The Return'
    )
    track(alice, title, 'planned')
    watch(alice, episodes[0], episodes[1])
    html = upcoming(client, portal_headers)
    item = dict(SECTION.findall(html))['week']
    assert f'href="/titles/{title.pk}/"' in item
    assert 'S1E3 · The Return' in item
    assert 'Sat, Oct 24' in item
    assert 'Plan to watch' in item
    assert 'mt-poster--thumb' in item


def test_an_unnamed_episode_is_called_by_its_number(
    client, portal_headers, alice, today
):
    title, _ = show_airing('Nameless', 4)
    track(alice, title)
    assert 'S1E1 · Episode 1' in upcoming(client, portal_headers)


def test_the_episode_name_follows_the_metadata_language(
    client, portal_headers, alice, today
):
    title, episode = show_airing('Journey', 4)
    EpisodeTranslation.objects.create(episode=episode, language='en', name='Return')
    EpisodeTranslation.objects.create(episode=episode, language='ru', name='Возврат')
    TitleTranslation.objects.create(title=title, language='ru', name='Путь')
    track(alice, title)
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    html = upcoming(client, portal_headers)
    assert 'S1E1 · Возврат' in html
    assert groups(html) == {'week': ['Путь']}


def test_a_movie_is_a_premiere(client, portal_headers, alice, today):
    movie = make_movie('Premiere', release_offset=12, status='upcoming')
    track(alice, movie, 'planned')
    html = upcoming(client, portal_headers)
    assert groups(html) == {'later': ['Premiere']}
    assert 'Movie premiere' in html
    assert 'Sat, Oct 31' in html


def test_a_movie_without_a_date_waits_in_the_last_group(
    client, portal_headers, alice, today
):
    track(alice, make_movie('Someday', release_offset=None, status='upcoming'))
    html = upcoming(client, portal_headers)
    assert groups(html) == {'tba': ['Someday']}
    assert 'Movie premiere' in html


def test_a_released_or_watched_movie_is_not_listed(
    client, portal_headers, alice, today
):
    track(alice, make_movie('Out', release_offset=-3))
    seen = make_movie('Seen', release_offset=5, status='upcoming')
    track(alice, seen, 'planned')
    WatchEvent.objects.create(user=alice, title=seen, watched_at=datetime.now(UTC))
    assert 'mt-up-name' not in upcoming(client, portal_headers)


def test_only_watching_and_planned_titles_appear(client, portal_headers, alice, today):
    for status in ('watching', 'planned', 'completed', 'dropped'):
        track(alice, show_airing(f'Is {status}', 3)[0], status)
    show_airing('Not tracked', 3)
    assert groups(upcoming(client, portal_headers)) == {
        'week': ['Is planned', 'Is watching']
    }


def test_other_users_titles_do_not_appear(client, portal_headers, alice, bob, today):
    track(alice, show_airing('Mine', 3)[0])
    track(bob, show_airing('Theirs', 3)[0])
    track(bob, make_movie('Their movie', release_offset=2, status='upcoming'))
    html = upcoming(client, portal_headers)
    assert groups(html) == {'week': ['Mine']}
    assert 'Theirs' not in html and 'Their movie' not in html


def test_the_empty_state_links_to_the_library(client, portal_headers, alice, today):
    html = upcoming(client, portal_headers)
    assert 'Nothing coming up' in html
    assert re.search(r'<a class="btn btn-primary mt-touch" href="/library/">', html)
    assert groups(html) == {}


def test_a_library_without_releases_gets_the_empty_state(
    client, portal_headers, alice, today
):
    title, episodes, _ = make_show('Done', dated=(-5,), extra=False)
    track(alice, title)
    watch(alice, episodes[0])
    assert 'Nothing coming up' in upcoming(client, portal_headers)


def test_titles_are_escaped(client, portal_headers, alice, today):
    title, episode = show_airing('<script>alert(1)</script>', 3)
    EpisodeTranslation.objects.create(episode=episode, language='en', name='<b>x</b>')
    track(alice, title)
    html = upcoming(client, portal_headers)
    assert '<script>alert' not in html and '<b>x</b>' not in html
    assert '&lt;script&gt;' in html and '&lt;b&gt;x&lt;/b&gt;' in html


def test_the_query_count_does_not_grow_with_the_titles(
    client, portal_headers, alice, today
):
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert client.get('/upcoming/', **portal_headers).status_code == 200
        return len(queries)

    empty = count()
    title, episode = show_airing('First', 3)
    EpisodeTranslation.objects.create(episode=episode, language='en', name='One')
    track(alice, title)
    one = count()
    for index in range(39):
        title, episode = show_airing(f'Show {index}', index % 25 - 3)
        EpisodeTranslation.objects.create(episode=episode, language='en', name='Ep')
        TitleTranslation.objects.create(title=title, language='en', name='T')
        track(alice, title, 'planned' if index % 2 else 'watching')
    many = count()
    assert many == one
    # The empty page runs the login queries and the entries; a page with titles
    # adds the episodes and the two translation lookups.
    assert many <= empty + 3


def test_upcoming_is_translated_to_russian(client, portal_headers, alice, today):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    for name, offset in (('A', 0), ('B', 1), ('C', 3), ('D', 20), ('E', None)):
        track(alice, show_airing(name, offset)[0])
    track(alice, make_movie('M', release_offset=9, status='upcoming'), 'planned')
    html = upcoming(client, portal_headers)
    for text in (
        'Сегодня',
        'Завтра',
        'На этой неделе',
        'Позже',
        'Дата не объявлена',
        'Даты пока нет',
        'Серия 1',
        'Премьера фильма',
        'Смотрю',
        'Буду смотреть',
        'Пн, 19 октября',
        'Вт, 20 октября',
        'Чт, 22 октября',
    ):
        assert text in html, text
    assert 'Mon,' not in html and 'Today' not in html


def test_the_empty_state_is_translated(client, portal_headers, alice, today):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    html = upcoming(client, portal_headers)
    assert 'Ничего не ожидается' in html
    assert 'Nothing' not in html


def test_the_year_is_shown_when_it_is_not_this_year(
    client, portal_headers, alice, today
):
    track(alice, make_movie('Next year', release_offset=90, status='upcoming'))
    track(alice, show_airing('This year', 30)[0])
    html = upcoming(client, portal_headers)
    assert 'Sun, Jan 17, 2027' in html
    assert 'Wed, Nov 18' in html and 'Nov 18, 2026' not in html


def test_the_page_is_labelled_and_has_a_heading(client, portal_headers, alice, today):
    track(alice, show_airing('Soon', 3)[0])
    html = upcoming(client, portal_headers)
    assert '<h1 class="mt-page-title">Upcoming</h1>' in html
    assert 'aria-labelledby="up-week"' in html


def test_anonymous_requests_are_refused(client):
    assert client.get('/upcoming/').status_code == 401
