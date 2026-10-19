from datetime import datetime, timedelta, timezone

import pytest
from catalog.models import EpisodeTranslation, Season, Title, TitleTranslation
from tracking import queries
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import TODAY, make_movie, make_show, watch

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def add(user, title, status=LibraryEntry.Status.WATCHING):
    return LibraryEntry.objects.create(user=user, title=title, status=status)


def test_main_episodes_skip_extra_seasons_and_keep_order():
    title, episodes, extra = make_show()
    other, other_episodes, _ = make_show('Other', extra=False)
    assert list(queries.main_episodes(title)) == episodes
    assert extra not in queries.main_episodes(title)
    both = queries.main_episodes_for([title, other])
    assert list(both) == [*episodes, *other_episodes]


def test_watched_episode_ids_are_the_users_own(alice, bob):
    title, episodes, extra = make_show()
    watch(alice, episodes[0], extra)
    watch(bob, episodes[1])
    assert queries.watched_episode_ids(alice, title) == {episodes[0].id, extra.id}
    assert queries.watched_episode_ids(bob, title) == {episodes[1].id}


def test_title_without_episodes_has_zero_progress(alice):
    title = Title.objects.create(
        kind='anime', original_title='Announced', status='upcoming'
    )
    assert queries.progress(alice, title).percent == 0
    assert queries.next_episode(alice, title) is None


def test_progress_and_next_episode_ignore_extras(alice):
    title, eps, ex = make_show()
    watch(alice, eps[0], eps[1], ex)
    progress = queries.progress(alice, title)
    assert (progress.watched, progress.total, progress.percent) == (2, 5, 40)
    assert queries.next_episode(alice, title) == eps[2]


def test_a_rewatch_does_not_count_twice(alice, bob):
    title, eps, _ = make_show()
    watch(alice, eps[0], at=T0)
    watch(alice, eps[0], at=T0 + timedelta(days=1))
    watch(bob, eps[1], eps[2])
    assert queries.progress(alice, title).watched == 1


def test_next_episode_may_be_unaired_and_has_its_season(alice):
    title, eps, _ = make_show(dated=(-30, 7))
    watch(alice, eps[0])
    nxt = queries.next_episode(alice, title)
    assert nxt == eps[1]
    assert nxt.season.number == 1


def test_next_episode_is_none_when_everything_is_watched(alice):
    title, eps, _ = make_show(extra=False)
    watch(alice, *eps)
    assert queries.next_episode(alice, title) is None
    assert queries.progress(alice, title).percent == 100


def test_library_rows_use_constant_queries(alice, bob, django_assert_num_queries):
    title, eps, _ = make_show('First')
    add(alice, title)
    watch(alice, eps[0], eps[1])
    foreign, foreign_eps, _ = make_show('Bobs')
    add(bob, foreign)
    watch(bob, foreign_eps[0])
    with django_assert_num_queries(3):
        rows = queries.library_rows(alice)
    assert len(rows) == 1
    row = rows[0]
    assert (row.entry.title, row.watched, row.total, row.percent) == (title, 2, 5, 40)
    assert row.next == eps[2]
    assert row.next.season.number == 1
    assert row.last_watched_at is not None

    for index in range(30):
        more, more_eps, _ = make_show(f'More {index}')
        add(alice, more)
        watch(alice, more_eps[0])
    with django_assert_num_queries(3):
        rows = queries.library_rows(alice)
    assert len(rows) == 31
    assert foreign not in [row.entry.title for row in rows]


def test_library_row_of_an_unwatched_title(alice):
    title, eps, _ = make_show()
    add(alice, title, LibraryEntry.Status.PLANNED)
    (row,) = queries.library_rows(alice)
    assert (row.watched, row.total, row.next, row.last_watched_at) == (
        0,
        5,
        eps[0],
        None,
    )


def test_a_movie_row_has_no_episodes(alice):
    add(alice, make_movie())
    (row,) = queries.library_rows(alice)
    assert (row.watched, row.total, row.next, row.percent) == (0, 0, None, 0)


def test_library_filters_by_kind_status_and_query(alice):
    show, _, _ = make_show('Breaking Show')
    movie = make_movie('Space Film')
    add(alice, show)
    add(alice, movie, LibraryEntry.Status.PLANNED)

    def names(**kwargs):
        return {
            row.entry.title.original_title
            for row in queries.library_rows(alice, **kwargs)
        }

    assert names() == {'Breaking Show', 'Space Film'}
    assert names(kind='movie') == {'Space Film'}
    assert names(status='watching') == {'Breaking Show'}
    assert names(kind='movie', status='watching') == set()
    assert names(query='SPACE') == {'Space Film'}
    assert names(query='nothing') == set()


def test_library_query_matches_translations(alice):
    title, _, _ = make_show('Sousou no Frieren')
    add(alice, title)
    TitleTranslation.objects.create(title=title, language='en', name='Frieren')
    TitleTranslation.objects.create(title=title, language='ru', name='Фрирен')
    assert len(queries.library_rows(alice, query='frieren')) == 1
    assert len(queries.library_rows(alice, query='ФРИРЕН', language='ru')) == 1
    assert len(queries.library_rows(alice, query='nothing')) == 0


def test_library_rows_carry_translations_for_the_language_and_english(alice):
    title, _, _ = make_show('Original')
    add(alice, title)
    TitleTranslation.objects.create(title=title, language='en', name='English')
    TitleTranslation.objects.create(title=title, language='ru', name='Русский')
    TitleTranslation.objects.create(title=title, language='de', name='Deutsch')
    (row,) = queries.library_rows(alice, language='ru')
    assert {t.language for t in row.entry.title.translations.all()} == {'en', 'ru'}


def test_continue_watching_orders_aired_first(alice):
    old, old_eps, _ = make_show('Old', extra=False)
    recent, recent_eps, _ = make_show('Recent', extra=False)
    soon, soon_eps, _ = make_show('Soon', dated=(-30, 3), extra=False)
    later, later_eps, _ = make_show('Later', dated=(-30, 9), extra=False)
    undated, undated_eps, _ = make_show('Undated', dated=(-30, None), extra=False)
    done, done_eps, _ = make_show('Done', extra=False)
    planned, planned_eps, _ = make_show('Planned', extra=False)
    for title, eps in [
        (old, old_eps),
        (recent, recent_eps),
        (soon, soon_eps),
        (later, later_eps),
        (undated, undated_eps),
    ]:
        add(alice, title)
        watch(alice, eps[0], at=T0 + timedelta(days=1 if title is recent else 0))
    add(alice, done)
    watch(alice, *done_eps)
    add(alice, planned, LibraryEntry.Status.PLANNED)
    watch(alice, planned_eps[0])
    # Soon and Later are not aired yet, so they go by date, and Undated is last
    rows = queries.continue_watching(alice, today=TODAY)
    assert [row.entry.title.original_title for row in rows] == [
        'Recent',
        'Old',
        'Soon',
        'Later',
        'Undated',
    ]


def test_upcoming_orders_dated_first_and_skips_ended_undated(alice):
    soon, soon_eps, _ = make_show('Soon', dated=(-30, 2, 9), extra=False)
    undated, _, _ = make_show('Undated airing', dated=(-30, None), extra=False)
    ended, _, _ = make_show('Ended', status='ended', dated=(-30, None), extra=False)
    completed, _, _ = make_show('Completed', dated=(-30, 5), extra=False)
    film = make_movie('Film', release_offset=30, status='upcoming')
    old_film = make_movie('Old film', release_offset=-5)
    unannounced = make_movie('Unannounced', release_offset=None, status='upcoming')
    for title in (soon, undated, ended, film, old_film):
        add(alice, title)
    add(alice, unannounced, LibraryEntry.Status.PLANNED)
    add(alice, completed, LibraryEntry.Status.COMPLETED)

    items = queries.upcoming(alice, today=TODAY)

    assert [(i.title.original_title, i.date) for i in items] == [
        ('Soon', TODAY + timedelta(days=2)),
        ('Film', TODAY + timedelta(days=30)),
        ('Unannounced', None),
        ('Undated airing', None),
    ]
    assert items[0].episode == soon_eps[1]
    assert items[1].episode is None
    assert items[2].episode is None
    assert items[3].episode.number == 2


def test_upcoming_includes_an_episode_aired_today(alice):
    title, eps, _ = make_show(dated=(-3, 0), extra=False)
    add(alice, title)
    (item,) = queries.upcoming(alice, today=TODAY)
    assert (item.episode, item.date) == (eps[1], TODAY)


def test_title_page_state_counts_views_and_uses_constant_queries(
    alice, bob, django_assert_num_queries
):
    title, eps, ex = make_show()
    entry = add(alice, title)
    watch(alice, eps[0], eps[1], at=T0)
    watch(alice, eps[1], ex, at=T0 + timedelta(days=1))
    watch(bob, eps[2])
    EpisodeTranslation.objects.create(episode=eps[0], language='en', name='Pilot')

    with django_assert_num_queries(5):
        state = queries.title_page_state(alice, title, today=TODAY)

    assert state.entry == entry
    assert [g.season.number for g in state.groups] == [1, 2]
    main, extras = state.groups
    assert (main.watched, main.total, main.aired_total) == (2, 5, 3)
    assert [(r.episode, r.aired, r.views) for r in main.rows] == [
        (eps[0], True, 1),
        (eps[1], True, 2),
        (eps[2], True, 0),
        (eps[3], False, 0),
        (eps[4], False, 0),
    ]
    assert (extras.watched, extras.total, extras.aired_total) == (1, 1, 1)
    assert (state.progress.watched, state.progress.total) == (2, 5)
    assert state.next_episode == eps[2]
    assert state.movie_views == 0

    big, big_eps, _ = make_show('Big', dated=tuple([-5] * 40))
    watch(alice, *big_eps[:10])
    with django_assert_num_queries(5):
        queries.title_page_state(alice, big, today=TODAY)


def test_title_page_state_of_a_movie_and_a_title_not_in_the_library(alice, bob):
    movie = make_movie()
    for day in (1, 2):
        WatchEvent.objects.create(
            user=alice, title=movie, watched_at=T0 + timedelta(days=day)
        )
    WatchEvent.objects.create(user=bob, title=movie, watched_at=T0)
    state = queries.title_page_state(alice, movie, today=TODAY)
    assert state.entry is None
    assert state.groups == []
    assert state.movie_views == 2
    assert state.next_episode is None
    assert state.progress.percent == 0


def test_title_page_state_keeps_seasons_without_episodes(alice):
    title = Title.objects.create(kind='anime', original_title='Bare', status='ended')
    Season.objects.create(
        title=title, number=1, kind='tv', episode_source=Season.EpisodeSource.NONE
    )
    (group,) = queries.title_page_state(alice, title, today=TODAY).groups
    assert (group.rows, group.watched, group.total, group.aired_total) == ([], 0, 0, 0)


def test_page_state_episode_translations_are_prefetched(alice):
    title, eps, _ = make_show(extra=False)
    EpisodeTranslation.objects.create(episode=eps[0], language='ru', name='Первый')
    EpisodeTranslation.objects.create(episode=eps[0], language='de', name='Erste')
    state = queries.title_page_state(alice, title, today=TODAY, language='ru')
    first = state.groups[0].rows[0].episode
    assert {t.language for t in first.translations.all()} == {'ru'}
