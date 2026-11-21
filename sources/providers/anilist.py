"""AniList client: anime entries, relations, schedule, search and browse (no key)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from providers.http import ProviderError, ProviderHttp
from providers.types import HitPage, SearchHit

URL = 'https://graphql.anilist.co'
PAGE_SIZE = 50
ANIME_FORMATS = {'TV', 'TV_SHORT', 'MOVIE', 'SPECIAL', 'OVA', 'ONA'}
DISCOVER_PAGE_SIZE = 20
DISCOVER_TTL = 3 * 3600
DETAILS_TTL = 24 * 3600
# AniList MediaSort value per site sort.
DISCOVER_SORTS = {
    'popularity': 'POPULARITY_DESC',
    'date': 'START_DATE_DESC',
    'title': 'TITLE_ENGLISH',
    'rating': 'SCORE_DESC',
}

MEDIA_FIELDS = """
  id idMal format status episodes averageScore isAdult
  title { romaji english native }
  coverImage { large }
  bannerImage
  startDate { year month day }
  relations { edges { relationType node { id idMal format type } } }
"""
MEDIA_QUERY = f"""
query ($ids: [Int], $perPage: Int) {{
  Page(perPage: $perPage) {{
    media(id_in: $ids, type: ANIME) {{ {MEDIA_FIELDS} }}
  }}
}}
"""
SEARCH_QUERY = f"""
query ($search: String, $perPage: Int) {{
  Page(perPage: $perPage) {{
    media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{ {MEDIA_FIELDS} }}
  }}
}}
"""
SCHEDULE_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    upcoming: airingSchedule(notYetAired: true, perPage: 50) { nodes { episode airingAt } }
    schedule: airingSchedule(perPage: 50) { nodes { episode airingAt } }
  }
}
"""
HIT_FIELDS = """
  id format isAdult averageScore
  title { romaji english }
  coverImage { large }
  startDate { year }
"""
DETAILS_QUERY = f"""
query ($id: Int) {{
  Media(id: $id, type: ANIME) {{
    genres
    countryOfOrigin
    trailer {{ id site }}
    characters(role: MAIN, sort: [ROLE, RELEVANCE], perPage: 10) {{
      edges {{
        node {{ name {{ full }} image {{ medium }} }}
        voiceActors(language: JAPANESE) {{ name {{ full }} }}
      }}
    }}
    recommendations(sort: RATING_DESC, perPage: 10) {{
      nodes {{ mediaRecommendation {{ {HIT_FIELDS} }} }}
    }}
  }}
}}
"""
DISCOVER_QUERY = f"""
query (
  $genre: String, $countryOfOrigin: CountryCode,
  $startDate_greater: FuzzyDateInt, $startDate_lesser: FuzzyDateInt,
  $sort: [MediaSort], $page: Int, $perPage: Int
) {{
  Page(page: $page, perPage: $perPage) {{
    pageInfo {{ total hasNextPage }}
    media(
      type: ANIME, isAdult: false, format_in: [TV, TV_SHORT, MOVIE, SPECIAL, OVA, ONA],
      genre: $genre, countryOfOrigin: $countryOfOrigin,
      startDate_greater: $startDate_greater, startDate_lesser: $startDate_lesser,
      sort: $sort
    ) {{
      {HIT_FIELDS}
      relations {{ edges {{ relationType node {{ type }} }} }}
    }}
  }}
}}
"""


@dataclass(frozen=True)
class AniListEdge:
    """A relation from one entry to another.

    ``format`` is None for freshly announced entries; such edges are kept.
    """

    relation: str
    anilist_id: int
    mal_id: int | None
    format: str | None
    media_type: str | None


@dataclass(frozen=True)
class AniListMedia:
    """One AniList anime entry.

    Attributes:
        start: ``(year, month, day)`` with None for the unknown parts.
        average_score: 0 to 100, or None.
    """

    anilist_id: int
    mal_id: int | None
    format: str | None
    status: str | None
    episodes: int | None
    average_score: int | None
    romaji: str
    english: str
    native: str
    cover_url: str
    banner_url: str
    start: tuple[int | None, int | None, int | None]
    edges: list[AniListEdge] = field(default_factory=list)

    @property
    def start_date(self) -> date | None:
        """Return the start date when year, month and day are all known."""
        year, month, day = self.start
        return date(year, month, day) if year and month and day else None

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        """Chronological sort key: unknown dates last, equal dates by AniList id."""
        year, month, day = self.start
        return (year or 9999, month or 12, day or 31, self.anilist_id)


@dataclass(frozen=True)
class AniListSchedule:
    """Airing times of an entry, as ``{episode_number: UTC datetime}``.

    ``upcoming`` holds the episodes not yet aired; ``all`` holds the first 50
    scheduled episodes of any age.
    """

    upcoming: dict[int, datetime]
    all: dict[int, datetime]


@dataclass(frozen=True)
class AniListCredit:
    """A main character and the Japanese voice actor, ``''`` when unknown."""

    character: str
    actor: str
    photo_url: str


@dataclass(frozen=True)
class AniListDetails:
    """What the Browse page shows beyond the entry itself.

    Attributes:
        genres: AniList genre names.
        country: ``countryOfOrigin`` code, empty when unknown.
        trailer: ``(site, id)`` of the trailer video, or None.
        cast: Main characters with their Japanese voice actors.
        recommendations: Other entries users recommend, best rated first.
    """

    genres: tuple[str, ...]
    country: str
    trailer: tuple[str, str] | None
    cast: tuple[AniListCredit, ...]
    recommendations: tuple[SearchHit, ...]


def media_from_json(data: dict) -> AniListMedia:
    """Convert one ``Media`` object of the GraphQL response."""
    start = data.get('startDate') or {}
    title = data.get('title') or {}
    edges = [
        AniListEdge(
            relation=edge['relationType'],
            anilist_id=edge['node']['id'],
            mal_id=edge['node'].get('idMal'),
            format=edge['node'].get('format'),
            media_type=edge['node'].get('type'),
        )
        for edge in (data.get('relations') or {}).get('edges') or []
        if edge and edge.get('node')
    ]
    return AniListMedia(
        anilist_id=data['id'],
        mal_id=data.get('idMal'),
        format=data.get('format'),
        status=data.get('status'),
        episodes=data.get('episodes'),
        average_score=data.get('averageScore'),
        romaji=title.get('romaji') or '',
        english=title.get('english') or '',
        native=title.get('native') or '',
        cover_url=(data.get('coverImage') or {}).get('large') or '',
        banner_url=data.get('bannerImage') or '',
        start=(start.get('year'), start.get('month'), start.get('day')),
        edges=edges,
    )


def _validate(body: object) -> None:
    """Reject a GraphQL body without usable ``data`` so it is never cached.

    Partial data next to ``errors`` is accepted; ``errors`` alone is not.

    Raises:
        ProviderError: The body is not an object or has no ``data`` object.
    """
    if isinstance(body, dict) and body.get('data'):
        return
    errors = body.get('errors') if isinstance(body, dict) else None
    message = errors[0].get('message') if errors else 'no data in the response'
    raise ProviderError(f'AniList error: {message}')


def _media_items(data: dict | None) -> list[dict]:
    """Return ``data['Page']['media']`` or raise when any level is missing."""
    media = ((data or {}).get('Page') or {}).get('media')
    if media is None:
        raise ProviderError('AniList error: no media in the response')
    return media


def _hit(item: dict) -> SearchHit:
    """Convert a ``HIT_FIELDS`` object into a hit."""
    title = item.get('title') or {}
    score = item.get('averageScore')
    return SearchHit(
        provider='anilist',
        external_id=str(item['id']),
        kind='anime',
        title=title.get('english') or title.get('romaji') or '',
        original_title=title.get('romaji') or '',
        year=(item.get('startDate') or {}).get('year'),
        poster_url=(item.get('coverImage') or {}).get('large') or '',
        overview='',
        rating=score / 10 if score else None,
    )


def _has_prequel(item: dict) -> bool:
    """Whether the entry continues another anime."""
    edges = (item.get('relations') or {}).get('edges') or []
    return any(
        edge.get('relationType') == 'PREQUEL'
        and (edge.get('node') or {}).get('type') == 'ANIME'
        for edge in edges
        if edge
    )


def _credit(edge: dict) -> AniListCredit:
    """Convert one character edge; the actor is the first voice actor."""
    node = edge.get('node') or {}
    actors = [actor for actor in edge.get('voiceActors') or [] if actor]
    return AniListCredit(
        character=(node.get('name') or {}).get('full') or '',
        actor=((actors[0].get('name') or {}).get('full') or '') if actors else '',
        photo_url=(node.get('image') or {}).get('medium') or '',
    )


def _nodes(connection: dict | None) -> dict[int, datetime]:
    """Turn an ``airingSchedule`` connection into ``{episode: datetime}``."""
    return {
        node['episode']: datetime.fromtimestamp(node['airingAt'], UTC)
        for node in (connection or {}).get('nodes', [])
    }


class AniListClient:
    """Typed access to the parts of the AniList GraphQL API the catalog needs."""

    def __init__(self, http: ProviderHttp) -> None:
        """Create the client.

        Args:
            http: Rate-limited wrapper around AniList's endpoint.
        """
        self._http = http

    def _query(
        self, query: str, variables: dict, ttl: int | None = None
    ) -> dict | None:
        """Run a GraphQL query and return its ``data`` object."""
        body = self._http.post_json(
            URL,
            json={'query': query, 'variables': variables},
            ttl=ttl,
            validate=_validate,
        )
        return None if body is None else body['data']

    def media_many(self, ids: Sequence[int]) -> list[AniListMedia]:
        """Fetch entries by AniList id, 50 per request.

        Args:
            ids: AniList ids. Unknown ids are simply absent from the result.

        Returns:
            The entries AniList knows, in no particular order.
        """
        result = []
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), PAGE_SIZE):
            data = self._query(
                MEDIA_QUERY,
                {'ids': unique[start : start + PAGE_SIZE], 'perPage': PAGE_SIZE},
            )
            result.extend(media_from_json(item) for item in _media_items(data))
        return result

    def schedule(self, anilist_id: int) -> AniListSchedule:
        """Fetch the airing schedule of one entry (empty when AniList has none)."""
        data = self._query(SCHEDULE_QUERY, {'id': anilist_id}, ttl=3600)
        media = (data or {}).get('Media') or {}
        return AniListSchedule(
            upcoming=_nodes(media.get('upcoming')), all=_nodes(media.get('schedule'))
        )

    def search(self, query: str) -> list[SearchHit]:
        """Search anime entries by title.

        Args:
            query: Text typed by the user.

        Returns:
            Entries in AniList's relevance order; adult and non-anime formats
            are dropped.
        """
        data = self._query(SEARCH_QUERY, {'search': query, 'perPage': 10}, ttl=3600)
        hits = []
        for item in _media_items(data):
            if item.get('isAdult') or item.get('format') not in ANIME_FORMATS:
                continue
            media = media_from_json(item)
            hits.append(
                SearchHit(
                    provider='anilist',
                    external_id=str(media.anilist_id),
                    kind='anime',
                    title=media.english or media.romaji,
                    original_title=media.romaji,
                    year=media.start[0],
                    poster_url=media.cover_url,
                    overview='',
                )
            )
        return hits

    def details(self, anilist_id: int) -> AniListDetails | None:
        """Fetch genres, country, trailer, main cast and recommendations.

        Args:
            anilist_id: AniList id of the entry.

        Returns:
            The details, or None when AniList does not know the id. Adult and
            non-anime recommendations are dropped.
        """
        data = self._query(DETAILS_QUERY, {'id': anilist_id}, ttl=DETAILS_TTL)
        media = (data or {}).get('Media')
        if media is None:
            return None
        trailer = media.get('trailer') or {}
        recommended = (
            (node or {}).get('mediaRecommendation')
            for node in (media.get('recommendations') or {}).get('nodes') or []
        )
        return AniListDetails(
            genres=tuple(media.get('genres') or ()),
            country=media.get('countryOfOrigin') or '',
            trailer=(
                (trailer['site'], trailer['id'])
                if trailer.get('site') and trailer.get('id')
                else None
            ),
            cast=tuple(
                _credit(edge)
                for edge in (media.get('characters') or {}).get('edges') or []
                if edge
            ),
            recommendations=tuple(
                _hit(item)
                for item in recommended
                if item
                and not item.get('isAdult')
                and item.get('format') in ANIME_FORMATS
            ),
        )

    def discover(
        self,
        *,
        genre: str | None,
        country: str | None,
        year: int | None,
        sort: str,
        page: int,
    ) -> HitPage:
        """List anime by genre, country and start year.

        Args:
            genre: An AniList genre name (not a slug).
            country: An ISO 3166-1 country code.
            year: Start year.
            sort: ``popularity``, ``date``, ``title`` or ``rating``.
            page: 1-based page number.

        Returns:
            One page of hits. Adult entries and entries with an anime prequel
            are left out, so only the first entry of a franchise is listed.
        """
        variables: dict = {
            'sort': [DISCOVER_SORTS[sort]],
            'page': page,
            'perPage': DISCOVER_PAGE_SIZE,
        }
        if genre:
            variables['genre'] = genre
        if country:
            variables['countryOfOrigin'] = country
        if year:
            # The bounds are exclusive FuzzyDateInts (YYYYMMDD with 0 and 32 as
            # the "unknown" month and day).
            variables['startDate_greater'] = year * 10000
            variables['startDate_lesser'] = year * 10000 + 1232
        data = self._query(DISCOVER_QUERY, variables, ttl=DISCOVER_TTL)
        page_info = ((data or {}).get('Page') or {}).get('pageInfo') or {}
        return HitPage(
            hits=[
                _hit(item)
                for item in _media_items(data)
                if item and not item.get('isAdult') and not _has_prequel(item)
            ],
            total=page_info.get('total') or 0,
            has_more=bool(page_info.get('hasNextPage')),
        )
