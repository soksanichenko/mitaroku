from report import render


def test_render_counts_coverage_and_marks_missing_probes():
    results = [
        {
            'label': 'Demo',
            'main': [
                {'fribb': True, 'simkl_id': 1, 'tmdb': ['tv', 5], 'imdb': True},
                {'fribb': False, 'simkl_id': None, 'tmdb': None, 'imdb': False},
            ],
            'extras': 3,
            'airing_schedule': 0,
            'simkl': None,
            'tmdb_seasons': None,
        },
        {'label': 'Ghost', 'main': None, 'extras': 0},
    ]
    table = render(results)
    assert '| Demo | 2 | 3 | 1/2 | 1/2 | 1/2 | 1/2 | 0 | n/a | n/a | n/a |' in table
    assert 'Ghost | not found on AniList' in table
