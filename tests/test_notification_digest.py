import re
from datetime import UTC, date, datetime, timedelta

import pytest
from catalog.models import Episode, EpisodeTranslation, Season, Title, TitleTranslation
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import translation
from notifications.digest import MAX_ITEMS, build_digest
from notifications.models import Notification, dedupe_key
from notifications.tokens import unsubscribe_user
from web.templatetags.web_extras import short_date

pytestmark = pytest.mark.django_db

Kind = Notification.Kind
NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
TODAY = date(2026, 5, 20)
PUBLIC_URL = 'https://m.example'


@pytest.fixture(autouse=True)
def public_url():
    """Give every test the same public URL."""
    with override_settings(PUBLIC_URL=PUBLIC_URL):
        yield


def make_title(name: str = 'Show', kind: str = Title.Kind.TV) -> Title:
    """Create a title."""
    return Title.objects.create(kind=kind, original_title=name)


def notify(
    user,
    title: Title,
    kind: str = Kind.EPISODE_AIRED,
    day: date = TODAY,
    payload=None,
    created_at: datetime = NOW,
) -> Notification:
    """Create a notification row, as the scan would."""
    return Notification.objects.create(
        user=user,
        kind=kind,
        title=title,
        day=day,
        payload=payload if payload is not None else {},
        dedupe_key=dedupe_key(kind, title.pk, day),
        created_at=created_at,
    )


def load(user) -> list[Notification]:
    """Read the user's notifications with their titles, as the sender will."""
    return list(Notification.objects.for_user(user).select_related('title'))


def parts(message) -> tuple[str, str]:
    """Return the text body and the HTML alternative of a message."""
    ((html, mimetype),) = message.alternatives
    assert mimetype == 'text/html'
    return message.body, html


def test_the_subject_is_fixed_text_and_the_count(alice):
    one = notify(alice, make_title('Secret Name'))
    notify(alice, make_title('Other Name'))
    notify(alice, make_title('Third Name'))
    notify(alice, make_title('Fourth Name'))
    notify(alice, make_title('Fifth Name'))
    notifications = load(alice)
    assert build_digest(alice, notifications, NOW).subject == '5 new notifications'
    assert build_digest(alice, [one], NOW).subject == '1 new notification'
    alice.profile.interface_language = 'ru'
    ru = {n: build_digest(alice, notifications[:n], NOW).subject for n in (1, 2, 5)}
    assert ru == {
        1: '1 новое уведомление',
        2: '2 новых уведомления',
        5: '5 новых уведомлений',
    }
    assert 'Name' not in build_digest(alice, notifications, NOW).subject


def test_the_body_lists_each_item_in_the_profile_language(alice):
    profile = alice.profile
    profile.interface_language = 'ru'
    profile.metadata_language = 'en'
    title = make_title('Original')
    TitleTranslation.objects.create(title=title, language='en', name='English Name')
    TitleTranslation.objects.create(title=title, language='ru', name='Русское имя')
    season = Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
    episode = Episode.objects.create(season=season, number=3, air_date=TODAY)
    EpisodeTranslation.objects.create(episode=episode, language='en', name='Pilot')
    notify(alice, title, payload={'episodes': [episode.pk]})
    other = make_title('Moved')
    notify(
        alice,
        other,
        kind=Kind.RELEASE_CHANGED,
        payload={'from': '2026-05-01', 'to': '2026-06-02'},
    )
    message = build_digest(alice, load(alice), NOW)
    text, html = parts(message)
    with translation.override('ru'):
        old, new = short_date(date(2026, 5, 1)), short_date(date(2026, 6, 2))
    for body in (text, html):
        assert 'Новая серия' in body
        assert 'Дата изменена' in body
        assert 'English Name' in body
        assert 'Русское имя' not in body
        assert 'Сегодня' in body
    assert 'S1E3 · Pilot' in text
    assert f'{old} → {new}' in text
    assert f'<s>{old}</s> → <strong>{new}</strong>' in html


def test_links_are_absolute_and_point_to_the_title_and_the_screen(alice):
    title = make_title()
    notify(alice, title)
    text, html = parts(build_digest(alice, load(alice), NOW))
    page = f'{PUBLIC_URL}/titles/{title.pk}/'
    screen = f'{PUBLIC_URL}/notifications/'
    assert f'href="{page}"' in html
    assert f'href="{screen}"' in html
    assert page in text
    assert screen in text


def test_the_unsubscribe_headers_and_link_share_one_valid_token(alice):
    notify(alice, make_title())
    message = build_digest(alice, load(alice), NOW)
    text, html = parts(message)
    header = message.extra_headers['List-Unsubscribe']
    match = re.fullmatch(rf'<{PUBLIC_URL}/email/unsubscribe/([^/]+)/>', header)
    assert match
    token = match.group(1)
    assert unsubscribe_user(token) == alice
    assert message.extra_headers['List-Unsubscribe-Post'] == (
        'List-Unsubscribe=One-Click'
    )
    link = f'{PUBLIC_URL}/email/unsubscribe/{token}/'
    assert link in text
    assert f'href="{link}"' in html


def test_items_are_grouped_by_local_day_with_today_and_yesterday(alice):
    yesterday = TODAY - timedelta(days=1)
    older = date(2026, 5, 10)
    notify(alice, make_title('Old Show'), day=older)
    notify(alice, make_title('Yesterday Show'), day=yesterday)
    notify(alice, make_title('Today Show'), day=TODAY)
    text, _html = parts(build_digest(alice, load(alice), NOW))
    positions = [
        text.index(marker)
        for marker in (
            'Today',
            'Today Show',
            'Yesterday',
            'Yesterday Show',
            short_date(older, 'weekday'),
            'Old Show',
        )
    ]
    assert positions == sorted(positions)


def test_today_is_the_local_day_of_the_profile_zone(alice):
    alice.profile.timezone = 'Asia/Tokyo'
    # 20:00 UTC on May 20 is already May 21 in Tokyo.
    late = datetime(2026, 5, 20, 20, 0, tzinfo=UTC)
    notify(alice, make_title(), day=date(2026, 5, 21))
    text, _html = parts(build_digest(alice, load(alice), late))
    assert 'Today' in text
    assert 'Yesterday' not in text


def test_the_newest_item_of_a_day_comes_first(alice):
    notify(alice, make_title('First'), created_at=NOW - timedelta(hours=2))
    notify(alice, make_title('Second'), created_at=NOW - timedelta(hours=1))
    text, _html = parts(build_digest(alice, load(alice), NOW))
    assert text.index('Second') < text.index('First')


def test_more_than_fifty_items_are_cut_with_an_and_n_more_line(alice):
    for number in range(60):
        notify(
            alice,
            make_title(f'Show {number:02}'),
            created_at=NOW - timedelta(minutes=number),
        )
    message = build_digest(alice, load(alice), NOW)
    text, html = parts(message)
    assert MAX_ITEMS == 50
    assert text.count('New episode') == 50
    assert 'Show 49' in text
    assert 'Show 50' not in text
    assert message.subject == '60 new notifications'
    for body in (text, html):
        assert 'and 10 more' in body
        assert f'{PUBLIC_URL}/notifications/' in body
    alice.profile.interface_language = 'ru'
    text, _html = parts(build_digest(alice, load(alice), NOW))
    assert 'и ещё 10' in text


def test_a_hostile_title_is_escaped_in_html_and_never_in_subject_or_headers(alice):
    hostile = '<script>x</script>\nBcc: evil@example.com'
    title = make_title()
    TitleTranslation.objects.create(title=title, language='en', name=hostile)
    notify(alice, title)
    message = build_digest(alice, load(alice), NOW)
    _text, html = parts(message)
    assert '&lt;script&gt;' in html
    assert '<script>' not in html
    assert 'script' not in message.subject
    assert 'evil' not in message.subject
    for name, value in message.extra_headers.items():
        assert 'evil' not in name + value
        assert '\n' not in value
    assert message.bcc == []
    assert 'evil@example.com' not in message.message().as_string().split('\n\n')[0]


def test_the_html_alternative_is_attached_and_the_text_body_has_no_tags(alice):
    notify(alice, make_title('Plain'))
    message = build_digest(alice, load(alice), NOW)
    text, html = parts(message)
    assert not re.search(r'</?[a-zA-Z]', text)
    assert '<html' in html.lower()
    assert 'Plain' in text
    assert 'Plain' in html


def test_the_message_goes_to_the_effective_address(alice):
    alice.email = 'portal@example.com'
    alice.profile.notify_email = 'chosen@example.com'
    notify(alice, make_title())
    assert build_digest(alice, load(alice), NOW).to == ['chosen@example.com']
    alice.profile.notify_email = ''
    assert build_digest(alice, load(alice), NOW).to == ['portal@example.com']


def test_building_does_not_change_the_active_language(alice):
    alice.profile.interface_language = 'ru'
    notify(alice, make_title())
    with translation.override('en'):
        build_digest(alice, load(alice), NOW)
        assert translation.get_language() == 'en'
    with translation.override('ru'):
        alice.profile.interface_language = 'en'
        build_digest(alice, load(alice), NOW)
        assert translation.get_language() == 'ru'


def test_the_query_count_does_not_grow_with_items(alice):
    def count(notifications) -> int:
        with CaptureQueriesContext(connection) as queries:
            build_digest(alice, notifications, NOW)
        return len(queries)

    for number in range(3):
        title = make_title(f'Show {number}')
        season = Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
        episode = Episode.objects.create(season=season, number=1, air_date=TODAY)
        TitleTranslation.objects.create(title=title, language='en', name=f'T{number}')
        notify(alice, title, payload={'episodes': [episode.pk]})
    few = count(load(alice))
    for number in range(3, 15):
        title = make_title(f'Show {number}')
        season = Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
        episode = Episode.objects.create(season=season, number=1, air_date=TODAY)
        TitleTranslation.objects.create(title=title, language='en', name=f'T{number}')
        notify(alice, title, payload={'episodes': [episode.pk]})
    assert count(load(alice)) == few
