import httpx
import pytest
from django.core.management import call_command
from providers import mapping, tasks
from providers.http import ProviderError
from providers.models import AnimeMapping

pytestmark = pytest.mark.django_db

FRIEREN = {
    'type': 'TV',
    'anilist_id': 154587,
    'mal_id': 52991,
    'imdb_id': ['tt22248376'],
    'themoviedb_id': {'tv': 209867},
    'season': {'tvdb': 1, 'tmdb': 1},
}
STEINS_MOVIE = {
    'type': 'MOVIE',
    'anilist_id': 11577,
    'mal_id': 11577,
    'imdb_id': ['tt2967286'],
    'themoviedb_id': {'movie': [225745]},
    'season': {'tvdb': 0},
}
INITIAL_D = {
    'type': 'OVA',
    'anilist_id': 821,
    'mal_id': 821,
    'themoviedb_id': {'movie': [1390599]},
    'season': {'tvdb': 0},
    'episode_offset': {'tvdb': 2},
}
LOVE_HINA = {
    'type': 'OVA',
    'anilist_id': 190,
    'themoviedb_id': {'tv': 30992},
    'season': {'tvdb': 0, 'tmdb': 0},
    'episode_offset': {'tvdb': 2, 'tmdb': 2},
}
NO_ANILIST = {'type': 'TV', 'mal_id': 5, 'themoviedb_id': {'tv': 1}}
NO_MAL = {'type': 'TV', 'anilist_id': 162314, 'themoviedb_id': {'tv': 77}}


def test_tv_entry_is_parsed():
    mapping.load_entries([FRIEREN])
    row = AnimeMapping.objects.get(pk=154587)
    assert (row.mal_id, row.tmdb_tv_id, row.tmdb_movie_id) == (52991, 209867, None)
    assert (row.imdb_id, row.tmdb_season, row.tmdb_episode_offset) == (
        'tt22248376',
        1,
        0,
    )
    assert row.entry_type == 'TV'


def test_movie_entry_takes_the_first_tmdb_movie_id():
    mapping.load_entries([STEINS_MOVIE])
    row = AnimeMapping.objects.get(pk=11577)
    assert (row.tmdb_tv_id, row.tmdb_movie_id) == (None, 225745)
    assert row.tmdb_season is None  # only the tvdb season is given


def test_missing_tmdb_season_and_offset_default_sensibly():
    mapping.load_entries([INITIAL_D, LOVE_HINA])
    initial_d = AnimeMapping.objects.get(pk=821)
    assert (initial_d.tmdb_season, initial_d.tmdb_episode_offset) == (None, 0)
    love_hina = AnimeMapping.objects.get(pk=190)
    assert (love_hina.tmdb_season, love_hina.tmdb_episode_offset) == (0, 2)
    assert love_hina.imdb_id == ''


def test_entries_without_an_anilist_id_are_skipped_and_a_missing_mal_id_is_fine():
    written = mapping.load_entries([NO_ANILIST, NO_MAL])
    assert written == 1
    assert AnimeMapping.objects.get().mal_id is None


def test_loading_twice_updates_rows_in_place():
    mapping.load_entries([FRIEREN])
    changed = {**FRIEREN, 'season': {'tmdb': 3}}
    mapping.load_entries([changed])
    assert AnimeMapping.objects.count() == 1
    assert AnimeMapping.objects.get().tmdb_season == 3


def _client(body, status=200):
    return httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, **body))
    )


def test_download_returns_the_entries():
    entries = [{'anilist_id': i} for i in range(mapping.MIN_ENTRIES)]
    assert len(
        mapping.download_dataset('https://x/y.json', _client({'json': entries}))
    ) == (mapping.MIN_ENTRIES)


def test_a_truncated_dataset_is_refused():
    with pytest.raises(ProviderError, match='truncated'):
        mapping.download_dataset('https://x/y.json', _client({'json': [FRIEREN]}))


def test_a_failed_download_is_a_provider_error():
    with pytest.raises(ProviderError, match='download failed'):
        mapping.download_dataset('https://x/y.json', _client({}, status=503))
    with pytest.raises(ProviderError, match='download failed'):
        mapping.download_dataset('https://x/y.json', _client({'content': b'<html>'}))


def test_command_loads_the_dataset(monkeypatch, capsys):
    monkeypatch.setattr(
        mapping,
        'download_dataset',
        lambda url, client: [FRIEREN, STEINS_MOVIE],
    )
    call_command('refresh_mapping')
    assert 'Loaded 2 anime mappings' in capsys.readouterr().out
    assert AnimeMapping.objects.count() == 2


def test_weekly_task_uses_the_same_loader(monkeypatch):
    monkeypatch.setattr(mapping, 'download_dataset', lambda url, client: [FRIEREN])
    assert tasks.refresh_mapping_weekly.call_local() == 1
