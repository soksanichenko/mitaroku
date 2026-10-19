import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from catalog.models import Episode, EpisodeTranslation, ExternalId, Title
from catalog.sync import get_or_create_title, sync_title
from catalog.sync.common import Clients
from django.db import connection
from fakes import FakeAniList, FakeTmdb, edge, media, tmdb_season, tmdb_show
from providers.anilist import AniListSchedule
from providers.http import ProviderError
from providers.models import AnimeMapping
from providers.types import SearchHit

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def franchise_entries():
    """Four main entries (A, B, C, D) and three extras (movie, OVA, special)."""
    return [
        media(
            10,
            [edge('SEQUEL', 20), edge('SIDE_STORY', 80, 'SPECIAL')],
            mal_id=110,
            english='Show',
            score=80,
        ),
        media(
            20,
            [
                edge('PREQUEL', 10),
                edge('SEQUEL', 30),
                edge('SUMMARY', 50, 'MOVIE'),
                edge('SIDE_STORY', 40, 'OVA'),
            ],
            month=7,
            mal_id=120,
            score=70,
        ),
        media(
            30,
            [edge('PREQUEL', 20), edge('SEQUEL', 70)],
            year=2021,
            episodes=None,
            status='RELEASING',
            score=None,
        ),
        media(70, [edge('PREQUEL', 30)], year=2022, episodes=13),
        media(50, [edge('PARENT', 20)], fmt='MOVIE', month=10, episodes=1),
        media(40, [], fmt='OVA', month=12, episodes=2),
        media(80, [], fmt='SPECIAL', year=2022, month=6, episodes=4),
    ]


def schedule(first, count, weekly=7):
    return {
        n: datetime(first.year, first.month, first.day, 12, tzinfo=UTC)
        + timedelta(days=weekly * (n - 1))
        for n in range(1, count + 1)
    }


def tmdb_with_show():
    seasons = {
        0: tmdb_season(0, 2, date(2020, 12, 1), 'Specials'),
        1: tmdb_season(1, 12, date(2020, 1, 1)),
        2: tmdb_season(2, 12, date(2020, 7, 1)),
        3: tmdb_season(3, 5, date(2021, 1, 1)),
    }
    return FakeTmdb(shows={2000: (tmdb_show(2000, list(seasons.values())), seasons)})


@pytest.fixture
def mappings():
    AnimeMapping.objects.create(
        anilist_id=10, mal_id=110, tmdb_tv_id=2000, tmdb_season=1, imdb_id='tt0001'
    )
    # Fribb is wrong about B (a season TMDB does not have); concatenation rescues it.
    AnimeMapping.objects.create(
        anilist_id=20, mal_id=120, tmdb_tv_id=2000, tmdb_season=9
    )
    AnimeMapping.objects.create(anilist_id=30, tmdb_tv_id=2000, tmdb_season=3)
    AnimeMapping.objects.create(anilist_id=40, tmdb_tv_id=2000, tmdb_season=0)
    AnimeMapping.objects.create(anilist_id=50, tmdb_movie_id=777)


def anime_title(anilist_id='10'):
    title = Title.objects.create(kind='anime', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='anilist', external_id=anilist_id)
    return title


def clients(tmdb=None, anilist=None):
    return Clients(
        tmdb=tmdb,
        anilist=anilist
        or FakeAniList(
            *franchise_entries(),
            schedules={
                70: AniListSchedule(upcoming={}, all=schedule(date(2022, 1, 1), 13))
            },
        ),
    )


def seasons_of(title):
    return {s.anilist_id: s for s in title.seasons.all()}


def test_structure_kinds_and_order(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    assert [
        (s.number, s.anilist_id, s.kind, s.is_extra) for s in title.seasons.all()
    ] == [
        (1, 10, 'tv', False),
        (2, 20, 'tv', False),
        (3, 30, 'tv', False),
        (4, 70, 'tv', False),
        (5, 50, 'movie', True),
        (6, 40, 'ova', True),
        (7, 80, 'special', True),
    ]


def test_guarded_tmdb_episodes_carry_titles_and_remember_their_origin(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    seasons = seasons_of(title)
    for anilist_id, expected in ((10, 12), (20, 12), (30, 5), (40, 2)):
        assert seasons[anilist_id].episode_source == 'tmdb'
        assert seasons[anilist_id].episodes.count() == expected
        assert seasons[anilist_id].tmdb_tv_id == 2000
    second = seasons[20].episodes.get(number=1)
    assert (second.tmdb_season_number, second.tmdb_episode_number) == (2, 1)
    assert second.air_date == date(2020, 7, 1)
    assert (
        EpisodeTranslation.objects.get(episode=second, language='en').name
        == 'Episode 1'
    )
    ova = seasons[40].episodes.get(number=1)
    assert ova.tmdb_season_number == 0


def test_running_entry_with_an_unknown_count_takes_what_tmdb_has(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    running = seasons_of(title)[30]
    assert running.episode_count is None
    assert running.episodes.count() == 5


def test_degraded_entry_uses_the_complete_airing_schedule(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    degraded = seasons_of(title)[70]
    assert degraded.episode_source == 'anilist'
    assert degraded.episodes.count() == 13
    assert degraded.episodes.get(number=1).air_date == date(2022, 1, 1)
    assert degraded.episodes.get(number=3).air_date == date(2022, 1, 15)
    assert not EpisodeTranslation.objects.filter(episode__season=degraded).exists()
    assert degraded.episodes.get(number=1).tmdb_season_number is None


def test_extras_get_counts_but_no_schedule_lookup(mappings):
    title = anime_title()
    anilist = clients(tmdb_with_show()).anilist
    sync_title(title, Clients(tmdb_with_show(), anilist), NOW)
    special = seasons_of(title)[80]
    assert special.episodes.count() == 4
    assert all(e.air_date is None for e in special.episodes.all())
    assert anilist.schedule_calls == [70]


def test_movie_entry_has_one_episode_on_its_release_date(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    movie = seasons_of(title)[50]
    assert movie.episode_source == 'anilist'
    assert [e.air_date for e in movie.episodes.all()] == [date(2020, 10, 1)]


def test_absolute_numbers_cover_the_main_seasons_only(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    seasons = seasons_of(title)
    assert seasons[10].episodes.get(number=1).absolute_number == 1
    assert seasons[20].episodes.get(number=1).absolute_number == 13
    assert seasons[30].episodes.get(number=5).absolute_number == 29
    assert seasons[70].episodes.get(number=13).absolute_number == 42
    assert not Episode.objects.filter(
        season__is_extra=True, absolute_number__isnull=False
    ).exists()


def test_title_fields_and_provider_ids(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    title.refresh_from_db()
    assert (title.original_title, title.original_language) == ('n10', 'ja')
    assert title.status == Title.Status.AIRING
    assert title.release_date == date(2020, 1, 1)
    assert title.poster_url == 'https://img.example/10.jpg'
    # The mean of the known main scores 80, 70 and 80 is 76.7, shown as 7.7.
    assert (str(title.rating), title.rating_source) == ('7.7', 'anilist')
    assert title.translations.get(language='en').name == 'Show'
    assert title.next_sync_at == NOW + timedelta(hours=6)
    links = {(link.provider, link.external_id) for link in title.external_ids.all()}
    assert {('anilist', str(i)) for i in (10, 20, 30, 70, 50, 40, 80)} <= links
    assert {
        ('mal', '110'),
        ('mal', '120'),
        ('tmdb_tv', '2000'),
        ('imdb', 'tt0001'),
    } <= links


def test_any_entry_finds_the_franchise_title(mappings):
    title = anime_title('20')
    sync_title(title, clients(tmdb_with_show()), NOW)
    hit = SearchHit('anilist', '10', 'anime', 'Show', 'n10', 2020, '', '')
    found, created = get_or_create_title(hit)
    assert created is False and found.pk == title.pk


def test_without_a_tmdb_client_every_entry_is_degraded(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb=None), NOW)
    sources = {s.anilist_id: s.episode_source for s in title.seasons.all()}
    # The running entry has no known count and no schedule, so it has no episodes.
    assert sources.pop(30) == 'none'
    assert set(sources.values()) == {'anilist'}
    assert not EpisodeTranslation.objects.exists()
    assert seasons_of(title)[10].episodes.count() == 12


def test_running_degraded_entry_without_a_count_numbers_up_to_the_last_node():
    upcoming = {
        13: datetime(2026, 11, 1, 12, tzinfo=UTC),
        14: datetime(2026, 11, 8, 12, tzinfo=UTC),
    }
    anilist = FakeAniList(
        *franchise_entries(), schedules={30: AniListSchedule(upcoming=upcoming, all={})}
    )
    title = anime_title()
    sync_title(title, Clients(None, anilist), NOW)
    running = seasons_of(title)[30]
    # Episodes 1-12 aired but have no node: they exist (so they can be logged)
    # without a date; nothing is invented for them.
    assert [(e.number, e.air_date) for e in running.episodes.all()] == [
        *[(n, None) for n in range(1, 13)],
        (13, date(2026, 11, 1)),
        (14, date(2026, 11, 8)),
    ]


def test_running_degraded_entry_without_a_count_keeps_the_aired_episodes():
    aired = schedule(date(2026, 7, 5), 12)
    upcoming = {
        13: datetime(2026, 11, 1, 12, tzinfo=UTC),
        14: datetime(2026, 11, 8, 12, tzinfo=UTC),
    }
    anilist = FakeAniList(
        *franchise_entries(),
        schedules={30: AniListSchedule(upcoming=upcoming, all=aired)},
    )
    title = anime_title()
    sync_title(title, Clients(None, anilist), NOW)
    running = seasons_of(title)[30]
    assert running.episodes.count() == 14
    assert running.episodes.get(number=1).air_date == date(2026, 7, 5)
    assert running.episodes.get(number=12).air_date == date(2026, 9, 20)
    assert running.episodes.get(number=14).air_date == date(2026, 11, 8)


def test_running_degraded_entry_without_a_count_dates_only_the_nodes_it_has():
    partial = {n: when for n, when in schedule(date(2026, 7, 5), 12).items() if n != 5}
    anilist = FakeAniList(
        *franchise_entries(),
        schedules={30: AniListSchedule(upcoming={}, all=partial)},
    )
    title = anime_title()
    sync_title(title, Clients(None, anilist), NOW)
    running = seasons_of(title)[30]
    assert running.episodes.count() == 12
    assert running.episodes.get(number=5).air_date is None
    assert running.episodes.get(number=6).air_date == date(2026, 8, 9)


def test_an_incomplete_schedule_gives_counts_without_dates():
    entries = franchise_entries()
    partial = AniListSchedule(upcoming={}, all=schedule(date(2022, 1, 1), 3))
    anilist = FakeAniList(*entries, schedules={70: partial})
    title = anime_title()
    sync_title(title, Clients(None, anilist), NOW)
    degraded = seasons_of(title)[70]
    assert degraded.episodes.count() == 13
    assert all(e.air_date is None for e in degraded.episodes.all())


def test_resync_is_idempotent(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    before = list(
        Episode.objects.order_by('pk').values_list('pk', 'season__anilist_id', 'number')
    )
    sync_title(title, clients(tmdb_with_show()), NOW)
    after = list(
        Episode.objects.order_by('pk').values_list('pk', 'season__anilist_id', 'number')
    )
    assert before == after
    assert title.seasons.count() == 7
    assert title.external_ids.count() == len(
        set(title.external_ids.values_list('provider', 'external_id'))
    )


def test_air_dates_are_the_day_in_japan():
    late_night = {
        1: datetime(2022, 1, 1, 15, 30, tzinfo=UTC)
    }  # 00:30 on 2 January in Japan
    entries = [media(30, [], episodes=None, status='RELEASING')]
    anilist = FakeAniList(
        *entries, schedules={30: AniListSchedule(upcoming=late_night, all={})}
    )
    title = anime_title('30')
    sync_title(title, Clients(None, anilist), NOW)
    assert seasons_of(title)[30].episodes.get().air_date == date(2022, 1, 2)


def test_a_new_entry_in_the_middle_renumbers_without_touching_existing_rows(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    pks = {s.anilist_id: s.pk for s in title.seasons.all()}
    entries = franchise_entries()
    entries[1] = media(
        20,
        [
            edge('PREQUEL', 10),
            edge('SEQUEL', 25),
            edge('SUMMARY', 50, 'MOVIE'),
            edge('SIDE_STORY', 40, 'OVA'),
        ],
        month=7,
        mal_id=120,
    )
    entries[2] = media(
        30,
        [edge('PREQUEL', 25), edge('SEQUEL', 70)],
        year=2021,
        episodes=None,
        status='RELEASING',
        score=None,
    )
    entries.append(
        media(25, [edge('PREQUEL', 20), edge('SEQUEL', 30)], year=2020, month=10)
    )
    anilist = FakeAniList(*entries)
    sync_title(title, clients(tmdb_with_show(), anilist), NOW)
    seasons = seasons_of(title)
    assert [seasons[i].number for i in (10, 20, 25, 30, 70)] == [1, 2, 3, 4, 5]
    assert all(seasons[i].pk == pk for i, pk in pks.items())


def test_an_entry_that_left_the_franchise_is_parked_as_an_extra(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    entries = [e for e in franchise_entries() if e.anilist_id != 70]
    entries[2] = media(
        30,
        [edge('PREQUEL', 20)],
        year=2021,
        episodes=None,
        status='RELEASING',
        score=None,
    )
    sync_title(title, clients(tmdb_with_show(), FakeAniList(*entries)), NOW)
    gone = seasons_of(title)[70]
    assert gone.is_extra is True and gone.number == 7  # after the 3 main + 3 extra
    assert gone.episodes.count() == 13
    sync_title(title, clients(tmdb_with_show(), FakeAniList(*entries)), NOW)
    assert seasons_of(title)[70].number == 7  # repeated syncs do not move it again


def test_a_rejected_allocation_removes_the_tmdb_titles_but_keeps_the_episodes(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    first = seasons_of(title)[10]
    ids = set(first.episodes.values_list('pk', flat=True))
    shifted = tmdb_with_show()
    shifted.shows[2000][1][1] = tmdb_season(
        1, 12, date(2020, 2, 20)
    )  # no longer matches
    sync_title(title, clients(shifted), NOW)
    first = seasons_of(title)[10]
    assert first.episode_source == 'anilist'
    assert set(first.episodes.values_list('pk', flat=True)) == ids
    assert not EpisodeTranslation.objects.filter(episode__season=first).exists()
    assert first.episodes.get(number=1).tmdb_season_number is None


def test_a_provider_outage_leaves_the_catalog_untouched(mappings):
    class Down(FakeTmdb):
        def tv(self, tmdb_id, language='en'):
            raise ProviderError('tmdb down')

    title = anime_title()
    with pytest.raises(ProviderError, match='tmdb down'):
        sync_title(title, clients(Down()), NOW)
    title.refresh_from_db()
    assert title.synced_at is None
    assert title.seasons.count() == 0


def test_ids_that_belong_to_another_title_are_not_stolen(mappings, caplog):
    other = Title.objects.create(kind='anime', original_title='other')
    ExternalId.objects.create(title=other, provider='anilist', external_id='80')
    title = anime_title()
    with caplog.at_level(logging.WARNING):
        sync_title(title, clients(tmdb_with_show()), NOW)
    assert (
        ExternalId.objects.get(provider='anilist', external_id='80').title_id
        == other.pk
    )
    assert 'belongs to title' in caplog.text


def test_an_empty_mapping_table_is_reported(caplog):
    title = anime_title()
    with caplog.at_level(logging.WARNING):
        sync_title(title, clients(tmdb_with_show()), NOW)
    assert 'refresh_mapping' in caplog.text


def test_an_entry_anilist_does_not_know_is_a_provider_error():
    title = anime_title('999')
    with pytest.raises(ProviderError, match='does not know'):
        sync_title(title, clients(), NOW)


def test_a_title_without_an_anilist_id_is_a_provider_error():
    title = Title.objects.create(kind='anime', original_title='x')
    with pytest.raises(ProviderError, match='no AniList id'):
        sync_title(title, clients(), NOW)


@pytest.fixture
def high_season_ids():
    """Push the season id sequence past the range of a smallint."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_serial_sequence('catalog_season', 'id')")
        sequence = cursor.fetchone()[0]
        cursor.execute(f'SELECT last_value, is_called FROM {sequence}')
        last_value, is_called = cursor.fetchone()
        cursor.execute('SELECT setval(%s, 40000)', [sequence])
    yield
    with connection.cursor() as cursor:
        cursor.execute('SELECT setval(%s, %s, %s)', [sequence, last_value, is_called])


def test_resync_works_when_season_ids_exceed_a_smallint(mappings, high_season_ids):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    before = [(s.pk, s.number) for s in title.seasons.order_by('number')]
    assert min(pk for pk, _ in before) > 32767
    sync_title(title, clients(tmdb_with_show()), NOW)
    assert [(s.pk, s.number) for s in title.seasons.order_by('number')] == before


def franchise_before_the_new_sequel():
    return [
        media(70, [edge('SEQUEL', 71)]),
        media(71, [edge('PREQUEL', 70)], month=7),
    ]


def franchise_with_new_sequel():
    return [
        media(70, [edge('SEQUEL', 71)]),
        media(71, [edge('PREQUEL', 70), edge('SEQUEL', 72)], month=7),
        media(72, [edge('PREQUEL', 71)], year=2021, status='NOT_YET_RELEASED'),
    ]


class NoAniList:
    """An AniList stand-in that fails the test when it is used."""

    def media_many(self, ids):
        raise AssertionError('AniList must not be called')


def test_a_new_entry_of_a_known_franchise_returns_that_title():
    title = anime_title('70')
    anilist = FakeAniList(*franchise_before_the_new_sequel())
    sync_title(title, Clients(None, anilist), NOW)
    assert not title.external_ids.filter(external_id='72').exists()

    hit = SearchHit('anilist', '72', 'anime', 'Sequel', 'n72', 2021, '', '')
    found, created = get_or_create_title(
        hit, Clients(None, FakeAniList(*franchise_with_new_sequel()))
    )
    assert created is False and found.pk == title.pk
    assert Title.objects.count() == 1


def test_an_unknown_anime_entry_still_creates_a_bare_title():
    hit = SearchHit('anilist', '99', 'anime', 'Lone', 'n99', 2021, '', '')
    title, created = get_or_create_title(hit, Clients(None, FakeAniList(media(99))))
    assert created is True and title.external_ids.get().external_id == '99'


def test_movie_and_tv_hits_do_not_call_anilist():
    clients_ = Clients(None, NoAniList())
    for provider, kind in (('tmdb_tv', 'tv'), ('tmdb_movie', 'movie')):
        hit = SearchHit(provider, '5', kind, 'T', 'T', None, '', '')
        _, created = get_or_create_title(hit, clients_)
        assert created is True


def remake_entries():
    """FMA 2003 (100) and its alternative version Brotherhood (200, sequel 201)."""
    return [
        media(100, [edge('ALTERNATIVE', 200)], year=2003, episodes=51),
        media(
            200,
            [edge('ALTERNATIVE', 100), edge('SEQUEL', 201)],
            year=2009,
            episodes=64,
        ),
        media(201, [edge('PREQUEL', 200)], year=2010, episodes=4),
    ]


def add_hit(anilist_id, clients):
    hit = SearchHit(
        'anilist', str(anilist_id), 'anime', 'T', f'n{anilist_id}', 0, '', ''
    )
    title, created = get_or_create_title(hit, clients)
    sync_title(title, clients, NOW)
    return title, created


def test_an_alternative_version_becomes_its_own_title():
    clients_ = Clients(None, FakeAniList(*remake_entries()))
    original, _ = add_hit(100, clients_)
    remake, created = add_hit(200, clients_)
    assert created is True and remake.pk != original.pk
    assert [(s.anilist_id, s.is_extra) for s in remake.seasons.all()][:2] == [
        (200, False),
        (201, False),
    ]
    # Both titles list the other's entry as an extra season, neither owns its id.
    assert (200, True) in [(s.anilist_id, s.is_extra) for s in original.seasons.all()]
    assert (100, True) in [(s.anilist_id, s.is_extra) for s in remake.seasons.all()]
    owner = {
        link.external_id: link.title_id
        for link in ExternalId.objects.filter(provider='anilist')
    }
    assert owner == {
        '100': original.pk,
        '200': remake.pk,
        '201': remake.pk,
    }


def test_an_alternative_version_added_first_does_not_swallow_the_original():
    clients_ = Clients(None, FakeAniList(*remake_entries()))
    remake, _ = add_hit(200, clients_)
    original, created = add_hit(100, clients_)
    assert created is True and original.pk != remake.pk
    assert {s.anilist_id for s in remake.seasons.filter(is_extra=False)} == {200, 201}


def test_a_side_story_entry_belongs_to_the_franchise_title():
    clients_ = Clients(None, FakeAniList(*franchise_entries()))
    title, _ = add_hit(10, clients_)
    assert title.external_ids.filter(provider='anilist', external_id='80').exists()
    found, created = add_hit(80, clients_)
    assert created is False and found.pk == title.pk


def test_a_spin_off_is_not_linked_to_the_parent_franchise():
    anilist = FakeAniList(
        media(300, [edge('SPIN_OFF', 310)]),
        media(310, [edge('PARENT', 300)], year=2021, mal_id=999),
    )
    clients_ = Clients(None, anilist)
    parent, _ = add_hit(300, clients_)
    assert not parent.external_ids.filter(external_id__in=['310', '999']).exists()
    assert parent.seasons.get(anilist_id=310).is_extra is True
    spin_off, created = add_hit(310, clients_)
    assert created is True


def test_a_spin_off_added_first_does_not_swallow_its_parent():
    anilist = FakeAniList(
        media(300, [edge('SPIN_OFF', 310)]),
        media(310, [edge('PARENT', 300)], year=2021),
    )
    clients_ = Clients(None, anilist)
    spin_off, _ = add_hit(310, clients_)
    parent, created = add_hit(300, clients_)
    assert created is True and parent.pk != spin_off.pk


def test_a_shrinking_allocation_releases_the_titles_of_the_next_part():
    AnimeMapping.objects.create(anilist_id=1, tmdb_tv_id=9, tmdb_season=1)
    AnimeMapping.objects.create(
        anilist_id=2, tmdb_tv_id=9, tmdb_season=1, tmdb_episode_offset=12
    )
    seasons = {1: tmdb_season(1, 24, date(2020, 1, 1))}
    tmdb = FakeTmdb(shows={9: (tmdb_show(9, list(seasons.values())), seasons)})
    title = anime_title('1')
    # Part 1 is running with an unknown count, so it takes the whole TMDB season.
    running = FakeAniList(media(1, episodes=None, status='RELEASING'))
    sync_title(title, Clients(tmdb, running), NOW)
    assert seasons_of(title)[1].episodes.count() == 24

    # AniList now knows part 1 has 12 episodes and lists part 2 separately.
    split = FakeAniList(
        media(1, [edge('SEQUEL', 2)], episodes=12),
        media(2, [edge('PREQUEL', 1)], month=3, day=25, episodes=12),
    )
    sync_title(title, Clients(tmdb, split), NOW)
    first, second = seasons_of(title)[1], seasons_of(title)[2]
    assert first.episodes.count() == 24  # rows are never deleted
    stale = first.episodes.filter(number__gt=12)
    assert not stale.exclude(tmdb_season_number=None, tmdb_episode_number=None).exists()
    assert not EpisodeTranslation.objects.filter(episode__in=stale).exists()
    assert EpisodeTranslation.objects.filter(name='Episode 13').count() == 1
    assert second.episodes.get(number=1).tmdb_episode_number == 13
    # The stale rows must not push part 2 off its place in the absolute numbering.
    assert not stale.exclude(absolute_number=None).exists()
    assert second.episodes.get(number=1).absolute_number == 13
