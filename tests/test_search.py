from providers.http import ProviderError
from providers.search import search
from providers.types import SearchHit


def hit(provider, external_id, kind='tv', anime_like=False):
    return SearchHit(
        provider, external_id, kind, f'T{external_id}', '', None, '', '', anime_like
    )


class FakeTmdbSearch:
    def __init__(self, hits=(), error=None):
        self.hits, self.error, self.languages = list(hits), error, []

    def search(self, query, language='en'):
        self.languages.append(language)
        if self.error:
            raise self.error
        return self.hits


class FakeAniListSearch:
    def __init__(self, hits=(), error=None):
        self.hits, self.error = list(hits), error

    def search(self, query):
        if self.error:
            raise self.error
        return self.hits


def ids(results):
    return [h.external_id for h in results.hits]


def test_results_of_both_providers_are_interleaved():
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch(
            [hit('tmdb_tv', '1'), hit('tmdb_movie', '2', 'movie'), hit('tmdb_tv', '3')]
        ),
        anilist=FakeAniListSearch([hit('anilist', '10', 'anime')]),
    )
    assert ids(results) == ['1', '10', '2', '3']
    assert results.failed == []


def test_japanese_animation_from_tmdb_is_dropped_in_favour_of_anilist():
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch(
            [hit('tmdb_tv', '1', anime_like=True), hit('tmdb_tv', '2')]
        ),
        anilist=FakeAniListSearch([hit('anilist', '10', 'anime')]),
    )
    assert ids(results) == ['2', '10']


def test_the_site_language_is_passed_to_tmdb():
    tmdb = FakeTmdbSearch()
    search('x', 'ru', tmdb=tmdb, anilist=FakeAniListSearch())
    assert tmdb.languages == ['ru']


def test_a_failing_provider_is_reported_and_the_other_still_answers():
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch(error=ProviderError('down')),
        anilist=FakeAniListSearch([hit('anilist', '10', 'anime')]),
    )
    assert ids(results) == ['10']
    assert results.failed == ['tmdb']
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch([hit('tmdb_tv', '1')]),
        anilist=FakeAniListSearch(error=ProviderError('down')),
    )
    assert ids(results) == ['1'] and results.failed == ['anilist']


def test_a_missing_tmdb_token_is_reported_as_a_failure_of_tmdb():
    results = search(
        'x', 'en', tmdb=None, anilist=FakeAniListSearch([hit('anilist', '10', 'anime')])
    )
    assert ids(results) == ['10'] and results.failed == ['tmdb']
