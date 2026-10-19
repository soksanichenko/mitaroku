import pytest
from catalog.models import (
    Episode,
    EpisodeTranslation,
    ExternalId,
    Season,
    Title,
    TitleTranslation,
)
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def title():
    return Title.objects.create(kind=Title.Kind.TV, original_title='Sousou no Frieren')


def test_title_defaults_to_unknown_status(title):
    assert title.status == Title.Status.UNKNOWN
    assert str(title) == 'Sousou no Frieren'


def test_external_id_is_unique_per_provider(title):
    ExternalId.objects.create(title=title, provider='tmdb_tv', external_id='209867')
    other = Title.objects.create(kind=Title.Kind.TV, original_title='Other')
    with pytest.raises(IntegrityError), transaction.atomic():
        ExternalId.objects.create(title=other, provider='tmdb_tv', external_id='209867')


def test_tmdb_movie_and_tv_ids_do_not_collide(title):
    movie = Title.objects.create(kind=Title.Kind.MOVIE, original_title='A movie')
    ExternalId.objects.create(title=title, provider='tmdb_tv', external_id='42')
    ExternalId.objects.create(title=movie, provider='tmdb_movie', external_id='42')
    assert ExternalId.objects.count() == 2


def test_season_numbers_are_unique_inside_a_title(title):
    Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
    with pytest.raises(IntegrityError), transaction.atomic():
        Season.objects.create(title=title, number=1, kind=Season.Kind.TV)
    other = Title.objects.create(kind=Title.Kind.TV, original_title='Other')
    Season.objects.create(title=other, number=1, kind=Season.Kind.TV)


def test_anilist_id_is_unique_inside_a_title_but_may_be_missing(title):
    Season.objects.create(title=title, number=1, kind='tv', anilist_id=154587)
    with pytest.raises(IntegrityError), transaction.atomic():
        Season.objects.create(title=title, number=2, kind='tv', anilist_id=154587)
    Season.objects.create(title=title, number=3, kind='tv')
    Season.objects.create(title=title, number=4, kind='tv')


def test_episode_numbers_are_unique_inside_a_season(title):
    season = Season.objects.create(title=title, number=1, kind='tv')
    Episode.objects.create(season=season, number=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Episode.objects.create(season=season, number=1)


def test_episodes_are_listed_in_order(title):
    season = Season.objects.create(title=title, number=1, kind='tv')
    for number in (3, 1, 2):
        Episode.objects.create(season=season, number=number)
    assert [e.number for e in season.episodes.all()] == [1, 2, 3]


def test_translation_needs_a_name_or_an_overview(title):
    TitleTranslation.objects.create(title=title, language='en', name='Frieren')
    TitleTranslation.objects.create(title=title, language='ru', overview='Описание')
    with pytest.raises(IntegrityError), transaction.atomic():
        TitleTranslation.objects.create(title=title, language='de')


def test_translation_is_unique_per_language(title):
    TitleTranslation.objects.create(title=title, language='en', name='Frieren')
    with pytest.raises(IntegrityError), transaction.atomic():
        TitleTranslation.objects.create(title=title, language='en', name='Again')


def test_episode_translation_needs_a_name_or_an_overview(title):
    season = Season.objects.create(title=title, number=1, kind='tv')
    episode = Episode.objects.create(season=season, number=1)
    EpisodeTranslation.objects.create(episode=episode, language='en', name='The End')
    with pytest.raises(IntegrityError), transaction.atomic():
        EpisodeTranslation.objects.create(episode=episode, language='ru')


def test_deleting_a_title_removes_everything_under_it(title):
    season = Season.objects.create(title=title, number=1, kind='tv')
    episode = Episode.objects.create(season=season, number=1)
    EpisodeTranslation.objects.create(episode=episode, language='en', name='x')
    TitleTranslation.objects.create(title=title, language='en', name='x')
    ExternalId.objects.create(title=title, provider='anilist', external_id='1')
    title.delete()
    for model in (Season, Episode, EpisodeTranslation, TitleTranslation, ExternalId):
        assert model.objects.count() == 0
