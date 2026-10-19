from dataclasses import replace
from datetime import date

import pytest
from catalog.models import (
    Episode,
    EpisodeTranslation,
    ExternalId,
    Season,
    Title,
    TitleTranslation,
)
from catalog.translations import (
    ensure_episode_translations,
    ensure_title_translation,
    localized_episode,
    localized_title,
)
from fakes import FakeTmdb, tmdb_movie, tmdb_season, tmdb_show

pytestmark = pytest.mark.django_db


class RussianTmdb(FakeTmdb):
    """Answers in Russian when asked for ``ru``, in English otherwise."""

    def movie(self, tmdb_id, language='en'):
        super().movie(tmdb_id, language)
        movie = self.movies[tmdb_id]
        if language == 'ru':
            return replace(movie, title='Матрица', overview='Хакер узнаёт правду.')
        return movie

    def tv(self, tmdb_id, language='en'):
        super().tv(tmdb_id, language)
        show = self.shows[tmdb_id][0]
        return replace(show, name='Шоу', overview='') if language == 'ru' else show

    def season(self, tmdb_id, number, language='en'):
        super().season(tmdb_id, number, language)
        season = self.shows[tmdb_id][1][number]
        if language != 'ru':
            return season
        return replace(
            season,
            episodes=[
                replace(
                    e, name=f'Серия {e.number}' if e.number != 2 else '', overview=''
                )
                for e in season.episodes
            ],
        )


@pytest.fixture
def title():
    return Title.objects.create(kind='movie', original_title='The Matrix (original)')


def test_the_name_falls_back_from_the_user_language_to_the_original_title(title):
    TitleTranslation.objects.create(
        title=title, language='en', name='The Matrix', overview='Hacker.'
    )
    assert localized_title(title, 'en') == ('The Matrix', 'Hacker.')
    # No Russian text: the original name, the English overview.
    assert localized_title(title, 'ru') == ('The Matrix (original)', 'Hacker.')
    TitleTranslation.objects.create(title=title, language='ru', name='Матрица')
    assert localized_title(title, 'ru') == ('Матрица', 'Hacker.')


def test_a_title_without_any_translation_shows_the_original_name(title):
    assert localized_title(title, 'ru') == ('The Matrix (original)', '')


def test_episode_texts_fall_back_to_english(title):
    season = Season.objects.create(title=title, number=1, kind='tv')
    episode = Episode.objects.create(season=season, number=1)
    assert localized_episode(episode, 'ru') == ('', '')
    EpisodeTranslation.objects.create(episode=episode, language='en', name='Pilot')
    assert localized_episode(episode, 'ru') == ('Pilot', '')
    EpisodeTranslation.objects.create(episode=episode, language='ru', name='Пилот')
    assert localized_episode(episode, 'ru') == ('Пилот', '')


def test_a_missing_movie_translation_is_fetched_once_and_kept(title):
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    tmdb = RussianTmdb(movies={603: tmdb_movie(603)})
    assert ensure_title_translation(title, 'ru', tmdb) is True
    assert title.translations.get(language='ru').name == 'Матрица'
    calls = len(tmdb.calls)
    assert ensure_title_translation(title, 'ru', tmdb) is True
    assert len(tmdb.calls) == calls  # nothing fetched the second time


def test_a_tv_translation_with_only_a_name_is_stored():
    title = Title.objects.create(kind='tv', original_title='Show (original)')
    ExternalId.objects.create(title=title, provider='tmdb_tv', external_id='1000')
    season = tmdb_season(1, 2, date(2020, 1, 1))
    tmdb = RussianTmdb(shows={1000: (tmdb_show(1000, [season]), {1: season})})
    assert ensure_title_translation(title, 'ru', tmdb) is True
    stored = title.translations.get(language='ru')
    assert (stored.name, stored.overview) == ('Шоу', '')


def test_nothing_is_stored_when_tmdb_has_no_text_or_the_title_has_no_tmdb_id(title):
    assert ensure_title_translation(title, 'ru', RussianTmdb()) is False  # no TMDB id
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    empty = FakeTmdb(movies={603: replace(tmdb_movie(603), title='', overview='')})
    assert ensure_title_translation(title, 'ru', empty) is False
    assert not title.translations.exists()


def tmdb_season_with_episodes():
    title = Title.objects.create(kind='anime', original_title='x')
    season = Season.objects.create(
        title=title, number=1, kind='tv', episode_source='tmdb', tmdb_tv_id=1000
    )
    for number in (1, 2, 3):
        Episode.objects.create(
            season=season,
            number=number,
            tmdb_season_number=1,
            tmdb_episode_number=number,
        )
    tmdb_data = tmdb_season(1, 3, date(2020, 1, 1))
    tmdb = RussianTmdb(shows={1000: (tmdb_show(1000, [tmdb_data]), {1: tmdb_data})})
    return season, tmdb


def test_episode_translations_are_fetched_for_the_missing_ones_and_skip_empty_texts():
    season, tmdb = tmdb_season_with_episodes()
    assert ensure_episode_translations(season, 'ru', tmdb) == 2  # episode 2 has no text
    names = {
        t.episode.number: t.name
        for t in EpisodeTranslation.objects.filter(language='ru')
    }
    assert names == {1: 'Серия 1', 3: 'Серия 3'}


def test_episode_translations_are_not_fetched_again_when_present():
    season, tmdb = tmdb_season_with_episodes()
    ensure_episode_translations(season, 'ru', tmdb)
    season_calls = [c for c in tmdb.calls if c[0] == 'season']
    # Episode 2 still lacks text, so only that one is asked for again.
    ensure_episode_translations(season, 'ru', tmdb)
    assert len([c for c in tmdb.calls if c[0] == 'season']) == len(season_calls) + 1


def test_a_season_without_tmdb_episodes_has_no_translations_to_fetch():
    title = Title.objects.create(kind='anime', original_title='x')
    season = Season.objects.create(
        title=title, number=1, kind='tv', episode_source='anilist'
    )
    Episode.objects.create(season=season, number=1)
    assert ensure_episode_translations(season, 'ru', RussianTmdb()) == 0
