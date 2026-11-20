from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from catalog.models import Episode, EpisodeTranslation, ExternalId, Title, TitleDetails
from catalog.sync import get_or_create_title, movies, sync_title
from catalog.sync.common import MAX_TRAILERS, Clients, similar_dicts, trailer_dicts
from fakes import FakeAniList, FakeTmdb, tmdb_movie, tmdb_season, tmdb_show
from providers.http import ProviderError
from providers.tmdb import TmdbCollectionRef, TmdbCredit, TmdbVideo
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


def test_a_failed_movie_write_leaves_the_title_untouched(monkeypatch):
    title = movie_title()
    sync_title(title, clients(FakeTmdb(movies={603: tmdb_movie(603)})), NOW)

    def fail(*args, **kwargs):
        raise RuntimeError('translation write failed')

    monkeypatch.setattr('catalog.sync.movies.save_title_translation', fail)
    changed = replace(tmdb_movie(603), original_title='Changed', rating=1.0)
    later = NOW + timedelta(days=1)
    with pytest.raises(RuntimeError):
        sync_title(title, clients(FakeTmdb(movies={603: changed})), later)
    title.refresh_from_db()
    assert title.synced_at == NOW
    assert (title.original_title, str(title.rating)) == ('The Matrix (original)', '8.2')


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


def video(key='abcdef12', kind='Trailer', official=True, site='YouTube'):
    return TmdbVideo(site, key, f'Video {key}', kind, official, 'en')


def similar_hit(number, **kwargs):
    fields = {
        'provider': 'tmdb_movie',
        'external_id': str(number),
        'kind': 'movie',
        'title': f'Similar {number}',
        'original_title': f'Original {number}',
        'year': 2000 + number,
        'poster_url': f'https://p/{number}.jpg',
        'overview': 'ignored',
        'rating': 7.5,
    }
    return SearchHit(**{**fields, **kwargs})


def credits(count):
    return tuple(
        TmdbCredit(f'Actor {i}', f'Role {i}', f'https://p/a{i}.jpg')
        for i in range(count)
    )


def test_trailer_dicts_order_and_filter():
    result = trailer_dicts(
        [
            video('vimeo0001', site='Vimeo'),
            video('a b'),
            video('unoffic01', 'Trailer', official=False),
            video('teaser001', 'Teaser'),
            video('trailer01', 'Trailer'),
        ]
    )
    assert [item['key'] for item in result] == ['trailer01', 'teaser001', 'unoffic01']
    assert result[0] == {
        'key': 'trailer01',
        'name': 'Video trailer01',
        'kind': 'Trailer',
        'language': 'en',
        'official': True,
    }
    many = [video(f'valid{i:03d}') for i in range(MAX_TRAILERS + 1)]
    assert len(trailer_dicts(many)) == MAX_TRAILERS


def test_similar_dicts_excludes_and_caps():
    hits = [similar_hit(n) for n in range(15)]
    assert len(similar_dicts(hits)) == 12
    result = similar_dicts(
        [similar_hit(1), similar_hit(2), similar_hit(3, anime_like=True)],
        exclude={('tmdb_movie', '1')},
    )
    assert result == [
        {
            'provider': 'tmdb_movie',
            'external_id': '2',
            'kind': 'movie',
            'title': 'Similar 2',
            'original_title': 'Original 2',
            'year': 2002,
            'poster_url': 'https://p/2.jpg',
            'rating': 7.5,
        }
    ]


def test_movie_sync_writes_details():
    title = movie_title()
    movie = tmdb_movie(
        603,
        genre_ids=[28, 878, 99999],
        countries=['US'],
        cast=credits(10),
        videos=[video()],
        recommendations=[similar_hit(1)],
        collection=TmdbCollectionRef(2344, 'The Matrix Collection', 'https://p/c.jpg'),
    )
    sync_title(title, clients(FakeTmdb(movies={603: movie})), NOW)
    details = title.details
    assert details.genres == ['action', 'sci-fi']
    assert details.countries == ['US']
    assert len(details.cast) == 10
    assert details.cast[0] == {
        'name': 'Actor 0',
        'role': 'Role 0',
        'photo_url': 'https://p/a0.jpg',
    }
    assert [t['key'] for t in details.trailers] == ['abcdef12']
    assert [s['external_id'] for s in details.similar] == ['1']
    assert details.collection == {
        'id': 2344,
        'name': 'The Matrix Collection',
        'poster_url': 'https://p/c.jpg',
    }


def test_a_movie_without_a_collection_stores_none():
    title = movie_title()
    sync_title(title, clients(FakeTmdb(movies={603: tmdb_movie(603)})), NOW)
    assert title.details.collection is None
    assert title.details.genres == [] and title.details.cast == []


def test_tv_sync_writes_details():
    title = tv_title()
    seasons = {1: tmdb_season(1, 3, date(2020, 1, 1))}
    show = tmdb_show(
        1000,
        list(seasons.values()),
        genre_ids=[10765],
        countries=['US', 'GB'],
        cast=credits(3),
        videos=[video('teaser001', 'Teaser')],
        recommendations=[similar_hit(5, provider='tmdb_tv', kind='tv')],
    )
    sync_title(title, clients(FakeTmdb(shows={1000: (show, seasons)})), NOW)
    details = title.details
    assert details.genres == ['sci-fi', 'fantasy']
    assert details.countries == ['US', 'GB']
    assert [c['name'] for c in details.cast] == ['Actor 0', 'Actor 1', 'Actor 2']
    assert details.trailers[0]['kind'] == 'Teaser'
    assert details.similar[0]['provider'] == 'tmdb_tv'
    assert details.collection is None


def test_resync_replaces_details():
    title = movie_title()
    sync_title(
        title, clients(FakeTmdb(movies={603: tmdb_movie(603, cast=credits(10))})), NOW
    )
    smaller = tmdb_movie(603, cast=credits(2))
    sync_title(title, clients(FakeTmdb(movies={603: smaller})), NOW)
    details = TitleDetails.objects.get(title=title)
    assert [c['name'] for c in details.cast] == ['Actor 0', 'Actor 1']
    assert TitleDetails.objects.filter(title=title).count() == 1


def test_the_details_are_rolled_back_when_the_sync_fails_after_writing_them(
    monkeypatch,
):
    title = movie_title()
    sync_title(
        title, clients(FakeTmdb(movies={603: tmdb_movie(603, cast=credits(3))})), NOW
    )

    real_write = movies.write_details

    def write_then_fail(*args, **kwargs):
        # The new details are written; the sync fails right after them.
        real_write(*args, **kwargs)
        assert len(TitleDetails.objects.get(title=title).cast) == 1
        raise RuntimeError('crashed after the details were written')

    monkeypatch.setattr('catalog.sync.movies.write_details', write_then_fail)
    changed = tmdb_movie(603, cast=credits(1))
    with pytest.raises(RuntimeError):
        sync_title(title, clients(FakeTmdb(movies={603: changed})), NOW)
    assert len(TitleDetails.objects.get(title=title).cast) == 3
