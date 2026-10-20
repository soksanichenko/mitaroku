"""One date format everywhere: month and day, the year only when it is not this year."""

import re
from datetime import date, timedelta

import pytest
from catalog.models import Episode, Title
from django.utils import timezone, translation
from tracking.models import LibraryEntry
from tracking_helpers import TODAY, make_movie, make_show, watch
from web.templatetags.web_extras import short_date

pytestmark = pytest.mark.django_db

THIS_YEAR = date(TODAY.year, 1, 20)
OLD = date(2008, 1, 20)


def render_in(language: str, value, *args) -> str:
    with translation.override(language):
        return short_date(value, *args)


@pytest.mark.parametrize(
    ('language', 'value', 'expected'),
    [
        ('en', THIS_YEAR, 'Jan 20'),
        ('en', OLD, 'Jan 20, 2008'),
        ('en', date(2023, 9, 29), 'Sep 29, 2023'),
        ('en', date(TODAY.year, 3, 1), 'Mar 1'),
        ('ru', THIS_YEAR, '20 янв.'),
        ('ru', OLD, '20 янв. 2008 г.'),
        ('ru', date(2023, 9, 29), '29 сент. 2023 г.'),
        ('ru', date(TODAY.year, 5, 5), '5 мая'),
        ('ru', date(TODAY.year, 3, 1), '1 марта'),
    ],
)
def test_the_date_helper_in_both_languages(today, language, value, expected):
    assert render_in(language, value) == expected


def test_the_date_helper_can_lead_with_the_weekday(today):
    assert render_in('en', date(2026, 10, 19), 'weekday') == 'Mon, Oct 19'
    assert render_in('ru', date(2027, 1, 17), 'weekday') == 'Вс, 17 янв. 2027 г.'


def test_the_date_helper_handles_none_and_never_uses_the_ap_forms(today):
    assert render_in('en', None) == ''
    for month in range(1, 13):
        text = render_in('en', date(TODAY.year, month, 9))
        assert '.' not in text and len(text.split()[0]) == 3, text


# The templates ------------------------------------------------------------


def synced(title: Title) -> Title:
    """Mark ``title`` synced, so opening its page queues no sync."""
    now = timezone.now()
    Title.objects.filter(pk=title.pk).update(
        synced_at=now, next_sync_at=now + timedelta(hours=1)
    )
    return title


def aired_long_ago(name: str = 'Old show') -> tuple[Title, list[Episode]]:
    title, episodes, _ = make_show(
        name, dated=(-1000, -900, 5), extra=False, status='airing'
    )
    return title, episodes


def library_page(client, headers, query: str = '') -> str:
    return client.get(f'/library/{query}', **headers).content.decode()


def test_the_strip_shows_the_year_of_an_old_episode_and_not_of_a_new_one(
    client, portal_headers, alice, today
):
    title, eps = aired_long_ago()
    LibraryEntry.objects.create(user=alice, title=title, status='watching')
    watch(alice, eps[0])
    html = library_page(client, portal_headers)
    strip = re.search(r'<section id="continue-watching".*?</section>', html, re.S)
    when = re.search(r'mt-cw-when">([^<]*)<', strip.group()).group(1)
    assert when == f'Aired {short_date(eps[1].air_date)}'
    assert re.fullmatch(r'Aired \w{3} \d+, \d{4}', when)
    assert 'Aired Jan 20' not in html


def test_the_strip_and_cards_keep_the_year_out_for_this_year(
    client, portal_headers, alice, today
):
    title, eps, _ = make_show('New', dated=(-5, 3), extra=False)
    LibraryEntry.objects.create(user=alice, title=title, status='watching')
    watch(alice, eps[0])
    html = library_page(client, portal_headers)
    assert 'Next: S1E2 · Oct 22' in html
    assert 'Next S1E2 · Oct 22' in html
    assert 'Oct 22, 2026' not in html


def test_the_strip_is_in_russian_with_russian_abbreviations(
    client, portal_headers, alice, today
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    title, eps = aired_long_ago()
    LibraryEntry.objects.create(user=alice, title=title, status='watching')
    watch(alice, eps[0])
    html = library_page(client, portal_headers)
    with translation.override('ru'):
        expected = short_date(eps[1].air_date)
    assert expected.endswith(' г.')  # an old date carries its year
    assert f'Вышла {expected}' in html


def test_movie_release_dates_in_the_library_use_the_short_form(
    client, portal_headers, alice, today
):
    movie = make_movie('Old film', release_offset=-9000)
    Title.objects.filter(pk=movie.pk).update(release_date=date(1999, 3, 31))
    LibraryEntry.objects.create(user=alice, title=movie, status='planned')
    html = library_page(client, portal_headers, '?tab=movie')
    assert 'Mar 31, 1999' in html
    assert 'March 31' not in html


def test_the_title_page_uses_the_short_form_for_episodes_and_the_long_one_for_facts(
    client, portal_headers, alice, today
):
    title, eps = aired_long_ago()
    old = date(2023, 9, 29)
    Episode.objects.filter(pk=eps[0].pk).update(air_date=old)
    synced(title)
    html = client.get(f'/titles/{title.pk}/', **portal_headers).content.decode()
    dates = ''.join(re.findall(r'<span class="mt-ep-date">(.*?)</span>', html, re.S))
    assert 'Sep 29, 2023' in dates
    assert 'Sept.' not in html

    movie = make_movie('Facts', release_offset=-9000)
    Title.objects.filter(pk=movie.pk).update(release_date=date(1999, 3, 31))
    synced(movie)
    html = client.get(f'/titles/{movie.pk}/', **portal_headers).content.decode()
    assert 'March 31, 1999' in html  # DATE_FORMAT, the long form


def test_the_continue_bar_date(client, portal_headers, alice, today):
    title, eps, _ = make_show('Soon', dated=(-5, 7, 400), extra=False)
    synced(title)
    LibraryEntry.objects.create(user=alice, title=title, status='watching')
    watch(alice, eps[0])
    html = client.get(f'/titles/{title.pk}/', **portal_headers).content.decode()
    assert 'Next: S1E2 · airs Oct 26' in html
    assert 'Oct 26, 2026' not in html
    watch(alice, eps[1])
    Episode.objects.filter(pk=eps[2].pk).update(air_date=TODAY + timedelta(days=400))
    html = client.get(f'/titles/{title.pk}/', **portal_headers).content.decode()
    assert f'airs {short_date(eps[2].air_date)}' in html
    assert re.search(r'airs \w{3} \d+, 2027', html)


def test_upcoming_dates_use_the_helper_with_a_weekday(
    client, portal_headers, alice, today
):
    title, eps, _ = make_show('Far', dated=(-5, 400), extra=False)
    LibraryEntry.objects.create(user=alice, title=title, status='watching')
    watch(alice, eps[0])
    html = client.get('/upcoming/', **portal_headers).content.decode()
    assert f'{short_date(eps[1].air_date, "weekday")}' in html
    assert re.search(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \w{3} \d+, 2027', html)
