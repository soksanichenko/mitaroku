from fribb import index_by_mal, tmdb_id


def test_index_by_mal_keeps_first_entry_and_skips_missing_ids():
    entries = [
        {'mal_id': 1, 'simkl_id': 10},
        {'mal_id': 1, 'simkl_id': 11},
        {'simkl_id': 12},
    ]
    assert index_by_mal(entries) == {1: {'mal_id': 1, 'simkl_id': 10}}


def test_tmdb_id_prefers_tv_and_handles_lists_and_absence():
    assert tmdb_id({'themoviedb_id': {'tv': 5}}) == ('tv', 5)
    assert tmdb_id({'themoviedb_id': {'movie': [7, 8]}}) == ('movie', 7)
    assert tmdb_id({'themoviedb_id': None}) is None
    assert tmdb_id({}) is None
