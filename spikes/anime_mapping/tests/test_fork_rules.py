from fork_rules import fork_group
from franchise import Node


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
    return {node.anilist_id: node for node in nodes}.get


def test_sequel_is_main_and_side_story_is_extra():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME'), ('SIDE_STORY', 3, 1003, 'OVA', 'ANIME')]),
        make(2, [('PREQUEL', 1, 1001, 'TV', 'ANIME')]),
        make(3, [('PARENT', 1, 1001, 'TV', 'ANIME')], fmt='OVA'),
    ]
    group = fork_group(fetcher(nodes), 1)
    assert group.main == {1, 2}
    assert group.extras == {3}


def test_alternative_and_spin_off_are_skipped_with_a_warning():
    nodes = [
        make(1, [('ALTERNATIVE', 2, 1002, 'TV', 'ANIME'), ('SPIN_OFF', 3, 1003, 'TV', 'ANIME')]),
        make(2, []),
        make(3, []),
    ]
    group = fork_group(fetcher(nodes), 1)
    assert group.main == {1}
    assert group.extras == set()
    assert len(group.warnings) == 2


def test_parent_edge_marks_the_current_entry_as_extra():
    nodes = [
        make(1, [('PARENT', 2, 1002, 'TV', 'ANIME')], fmt='OVA'),
        make(2, []),
    ]
    group = fork_group(fetcher(nodes), 1)
    assert group.extras == {1}
    assert group.main == {2}
