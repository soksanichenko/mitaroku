from datetime import UTC, date, datetime

import httpx
import pytest
from providers.anilist import AniListClient, AniListCredit, AniListDetails
from providers.http import ProviderError

FRIEREN = {
    'id': 154587,
    'idMal': 52991,
    'format': 'TV',
    'status': 'FINISHED',
    'episodes': 28,
    'averageScore': 91,
    'isAdult': False,
    'title': {
        'romaji': 'Sousou no Frieren',
        'english': 'Frieren',
        'native': '葬送のフリーレン',
    },
    'coverImage': {'large': 'https://img.example/f.jpg'},
    'bannerImage': None,
    'startDate': {'year': 2023, 'month': 9, 'day': 29},
    'relations': {
        'edges': [
            {
                'relationType': 'SEQUEL',
                'node': {'id': 182255, 'idMal': None, 'format': None, 'type': 'ANIME'},
            },
            {
                'relationType': 'ADAPTATION',
                'node': {
                    'id': 118586,
                    'idMal': 126287,
                    'format': 'MANGA',
                    'type': 'MANGA',
                },
            },
        ]
    },
}
ANNOUNCED = {
    'id': 182255,
    'idMal': None,
    'format': None,
    'status': 'NOT_YET_RELEASED',
    'episodes': None,
    'averageScore': None,
    'isAdult': False,
    'title': {
        'romaji': 'Sousou no Frieren 2nd Season',
        'english': None,
        'native': None,
    },
    'coverImage': None,
    'bannerImage': None,
    'startDate': {'year': 2026, 'month': None, 'day': None},
    'relations': None,
}


def page(*media):
    return {'data': {'Page': {'media': list(media)}}}


@pytest.fixture
def anilist(provider_http):
    bodies = []

    def handler(request):
        import json

        body = json.loads(request.content)
        bodies.append(body)
        variables = body['variables']
        if 'airingSchedule' in body['query']:
            if variables['id'] == 404:
                return httpx.Response(404, json={'errors': [{'message': 'Not Found.'}]})
            return httpx.Response(
                200,
                json={
                    'data': {
                        'Media': {
                            'upcoming': {
                                'nodes': [{'episode': 29, 'airingAt': 1798985760}]
                            },
                            'schedule': {
                                'nodes': [
                                    {'episode': 1, 'airingAt': 1695997200},
                                    {'episode': 29, 'airingAt': 1798985760},
                                ]
                            },
                        }
                    }
                },
            )
        if 'search' in variables:
            adult = {**FRIEREN, 'id': 5, 'isAdult': True}
            manga_like = {**FRIEREN, 'id': 6, 'format': 'MUSIC'}
            return httpx.Response(200, json=page(FRIEREN, adult, manga_like))
        known = {154587: FRIEREN, 182255: ANNOUNCED}
        return httpx.Response(
            200, json=page(*[known[i] for i in variables['ids'] if i in known])
        )

    client = AniListClient(provider_http(handler, name='anilist'))
    client.bodies = bodies
    return client


def test_media_fields(anilist):
    media = anilist.media_many([154587])[0]
    assert media.anilist_id == 154587
    assert media.mal_id == 52991
    assert media.english == 'Frieren'
    assert media.native == '葬送のフリーレン'
    assert media.episodes == 28
    assert media.average_score == 91
    assert media.start_date == date(2023, 9, 29)
    assert media.cover_url == 'https://img.example/f.jpg'


def test_edges_keep_a_null_format(anilist):
    media = anilist.media_many([154587])[0]
    assert [(e.relation, e.anilist_id, e.format) for e in media.edges] == [
        ('SEQUEL', 182255, None),
        ('ADAPTATION', 118586, 'MANGA'),
    ]


def test_announced_entry_with_partial_data(anilist):
    media = anilist.media_many([182255])[0]
    assert media.format is None
    assert media.edges == []
    assert media.start == (2026, None, None)
    assert media.start_date is None
    assert media.english == ''


def test_unknown_ids_are_absent(anilist):
    assert anilist.media_many([1, 2]) == []


def test_ids_are_sent_in_pages_of_fifty(anilist):
    anilist.media_many(list(range(1, 121)))
    assert [len(b['variables']['ids']) for b in anilist.bodies] == [50, 50, 20]


def test_schedule(anilist):
    schedule = anilist.schedule(154587)
    assert schedule.upcoming == {29: datetime.fromtimestamp(1798985760, UTC)}
    assert sorted(schedule.all) == [1, 29]


def test_schedule_of_an_unknown_entry_is_empty(anilist):
    schedule = anilist.schedule(404)
    assert schedule.upcoming == {} and schedule.all == {}


def test_search_drops_adult_and_non_anime_formats(anilist):
    hits = anilist.search('frieren')
    assert [(h.provider, h.external_id, h.kind) for h in hits] == [
        ('anilist', '154587', 'anime')
    ]
    assert hits[0].title == 'Frieren'
    assert hits[0].original_title == 'Sousou no Frieren'
    assert hits[0].year == 2023


def test_graphql_errors_without_data_are_a_provider_error(provider_http):
    def handler(request):
        return httpx.Response(200, json={'errors': [{'message': 'boom'}]})

    client = AniListClient(provider_http(handler, name='anilist'))
    with pytest.raises(ProviderError, match='boom'):
        client.media_many([1])


def test_null_page_is_a_provider_error(provider_http):
    handler = lambda request: httpx.Response(200, json={'data': {'Page': None}})  # noqa: E731
    client = AniListClient(provider_http(handler, name='anilist'))
    with pytest.raises(ProviderError):
        client.media_many([1])
    with pytest.raises(ProviderError):
        client.search('x')


def test_an_error_body_without_data_is_a_provider_error_and_not_cached(provider_http):
    bodies = iter(
        [{'errors': [{'message': 'boom'}]}, {'data': {'Page': {'media': []}}}]
    )
    client = AniListClient(
        provider_http(lambda r: httpx.Response(200, json=next(bodies)), name='anilist')
    )
    with pytest.raises(ProviderError):
        client.media_many([1])
    assert client.media_many([1]) == []  # the error was not cached


def recommendation(anilist_id, **changes):
    media = {
        'id': anilist_id,
        'format': 'TV',
        'isAdult': False,
        'averageScore': 84,
        'title': {'romaji': 'Mushishi', 'english': 'Mushi-shi'},
        'coverImage': {'large': 'https://img.example/m.jpg'},
        'startDate': {'year': 2005},
    }
    return {'mediaRecommendation': {**media, **changes}}


DETAILS = {
    'genres': ['Adventure', 'Fantasy'],
    'countryOfOrigin': 'JP',
    'trailer': {'id': 'tR8YH0G67Rk', 'site': 'youtube'},
    'characters': {
        'edges': [
            {
                'node': {
                    'name': {'full': 'Frieren'},
                    'image': {'medium': 'https://img.example/c1.jpg'},
                },
                'voiceActors': [
                    {'name': {'full': 'Atsumi Tanezaki'}},
                    {'name': {'full': 'Someone Else'}},
                ],
            },
            {
                'node': {'name': {'full': 'Fern'}, 'image': {'medium': None}},
                'voiceActors': [],
            },
        ]
    },
    'recommendations': {
        'nodes': [
            recommendation(1),
            {'mediaRecommendation': None},
            recommendation(2, isAdult=True),
            recommendation(3, format='MANGA'),
        ]
    },
}


def graphql_client(provider_http, body):
    """Return a client answering every request with ``body``; it records requests."""
    bodies = []

    def handler(request):
        import json

        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    client = AniListClient(provider_http(handler, name='anilist'))
    client.bodies = bodies
    return client


def test_details_parse(provider_http):
    client = graphql_client(provider_http, {'data': {'Media': DETAILS}})
    details = client.details(154587)
    assert client.bodies[0]['variables'] == {'id': 154587}
    assert details.genres == ('Adventure', 'Fantasy')
    assert details.country == 'JP'
    assert details.trailer == ('youtube', 'tR8YH0G67Rk')
    assert details.cast == (
        AniListCredit('Frieren', 'Atsumi Tanezaki', 'https://img.example/c1.jpg'),
        AniListCredit('Fern', '', ''),
    )
    assert [(h.provider, h.external_id, h.kind) for h in details.recommendations] == [
        ('anilist', '1', 'anime')
    ]
    hit = details.recommendations[0]
    assert hit.title == 'Mushi-shi'
    assert hit.original_title == 'Mushishi'
    assert hit.year == 2005
    assert hit.poster_url == 'https://img.example/m.jpg'
    assert hit.rating == 8.4


def test_details_without_voice_actor_has_an_empty_actor(provider_http):
    client = graphql_client(provider_http, {'data': {'Media': DETAILS}})
    assert client.details(154587).cast[1].actor == ''


def test_details_of_a_sparse_entry(provider_http):
    sparse = {'genres': None, 'countryOfOrigin': None, 'trailer': None}
    client = graphql_client(provider_http, {'data': {'Media': sparse}})
    assert client.details(1) == AniListDetails(
        genres=(), country='', trailer=None, cast=(), recommendations=()
    )


def test_unknown_entry_has_no_details(provider_http):
    client = graphql_client(provider_http, {'data': {'Media': None}})
    assert client.details(404) is None


def discover_item(anilist_id, edges=(), **changes):
    item = {
        'id': anilist_id,
        'isAdult': False,
        'averageScore': 77,
        'title': {'romaji': f'Romaji {anilist_id}', 'english': None},
        'coverImage': {'large': f'https://img.example/{anilist_id}.jpg'},
        'startDate': {'year': 2022},
        'relations': {
            'edges': [
                {'relationType': relation, 'node': {'type': node_type}}
                for relation, node_type in edges
            ]
        },
    }
    return {**item, **changes}


def discover_body(*media, total=2, has_next=False):
    page_info = {'total': total, 'hasNextPage': has_next}
    return {'data': {'Page': {'pageInfo': page_info, 'media': list(media)}}}


def test_discover_variables(provider_http):
    client = graphql_client(provider_http, discover_body())
    client.discover(genre='Sports', country='JP', year=2022, sort='rating', page=3)
    assert client.bodies[0]['variables'] == {
        'genre': 'Sports',
        'countryOfOrigin': 'JP',
        'startDate_greater': 20220000,
        'startDate_lesser': 20221232,
        'sort': ['SCORE_DESC'],
        'page': 3,
        'perPage': 20,
    }


def test_discover_without_filters_omits_them(provider_http):
    client = graphql_client(provider_http, discover_body())
    client.discover(genre=None, country=None, year=None, sort='popularity', page=1)
    assert client.bodies[0]['variables'] == {
        'sort': ['POPULARITY_DESC'],
        'page': 1,
        'perPage': 20,
    }


@pytest.mark.parametrize(
    ('sort', 'value'),
    [
        ('popularity', 'POPULARITY_DESC'),
        ('date', 'START_DATE_DESC'),
        ('title', 'TITLE_ENGLISH'),
        ('rating', 'SCORE_DESC'),
    ],
)
def test_discover_sorts(provider_http, sort, value):
    client = graphql_client(provider_http, discover_body())
    client.discover(genre=None, country=None, year=None, sort=sort, page=1)
    assert client.bodies[0]['variables']['sort'] == [value]


def test_discover_unknown_sort_is_a_key_error(provider_http):
    client = graphql_client(provider_http, discover_body())
    with pytest.raises(KeyError):
        client.discover(genre=None, country=None, year=None, sort='nope', page=1)


def test_discover_drops_sequels_and_adult(provider_http):
    client = graphql_client(
        provider_http,
        discover_body(
            discover_item(1, edges=[('SEQUEL', 'ANIME'), ('ADAPTATION', 'MANGA')]),
            discover_item(2, edges=[('PREQUEL', 'ANIME')]),
            discover_item(3, edges=[('PREQUEL', 'MANGA')]),
            discover_item(4, isAdult=True),
        ),
    )
    page = client.discover(genre=None, country=None, year=None, sort='date', page=1)
    assert [h.external_id for h in page.hits] == ['1', '3']
    hit = page.hits[0]
    assert (hit.provider, hit.kind) == ('anilist', 'anime')
    assert hit.title == 'Romaji 1'
    assert hit.original_title == 'Romaji 1'
    assert hit.year == 2022
    assert hit.poster_url == 'https://img.example/1.jpg'
    assert hit.rating == 7.7


def test_discover_unrated_entry_has_no_rating(provider_http):
    client = graphql_client(
        provider_http, discover_body(discover_item(1, averageScore=None))
    )
    page = client.discover(genre=None, country=None, year=None, sort='date', page=1)
    assert page.hits[0].rating is None


def test_discover_paging(provider_http):
    client = graphql_client(
        provider_http, discover_body(discover_item(1), total=137, has_next=True)
    )
    page = client.discover(genre=None, country=None, year=None, sort='date', page=1)
    assert (page.total, page.has_more) == (137, True)


def test_discover_bad_body_is_an_error(provider_http):
    client = graphql_client(provider_http, {'errors': [{'message': 'boom'}]})
    with pytest.raises(ProviderError, match='boom'):
        client.discover(genre=None, country=None, year=None, sort='date', page=1)
