from datetime import date

from django.http import QueryDict
from providers.browse import MAX_PAGE, BrowseQuery, browse
from providers.http import ProviderError
from providers.types import HitPage, SearchHit


def hit(provider, external_id, kind='tv', anime_like=False):
    return SearchHit(
        provider, external_id, kind, f'T{external_id}', '', None, '', '', anime_like
    )


class FakeTmdbDiscover:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {}
        self.error = error
        self.calls = []

    def discover(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        if self.error:
            raise self.error
        return self.pages.get(kind, HitPage([], 0, False))


class FakeAniListDiscover:
    def __init__(self, page=None, error=None):
        self.page = page or HitPage([], 0, False)
        self.error = error
        self.calls = []

    def discover(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.page


def ids(results):
    return [h.external_id for h in results.hits]


def test_parse_accepts_valid_values():
    year = date.today().year + 1
    query = BrowseQuery.parse(
        {
            'type': 'anime',
            'genre': 'sci-fi',
            'country': 'JP',
            'year': str(year),
            'sort': 'rating',
            'page': '3',
        }
    )
    assert query == BrowseQuery('anime', 'sci-fi', 'JP', year, 'rating', 3)
    assert BrowseQuery.parse({}) == BrowseQuery()
    assert BrowseQuery.parse({'year': '1950'}).year == 1950


def test_parse_ignores_invalid_values():
    query = BrowseQuery.parse(
        {
            'year': 'abc',
            'genre': '<script>',
            'country': 'ZZ',
            'sort': 'x',
            'type': 'music',
            'page': '0',
        }
    )
    assert query == BrowseQuery()
    for year in (
        '1800',
        '1949',
        str(date.today().year + 2),
        '202',
        '20245',
        '２０２４',
    ):
        assert BrowseQuery.parse({'year': year}).year is None
    for country in ('jp', 'J', 'JPN', 'JP\n', 'DD'):
        assert BrowseQuery.parse({'country': country}).country is None
    for page in ('-3', 'abc', '', '1.5', ' 2', '²', '٣'):
        assert BrowseQuery.parse({'page': page}).page == 1


def test_parse_reads_the_type_key_and_not_the_kind_key():
    assert BrowseQuery.parse({'type': 'tv'}).kind == 'tv'
    assert BrowseQuery.parse({'kind': 'tv'}) == BrowseQuery()
    assert BrowseQuery.parse({'type': 'anime', 'kind': 'tv'}).kind == 'anime'


def test_parse_takes_the_first_value_of_a_repeated_key():
    params = QueryDict('type=tv&type=anime&page=2&page=5')
    assert BrowseQuery.parse(params) == BrowseQuery(kind='tv', page=2)


def test_parse_survives_absurd_input():
    query = BrowseQuery.parse(
        {key: '9' * 10_000 for key in ('type', 'genre', 'country', 'year', 'page')}
    )
    assert query == BrowseQuery(page=MAX_PAGE)


def test_browse_query_clamps_the_page():
    assert BrowseQuery.parse({'page': '99999999'}).page == MAX_PAGE
    assert BrowseQuery.parse({'page': str(MAX_PAGE)}).page == MAX_PAGE
    assert BrowseQuery.parse({'page': '0007'}).page == 7


def test_all_kinds_query_three_sources_and_interleave():
    tmdb = FakeTmdbDiscover(
        {
            'movie': HitPage([hit('tmdb_movie', 'm1'), hit('tmdb_movie', 'm2')], 2, 0),
            'tv': HitPage([hit('tmdb_tv', 't1'), hit('tmdb_tv', 't2')], 2, 0),
        }
    )
    anilist = FakeAniListDiscover(
        HitPage([hit('anilist', 'a1', 'anime'), hit('anilist', 'a2', 'anime')], 2, 0)
    )
    results = browse(
        BrowseQuery(genre='drama', country='JP', year=2020, sort='date', page=2),
        'ru',
        tmdb=tmdb,
        anilist=anilist,
    )
    assert ids(results) == ['m1', 't1', 'a1', 'm2', 't2', 'a2']
    assert results.failed == []
    assert tmdb.calls == [
        (
            kind,
            {
                'genre': 'drama',
                'country': 'JP',
                'year': 2020,
                'sort': 'date',
                'page': 2,
                'language': 'ru',
            },
        )
        for kind in ('movie', 'tv')
    ]
    # AniList takes the genre name and no language.
    assert anilist.calls == [
        {'genre': 'Drama', 'country': 'JP', 'year': 2020, 'sort': 'date', 'page': 2}
    ]


def test_a_single_kind_queries_one_source():
    tmdb = FakeTmdbDiscover({'tv': HitPage([hit('tmdb_tv', 't1')], 1, False)})
    anilist = FakeAniListDiscover()
    results = browse(BrowseQuery(kind='tv'), 'en', tmdb=tmdb, anilist=anilist)
    assert ids(results) == ['t1']
    assert [kind for kind, _ in tmdb.calls] == ['tv']
    assert anilist.calls == []

    tmdb = FakeTmdbDiscover()
    anilist = FakeAniListDiscover(HitPage([hit('anilist', 'a1', 'anime')], 1, False))
    results = browse(BrowseQuery(kind='anime'), 'en', tmdb=tmdb, anilist=anilist)
    assert ids(results) == ['a1']
    assert tmdb.calls == []
    assert anilist.calls[0]['genre'] is None


def test_anime_is_skipped_for_a_country_it_does_not_cover():
    tmdb = FakeTmdbDiscover({'movie': HitPage([hit('tmdb_movie', 'm1')], 1, False)})
    anilist = FakeAniListDiscover()
    results = browse(BrowseQuery(country='FR'), 'en', tmdb=tmdb, anilist=anilist)
    assert anilist.calls == []
    assert results.failed == []
    assert ids(results) == ['m1']


def test_anime_is_skipped_for_a_genre_it_lacks():
    tmdb = FakeTmdbDiscover()
    anilist = FakeAniListDiscover()
    results = browse(BrowseQuery(genre='western'), 'en', tmdb=tmdb, anilist=anilist)
    assert anilist.calls == []
    assert results.failed == []
    assert [kind for kind, _ in tmdb.calls] == ['movie', 'tv']


def test_a_skipped_anime_kind_is_empty_and_not_failed():
    tmdb = FakeTmdbDiscover()
    anilist = FakeAniListDiscover()
    results = browse(
        BrowseQuery(kind='anime', country='FR'), 'en', tmdb=tmdb, anilist=anilist
    )
    assert results.hits == [] and results.failed == []
    assert results.has_more is False
    assert tmdb.calls == [] and anilist.calls == []


def test_japanese_animation_from_tmdb_is_dropped():
    tmdb = FakeTmdbDiscover(
        {
            'tv': HitPage(
                [hit('tmdb_tv', 't1', anime_like=True), hit('tmdb_tv', 't2')], 2, False
            )
        }
    )
    results = browse(
        BrowseQuery(kind='tv'), 'en', tmdb=tmdb, anilist=FakeAniListDiscover()
    )
    assert ids(results) == ['t2']


def test_one_source_down():
    anilist_down = FakeAniListDiscover(error=ProviderError('down'))
    tmdb = FakeTmdbDiscover({'movie': HitPage([hit('tmdb_movie', 'm1')], 1, False)})
    results = browse(BrowseQuery(), 'en', tmdb=tmdb, anilist=anilist_down)
    assert ids(results) == ['m1']
    assert results.failed == ['anilist']

    anilist = FakeAniListDiscover(HitPage([hit('anilist', 'a1', 'anime')], 1, False))
    results = browse(
        BrowseQuery(),
        'en',
        tmdb=FakeTmdbDiscover(error=ProviderError('down')),
        anilist=anilist,
    )
    assert ids(results) == ['a1']
    assert results.failed == ['tmdb']

    results = browse(
        BrowseQuery(),
        'en',
        tmdb=FakeTmdbDiscover(error=ProviderError('down')),
        anilist=anilist_down,
    )
    assert results.hits == []
    assert results.failed == ['tmdb', 'anilist']
    assert results.has_more is False


def test_no_tmdb_token_is_a_failed_source():
    anilist = FakeAniListDiscover(HitPage([hit('anilist', 'a1', 'anime')], 1, False))
    results = browse(BrowseQuery(), 'en', tmdb=None, anilist=anilist)
    assert ids(results) == ['a1']
    assert results.failed == ['tmdb']
    # Anime does not need TMDB.
    results = browse(BrowseQuery(kind='anime'), 'en', tmdb=None, anilist=anilist)
    assert results.failed == []
    results = browse(BrowseQuery(kind='movie'), 'en', tmdb=None, anilist=anilist)
    assert results.failed == ['tmdb'] and results.hits == []


def test_has_more_is_true_when_any_source_has_a_later_page():
    tmdb = FakeTmdbDiscover(
        {
            'movie': HitPage([hit('tmdb_movie', 'm1')], 100, False),
            'tv': HitPage([hit('tmdb_tv', 't1')], 40, True),
        }
    )
    anilist = FakeAniListDiscover(HitPage([hit('anilist', 'a1', 'anime')], 7, False))
    assert browse(BrowseQuery(), 'en', tmdb=tmdb, anilist=anilist).has_more is True
    tmdb = FakeTmdbDiscover({'movie': HitPage([], 100, False)})
    assert browse(BrowseQuery(), 'en', tmdb=tmdb, anilist=anilist).has_more is False


def test_a_genre_tmdb_cannot_filter_skips_tmdb_and_asks_anilist():
    tmdb = FakeTmdbDiscover()
    anilist = FakeAniListDiscover(HitPage([hit('anilist', 'a1', 'anime')], 1, False))
    results = browse(BrowseQuery(genre='sports'), 'en', tmdb=tmdb, anilist=anilist)
    assert tmdb.calls == []
    assert [call['genre'] for call in anilist.calls] == ['Sports']
    assert ids(results) == ['a1'] and results.failed == []


def test_a_genre_tmdb_cannot_filter_is_not_a_failure_for_a_single_tmdb_kind():
    tmdb = FakeTmdbDiscover()
    anilist = FakeAniListDiscover()
    for kind in ('movie', 'tv'):
        results = browse(
            BrowseQuery(kind=kind, genre='sports'), 'en', tmdb=tmdb, anilist=anilist
        )
        assert results.hits == [] and results.failed == []
        assert results.has_more is False
    assert tmdb.calls == [] and anilist.calls == []


def test_skipping_tmdb_for_a_genre_needs_no_token():
    anilist = FakeAniListDiscover(HitPage([hit('anilist', 'a1', 'anime')], 1, False))
    results = browse(BrowseQuery(genre='mecha'), 'en', tmdb=None, anilist=anilist)
    assert ids(results) == ['a1'] and results.failed == []
