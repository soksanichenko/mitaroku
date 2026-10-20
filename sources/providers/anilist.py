"""AniList client: anime entries, relations, schedule and search (no API key)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from providers.http import ProviderError, ProviderHttp
from providers.types import SearchHit

URL = 'https://graphql.anilist.co'
PAGE_SIZE = 50
ANIME_FORMATS = {'TV', 'TV_SHORT', 'MOVIE', 'SPECIAL', 'OVA', 'ONA'}

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
        for edge in (data.get('relations') or {}).get('edges', [])
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
