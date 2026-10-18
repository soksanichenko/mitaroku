from datetime import date

from episodes_tmdb import allocate_concat, allocate_fribb, date_matches


def ep(n, day):
    return {'episode_number': n, 'air_date': f'2020-01-{day:02d}'}


def test_concat_slices_by_cumulative_counts_and_gives_the_rest_to_open_ended():
    flat = [ep(i, i) for i in range(1, 8)]
    parts = allocate_concat(flat, [3, 2, None])
    assert [len(p) for p in parts] == [3, 2, 2]
    assert parts[1][0]['episode_number'] == 4


def test_concat_runs_short_when_tmdb_has_fewer_episodes():
    flat = [ep(i, i) for i in range(1, 4)]
    parts = allocate_concat(flat, [2, 5])
    assert [len(p) for p in parts] == [2, 1]


def test_fribb_uses_season_and_offset():
    seasons = {1: [ep(i, i) for i in range(1, 13)], 2: [ep(i, i) for i in range(1, 5)]}
    assert [e['episode_number'] for e in allocate_fribb(seasons, 1, 8, 3)] == [9, 10, 11]
    assert allocate_fribb(seasons, 9, 0, 3) == []
    assert allocate_fribb(seasons, None, 0, 3) == []


def test_date_matches_within_tolerance():
    assert date_matches('2020-01-10', (2020, 1, 1), days=14)
    assert not date_matches('2020-03-10', (2020, 1, 1), days=14)
    assert not date_matches(None, (2020, 1, 1), days=14)
    assert not date_matches('2020-01-10', (2020, None, None), days=14)


def test_accepts_only_tight_date_and_matching_length():
    from episodes_tmdb import accepts

    part = [ep(i, i) for i in range(1, 13)]
    assert accepts(part, 12, (2020, 1, 1))
    assert accepts(part, None, (2020, 1, 1))
    assert accepts(part, 12, (2019, 12, 30))  # two days earlier
    assert not accepts(part, 12, (2019, 12, 20))  # eleven days earlier
    assert not accepts(part, 13, (2020, 1, 1))  # one episode short
    assert not accepts([], 12, (2020, 1, 1))


def test_first_accepted_picks_the_first_candidate_that_passes():
    from episodes_tmdb import first_accepted

    bad = [ep(i, 20 + i) for i in range(1, 4)]
    good = [ep(i, i) for i in range(1, 4)]
    assert first_accepted([bad, good], 3, (2020, 1, 1)) == 1
    assert first_accepted([bad, bad], 3, (2020, 1, 1)) is None
