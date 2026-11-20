from datetime import date

from catalog import vocab
from catalog.vocab import (
    GENRES,
    anilist_genre_name,
    browse_years,
    country_name,
    genre_label,
    genres_from_anilist,
    genres_from_tmdb,
    tmdb_genre_filter,
)
from django.utils import translation


def test_tmdb_genres_map_to_slugs():
    assert genres_from_tmdb([10759, 18, 10770, 999]) == ['action', 'adventure', 'drama']
    assert genres_from_tmdb([28, 10759]) == ['action', 'adventure']


def test_anilist_genres_map_to_slugs():
    names = ['Slice of Life', 'Ecchi', 'Sci-Fi']
    assert genres_from_anilist(names) == ['slice-of-life', 'sci-fi']


def test_genre_filters():
    assert tmdb_genre_filter('sci-fi') == '878|10765'
    assert tmdb_genre_filter('war') == '10752|10768'
    assert tmdb_genre_filter('nope') == ''
    assert anilist_genre_name('mecha') == 'Mecha'
    assert anilist_genre_name('western') is None


def test_every_slug_is_reachable_and_labelled():
    reachable = set(genres_from_tmdb(vocab.TMDB_GENRES))
    reachable |= set(genres_from_anilist(vocab.ANILIST_GENRES))
    assert set(GENRES) == reachable
    assert all(genre_label(slug) for slug in GENRES)
    assert genre_label('nope') == 'nope'


def test_country_names_follow_the_language():
    assert country_name('JP', 'en') == 'Japan'
    assert country_name('JP', 'ru') == 'Япония'
    assert country_name('ZZ', 'en') == 'ZZ'


def test_browse_years():
    years = browse_years(date(2026, 10, 20))
    assert years[0] == 2026
    assert years[-1] == 1950


def test_russian_labels_exist():
    with translation.override('ru'):
        assert genre_label('drama') == 'Драма'
