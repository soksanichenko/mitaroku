"""TMDB client: search, movies, TV shows and seasons."""

from dataclasses import dataclass, field
from datetime import date

from providers.http import ProviderHttp
from providers.types import SearchHit

BASE = 'https://api.themoviedb.org/3'
IMAGE_BASE = 'https://image.tmdb.org/t/p/'
LANGUAGE_TAGS = {'en': 'en-US', 'ru': 'ru-RU'}
ANIMATION_GENRE = 16


@dataclass(frozen=True)
class TmdbMovie:
    """A TMDB movie in one language."""

    tmdb_id: int
    title: str
    original_title: str
    original_language: str
    overview: str
    release_date: date | None
    runtime: int | None
    status: str
    poster_url: str
    backdrop_url: str
    rating: float | None
    votes: int
    imdb_id: str


@dataclass(frozen=True)
class TmdbSeasonSummary:
    """A season as listed on the show page (specials are season 0)."""

    number: int
    name: str
    episode_count: int


@dataclass(frozen=True)
class TmdbShow:
    """A TMDB TV show in one language."""

    tmdb_id: int
    name: str
    original_name: str
    original_language: str
    overview: str
    first_air_date: date | None
    status: str
    poster_url: str
    backdrop_url: str
    rating: float | None
    votes: int
    imdb_id: str
    seasons: list[TmdbSeasonSummary] = field(default_factory=list)


@dataclass(frozen=True)
class TmdbEpisode:
    """An episode of a TMDB season."""

    season_number: int
    number: int
    name: str
    overview: str
    air_date: date | None


@dataclass(frozen=True)
class TmdbSeason:
    """A TMDB season with its episodes."""

    number: int
    name: str
    episodes: list[TmdbEpisode]


def language_tag(language: str) -> str:
    """Map a site language (``en``, ``ru``) to the TMDB language tag."""
    return LANGUAGE_TAGS.get(language, language)


def _date(value: str | None) -> date | None:
    """Parse TMDB's ISO date; TMDB sends an empty string when unknown."""
    return date.fromisoformat(value) if value else None


def _image(path: str | None, size: str) -> str:
    """Build an image URL, or an empty string when there is no image."""
    return f'{IMAGE_BASE}{size}{path}' if path else ''


def _rating(data: dict) -> float | None:
    """Return the average vote, or None when nobody has voted."""
    return data['vote_average'] if data.get('vote_count') else None


class TmdbClient:
    """Typed access to the parts of the TMDB v3 API the catalog needs."""

    def __init__(self, http: ProviderHttp) -> None:
        """Create the client.

        Args:
            http: Wrapper whose client already carries the Bearer token.
        """
        self._http = http

    def search(self, query: str, language: str = 'en') -> list[SearchHit]:
        """Search movies and TV shows.

        Args:
            query: Text typed by the user.
            language: Site language of the titles in the result.

        Returns:
            Movie and TV hits in TMDB's relevance order; people are dropped.
        """
        data = self._http.get_json(
            f'{BASE}/search/multi',
            params={
                'query': query,
                'language': language_tag(language),
                'include_adult': 'false',
                'page': 1,
            },
            ttl=3600,
        )
        hits = []
        for item in (data or {}).get('results', []):
            media_type = item.get('media_type')
            if media_type not in ('movie', 'tv'):
                continue
            is_movie = media_type == 'movie'
            released = _date(item.get('release_date' if is_movie else 'first_air_date'))
            hits.append(
                SearchHit(
                    provider='tmdb_movie' if is_movie else 'tmdb_tv',
                    external_id=str(item['id']),
                    kind='movie' if is_movie else 'tv',
                    title=item.get('title' if is_movie else 'name') or '',
                    original_title=item.get(
                        'original_title' if is_movie else 'original_name'
                    )
                    or '',
                    year=released.year if released else None,
                    poster_url=_image(item.get('poster_path'), 'w185'),
                    overview=item.get('overview') or '',
                    anime_like=ANIMATION_GENRE in item.get('genre_ids', [])
                    and item.get('original_language') == 'ja',
                )
            )
        return hits

    def movie(self, tmdb_id: int, language: str = 'en') -> TmdbMovie | None:
        """Return a movie, or None when TMDB does not know the id."""
        data = self._http.get_json(
            f'{BASE}/movie/{tmdb_id}',
            params={
                'language': language_tag(language),
                'append_to_response': 'external_ids',
            },
        )
        if data is None:
            return None
        return TmdbMovie(
            tmdb_id=data['id'],
            title=data.get('title') or '',
            original_title=data.get('original_title') or '',
            original_language=data.get('original_language') or '',
            overview=data.get('overview') or '',
            release_date=_date(data.get('release_date')),
            runtime=data.get('runtime') or None,
            status=data.get('status') or '',
            poster_url=_image(data.get('poster_path'), 'w342'),
            backdrop_url=_image(data.get('backdrop_path'), 'w780'),
            rating=_rating(data),
            votes=data.get('vote_count') or 0,
            imdb_id=(data.get('external_ids') or {}).get('imdb_id') or '',
        )

    def tv(self, tmdb_id: int, language: str = 'en') -> TmdbShow | None:
        """Return a TV show with its season list, or None when the id is unknown."""
        data = self._http.get_json(
            f'{BASE}/tv/{tmdb_id}',
            params={
                'language': language_tag(language),
                'append_to_response': 'external_ids',
            },
        )
        if data is None:
            return None
        return TmdbShow(
            tmdb_id=data['id'],
            name=data.get('name') or '',
            original_name=data.get('original_name') or '',
            original_language=data.get('original_language') or '',
            overview=data.get('overview') or '',
            first_air_date=_date(data.get('first_air_date')),
            status=data.get('status') or '',
            poster_url=_image(data.get('poster_path'), 'w342'),
            backdrop_url=_image(data.get('backdrop_path'), 'w780'),
            rating=_rating(data),
            votes=data.get('vote_count') or 0,
            imdb_id=(data.get('external_ids') or {}).get('imdb_id') or '',
            seasons=[
                TmdbSeasonSummary(
                    number=item['season_number'],
                    name=item.get('name') or '',
                    episode_count=item.get('episode_count') or 0,
                )
                for item in data.get('seasons', [])
            ],
        )

    def season(
        self, tmdb_id: int, number: int, language: str = 'en'
    ) -> TmdbSeason | None:
        """Return one season with its episodes, or None when it does not exist."""
        data = self._http.get_json(
            f'{BASE}/tv/{tmdb_id}/season/{number}',
            params={'language': language_tag(language)},
        )
        if data is None:
            return None
        return TmdbSeason(
            number=data['season_number'],
            name=data.get('name') or '',
            episodes=[
                TmdbEpisode(
                    season_number=data['season_number'],
                    number=item['episode_number'],
                    name=item.get('name') or '',
                    overview=item.get('overview') or '',
                    air_date=_date(item.get('air_date')),
                )
                for item in data.get('episodes', [])
            ],
        )

    def seasons(self, show: TmdbShow, language: str = 'en') -> dict[int, TmdbSeason]:
        """Fetch every season of a show, specials (season 0) included.

        Args:
            show: The show, as returned by ``tv``.
            language: Site language of episode names and overviews.

        Returns:
            ``{season_number: season}``; seasons TMDB cannot return are omitted.
        """
        result = {}
        for summary in show.seasons:
            season = self.season(show.tmdb_id, summary.number, language)
            if season is not None:
                result[summary.number] = season
        return result
