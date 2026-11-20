from datetime import date

import httpx
import pytest
from providers.clients import get_tmdb
from providers.http import ProviderError
from providers.tmdb import COLLECTION_TTL, DISCOVER_TTL, TmdbClient

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
    'genres': [{'id': 28, 'name': 'Action'}, {'id': 878, 'name': 'Science Fiction'}],
    'production_countries': [{'iso_3166_1': 'US', 'name': 'United States'}],
    'credits': {
        # Shuffled on purpose: the client sorts by ``order``.
        'cast': [
            {
                'name': f'Actor {order}',
                'character': f'Role {order}',
                'order': order,
                'profile_path': None if order == 3 else f'/a{order}.jpg',
            }
            for order in reversed(range(12))
        ]
    },
    'videos': {
        'results': [
            {
                'site': 'Vimeo',
                'key': 'v1',
                'name': 'Clip',
                'type': 'Clip',
                'official': False,
                'iso_639_1': 'en',
            },
            {
                'site': 'YouTube',
                'key': 'yt1',
                'name': 'Official Trailer',
                'type': 'Trailer',
                'official': True,
                'iso_639_1': 'en',
            },
        ]
    },
    'recommendations': {
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
                'vote_average': 8.7,
                'vote_count': 900,
            },
            {
                'media_type': 'movie',
                'id': 604,
                'title': 'The Matrix Reloaded',
                'original_title': 'The Matrix Reloaded',
                'original_language': 'en',
                'genre_ids': [28],
                'release_date': '2003-05-15',
                'vote_average': 0.0,
                'vote_count': 0,
            },
        ]
    },
    'belongs_to_collection': {
        'id': 2344,
        'name': 'The Matrix Collection',
        'poster_path': '/matrix-coll.jpg',
    },
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
    'genres': [{'id': 16, 'name': 'Animation'}, {'id': 10765, 'name': 'Sci-Fi'}],
    'origin_country': ['JP'],
    'production_countries': [{'iso_3166_1': 'US', 'name': 'United States'}],
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
DISCOVER_MOVIES = {
    'page': 1,
    'total_pages': 3,
    'total_results': 55,
    'results': [
        {
            'id': 603,
            'title': 'The Matrix',
            'original_title': 'The Matrix',
            'original_language': 'en',
            'genre_ids': [28, 878],
            'release_date': '1999-03-31',
            'poster_path': '/matrix.jpg',
            'overview': 'A hacker learns the truth.',
            'vote_average': 8.2,
            'vote_count': 25000,
        },
        {
            'id': 12,
            'title': 'Akira',
            'original_title': 'アキラ',
            'original_language': 'ja',
            'genre_ids': [16, 878],
            'release_date': '',
            'vote_average': 0.0,
            'vote_count': 0,
        },
    ],
}
DISCOVER_SHOWS = {
    'page': 3,
    'total_pages': 3,
    'total_results': 55,
    'results': [
        {
            'id': 209867,
            'name': 'Frieren',
            'original_name': '葬送のフリーレン',
            'original_language': 'ja',
            'genre_ids': [16],
            'first_air_date': '2023-09-29',
            'vote_average': 8.7,
            'vote_count': 900,
        },
    ],
}
COLLECTION = {
    'id': 2344,
    'name': 'The Matrix Collection',
    'overview': 'The saga.',
    'poster_path': '/matrix-coll.jpg',
    'parts': [
        {'id': 605, 'title': 'The Matrix Revolutions', 'release_date': '2003-11-05'},
        {'id': 999, 'title': 'The Matrix 5', 'release_date': ''},
        {
            'id': 603,
            'title': 'The Matrix',
            'original_title': 'The Matrix',
            'original_language': 'en',
            'release_date': '1999-03-31',
            'poster_path': '/matrix.jpg',
            'vote_average': 8.2,
            'vote_count': 25000,
        },
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
            '/3/discover/movie': DISCOVER_MOVIES,
            '/3/discover/tv': DISCOVER_SHOWS,
            '/3/collection/2344': COLLECTION,
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


def test_movie_carries_the_enrichment(tmdb):
    movie = tmdb.movie(603, 'ru')
    assert movie.genre_ids == (28, 878)
    assert movie.countries == ('US',)
    assert [c.name for c in movie.cast] == [f'Actor {i}' for i in range(10)]
    assert movie.cast[0].character == 'Role 0'
    assert movie.cast[0].photo_url == 'https://image.tmdb.org/t/p/w185/a0.jpg'
    assert movie.cast[3].photo_url == ''
    assert [(v.site, v.key, v.kind, v.official) for v in movie.videos] == [
        ('YouTube', 'yt1', 'Trailer', True)
    ]
    assert movie.videos[0].name == 'Official Trailer'
    assert movie.videos[0].language == 'en'
    assert movie.collection.tmdb_id == 2344
    assert movie.collection.name == 'The Matrix Collection'
    assert movie.collection.poster_url == (
        'https://image.tmdb.org/t/p/w185/matrix-coll.jpg'
    )
    params = tmdb.requests[0].url.params
    assert {'credits', 'videos', 'recommendations'} <= set(
        params['append_to_response'].split(',')
    )
    assert params['include_video_language'] == 'ru,en,null'


def test_show_without_credits_or_videos_has_empty_enrichment(tmdb):
    show = tmdb.tv(209867)
    assert show.cast == ()
    assert show.videos == ()
    assert show.recommendations == ()
    assert show.genre_ids == (16, 10765)


def test_tv_countries_come_from_origin_country(tmdb):
    assert tmdb.tv(209867).countries == ('JP',)


def test_tv_countries_fall_back_to_production_countries(provider_http):
    body = {**SHOW, 'origin_country': []}
    client = TmdbClient(
        provider_http(lambda request: httpx.Response(200, json=body), name='tmdb')
    )
    assert client.tv(209867).countries == ('US',)


def test_recommendations_are_search_hits(tmdb):
    show, movie = tmdb.movie(603).recommendations
    assert (show.provider, show.external_id, show.kind) == (
        'tmdb_tv',
        '209867',
        'tv',
    )
    assert show.year == 2023
    assert show.rating == 8.7
    assert show.anime_like is True
    assert (movie.provider, movie.kind, movie.year) == ('tmdb_movie', 'movie', 2003)
    assert movie.rating is None
    assert movie.anime_like is False


def test_search_hits_carry_the_rating(tmdb):
    assert [h.rating for h in tmdb.search('x')] == [None, None]


def test_discover_movie_parameters(tmdb):
    tmdb.discover(
        'movie',
        genre='sci-fi',
        country='FR',
        year=2019,
        sort='rating',
        page=2,
        language='ru',
    )
    params = tmdb.requests[0].url.params
    assert tmdb.requests[0].url.path == '/3/discover/movie'
    assert params['sort_by'] == 'vote_average.desc'
    assert params['vote_count.gte'] == '100'
    assert params['with_genres'] == '878|10765'
    assert params['with_origin_country'] == 'FR'
    assert params['primary_release_year'] == '2019'
    assert params['include_adult'] == 'false'
    assert params['language'] == 'ru-RU'
    assert params['page'] == '2'


def test_discover_tv_parameters(tmdb):
    tmdb.discover(
        'tv', genre=None, country=None, year=2020, sort='title', page=1, language='en'
    )
    params = tmdb.requests[0].url.params
    assert tmdb.requests[0].url.path == '/3/discover/tv'
    assert params['sort_by'] == 'name.asc'
    assert params['first_air_date_year'] == '2020'
    for absent in (
        'with_genres',
        'with_origin_country',
        'primary_release_year',
        'vote_count.gte',
    ):
        assert absent not in params


def test_discover_sort_orders(tmdb):
    sorts = {}
    for kind in ('movie', 'tv'):
        for sort in ('popularity', 'date', 'title', 'rating'):
            tmdb.discover(
                kind,
                genre=None,
                country=None,
                year=None,
                sort=sort,
                page=1,
                language='en',
            )
            sorts[kind, sort] = tmdb.requests[-1].url.params['sort_by']
    assert sorts == {
        ('movie', 'popularity'): 'popularity.desc',
        ('movie', 'date'): 'primary_release_date.desc',
        ('movie', 'title'): 'title.asc',
        ('movie', 'rating'): 'vote_average.desc',
        ('tv', 'popularity'): 'popularity.desc',
        ('tv', 'date'): 'first_air_date.desc',
        ('tv', 'title'): 'name.asc',
        ('tv', 'rating'): 'vote_average.desc',
    }


def test_discover_unknown_genre_is_not_filtered(tmdb):
    tmdb.discover(
        'movie',
        genre='no-such-genre',
        country=None,
        year=None,
        sort='popularity',
        page=1,
        language='en',
    )
    assert 'with_genres' not in tmdb.requests[0].url.params


def test_discover_hits(tmdb):
    page = tmdb.discover(
        'movie',
        genre=None,
        country=None,
        year=None,
        sort='popularity',
        page=1,
        language='en',
    )
    matrix, akira = page.hits
    assert (matrix.provider, matrix.kind, matrix.year) == ('tmdb_movie', 'movie', 1999)
    assert matrix.rating == 8.2
    assert akira.year is None
    assert akira.rating is None
    assert akira.anime_like is True


def test_discover_page_totals(tmdb):
    first = tmdb.discover(
        'movie',
        genre=None,
        country=None,
        year=None,
        sort='popularity',
        page=1,
        language='en',
    )
    last = tmdb.discover(
        'tv',
        genre=None,
        country=None,
        year=None,
        sort='popularity',
        page=3,
        language='en',
    )
    assert (first.total, first.has_more) == (55, True)
    assert (last.total, last.has_more) == (55, False)


def test_unknown_discover_page_is_empty(provider_http):
    client = TmdbClient(provider_http(lambda request: httpx.Response(404), name='tmdb'))
    page = client.discover(
        'movie',
        genre=None,
        country=None,
        year=None,
        sort='popularity',
        page=99,
        language='en',
    )
    assert (page.hits, page.total, page.has_more) == ([], 0, False)


def test_discover_is_cached_for_three_hours(tmdb, redis_client, redis_prefix):
    for _ in range(2):
        tmdb.discover(
            'movie',
            genre=None,
            country=None,
            year=None,
            sort='popularity',
            page=1,
            language='en',
        )
    assert len(tmdb.requests) == 1
    keys = [
        key
        for key in redis_client.scan_iter(f'{redis_prefix}*')
        if ':stale:' not in key and ':lock:' not in key
    ]
    assert len(keys) == 1
    assert DISCOVER_TTL - 60 < redis_client.ttl(keys[0]) <= DISCOVER_TTL


def test_discover_cache_key_varies_with_the_arguments(tmdb):
    base = {
        'genre': None,
        'country': None,
        'year': None,
        'sort': 'popularity',
        'page': 1,
        'language': 'en',
    }
    variations = [
        {},
        {'language': 'ru'},
        {'page': 2},
        {'genre': 'sci-fi'},
        {'sort': 'rating'},
        {'country': 'FR'},
        {'year': 2019},
    ]
    for sent, change in enumerate(variations, start=1):
        tmdb.discover('movie', **{**base, **change})
        assert len(tmdb.requests) == sent
    tmdb.discover('tv', **base)
    assert len(tmdb.requests) == len(variations) + 1
    for change in variations:
        tmdb.discover('movie', **{**base, **change})
    assert len(tmdb.requests) == len(variations) + 1


def test_collection_parts_are_sorted_and_cached(tmdb, redis_client, redis_prefix):
    collection = tmdb.collection(2344, 'ru')
    tmdb.collection(2344, 'ru')
    assert len(tmdb.requests) == 1
    assert tmdb.requests[0].url.params['language'] == 'ru-RU'
    assert (collection.tmdb_id, collection.name, collection.overview) == (
        2344,
        'The Matrix Collection',
        'The saga.',
    )
    assert collection.poster_url == 'https://image.tmdb.org/t/p/w342/matrix-coll.jpg'
    assert [(p.external_id, p.provider, p.kind) for p in collection.parts] == [
        ('603', 'tmdb_movie', 'movie'),
        ('605', 'tmdb_movie', 'movie'),
        ('999', 'tmdb_movie', 'movie'),
    ]
    assert collection.parts[0].rating == 8.2
    assert collection.parts[2].year is None
    keys = [
        key
        for key in redis_client.scan_iter(f'{redis_prefix}*')
        if ':stale:' not in key and ':lock:' not in key
    ]
    assert len(keys) == 1
    assert COLLECTION_TTL - 60 < redis_client.ttl(keys[0]) <= COLLECTION_TTL


def test_unknown_collection_is_none(tmdb):
    assert tmdb.collection(1) is None


def test_get_tmdb_requires_a_token(monkeypatch):
    from mitaroku.config import load_settings

    monkeypatch.delenv('MITAROKU_PROVIDERS__TMDB_TOKEN', raising=False)
    load_settings.cache_clear()
    try:
        with pytest.raises(ProviderError, match='tmdb_token'):
            get_tmdb()
    finally:
        load_settings.cache_clear()
