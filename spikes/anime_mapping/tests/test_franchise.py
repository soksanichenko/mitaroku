from franchise import Node, build_franchise


def make(anilist_id, edges, fmt='TV', year=2020):
    return Node(
        anilist_id=anilist_id,
        mal_id=anilist_id + 1000,
        format=fmt,
        title=f'n{anilist_id}',
        episodes=12,
        start=(year, 1, 1),
        edges=edges,
    )


def fetcher(nodes):
    by_id = {node.anilist_id: node for node in nodes}
    return by_id.get


def test_follows_sequels_and_prequels_in_both_directions():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')], year=2018),
        make(2, [('PREQUEL', 1, 1001, 'TV', 'ANIME'), ('SEQUEL', 3, 1003, 'TV', 'ANIME')], year=2019),
        make(3, [('PREQUEL', 2, 1002, 'TV', 'ANIME')], year=2020),
    ]
    franchise = build_franchise(fetcher(nodes), 2)
    assert [n.anilist_id for n in franchise.main] == [1, 2, 3]


def test_cycle_does_not_loop_forever():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')], year=2018),
        make(2, [('SEQUEL', 1, 1001, 'TV', 'ANIME')], year=2019),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert sorted(n.anilist_id for n in franchise.main) == [1, 2]


def test_manga_and_music_nodes_are_ignored():
    nodes = [
        make(1, [('SEQUEL', 9, None, 'MANGA', 'MANGA'), ('SEQUEL', 8, None, 'MUSIC', 'ANIME')]),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1]
    assert franchise.extras == []


def test_side_entries_are_extras_and_not_expanded():
    nodes = [
        make(1, [('SIDE_STORY', 5, 1005, 'OVA', 'ANIME')]),
        make(5, [('SEQUEL', 6, 1006, 'TV', 'ANIME')], fmt='OVA'),
        make(6, []),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1]
    assert franchise.extras == [(5, 1005, 'OVA', 'SIDE_STORY')]


def test_unavailable_node_is_skipped():
    nodes = [make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')])]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1]


def test_unknown_start_dates_sort_last():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')], year=2020),
        make(2, [('PREQUEL', 1, 1001, 'TV', 'ANIME')], year=None),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1, 2]
