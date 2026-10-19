from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from catalog.models import Episode, EpisodeTranslation, ExternalId, Title
from catalog.sync import get_or_create_title, sync_title
from catalog.sync.common import Clients
from fakes import FakeAniList, FakeTmdb, tmdb_movie, tmdb_season, tmdb_show
from providers.http import ProviderError
from providers.types import SearchHit

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def clients(tmdb):
    return Clients(tmdb=tmdb, anilist=FakeAniList())


def movie_title(tmdb_id='603'):
    title = Title.objects.create(kind='movie', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id=tmdb_id)
    return title


def tv_title(tmdb_id='1000'):
    title = Title.objects.create(kind='tv', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_tv', external_id=tmdb_id)
    return title


def test_movie_is_filled_from_tmdb():
    title = movie_title()
    sync_title(title, clients(FakeTmdb(movies={603: tmdb_movie(603)})), NOW)
    title.refresh_from_db()
    assert title.original_title == 'The Matrix (original)'
    assert title.release_date == date(1999, 3, 31)
    assert title.status == Title.Status.ENDED
    assert (title.runtime_minutes, title.rating, title.rating_source) == (
        136,
        Decimal('8.2'),
        'tmdb',
    )
    assert title.rating_votes == 25000
    assert title.poster_url.endswith('/m.jpg')
    assert title.synced_at == NOW
    assert title.next_sync_at == NOW + timedelta(days=14)
    assert title.external_ids.get(provider='imdb').external_id == 'tt0133093'
    assert title.translations.get(language='en').name == 'The Matrix'


def test_an_unreleased_movie_is_upcoming_and_synced_often():
    title = movie_title()
    future = tmdb_movie(
        603, release=date(2099, 1, 1), status='Post Production', rating=None
    )
    sync_title(title, clients(FakeTmdb(movies={603: future})), NOW)
    title.refresh_from_db()
    assert title.status == Title.Status.UPCOMING
    assert title.rating is None and title.rating_source == ''
    assert title.next_sync_at == NOW + timedelta(hours=24)


def test_syncing_a_movie_twice_does_not_duplicate_anything():
    title = movie_title()
    tmdb = FakeTmdb(movies={603: tmdb_movie(603)})
    sync_title(title, clients(tmdb), NOW)
    sync_title(title, clients(tmdb), NOW)
    assert title.external_ids.count() == 2
    assert title.translations.count() == 1


def test_unknown_movie_is_a_provider_error():
    title = movie_title()
    with pytest.raises(ProviderError, match='does not know'):
        sync_title(title, clients(FakeTmdb()), NOW)


def test_missing_tmdb_token_is_a_provider_error():
    title = movie_title()
    with pytest.raises(ProviderError, match='not configured'):
        sync_title(title, Clients(tmdb=None, anilist=FakeAniList()), NOW)


def test_title_without_its_provider_id_is_a_provider_error():
    title = Title.objects.create(kind='movie', original_title='x')
    with pytest.raises(ProviderError, match='no TMDB movie id'):
        sync_title(title, clients(FakeTmdb()), NOW)


def make_show(status='Returning Series'):
    seasons = {
        0: tmdb_season(0, 2, date(2019, 12, 1), 'Specials'),
        1: tmdb_season(1, 3, date(2020, 1, 1)),
        2: tmdb_season(2, 2, date(2021, 1, 1)),
    }
    return FakeTmdb(
        shows={1000: (tmdb_show(1000, list(seasons.values()), status, 'Show'), seasons)}
    )


def test_tv_show_creates_seasons_and_episodes():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    title.refresh_from_db()
    assert title.status == Title.Status.AIRING
    assert title.next_sync_at == NOW + timedelta(hours=6)
    assert [
        (s.number, s.kind, s.is_extra, s.episode_source) for s in title.seasons.all()
    ] == [
        (0, 'special', True, 'tmdb'),
        (1, 'tv', False, 'tmdb'),
        (2, 'tv', False, 'tmdb'),
    ]
    season_one = title.seasons.get(number=1)
    assert season_one.start_date == date(2020, 1, 1)
    assert season_one.episodes.count() == 3
    assert season_one.tmdb_tv_id == 1000


def test_absolute_numbers_run_across_regular_seasons_and_skip_specials():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    absolute = {
        (e.season.number, e.number): e.absolute_number
        for e in Episode.objects.select_related('season')
    }
    assert absolute[(1, 1)] == 1 and absolute[(1, 3)] == 3
    assert absolute[(2, 1)] == 4 and absolute[(2, 2)] == 5
    assert absolute[(0, 1)] is None


def test_episode_texts_are_stored_in_english_and_remember_their_tmdb_origin():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    episode = Episode.objects.get(season__title=title, season__number=2, number=2)
    assert (episode.tmdb_season_number, episode.tmdb_episode_number) == (2, 2)
    assert (
        EpisodeTranslation.objects.get(episode=episode, language='en').name
        == 'Episode 2'
    )


def test_resync_keeps_episode_rows_and_updates_dates():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    before = {
        e.number: e.pk
        for e in Episode.objects.filter(season__title=title, season__number=1)
    }
    moved = make_show()
    episodes = moved.shows[1000][1][1].episodes
    episodes[0] = replace(episodes[0], air_date=date(2020, 2, 1))
    sync_title(title, clients(moved), NOW)
    after = {
        e.number: e.pk
        for e in Episode.objects.filter(season__title=title, season__number=1)
    }
    assert before == after
    assert Episode.objects.get(
        season__title=title, season__number=1, number=1
    ).air_date == date(2020, 2, 1)


def test_an_episode_tmdb_stops_listing_is_kept():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    shorter = make_show()
    shorter.shows[1000][1][2].episodes.pop()
    sync_title(title, clients(shorter), NOW)
    assert Episode.objects.filter(season__title=title, season__number=2).count() == 2


def test_an_ended_show_is_checked_rarely():
    title = tv_title()
    sync_title(title, clients(make_show('Ended')), NOW)
    title.refresh_from_db()
    assert title.status == Title.Status.ENDED
    assert title.next_sync_at == NOW + timedelta(days=14)


def test_unknown_show_is_a_provider_error():
    title = tv_title('999')
    with pytest.raises(ProviderError, match='does not know'):
        sync_title(title, clients(make_show()), NOW)


def hit(provider='tmdb_tv', external_id='1000', kind='tv'):
    return SearchHit(
        provider, external_id, kind, 'Title', 'Original', 2020, 'https://p/x.jpg', ''
    )


def test_a_new_search_hit_creates_a_bare_title():
    title, created = get_or_create_title(hit())
    assert created is True
    assert (title.kind, title.original_title, title.poster_url) == (
        'tv',
        'Original',
        'https://p/x.jpg',
    )
    assert title.external_ids.get().external_id == '1000'
    assert title.synced_at is None


def test_the_same_hit_returns_the_same_title():
    first, _ = get_or_create_title(hit())
    second, created = get_or_create_title(hit())
    assert created is False and second.pk == first.pk
    assert Title.objects.count() == 1


def test_tmdb_movie_and_tv_hits_with_equal_numbers_are_different_titles():
    get_or_create_title(hit('tmdb_tv', '42', 'tv'))
    get_or_create_title(hit('tmdb_movie', '42', 'movie'))
    assert Title.objects.count() == 2


def test_a_hit_without_a_native_title_falls_back_to_the_display_title():
    bare = SearchHit('tmdb_tv', '7', 'tv', 'Display', '', None, '', '')
    title, _ = get_or_create_title(bare)
    assert title.original_title == 'Display'
