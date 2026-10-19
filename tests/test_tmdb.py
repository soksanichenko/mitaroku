from datetime import date

import httpx
import pytest
from providers.clients import get_tmdb
from providers.http import ProviderError
from providers.tmdb import TmdbClient

SEARCH = {
    'results': [
        {
            'media_type': 'tv',
            'id': 209867,
            'name': 'Frieren: Beyond Journey’s End',
            'original_name': '葬送のフリーレン',
            'original_language': 'ja',
            'genre_ids': [16, 18, 10765],
            'first_air_date': '2023-09-29',
            'poster_path': '/frieren.jpg',
            'overview': 'An elf mage.',
        },
        {
            'media_type': 'movie',
            'id': 603,
            'title': 'The Matrix',
            'original_title': 'The Matrix',
            'original_language': 'en',
            'genre_ids': [28, 878],
            'release_date': '1999-03-31',
            'poster_path': None,
            'overview': '',
        },
        {'media_type': 'person', 'id': 1, 'name': 'Somebody'},
    ]
}
MOVIE = {
    'id': 603,
    'title': 'The Matrix',
    'original_title': 'The Matrix',
    'original_language': 'en',
    'overview': 'A hacker learns the truth.',
    'release_date': '1999-03-31',
    'runtime': 136,
    'status': 'Released',
    'poster_path': '/matrix.jpg',
    'backdrop_path': '/matrix-bg.jpg',
    'vote_average': 8.2,
    'vote_count': 25000,
    'external_ids': {'imdb_id': 'tt0133093'},
}
SHOW = {
    'id': 209867,
    'name': 'Frieren',
    'original_name': '葬送のフリーレン',
    'original_language': 'ja',
    'overview': 'An elf mage.',
    'first_air_date': '2023-09-29',
    'status': 'Returning Series',
    'poster_path': '/frieren.jpg',
    'backdrop_path': None,
    'vote_average': 0.0,
    'vote_count': 0,
    'external_ids': {'imdb_id': None},
    'seasons': [
        {'season_number': 0, 'name': 'Specials', 'episode_count': 2},
        {'season_number': 1, 'name': 'Season 1', 'episode_count': 28},
    ],
}
SEASON = {
    'season_number': 1,
    'name': 'Season 1',
    'episodes': [
        {
            'episode_number': 1,
            'name': 'The Journey’s End',
            'overview': 'Start.',
            'air_date': '2023-09-29',
        },
        {'episode_number': 2, 'name': 'Next', 'overview': '', 'air_date': ''},
    ],
}


@pytest.fixture
def tmdb(provider_http):
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path
        routes = {
            '/3/search/multi': SEARCH,
            '/3/movie/603': MOVIE,
            '/3/tv/209867': SHOW,
            '/3/tv/209867/season/1': SEASON,
            '/3/tv/209867/season/0': {
                **SEASON,
                'season_number': 0,
                'name': 'Specials',
                'episodes': [],
            },
        }
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404)

    client = TmdbClient(provider_http(handler, name='tmdb'))
    client.requests = seen
    return client


def test_search_keeps_movies_and_shows_and_drops_people(tmdb):
    hits = tmdb.search('frieren', 'ru')
    assert [(h.provider, h.external_id, h.kind) for h in hits] == [
        ('tmdb_tv', '209867', 'tv'),
        ('tmdb_movie', '603', 'movie'),
    ]
    assert tmdb.requests[0].url.params['language'] == 'ru-RU'


def test_search_hit_fields(tmdb):
    show, movie = tmdb.search('x')
    assert show.title == 'Frieren: Beyond Journey’s End'
    assert show.original_title == '葬送のフリーレン'
    assert show.year == 2023
    assert show.poster_url == 'https://image.tmdb.org/t/p/w185/frieren.jpg'
    assert movie.poster_url == ''


def test_japanese_animation_is_marked_anime_like(tmdb):
    show, movie = tmdb.search('x')
    assert show.anime_like is True
    assert movie.anime_like is False


def test_movie_details(tmdb):
    movie = tmdb.movie(603)
    assert movie.title == 'The Matrix'
    assert movie.release_date == date(1999, 3, 31)
    assert movie.runtime == 136
    assert movie.rating == 8.2
    assert movie.imdb_id == 'tt0133093'
    assert movie.poster_url == 'https://image.tmdb.org/t/p/w342/matrix.jpg'


def test_show_without_votes_has_no_rating_and_no_imdb_id(tmdb):
    show = tmdb.tv(209867)
    assert show.rating is None
    assert show.imdb_id == ''
    assert show.backdrop_url == ''
    assert [(s.number, s.episode_count) for s in show.seasons] == [(0, 2), (1, 28)]


def test_unknown_id_returns_none(tmdb):
    assert tmdb.movie(1) is None
    assert tmdb.tv(1) is None
    assert tmdb.season(209867, 9) is None


def test_season_episodes_with_missing_dates(tmdb):
    season = tmdb.season(209867, 1)
    assert season.episodes[0].air_date == date(2023, 9, 29)
    assert season.episodes[0].season_number == 1
    assert season.episodes[1].air_date is None
    assert season.episodes[1].overview == ''


def test_seasons_fetches_every_season_including_specials(tmdb):
    show = tmdb.tv(209867)
    seasons = tmdb.seasons(show)
    assert sorted(seasons) == [0, 1]
    assert len(seasons[1].episodes) == 2


def test_get_tmdb_requires_a_token(monkeypatch):
    from mitaroku.config import load_settings

    monkeypatch.delenv('MITAROKU_PROVIDERS__TMDB_TOKEN', raising=False)
    load_settings.cache_clear()
    try:
        with pytest.raises(ProviderError, match='tmdb_token'):
            get_tmdb()
    finally:
        load_settings.cache_clear()
