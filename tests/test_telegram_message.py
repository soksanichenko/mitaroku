import html
from datetime import date, timedelta

import pytest
from catalog.models import TitleTranslation
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation
from notification_helpers import NOW, TODAY, Kind, load, make_title, notify
from notifications.channel import absolute_url
from notifications.telegram_message import LIMIT, build_message
from web.templatetags.web_extras import short_date

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_url():
    """Give every test the same public URL."""
    with override_settings(PUBLIC_URL='https://m.example'):
        yield


def link(title) -> str:
    """Return the absolute URL of a title page."""
    return absolute_url(reverse('title', args=[title.pk]))


def test_the_message_lists_the_item_with_a_link_in_the_profile_language(alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    title = make_title('Show')
    notify(alice, title)

    text = build_message(alice, load(alice), NOW)

    assert 'Новая серия' in text
    assert 'Сегодня' in text
    assert f'<a href="{link(title)}">Show</a>' in text
    assert absolute_url(reverse('notifications')) in text
    assert 'Все уведомления' in text


def test_the_first_line_is_the_bold_count_in_english(alice):
    notify(alice, make_title('A'))
    notify(alice, make_title('B'))

    text = build_message(alice, load(alice), NOW)

    assert text.splitlines()[0] == '<b>2 new notifications</b>'
    assert '<b>Today</b>' in text


def test_an_item_line_has_the_label_the_link_and_the_text(alice):
    title = make_title('Show')
    notify(alice, title, kind=Kind.MOVIE_RELEASED)

    text = build_message(alice, load(alice), NOW)

    assert f'Released: <a href="{link(title)}">Show</a> — The movie was released.' in (
        text.splitlines()
    )


def test_a_date_change_shows_both_dates(alice):
    title = make_title('Moved')
    notify(
        alice,
        title,
        kind=Kind.RELEASE_CHANGED,
        payload={'from': '2026-05-01', 'to': '2026-06-02'},
    )

    text = build_message(alice, load(alice), NOW)

    old, new = short_date(date(2026, 5, 1)), short_date(date(2026, 6, 2))
    assert f'Date changed: <a href="{link(title)}">Moved</a> ({old} → {new})' in text


def test_an_announced_date_shows_the_date_alone(alice):
    notify(
        alice,
        make_title('Soon'),
        kind=Kind.RELEASE_ANNOUNCED,
        payload={'to': '2026-06-02'},
    )

    text = build_message(alice, load(alice), NOW)

    assert f'</a> ({short_date(date(2026, 6, 2))})' in text
    assert '→' not in text


def test_items_are_grouped_by_day_newest_first(alice):
    notify(alice, make_title('Old Show'), day=TODAY - timedelta(days=1))
    notify(alice, make_title('Today Show'))

    text = build_message(alice, load(alice), NOW)

    positions = [
        text.index(marker)
        for marker in ('<b>Today</b>', 'Today Show', '<b>Yesterday</b>', 'Old Show')
    ]
    assert positions == sorted(positions)


def test_a_hostile_title_is_escaped(alice):
    notify(alice, make_title('<b>&"x"'))

    text = build_message(alice, load(alice), NOW)

    assert '<b>&"x"' not in text
    assert '&lt;b&gt;&amp;&quot;x&quot;' in text
    assert text.count('<a ') == text.count('</a>') == 2


def test_a_hostile_translation_cannot_break_the_link_markup(alice):
    title = make_title()
    hostile = '"></a><a href="https://evil.example">'
    TitleTranslation.objects.create(title=title, language='en', name=hostile)
    notify(alice, title)

    text = build_message(alice, load(alice), NOW)

    assert 'evil.example">' not in text
    assert html.escape(hostile, quote=True) in text
    assert text.count('<a ') == text.count('</a>') == 2


def test_more_than_fifty_items_are_cut_with_an_and_n_more_line(alice):
    for number in range(60):
        notify(
            alice,
            make_title(f'Show {number:02}'),
            created_at=NOW - timedelta(minutes=number),
        )

    text = build_message(alice, load(alice), NOW)

    assert text.splitlines()[0] == '<b>60 new notifications</b>'
    assert text.count('/titles/') == 50
    assert 'Show 49' in text
    assert 'Show 50' not in text
    assert 'and 10 more' in text
    assert absolute_url(reverse('notifications')) in text
    alice.profile.interface_language = 'ru'
    assert 'и ещё 10' in build_message(alice, load(alice), NOW)


def test_a_long_message_is_cut_to_the_limit_on_whole_items(alice):
    for number in range(50):
        notify(
            alice,
            make_title(f'{number:02} ' + 'x' * 197),
            created_at=NOW - timedelta(minutes=number),
        )

    text = build_message(alice, load(alice), NOW)

    assert len(text) <= LIMIT
    assert text.count('<a ') == text.count('</a>')
    assert text.rstrip().endswith('</a>')
    shown = text.count('/titles/')
    assert 0 < shown < 50
    assert f'and {50 - shown} more' in text
    assert text.count('x' * 197) == shown
    # The cut is on whole items: the newest ones are the ones kept.
    assert f'>{shown - 1:02} x' in text
    assert f'>{shown:02} x' not in text


def test_the_hidden_count_adds_to_the_items_dropped_by_the_cut(alice):
    for number in range(55):
        notify(
            alice,
            make_title(f'{number:02} ' + 'x' * 197),
            created_at=NOW - timedelta(minutes=number),
        )

    text = build_message(alice, load(alice), NOW)

    assert len(text) <= LIMIT
    assert f'and {55 - text.count("/titles/")} more' in text


def test_a_message_that_fits_has_no_more_line(alice):
    notify(alice, make_title('A'))

    assert ' more' not in build_message(alice, load(alice), NOW)


def test_building_does_not_change_the_active_language(alice):
    alice.profile.interface_language = 'ru'
    notify(alice, make_title())
    with translation.override('en'):
        build_message(alice, load(alice), NOW)
        assert translation.get_language() == 'en'
    with translation.override('ru'):
        alice.profile.interface_language = 'en'
        build_message(alice, load(alice), NOW)
        assert translation.get_language() == 'ru'


def test_the_query_count_does_not_grow_with_items(alice):
    def count() -> int:
        notifications = load(alice)
        with CaptureQueriesContext(connection) as queries:
            build_message(alice, notifications, NOW)
        return len(queries)

    for number in range(3):
        notify(alice, make_title(f'Show {number}'))
    few = count()
    for number in range(3, 15):
        notify(alice, make_title(f'Show {number}'))
    assert count() == few
