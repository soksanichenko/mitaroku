"""In-memory stand-ins for the provider clients, and data builders."""

from datetime import date, timedelta

from providers.anilist import AniListEdge, AniListMedia, AniListSchedule
from providers.http import ProviderError
from providers.tmdb import (
    TmdbEpisode,
    TmdbMovie,
    TmdbSeason,
    TmdbSeasonSummary,
    TmdbShow,
)


def edge(relation, anilist_id, fmt='TV', media_type='ANIME'):
    return AniListEdge(relation, anilist_id, None, fmt, media_type)


def media(
    anilist_id,
    edges=(),
    fmt='TV',
    year=2020,
    month=1,
    day=1,
    episodes=12,
    status='FINISHED',
    score=80,
    mal_id=None,
    english='',
):
    return AniListMedia(
        anilist_id=anilist_id,
        mal_id=mal_id,
        format=fmt,
        status=status,
        episodes=episodes,
        average_score=score,
        romaji=f'n{anilist_id}',
        english=english,
        native='',
        cover_url=f'https://img.example/{anilist_id}.jpg',
        banner_url='',
        start=(year, month, day),
        edges=list(edges),
    )


def tmdb_episodes(count, first, season=1, first_number=1, weekly=7):
    return [
        TmdbEpisode(
            season,
            first_number + i,
            f'Episode {first_number + i}',
            f'About {first_number + i}',
            first + timedelta(days=weekly * i),
        )
        for i in range(count)
    ]


def tmdb_season(number, count, first, name=''):
    return TmdbSeason(
        number, name or f'Season {number}', tmdb_episodes(count, first, number)
    )


def tmdb_show(tmdb_id, seasons, status='Ended', name='Show', imdb_id=''):
    return TmdbShow(
        tmdb_id=tmdb_id,
        name=name,
        original_name=f'{name} (original)',
        original_language='ja',
        overview='About the show',
        first_air_date=date(2020, 1, 1),
        status=status,
        poster_url='https://image.tmdb.org/t/p/w342/p.jpg',
        backdrop_url='',
        rating=7.84,
        votes=120,
        imdb_id=imdb_id,
        seasons=[TmdbSeasonSummary(s.number, s.name, len(s.episodes)) for s in seasons],
    )


def tmdb_movie(tmdb_id, release=date(1999, 3, 31), status='Released', rating=8.2):
    return TmdbMovie(
        tmdb_id=tmdb_id,
        title='The Matrix',
        original_title='The Matrix (original)',
        original_language='en',
        overview='A hacker learns the truth.',
        release_date=release,
        runtime=136,
        status=status,
        poster_url='https://image.tmdb.org/t/p/w342/m.jpg',
        backdrop_url='https://image.tmdb.org/t/p/w780/b.jpg',
        rating=rating,
        votes=25000 if rating is not None else 0,
        imdb_id='tt0133093',
    )


class FakeTmdb:
    """``TmdbClient`` over dictionaries. ``shows`` maps id to ``(TmdbShow, seasons)``."""

    def __init__(self, movies=None, shows=None):
        self.movies = movies or {}
        self.shows = shows or {}
        self.calls = []

    def movie(self, tmdb_id, language='en'):
        self.calls.append(('movie', tmdb_id, language))
        return self.movies.get(tmdb_id)

    def tv(self, tmdb_id, language='en'):
        self.calls.append(('tv', tmdb_id, language))
        entry = self.shows.get(tmdb_id)
        return entry[0] if entry else None

    def season(self, tmdb_id, number, language='en'):
        self.calls.append(('season', tmdb_id, number, language))
        entry = self.shows.get(tmdb_id)
        return entry[1].get(number) if entry else None

    def seasons(self, show, language='en'):
        self.calls.append(('seasons', show.tmdb_id, language))
        return dict(self.shows[show.tmdb_id][1])


class FakeAniList:
    """``AniListClient`` over dictionaries; ``schedules`` maps id to ``AniListSchedule``."""

    def __init__(self, *entries, schedules=None):
        self.by_id = {entry.anilist_id: entry for entry in entries}
        self.schedules = schedules or {}
        self.schedule_calls = []

    def media_many(self, ids):
        return [self.by_id[i] for i in ids if i in self.by_id]

    def schedule(self, anilist_id):
        self.schedule_calls.append(anilist_id)
        return self.schedules.get(anilist_id, AniListSchedule(upcoming={}, all={}))


class FailingProvider:
    """Stand-in for a provider client whose every method raises ``ProviderError``."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ProviderError(f'{name}: the provider is down')

        return fail
