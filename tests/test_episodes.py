from datetime import date, timedelta

from catalog.episodes import EntrySpec, allocate_show, passes_guard
from providers.tmdb import TmdbEpisode, TmdbSeason

START = date(2020, 1, 1)


def episodes(count, first=START, first_number=1):
    return [
        TmdbEpisode(1, first_number + i, f'ep{i}', '', first + timedelta(days=7 * i))
        for i in range(count)
    ]


def season(number, count, first=START):
    return TmdbSeason(number, f'S{number}', episodes(count, first))


def spec(anilist_id, start, expected, fmt='TV', tmdb_season=None, offset=0):
    return EntrySpec(
        anilist_id=anilist_id,
        format=fmt,
        start=start,
        expected=expected,
        tmdb_season=tmdb_season,
        offset=offset,
        sort_key=(start.year, start.month, start.day) if start else (9999, 12, 31),
    )


def test_guard_needs_the_first_episode_close_to_the_start_date():
    part = episodes(12)
    assert passes_guard(part, 12, START)
    assert passes_guard(part, 12, START + timedelta(days=2))
    assert passes_guard(part, 12, START - timedelta(days=2))
    assert not passes_guard(part, 12, START + timedelta(days=3))


def test_guard_rejects_an_off_by_one_length():
    assert not passes_guard(episodes(12), 13, START)
    assert not passes_guard(episodes(13), 12, START)


def test_guard_ignores_the_length_while_the_count_is_unknown():
    assert passes_guard(episodes(5), None, START)


def test_guard_rejects_missing_dates_and_empty_parts():
    assert not passes_guard([], 12, START)
    assert not passes_guard(episodes(12), 12, None)
    undated = [TmdbEpisode(1, 1, 'x', '', None)]
    assert not passes_guard(undated, 1, START)


def test_fribb_season_and_offset_pick_the_right_slice():
    # One TMDB season of 24 that AniList splits into two cours of 12.
    seasons = {1: season(1, 24)}
    second_start = START + timedelta(days=7 * 12)
    result = allocate_show(
        seasons,
        [
            spec(1, START, 12, tmdb_season=1, offset=0),
            spec(2, second_start, 12, tmdb_season=1, offset=12),
        ],
    )
    assert [e.number for e in result[2]] == list(range(13, 25))
    assert len(result[1]) == 12


def test_concatenation_rescues_an_entry_fribb_maps_wrongly():
    seasons = {1: season(1, 12), 2: season(2, 12, START + timedelta(days=7 * 12))}
    result = allocate_show(
        seasons,
        [
            spec(1, START, 12, tmdb_season=1),
            # Fribb says season 5, which TMDB does not have.
            spec(2, START + timedelta(days=7 * 12), 12, tmdb_season=5),
        ],
    )
    assert result[2] is not None
    assert result[2][0].air_date == START + timedelta(days=7 * 12)


def test_an_entry_that_fails_both_candidates_is_none():
    seasons = {1: season(1, 12, START + timedelta(days=100))}
    result = allocate_show(seasons, [spec(1, START, 12, tmdb_season=1)])
    assert result == {1: None}


def test_season_zero_serves_specials_and_ovas_through_fribb_only():
    seasons = {0: season(0, 3), 1: season(1, 12)}
    ova = spec(5, START, 3, fmt='OVA', tmdb_season=0)
    result = allocate_show(seasons, [ova, spec(1, START, 12, tmdb_season=1)])
    assert len(result[5]) == 3
    # An OVA never takes episodes from the regular stream.
    no_fribb = spec(6, START, 12, fmt='OVA')
    assert allocate_show(seasons, [no_fribb]) == {6: None}


def test_ova_entries_do_not_consume_the_stream():
    seasons = {1: season(1, 12), 2: season(2, 12, START + timedelta(days=100))}
    result = allocate_show(
        seasons,
        [
            spec(1, START, 12),
            spec(9, START + timedelta(days=50), 1, fmt='OVA'),
            spec(2, START + timedelta(days=100), 12),
        ],
    )
    assert result[2] is not None and result[2][0].number == 1


def test_a_running_entry_with_an_unknown_count_takes_the_rest():
    seasons = {1: season(1, 12), 2: season(2, 5, START + timedelta(days=100))}
    result = allocate_show(
        seasons, [spec(1, START, 12), spec(2, START + timedelta(days=100), None)]
    )
    assert len(result[2]) == 5


def test_unknown_start_date_means_no_allocation():
    seasons = {1: season(1, 12)}
    assert allocate_show(seasons, [spec(1, None, 12, tmdb_season=1)]) == {1: None}


def test_tmdb_having_fewer_episodes_than_anilist_is_rejected():
    # Bakemonogatari: AniList counts 15, TMDB season 1 has 12.
    seasons = {1: season(1, 12)}
    assert allocate_show(seasons, [spec(1, START, 15, tmdb_season=1)]) == {1: None}
