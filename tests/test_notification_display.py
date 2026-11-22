from datetime import date

import pytest
from catalog.models import (
    Episode,
    EpisodeTranslation,
    Season,
    Title,
    TitleTranslation,
)
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation
from notifications.display import Row, display_name, prepare_rows
from notifications.models import Notification, dedupe_key

pytestmark = pytest.mark.django_db

Kind = Notification.Kind


def make_title(name: str = 'Show', kind: str = Title.Kind.TV) -> Title:
    """Create a title."""
    return Title.objects.create(kind=kind, original_title=name)


def make_episodes(title: Title, count: int = 1) -> list[Episode]:
    """Create ``count`` episodes in season 1 of ``title``."""
    season = Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
    return [
        Episode.objects.create(season=season, number=number, air_date=date(2026, 1, 1))
        for number in range(1, count + 1)
    ]


def notify(
    user,
    title: Title,
    kind: str = Kind.EPISODE_AIRED,
    payload=None,
    read: bool = False,
) -> Notification:
    """Create a notification row, as the scan would."""
    day = date(2026, 1, 1)
    return Notification.objects.create(
        user=user,
        kind=kind,
        title=title,
        day=day,
        payload=payload if payload is not None else {},
        dedupe_key=dedupe_key(kind, title.pk, day),
        read_at=timezone.now() if read else None,
    )


def reload(user) -> list[Notification]:
    """Read the user's notifications the way the screen does."""
    return list(
        Notification.objects.for_user(user)
        .select_related('title')
        .order_by('-day', '-created_at', '-pk')
    )


def test_one_episode_shows_code_and_name(alice):
    title = make_title()
    (episode,) = make_episodes(title)
    episode.number = 3
    episode.save()
    EpisodeTranslation.objects.create(episode=episode, language='en', name='Name')
    notify(alice, title, payload={'episodes': [episode.pk]})
    (row,) = prepare_rows(reload(alice), 'en')
    assert isinstance(row, Row)
    assert row.text == 'S1E3 · Name'
    assert row.icon == 'aired'
    assert row.label == 'New episode'


def test_several_episodes_show_codes(alice):
    title = make_title()
    episodes = make_episodes(title, 4)[2:]
    notify(
        alice, title, kind=Kind.PREMIERE, payload={'episodes': [e.pk for e in episodes]}
    )
    (row,) = prepare_rows(reload(alice), 'en')
    assert row.text == 'S1E3, S1E4'


def test_the_movie_sentence_is_translated_under_the_active_language(alice):
    notify(alice, make_title(kind=Title.Kind.MOVIE), kind=Kind.MOVIE_RELEASED)
    notifications = reload(alice)
    with translation.override('en'):
        assert prepare_rows(notifications, 'en')[0].text == 'The movie was released.'
    with translation.override('ru'):
        assert prepare_rows(notifications, 'ru')[0].text == 'Фильм вышел.'


def test_date_kinds_carry_old_and_new(alice):
    announced = notify(
        alice,
        make_title('A'),
        kind=Kind.RELEASE_ANNOUNCED,
        payload={'to': '2026-05-01'},
    )
    changed = notify(
        alice,
        make_title('B'),
        kind=Kind.RELEASE_CHANGED,
        payload={'from': '2026-05-01', 'to': '2026-06-02'},
    )
    rows = {row.pk: row for row in prepare_rows(reload(alice), 'en')}
    assert (rows[announced.pk].old, rows[announced.pk].new) == (
        None,
        date(2026, 5, 1),
    )
    assert (rows[changed.pk].old, rows[changed.pk].new) == (
        date(2026, 5, 1),
        date(2026, 6, 2),
    )
    assert rows[changed.pk].text == ''


def test_a_deleted_episode_is_skipped_and_bad_payloads_are_tolerated(alice):
    title = make_title()
    kept, gone = make_episodes(title, 2)
    notify(
        alice,
        title,
        payload={'episodes': [kept.pk, gone.pk, True]},
    )
    gone.delete()
    notify(alice, make_title('B'), kind=Kind.PREMIERE, payload={'episodes': 'x'})
    notify(alice, make_title('C'), payload=['not', 'a', 'dict'])
    rows = {row.title.original_title: row for row in prepare_rows(reload(alice), 'en')}
    assert rows['Show'].text == 'S1E1'
    assert rows['B'].text == ''
    assert rows['C'].text == ''


def test_prepare_rows_keeps_the_input_order_and_the_unread_flag(alice):
    first = notify(alice, make_title('A'), read=True)
    second = notify(alice, make_title('B'))
    third = notify(alice, make_title('C'), read=True)
    rows = prepare_rows([second, third, first], 'en')
    assert [row.pk for row in rows] == [second.pk, third.pk, first.pk]
    assert [row.unread for row in rows] == [True, False, False]


def test_the_query_count_does_not_grow_with_rows(alice):
    def add(count: int) -> None:
        for _ in range(count):
            title = make_title(f'T{Notification.objects.count()}')
            episodes = make_episodes(title, 2)
            for episode in episodes:
                EpisodeTranslation.objects.create(
                    episode=episode, language='en', name=f'E{episode.pk}'
                )
            TitleTranslation.objects.create(title=title, language='en', name='X')
            notify(alice, title, payload={'episodes': [e.pk for e in episodes]})

    def queries_for(notifications: list[Notification]) -> int:
        with CaptureQueriesContext(connection) as queries:
            rows = prepare_rows(notifications, 'ru')
            # Reading the names must not query either: they are prefetched.
            for row in rows:
                display_name(row.title, 'ru')
        return len(queries)

    add(3)
    three = queries_for(reload(alice))
    add(9)
    twelve = queries_for(reload(alice))
    assert three == twelve


def test_display_name_falls_back_to_the_english_name_then_untitled():
    title = Title.objects.create(kind=Title.Kind.TV, original_title='')
    assert display_name(title, 'ru') == 'Untitled'
    TitleTranslation.objects.create(title=title, language='en', name='English')
    assert display_name(Title.objects.get(pk=title.pk), 'ru') == 'English'
    TitleTranslation.objects.create(title=title, language='ru', name='Русское')
    assert display_name(Title.objects.get(pk=title.pk), 'ru') == 'Русское'
    assert display_name(Title.objects.get(pk=title.pk), 'en') == 'English'
