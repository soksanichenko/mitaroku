"""TMDB client: search, discover, movies, TV shows, seasons and collections."""

from dataclasses import dataclass, field
from datetime import date

from catalog.vocab import tmdb_genre_filter
from providers.http import ProviderHttp
from providers.types import HitPage, SearchHit

BASE = 'https://api.themoviedb.org/3'
IMAGE_BASE = 'https://image.tmdb.org/t/p/'
LANGUAGE_TAGS = {'en': 'en-US', 'ru': 'ru-RU'}
ANIMATION_GENRE = 16
CAST_LIMIT = 10
DISCOVER_TTL = 3 * 3600
COLLECTION_TTL = 7 * 24 * 3600
APPEND_TO_RESPONSE = 'external_ids,credits,videos,recommendations'
# TMDB sort_by value per site sort, for movies and for TV.
DISCOVER_SORTS = {
    'movie': {
        'popularity': 'popularity.desc',
        'date': 'primary_release_date.desc',
        'title': 'title.asc',
        'rating': 'vote_average.desc',
    },
    'tv': {
        'popularity': 'popularity.desc',
        'date': 'first_air_date.desc',
        'title': 'name.asc',
        'rating': 'vote_average.desc',
    },
}
# Keeps the rating sort from being led by titles with a handful of votes.
RATING_SORT_MIN_VOTES = 100


@dataclass(frozen=True)
class TmdbCredit:
    """A cast member with the role they play."""

    name: str
    character: str
    photo_url: str


@dataclass(frozen=True)
class TmdbVideo:
    """A video attached to a title (only YouTube ones are kept)."""

    site: str
    key: str
    name: str
    kind: str
    official: bool
    language: str


@dataclass(frozen=True)
class TmdbCollectionRef:
    """The collection a movie belongs to, as listed on the movie."""

    tmdb_id: int
    name: str
    poster_url: str


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
    genre_ids: tuple[int, ...] = ()
    countries: tuple[str, ...] = ()
    cast: tuple[TmdbCredit, ...] = ()
    videos: tuple[TmdbVideo, ...] = ()
    recommendations: tuple[SearchHit, ...] = ()
    collection: TmdbCollectionRef | None = None


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
    genre_ids: tuple[int, ...] = ()
    countries: tuple[str, ...] = ()
    cast: tuple[TmdbCredit, ...] = ()
    videos: tuple[TmdbVideo, ...] = ()
    recommendations: tuple[SearchHit, ...] = ()


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


@dataclass(frozen=True)
class TmdbCollection:
    """A TMDB movie collection (a franchise) with its movies."""

    tmdb_id: int
    name: str
    overview: str
    poster_url: str
    parts: list[SearchHit]


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


def _hit(item: dict, media_type: str) -> SearchHit:
    """Build a search hit from a TMDB movie or TV list item."""
    is_movie = media_type == 'movie'
    released = _date(item.get('release_date' if is_movie else 'first_air_date'))
    return SearchHit(
        provider='tmdb_movie' if is_movie else 'tmdb_tv',
        external_id=str(item['id']),
        kind='movie' if is_movie else 'tv',
        title=item.get('title' if is_movie else 'name') or '',
        original_title=item.get('original_title' if is_movie else 'original_name')
        or '',
        year=released.year if released else None,
        poster_url=_image(item.get('poster_path'), 'w185'),
        overview=item.get('overview') or '',
        anime_like=ANIMATION_GENRE in item.get('genre_ids', [])
        and item.get('original_language') == 'ja',
        rating=_rating(item),
    )


def _multi_hits(results: list[dict]) -> list[SearchHit]:
    """Build hits from a list mixing media types, dropping people."""
    return [
        _hit(item, item['media_type'])
        for item in results
        if item.get('media_type') in ('movie', 'tv')
    ]


def _enrichment(data: dict) -> dict:
    """Read the fields movies and TV shows share from an appended response."""
    return {
        'genre_ids': tuple(genre['id'] for genre in data.get('genres') or []),
        'cast': tuple(
            TmdbCredit(
                name=person.get('name') or '',
                character=person.get('character') or '',
                photo_url=_image(person.get('profile_path'), 'w185'),
            )
            for person in sorted(
                (data.get('credits') or {}).get('cast') or [],
                key=lambda person: person.get('order') or 0,
            )[:CAST_LIMIT]
        ),
        'videos': tuple(
            TmdbVideo(
                site=video['site'],
                key=video.get('key') or '',
                name=video.get('name') or '',
                kind=video.get('type') or '',
                official=bool(video.get('official')),
                language=video.get('iso_639_1') or '',
            )
            for video in (data.get('videos') or {}).get('results') or []
            if video.get('site') == 'YouTube'
        ),
        'recommendations': tuple(
            _multi_hits((data.get('recommendations') or {}).get('results') or [])
        ),
    }


def _production_countries(data: dict) -> tuple[str, ...]:
    """Read the ISO codes of the production countries."""
    return tuple(c['iso_3166_1'] for c in data.get('production_countries') or [])


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
        return _multi_hits((data or {}).get('results', []))

    def movie(self, tmdb_id: int, language: str = 'en') -> TmdbMovie | None:
        """Return a movie, or None when TMDB does not know the id."""
        data = self._http.get_json(
            f'{BASE}/movie/{tmdb_id}',
            params={
                'language': language_tag(language),
                'append_to_response': APPEND_TO_RESPONSE,
                'include_video_language': f'{language},en,null',
            },
        )
        if data is None:
            return None
        collection = data.get('belongs_to_collection')
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
            countries=_production_countries(data),
            collection=TmdbCollectionRef(
                tmdb_id=collection['id'],
                name=collection.get('name') or '',
                poster_url=_image(collection.get('poster_path'), 'w185'),
            )
            if collection
            else None,
            **_enrichment(data),
        )

    def tv(self, tmdb_id: int, language: str = 'en') -> TmdbShow | None:
        """Return a TV show with its season list, or None when the id is unknown."""
        data = self._http.get_json(
            f'{BASE}/tv/{tmdb_id}',
            params={
                'language': language_tag(language),
                'append_to_response': APPEND_TO_RESPONSE,
                'include_video_language': f'{language},en,null',
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
                for item in data.get('seasons') or []
            ],
            countries=tuple(data.get('origin_country') or ())
            or _production_countries(data),
            **_enrichment(data),
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

    def discover(
        self,
        kind: str,
        *,
        genre: str | None,
        country: str | None,
        year: int | None,
        sort: str,
        page: int,
        language: str,
    ) -> HitPage:
        """List movies or TV shows by genre, country and year.

        Args:
            kind: ``movie`` or ``tv``.
            genre: A genre slug; an unknown slug filters nothing.
            country: An ISO 3166-1 country code.
            year: Release (movie) or first air (TV) year.
            sort: ``popularity``, ``date``, ``title`` or ``rating``.
            page: 1-based page number.
            language: Site language of the titles in the result.

        Returns:
            One page of hits; empty when TMDB answers 404.
        """
        params = {
            'language': language_tag(language),
            'include_adult': 'false',
            'page': page,
            'sort_by': DISCOVER_SORTS[kind][sort],
        }
        if sort == 'rating':
            params['vote_count.gte'] = RATING_SORT_MIN_VOTES
        if genre and (genre_filter := tmdb_genre_filter(genre)):
            params['with_genres'] = genre_filter
        if country:
            params['with_origin_country'] = country
        if year:
            year_param = (
                'primary_release_year' if kind == 'movie' else 'first_air_date_year'
            )
            params[year_param] = year
        data = self._http.get_json(
            f'{BASE}/discover/{kind}', params=params, ttl=DISCOVER_TTL
        )
        if data is None:
            return HitPage(hits=[], total=0, has_more=False)
        return HitPage(
            hits=[_hit(item, kind) for item in data.get('results', [])],
            total=data.get('total_results') or 0,
            has_more=page < (data.get('total_pages') or 0),
        )

    def collection(self, tmdb_id: int, language: str = 'en') -> TmdbCollection | None:
        """Return a movie collection, or None when TMDB does not know the id."""
        data = self._http.get_json(
            f'{BASE}/collection/{tmdb_id}',
            params={'language': language_tag(language)},
            ttl=COLLECTION_TTL,
        )
        if data is None:
            return None
        # ISO dates sort as strings; parts without a date go last.
        parts = sorted(
            data.get('parts', []),
            key=lambda item: (
                not item.get('release_date'),
                item.get('release_date') or '',
            ),
        )
        return TmdbCollection(
            tmdb_id=data['id'],
            name=data.get('name') or '',
            overview=data.get('overview') or '',
            poster_url=_image(data.get('poster_path'), 'w342'),
            parts=[_hit(item, 'movie') for item in parts],
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
