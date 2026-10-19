from catalog.franchise import load_franchise
from fakes import edge, media


class Fake:
    """Stands in for ``AniListClient.media_many`` and counts the calls."""

    def __init__(self, *entries):
        self.by_id = {entry.anilist_id: entry for entry in entries}
        self.calls = []

    def __call__(self, ids):
        self.calls.append(list(ids))
        return [self.by_id[i] for i in ids if i in self.by_id]


def ids(entries):
    return [entry.anilist_id for entry in entries]


def test_follows_sequels_and_prequels_in_both_directions():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2018),
        media(2, [edge('PREQUEL', 1), edge('SEQUEL', 3)], year=2019),
        media(3, [edge('PREQUEL', 2)], year=2020),
    )
    assert ids(load_franchise(fetch, 2).main) == [1, 2, 3]


def test_a_cycle_does_not_loop_forever():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2018),
        media(2, [edge('SEQUEL', 1)], year=2019),
    )
    assert ids(load_franchise(fetch, 1).main) == [1, 2]


def test_each_level_is_one_request():
    fetch = Fake(media(1, [edge('SEQUEL', 2)]), media(2, [edge('SEQUEL', 3)]), media(3))
    load_franchise(fetch, 1)
    assert fetch.calls == [[1], [2], [3]]


def test_manga_and_music_edges_are_ignored():
    fetch = Fake(
        media(1, [edge('SEQUEL', 9, 'MANGA', 'MANGA'), edge('SEQUEL', 8, 'MUSIC')])
    )
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1]
    assert franchise.extras == []


def test_side_entries_are_extras_and_not_walked():
    fetch = Fake(
        media(1, [edge('SIDE_STORY', 5, 'OVA')]),
        media(5, [edge('SEQUEL', 6)], fmt='OVA'),
        media(6),
    )
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1]
    assert ids(franchise.extras) == [5]


def test_edge_with_a_null_format_is_kept_as_an_unresolved_extra():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2, None)]),
        media(2, fmt=None, year=None),
    )
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1]
    assert ids(franchise.extras) == [2]


def test_unavailable_entry_is_skipped():
    fetch = Fake(media(1, [edge('SEQUEL', 2)]))
    assert ids(load_franchise(fetch, 1).main) == [1]


def test_unknown_start_dates_sort_last():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2020),
        media(2, [edge('PREQUEL', 1)], year=None),
    )
    assert ids(load_franchise(fetch, 1).main) == [1, 2]
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=None),
        media(2, [edge('PREQUEL', 1)], year=2020),
    )
    assert ids(load_franchise(fetch, 1).main) == [2, 1]


def test_a_movie_with_a_parent_is_an_extra_but_the_chain_passes_through_it():
    # AniList 204066: a compilation film that links season 2 to season 3.
    fetch = Fake(
        media(145064, [edge('SEQUEL', 204066, 'MOVIE')], year=2023),
        media(
            204066,
            [edge('PREQUEL', 145064), edge('PARENT', 145064), edge('SEQUEL', 172463)],
            fmt='MOVIE',
            year=2025,
        ),
        media(172463, [edge('PREQUEL', 204066)], year=2026),
    )
    franchise = load_franchise(fetch, 145064)
    assert ids(franchise.main) == [145064, 172463]
    assert ids(franchise.extras) == [204066]


def test_a_canon_film_with_a_parent_is_listed_as_an_extra():
    # AniList 11577: demoted by the rule, but still part of the franchise.
    fetch = Fake(
        media(9253, [edge('SEQUEL', 10863, 'OVA')], year=2011),
        media(
            10863,
            [edge('PREQUEL', 9253), edge('SEQUEL', 11577, 'MOVIE')],
            fmt='OVA',
            year=2012,
        ),
        media(
            11577,
            [edge('PREQUEL', 10863, 'OVA'), edge('PARENT', 9253)],
            fmt='MOVIE',
            year=2013,
        ),
    )
    franchise = load_franchise(fetch, 9253)
    assert ids(franchise.main) == [9253, 10863]
    assert ids(franchise.extras) == [11577]


def test_a_special_with_a_parent_is_an_extra_too():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2, 'SPECIAL')]),
        media(2, [edge('PARENT', 1)], fmt='SPECIAL'),
    )
    assert ids(load_franchise(fetch, 1).extras) == [2]


def test_extras_are_chronological():
    fetch = Fake(
        media(1, [edge('SIDE_STORY', 5, 'OVA'), edge('SIDE_STORY', 6, 'OVA')]),
        media(5, fmt='OVA', year=2022),
        media(6, fmt='OVA', year=2019),
    )
    assert ids(load_franchise(fetch, 1).extras) == [6, 5]


def test_a_franchise_found_through_a_compilation_reaches_its_seasons():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2020),
        media(2, [edge('PREQUEL', 1)], year=2021),
        media(3, [edge('PARENT', 2, 'TV')], fmt='SPECIAL', year=2022),
    )
    franchise = load_franchise(fetch, 3)
    assert ids(franchise.main) == [1, 2]
    assert ids(franchise.extras) == [3]


def test_a_standalone_movie_is_its_own_main_entry():
    fetch = Fake(media(1, [edge('ADAPTATION', 9, 'NOVEL', 'MANGA')], fmt='MOVIE'))
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1] and franchise.extras == []


def test_alternatives_and_spin_offs_are_listed_but_not_the_franchises_own():
    fetch = Fake(
        media(
            1,
            [
                edge('ALTERNATIVE', 2),
                edge('SPIN_OFF', 3),
                edge('SIDE_STORY', 4, 'OVA'),
                edge('SPIN_OFF', 5),
                edge('SIDE_STORY', 5),
            ],
        ),
        media(2, [edge('ALTERNATIVE', 1)], year=2021),
        media(3, [edge('PARENT', 1)], year=2022),
        media(4, [edge('PARENT', 1)], fmt='OVA', year=2023),
        media(5, [edge('PARENT', 1)], year=2024),
    )
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.extras) == [2, 3, 4, 5]
    assert franchise.detached == {2, 3}
    assert ids(franchise.own) == [1, 4, 5]


def test_a_franchise_without_loose_relations_owns_everything():
    fetch = Fake(media(1, [edge('SIDE_STORY', 4, 'OVA')]), media(4, [], fmt='OVA'))
    franchise = load_franchise(fetch, 1)
    assert franchise.detached == set()
    assert ids(franchise.own) == [1, 4]


def test_equal_dates_sort_by_anilist_id():
    first, second = media(20, year=2020), media(10, year=2020)
    assert sorted([first, second], key=lambda m: m.sort_key)[0].anilist_id == 10
