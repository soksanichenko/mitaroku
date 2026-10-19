from datetime import UTC, date, datetime

import httpx
import pytest
from providers.anilist import AniListClient
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
