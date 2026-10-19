# Plan 0: Anime Source and ID-Mapping Spike

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide which providers supply anime franchise structure, episode lists with air dates, and ID bridges to TMDB, so that the data model and provider contracts of plan 2 rest on measured facts.

**Architecture:** Throwaway scripts under `spikes/anime_mapping/` (outside `sources/`, so CI ignores them). They use the public `Fribb/anime-lists` dataset as the ID bridge, AniList (no key) as the franchise and schedule candidate, and optionally Simkl and TMDB when keys are available. The output is a written recommendation in `docs/superpowers/spikes/`, plus a spec update. The scripts are labeled throwaway; plan 2 re-implements whatever it keeps inside `sources/`.

**Tech Stack:** Python 3.12, httpx 0.28.1, pytest 9.1.1.

**Spec:** `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 3, 4 and 13).

## Global Constraints

- Python 3.12; single quotes for strings; type annotations everywhere; Google-style docstrings on public functions and classes; PEP 8, 88 columns preferred.
- Use `logging` through a `get_logger(logger_name)` helper, not `print()`, for operator output. The one exception is the report script, whose stdout is its product.
- Everything committed (code, comments, commit messages, the report) is in English.
- Spike code is throwaway: it is not imported by anything in `sources/`.
- Never commit API keys. Keys come from the environment variables `SIMKL_CLIENT_ID` and `TMDB_TOKEN` and are optional.
- Simkl's published API rules (https://api.simkl.org/api-rules.md, rule 2) say a tracker may use the Simkl catalog only if it also offers Simkl login and sync. The report must treat this as a decision input, not a footnote.

## Facts established before this plan (verified 2026-10-10)

- `Fribb/anime-lists` `anime-list-full.json` (raw URL in `fribb.py` below) has 39,619 entries. Each entry can carry `mal_id`, `simkl_id`, `themoviedb_id` (an object with a `tv` or `movie` key), `imdb_id` (a list), `anilist_id`, `tvdb_id`, and, for multi-cour shows, `season` and `episode_offset` objects with `tvdb` and `tmdb` keys. 8,281 entries have MAL, Simkl and TMDB ids together.
- AniList's GraphQL endpoint works without a key and returns `idMal`, relations with `relationType`, `format`, `episodes`, `nextAiringEpisode` and `airingSchedule`. It rate-limits (HTTP 429 with `Retry-After`) at roughly one request per second sustained.
- Simkl catalog calls need `client_id`, `app-name` and `app-version` as URL parameters and a `User-Agent` header. `GET /anime/{id}?extended=full` returns `relations`; `GET /anime/episodes/{id}` returns episodes with `date`, `type`, `episode` and a `tvdb` season/episode pair. Catalog calls need no user token and do not count against the quota.
- A bare `GET https://api.simkl.com/anime/episodes/39687` without any parameters returned 200 with 40 entries for Attack on Titan season 1 (25 episodes and 15 specials, 35 with a `date`) on 2026-10-10. Whether Simkl keeps allowing this is not guaranteed; the key requirement above is the documented one.
- Kitsu (`kitsu.io/api/edge`) timed out from the development machine, so it is not a candidate here.
- Jikan (`api.jikan.moe`) was unreachable from the development machine, and the official MAL API needs a client id. Neither is probed here.

## Review Focus

- A franchise whose sequel/prequel graph contains a cycle must terminate (test in Task 2).
- An AniList relation to a manga, novel or music item must not become a franchise entry (test in Task 2).
- A franchise entry that has no Fribb record must be counted as uncovered, not crash the run (the `fribb` flag in `run.py`, Task 4).
- A title that AniList does not know must appear in the report as "not found", not abort the run (test in Task 4).
- AniList rate limiting must back off and retry rather than fail the whole run (Task 3, `_request`).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `spikes/anime_mapping/log.py` | `get_logger` helper |
| `spikes/anime_mapping/cache.py` | `DiskCache`, JSON files under `.cache/` |
| `spikes/anime_mapping/fixtures.py` | The 12 titles under test |
| `spikes/anime_mapping/franchise.py` | Pure franchise grouping over AniList relations |
| `spikes/anime_mapping/fork_rules.py`, `compare_fork.py` | The Yamtrack fork's relation rules over AniList types, and the side-by-side table |
| `spikes/anime_mapping/episodes_tmdb.py` | TMDB-only episode allocation scored against Simkl |
| `spikes/anime_mapping/anilist.py` | AniList client with cache and rate limiting |
| `spikes/anime_mapping/fribb.py` | Dataset download and lookups |
| `spikes/anime_mapping/simkl.py`, `tmdb.py` | Optional probes |
| `spikes/anime_mapping/run.py` | Runs every fixture, writes `results.json` |
| `spikes/anime_mapping/report.py` | Renders `results.json` as a markdown table |
| `spikes/anime_mapping/tests/` | Unit tests for the pure parts |

---

### Task 1: Scaffold the spike directory

**Files:**
- Create: `spikes/anime_mapping/requirements.txt`, `spikes/anime_mapping/pytest.ini`, `spikes/anime_mapping/log.py`, `spikes/anime_mapping/cache.py`, `spikes/anime_mapping/fixtures.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `get_logger(logger_name: str) -> logging.Logger`; `DiskCache(directory: Path)` with `get(key: str) -> object | None` and `put(key: str, value: object) -> None`; `FIXTURES: list[Fixture]` where `Fixture` has `mal_id: int`, `label: str`, `why: str`.

- [ ] **Step 1: Create the environment files**

`spikes/anime_mapping/requirements.txt`:

```text
httpx==0.28.1
pytest==9.1.1
```

`spikes/anime_mapping/pytest.ini`:

```ini
[pytest]
pythonpath = .
testpaths = tests
```

Append to `.gitignore`:

```text
spikes/**/.cache/
spikes/**/results.json
```

- [ ] **Step 2: Create `log.py`**

```python
"""Logging helper shared by the spike scripts."""

import logging


def get_logger(logger_name: str) -> logging.Logger:
    """Return a logger that writes plain messages to stderr.

    Args:
        logger_name: Name of the logger, usually the module name.

    Returns:
        A configured ``logging.Logger``.
    """
    logger = logging.getLogger(logger_name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

- [ ] **Step 3: Create `cache.py`**

```python
"""Tiny on-disk JSON cache so spike runs are repeatable and cheap."""

import json
from pathlib import Path


class DiskCache:
    """Store JSON-serialisable values as files under one directory."""

    def __init__(self, directory: Path) -> None:
        """Create the cache directory if needed.

        Args:
            directory: Where cache files live.
        """
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> object | None:
        """Return the cached value for ``key`` or None."""
        path = self._directory / f'{key}.json'
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding='utf-8'))

    def put(self, key: str, value: object) -> None:
        """Store ``value`` under ``key``."""
        path = self._directory / f'{key}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
```

- [ ] **Step 4: Create `fixtures.py`**

```python
"""Hand-picked anime that stress the franchise and id-mapping logic."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    """One title the spike is run against.

    Attributes:
        mal_id: MyAnimeList id of the entry used as the franchise root.
        label: Human-readable name for the report.
        why: What makes this title a useful test case.
    """

    mal_id: int
    label: str
    why: str


FIXTURES: list[Fixture] = [
    Fixture(52991, 'Frieren', 'Finished season with a second season announced'),
    Fixture(16498, 'Attack on Titan', 'One show split into many cour entries'),
    Fixture(21, 'One Piece', 'A single absolutely numbered entry'),
    Fixture(5114, 'Fullmetal Alchemist: Brotherhood', 'TV plus OVAs, remake of an older show'),
    Fixture(5081, 'Bakemonogatari', 'Monogatari: many entries, release order differs'),
    Fixture(22297, 'Fate/stay night: UBW', 'Large multi-adaptation franchise'),
    Fixture(9253, 'Steins;Gate', 'TV, movie and sequel with specials'),
    Fixture(38000, 'Demon Slayer', 'Seasons plus arc-compilation movies'),
    Fixture(40748, 'Jujutsu Kaisen', 'Two seasons and a prequel movie'),
    Fixture(457, 'Mushishi', 'TV, special and delayed sequel'),
    Fixture(1, 'Cowboy Bebop', 'Single TV plus a movie'),
    Fixture(32281, 'Your Name', 'Standalone anime movie'),
]
```

- [ ] **Step 5: Create the virtualenv and check the imports**

Run from the repo root:

```bash
python3 -m venv spikes/.venv
spikes/.venv/bin/pip install -r spikes/anime_mapping/requirements.txt
cd spikes/anime_mapping && ../.venv/bin/python -c "import log, cache, fixtures; print(len(fixtures.FIXTURES))"
```

Expected: prints `12`.

- [ ] **Step 6: Commit**

```bash
git add .gitignore spikes/anime_mapping
git commit -m "spike: scaffold anime mapping spike"
```

(`spikes/.venv/` is covered by the existing `.venv/` rule in `.gitignore`.)

---

### Task 2: Franchise grouping (pure logic, test first)

**Files:**
- Create: `spikes/anime_mapping/tests/test_franchise.py`, then `spikes/anime_mapping/franchise.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Node` dataclass (`anilist_id`, `mal_id`, `format`, `title`, `episodes`, `start`, `edges`); `Franchise` dataclass (`main: list[Node]`, `extras: list[tuple[int, int | None, str | None, str]]`); `node_from_media(media: dict) -> Node`; `build_franchise(fetch: Callable[[int], Node | None], root_anilist_id: int) -> Franchise`.

- [ ] **Step 1: Write the failing tests**

```python
from franchise import Node, build_franchise


def make(anilist_id, edges, fmt='TV', year=2020):
    return Node(
        anilist_id=anilist_id,
        mal_id=anilist_id + 1000,
        format=fmt,
        title=f'n{anilist_id}',
        episodes=12,
        start=(year, 1, 1),
        edges=edges,
    )


def fetcher(nodes):
    by_id = {node.anilist_id: node for node in nodes}
    return by_id.get


def test_follows_sequels_and_prequels_in_both_directions():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')], year=2018),
        make(2, [('PREQUEL', 1, 1001, 'TV', 'ANIME'), ('SEQUEL', 3, 1003, 'TV', 'ANIME')], year=2019),
        make(3, [('PREQUEL', 2, 1002, 'TV', 'ANIME')], year=2020),
    ]
    franchise = build_franchise(fetcher(nodes), 2)
    assert [n.anilist_id for n in franchise.main] == [1, 2, 3]


def test_cycle_does_not_loop_forever():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')], year=2018),
        make(2, [('SEQUEL', 1, 1001, 'TV', 'ANIME')], year=2019),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert sorted(n.anilist_id for n in franchise.main) == [1, 2]


def test_manga_and_music_nodes_are_ignored():
    nodes = [
        make(1, [('SEQUEL', 9, None, 'MANGA', 'MANGA'), ('SEQUEL', 8, None, 'MUSIC', 'ANIME')]),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1]
    assert franchise.extras == []


def test_side_entries_are_extras_and_not_expanded():
    nodes = [
        make(1, [('SIDE_STORY', 5, 1005, 'OVA', 'ANIME')]),
        make(5, [('SEQUEL', 6, 1006, 'TV', 'ANIME')], fmt='OVA'),
        make(6, []),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1]
    assert franchise.extras == [(5, 1005, 'OVA', 'SIDE_STORY')]


def test_unavailable_node_is_skipped():
    nodes = [make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')])]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1]


def test_unknown_start_dates_sort_last():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME')], year=2020),
        make(2, [('PREQUEL', 1, 1001, 'TV', 'ANIME')], year=None),
    ]
    franchise = build_franchise(fetcher(nodes), 1)
    assert [n.anilist_id for n in franchise.main] == [1, 2]
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest tests/test_franchise.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'franchise'`.

- [ ] **Step 3: Implement `franchise.py`**

```python
"""Group AniList entries into a franchise using sequel/prequel chains."""

from collections.abc import Callable
from dataclasses import dataclass, field

MAIN_RELATIONS = {'SEQUEL', 'PREQUEL'}
EXTRA_RELATIONS = {'SIDE_STORY', 'SUMMARY', 'SPIN_OFF', 'ALTERNATIVE', 'PARENT'}
ANIME_FORMATS = {'TV', 'TV_SHORT', 'MOVIE', 'SPECIAL', 'OVA', 'ONA'}


@dataclass
class Node:
    """One anime entry as returned by AniList.

    Attributes:
        anilist_id: AniList media id.
        mal_id: MyAnimeList id, or None when AniList has no mapping.
        format: AniList format (TV, MOVIE, OVA, ...).
        title: Romaji title.
        episodes: Episode count, or None while unknown.
        start: (year, month, day) with None for unknown parts.
        edges: List of (relation_type, anilist_id, mal_id, format, media_type).
    """

    anilist_id: int
    mal_id: int | None
    format: str | None
    title: str
    episodes: int | None
    start: tuple[int | None, int | None, int | None]
    edges: list[tuple[str, int, int | None, str | None, str]] = field(default_factory=list)


@dataclass
class Franchise:
    """A franchise split into the sequel/prequel chain and its side entries."""

    main: list[Node]
    extras: list[tuple[int, int | None, str | None, str]]


def node_from_media(media: dict) -> Node:
    """Convert an AniList ``Media`` object into a ``Node``.

    Args:
        media: Parsed ``data.Media`` object from the AniList response.

    Returns:
        The corresponding ``Node``.
    """
    start = media.get('startDate') or {}
    edges = [
        (
            edge['relationType'],
            edge['node']['id'],
            edge['node'].get('idMal'),
            edge['node'].get('format'),
            edge['node'].get('type', ''),
        )
        for edge in (media.get('relations') or {}).get('edges', [])
    ]
    return Node(
        anilist_id=media['id'],
        mal_id=media.get('idMal'),
        format=media.get('format'),
        title=(media.get('title') or {}).get('romaji') or '',
        episodes=media.get('episodes'),
        start=(start.get('year'), start.get('month'), start.get('day')),
        edges=edges,
    )


def _sort_key(node: Node) -> tuple[int, int, int]:
    """Sort chronologically, pushing unknown dates to the end."""
    year, month, day = node.start
    return (year or 9999, month or 12, day or 31)


def build_franchise(
    fetch: Callable[[int], Node | None], root_anilist_id: int
) -> Franchise:
    """Walk sequel/prequel links from a root entry.

    Side entries (side stories, summaries, spin-offs, alternatives, parents) of
    main entries are listed as extras but never expanded, which keeps a
    franchise bounded. Non-anime nodes (manga, music) are ignored.

    Args:
        fetch: Returns the ``Node`` for an AniList id, or None if unavailable.
        root_anilist_id: AniList id to start from.

    Returns:
        The franchise with ``main`` sorted by start date.
    """
    seen: set[int] = set()
    main: list[Node] = []
    queue = [root_anilist_id]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        node = fetch(current)
        if node is None:
            continue
        main.append(node)
        for relation, other_id, _mal, fmt, media_type in node.edges:
            if relation in MAIN_RELATIONS and media_type == 'ANIME' and fmt in ANIME_FORMATS:
                queue.append(other_id)
    main_ids = {node.anilist_id for node in main}
    extras: dict[int, tuple[int, int | None, str | None, str]] = {}
    for node in main:
        for relation, other_id, mal, fmt, media_type in node.edges:
            if (
                relation in EXTRA_RELATIONS
                and media_type == 'ANIME'
                and fmt in ANIME_FORMATS
                and other_id not in main_ids
            ):
                extras[other_id] = (other_id, mal, fmt, relation)
    main.sort(key=_sort_key)
    return Franchise(main=main, extras=list(extras.values()))
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest tests/test_franchise.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add spikes/anime_mapping/franchise.py spikes/anime_mapping/tests/test_franchise.py
git commit -m "spike: group AniList entries into franchises"
```

---

### Task 3: Fribb loader and AniList client

**Files:**
- Create: `spikes/anime_mapping/tests/test_fribb.py`, `spikes/anime_mapping/fribb.py`, `spikes/anime_mapping/anilist.py`

**Interfaces:**
- Consumes: `get_logger`, `DiskCache`, `Node`, `node_from_media`.
- Produces: `fribb.load(path: Path) -> list[dict]`; `fribb.index_by_mal(entries: list[dict]) -> dict[int, dict]`; `fribb.tmdb_id(entry: dict) -> tuple[str, int] | None`; `AniListClient(cache: DiskCache, min_interval: float = 0.8)` with `media(*, anilist_id=None, mal_id=None) -> dict | None` and `node(anilist_id: int) -> Node | None`.

- [ ] **Step 1: Write the failing tests for the Fribb helpers**

```python
from fribb import index_by_mal, tmdb_id


def test_index_by_mal_keeps_first_entry_and_skips_missing_ids():
    entries = [
        {'mal_id': 1, 'simkl_id': 10},
        {'mal_id': 1, 'simkl_id': 11},
        {'simkl_id': 12},
    ]
    assert index_by_mal(entries) == {1: {'mal_id': 1, 'simkl_id': 10}}


def test_tmdb_id_prefers_tv_and_handles_lists_and_absence():
    assert tmdb_id({'themoviedb_id': {'tv': 5}}) == ('tv', 5)
    assert tmdb_id({'themoviedb_id': {'movie': [7, 8]}}) == ('movie', 7)
    assert tmdb_id({'themoviedb_id': None}) is None
    assert tmdb_id({}) is None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest tests/test_fribb.py -q`
Expected: `ModuleNotFoundError: No module named 'fribb'`.

- [ ] **Step 3: Implement `fribb.py`**

```python
"""Load the Fribb/anime-lists id mapping dataset."""

import json
from pathlib import Path

import httpx

from log import get_logger

logger = get_logger(__name__)

URL = 'https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json'


def load(path: Path) -> list[dict]:
    """Download the dataset once and return its entries.

    Args:
        path: Local file to cache the dataset in.

    Returns:
        The parsed list of mapping entries.
    """
    if not path.exists():
        logger.info('Downloading %s', URL)
        response = httpx.get(URL, timeout=120, follow_redirects=True)
        response.raise_for_status()
        path.write_bytes(response.content)
    return json.loads(path.read_text(encoding='utf-8'))


def index_by_mal(entries: list[dict]) -> dict[int, dict]:
    """Index entries by ``mal_id``; the first entry wins on duplicates."""
    result: dict[int, dict] = {}
    for entry in entries:
        mal_id = entry.get('mal_id')
        if mal_id is not None and mal_id not in result:
            result[mal_id] = entry
    return result


def tmdb_id(entry: dict) -> tuple[str, int] | None:
    """Return ``('tv'|'movie', id)`` for the first TMDB id, or None."""
    tmdb = entry.get('themoviedb_id')
    if not tmdb:
        return None
    for kind in ('tv', 'movie'):
        value = tmdb.get(kind)
        if value:
            return kind, value[0] if isinstance(value, list) else value
    return None
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest tests/test_fribb.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Implement `anilist.py`**

The client has no unit test because it only talks to the network; Step 6 exercises it live. Its retry loop is the rate-limit handling named in Review Focus.

```python
"""Minimal AniList GraphQL client (no API key needed)."""

import time

import httpx

from cache import DiskCache
from franchise import Node, node_from_media
from log import get_logger

logger = get_logger(__name__)

URL = 'https://graphql.anilist.co'
QUERY = """
query ($id: Int, $idMal: Int) {
  Media(id: $id, idMal: $idMal, type: ANIME) {
    id idMal format status episodes
    title { romaji }
    startDate { year month day }
    nextAiringEpisode { episode airingAt }
    airingSchedule(notYetAired: true, perPage: 50) { nodes { episode airingAt } }
    relations { edges { relationType node { id idMal format type } } }
  }
}
"""


class AniListClient:
    """Fetch anime by AniList or MAL id, politely and with caching."""

    def __init__(self, cache: DiskCache, min_interval: float = 0.8) -> None:
        """Create the client.

        Args:
            cache: Disk cache for raw ``Media`` objects.
            min_interval: Minimum seconds between network requests.
        """
        self._cache = cache
        self._min_interval = min_interval
        self._last_request = 0.0
        self._http = httpx.Client(timeout=30)

    def media(self, *, anilist_id: int | None = None, mal_id: int | None = None) -> dict | None:
        """Return the raw ``Media`` object, or None when AniList has no match.

        Args:
            anilist_id: AniList id to look up.
            mal_id: MyAnimeList id to look up (used when ``anilist_id`` is None).

        Returns:
            The ``Media`` dict, or None if the entry does not exist.
        """
        key = f'anilist-{anilist_id}' if anilist_id else f'anilist-mal-{mal_id}'
        cached = self._cache.get(key)
        if cached is not None:
            return cached or None
        variables = {'id': anilist_id} if anilist_id else {'idMal': mal_id}
        media = self._request(variables)
        self._cache.put(key, media or {})
        return media

    def node(self, anilist_id: int) -> Node | None:
        """Return a franchise ``Node`` for an AniList id."""
        media = self.media(anilist_id=anilist_id)
        return node_from_media(media) if media else None

    def _request(self, variables: dict) -> dict | None:
        """POST the query, respecting the rate limit and ``Retry-After``."""
        for attempt in range(5):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            response = self._http.post(URL, json={'query': QUERY, 'variables': variables})
            if response.status_code == 429:
                delay = int(response.headers.get('Retry-After', '60'))
                logger.info('AniList rate limit hit, sleeping %s s', delay)
                time.sleep(delay)
                continue
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()['data']['Media']
        raise RuntimeError('AniList rate limit not cleared after 5 attempts')
```

- [ ] **Step 6: Smoke-test the client against the live API**

```bash
cd spikes/anime_mapping && ../.venv/bin/python - <<'EOF'
from pathlib import Path
from anilist import AniListClient
from cache import DiskCache
client = AniListClient(DiskCache(Path('.cache/anilist')))
media = client.media(mal_id=52991)
print(media['id'], media['title']['romaji'], media['episodes'])
EOF
```

Expected: `154587 Sousou no Frieren 28`.

- [ ] **Step 7: Commit**

```bash
git add spikes/anime_mapping/fribb.py spikes/anime_mapping/anilist.py spikes/anime_mapping/tests/test_fribb.py
git commit -m "spike: add Fribb loader and AniList client"
```

---

### Task 4: Optional probes, runner and report

**Files:**
- Create: `spikes/anime_mapping/tests/test_report.py`, `spikes/anime_mapping/simkl.py`, `spikes/anime_mapping/tmdb.py`, `spikes/anime_mapping/run.py`, `spikes/anime_mapping/report.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 3.
- Produces: `SimklClient(client_id: str)` with `anime(simkl_id) -> dict` and `episodes(simkl_id) -> list[dict]`; `TmdbClient(token: str)` with `tv_seasons(tv_id) -> list[tuple[int, int]]`; `run.main()` writing `results.json`; `report.render(results: list[dict]) -> str`.

- [ ] **Step 1: Write the failing report test**

```python
from report import render


def test_render_counts_coverage_and_marks_missing_probes():
    results = [
        {
            'label': 'Demo',
            'main': [
                {'fribb': True, 'simkl_id': 1, 'tmdb': ['tv', 5], 'imdb': True},
                {'fribb': False, 'simkl_id': None, 'tmdb': None, 'imdb': False},
            ],
            'extras': 3,
            'airing_schedule': 0,
            'simkl': None,
            'tmdb_seasons': None,
        },
        {'label': 'Ghost', 'main': None, 'extras': 0},
    ]
    table = render(results)
    assert '| Demo | 2 | 3 | 1/2 | 1/2 | 1/2 | 1/2 | 0 | n/a | n/a | n/a |' in table
    assert 'Ghost | not found on AniList' in table
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest tests/test_report.py -q`
Expected: `ModuleNotFoundError: No module named 'report'`.

- [ ] **Step 3: Implement `report.py`**

```python
"""Render results.json as a markdown table for the spike report."""

import json
from pathlib import Path

HERE = Path(__file__).parent


def render(results: list[dict]) -> str:
    """Build the coverage table.

    Args:
        results: Parsed ``results.json``.

    Returns:
        A markdown table, one row per fixture.
    """
    header = (
        '| Title | Main entries | Extras | In Fribb | With Simkl id | With TMDB id '
        '| With IMDb id | AniList upcoming episodes | Simkl relations | '
        'Simkl episodes (dated) | TMDB seasons |\n'
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n'
    )
    rows = []
    for item in results:
        main = item.get('main')
        if not main:
            rows.append(f"| {item['label']} | not found on AniList |" + ' |' * 9)
            continue
        total = len(main)
        simkl = item.get('simkl')
        simkl_relations = simkl['relations'] if simkl else 'n/a'
        simkl_episodes = (
            f"{simkl['episodes']} ({simkl['episodes_with_date']})" if simkl else 'n/a'
        )
        seasons = item.get('tmdb_seasons')
        tmdb_seasons = len(seasons) if seasons else 'n/a'
        rows.append(
            f"| {item['label']} | {total} | {item['extras']} "
            f"| {sum(e['fribb'] for e in main)}/{total} "
            f"| {sum(1 for e in main if e['simkl_id'])}/{total} "
            f"| {sum(1 for e in main if e['tmdb'])}/{total} "
            f"| {sum(e['imdb'] for e in main)}/{total} "
            f"| {item['airing_schedule']} | {simkl_relations} "
            f"| {simkl_episodes} | {tmdb_seasons} |"
        )
    return header + '\n'.join(rows) + '\n'


def main() -> None:
    """Print the table for ``results.json`` to stdout."""
    results = json.loads((HERE / 'results.json').read_text(encoding='utf-8'))
    print(render(results))  # noqa: T201 - report output is the script's product


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest -q`
Expected: `9 passed`.

- [ ] **Step 5: Implement the probes**

`spikes/anime_mapping/simkl.py`:

```python
"""Minimal Simkl catalog client (needs a free client_id)."""

import httpx

BASE = 'https://api.simkl.com'


class SimklClient:
    """Read-only calls to the cached Simkl catalog endpoints."""

    def __init__(self, client_id: str) -> None:
        """Create the client.

        Args:
            client_id: Simkl application client id.
        """
        self._params = {
            'client_id': client_id,
            'app-name': 'mitaroku-spike',
            'app-version': '0.1',
        }
        self._http = httpx.Client(
            timeout=30, headers={'User-Agent': 'mitaroku-spike/0.1'}
        )

    def anime(self, simkl_id: int) -> dict:
        """Return the full anime record including ``relations``."""
        response = self._http.get(
            f'{BASE}/anime/{simkl_id}', params={**self._params, 'extended': 'full'}
        )
        response.raise_for_status()
        return response.json()

    def episodes(self, simkl_id: int) -> list[dict]:
        """Return the episode list with air dates."""
        response = self._http.get(
            f'{BASE}/anime/episodes/{simkl_id}', params=self._params
        )
        response.raise_for_status()
        return response.json()
```

`spikes/anime_mapping/tmdb.py`:

```python
"""Minimal TMDB client for the TV season comparison."""

import httpx

BASE = 'https://api.themoviedb.org/3'


class TmdbClient:
    """Read TV show structure with a v4 read access token."""

    def __init__(self, token: str) -> None:
        """Create the client.

        Args:
            token: TMDB API read access token (Bearer).
        """
        self._http = httpx.Client(
            timeout=30, headers={'Authorization': f'Bearer {token}'}
        )

    def tv_seasons(self, tv_id: int) -> list[tuple[int, int]]:
        """Return ``(season_number, episode_count)`` for a TV show."""
        response = self._http.get(f'{BASE}/tv/{tv_id}')
        response.raise_for_status()
        return [
            (season['season_number'], season['episode_count'])
            for season in response.json().get('seasons', [])
        ]
```

- [ ] **Step 6: Implement the runner**

```python
"""Run the id-mapping and franchise spike and write results.json."""

import json
import os
from pathlib import Path

import fribb
import tmdb
import simkl
from anilist import AniListClient
from cache import DiskCache
from fixtures import FIXTURES
from franchise import build_franchise
from log import get_logger

logger = get_logger('run')
HERE = Path(__file__).parent
CACHE_DIR = HERE / '.cache'


def probe_simkl(client: simkl.SimklClient | None, simkl_id: int | None) -> dict | None:
    """Collect Simkl relation and episode-date numbers for one entry."""
    if client is None or simkl_id is None:
        return None
    detail = client.anime(simkl_id)
    episodes = client.episodes(simkl_id)
    dated = [e for e in episodes if e.get('date')]
    return {
        'relations': len(detail.get('relations') or []),
        'episodes': len(episodes),
        'episodes_with_date': len(dated),
    }


def probe_tmdb(client: tmdb.TmdbClient | None, mapped: tuple[str, int] | None) -> list | None:
    """Return TMDB season structure when the entry maps to a TV show."""
    if client is None or mapped is None or mapped[0] != 'tv':
        return None
    return client.tv_seasons(mapped[1])


def main() -> None:
    """Run every fixture and write ``results.json`` next to this file."""
    entries = fribb.load(CACHE_DIR / 'anime-list-full.json')
    by_mal = fribb.index_by_mal(entries)
    anilist = AniListClient(DiskCache(CACHE_DIR / 'anilist'))
    simkl_id = os.environ.get('SIMKL_CLIENT_ID')
    simkl_client = simkl.SimklClient(simkl_id) if simkl_id else None
    tmdb_token = os.environ.get('TMDB_TOKEN')
    tmdb_client = tmdb.TmdbClient(tmdb_token) if tmdb_token else None
    if simkl_client is None:
        logger.info('SIMKL_CLIENT_ID not set: Simkl probe skipped')
    if tmdb_client is None:
        logger.info('TMDB_TOKEN not set: TMDB probe skipped')

    results = []
    for fixture in FIXTURES:
        logger.info('== %s (MAL %s)', fixture.label, fixture.mal_id)
        root = anilist.media(mal_id=fixture.mal_id)
        if root is None:
            results.append({'label': fixture.label, 'mal_id': fixture.mal_id, 'anilist': None})
            continue
        franchise = build_franchise(anilist.node, root['id'])
        main_rows = []
        for node in franchise.main:
            mapping = by_mal.get(node.mal_id) if node.mal_id else None
            mapped_tmdb = fribb.tmdb_id(mapping) if mapping else None
            main_rows.append(
                {
                    'anilist_id': node.anilist_id,
                    'mal_id': node.mal_id,
                    'format': node.format,
                    'title': node.title,
                    'episodes': node.episodes,
                    'start': node.start,
                    'fribb': bool(mapping),
                    'simkl_id': mapping.get('simkl_id') if mapping else None,
                    'tmdb': mapped_tmdb,
                    'imdb': bool(mapping and mapping.get('imdb_id')),
                    'season': mapping.get('season') if mapping else None,
                    'episode_offset': mapping.get('episode_offset') if mapping else None,
                }
            )
        root_row = next((r for r in main_rows if r['anilist_id'] == root['id']), None)
        results.append(
            {
                'label': fixture.label,
                'mal_id': fixture.mal_id,
                'main': main_rows,
                'extras': len(franchise.extras),
                'airing_schedule': len((root.get('airingSchedule') or {}).get('nodes', [])),
                'simkl': probe_simkl(simkl_client, root_row['simkl_id'] if root_row else None),
                'tmdb_seasons': probe_tmdb(tmdb_client, root_row['tmdb'] if root_row else None),
            }
        )
    out = HERE / 'results.json'
    out.write_text(json.dumps(results, indent=1), encoding='utf-8')
    logger.info('Wrote %s', out)


if __name__ == '__main__':
    main()
```

- [ ] **Step 7: Commit**

```bash
git add spikes/anime_mapping
git commit -m "spike: add probes, runner and report renderer"
```

---

### Task 5: Run the spike

**Files:**
- Produces (not committed): `spikes/anime_mapping/results.json`

- [ ] **Step 1: Get the optional keys**

- Simkl: register an app at https://simkl.com/settings/developer/ and copy the `client_id`.
- TMDB: create an account, then copy the "API Read Access Token" from https://www.themoviedb.org/settings/api.

Export them for this shell only:

```bash
export SIMKL_CLIENT_ID=...
export TMDB_TOKEN=...
```

Skipping a key is allowed; its columns then show `n/a` and the report must say that probe was not run.

- [ ] **Step 2: Run the fixtures**

```bash
cd spikes/anime_mapping && ../.venv/bin/python run.py
```

Expected: one `== <title> (MAL <id>)` line per fixture, then `Wrote .../results.json`. The first run takes a few minutes because of AniList rate limiting; later runs read `.cache/`. If the run stops with an HTTP error, read it: a 401 from Simkl or TMDB means a wrong key, and the fix is to correct the key and rerun (cached AniList data is kept).

- [ ] **Step 3: Render the coverage table**

```bash
cd spikes/anime_mapping && ../.venv/bin/python report.py > ../../table.md && cat ../../table.md
```

Expected: a 12-row markdown table. Keep `table.md` out of git; its contents go into the report in Task 8.

- [ ] **Step 4: Inspect the awkward cases by hand**

Print the main chain of Attack on Titan, Bakemonogatari, Fate/stay night: UBW and Demon Slayer and compare each with the real franchise (MyAnimeList or AniList pages). Note every missing, extra or misordered entry; these notes are evidence for the report.

```bash
cd spikes/anime_mapping && ../.venv/bin/python - <<'EOF'
import json
data = {x['label']: x for x in json.load(open('results.json'))}
for label in ('Attack on Titan', 'Bakemonogatari', 'Fate/stay night: UBW', 'Demon Slayer'):
    print('==', label)
    for entry in data[label]['main']:
        print(entry['format'], entry['start'], entry['title'], entry['episodes'],
              'tmdb=', entry['tmdb'], 'season=', entry['season'], 'offset=', entry['episode_offset'])
EOF
```

---

### Task 6: Compare with the Yamtrack fork's franchise rules

The fork (`~/PycharmProjects/_forks/Yamtrack`, branch `anime-series`, file `src/app/anime_series_builder.py`) already groups MAL relations into seasons and extras. This task re-expresses its rules over AniList relation types and measures where the two groupings disagree on the 12 fixtures. Disagreements are findings for the report, not bugs to fix here.

**Files:**
- Create: `spikes/anime_mapping/tests/test_fork_rules.py`, then `spikes/anime_mapping/fork_rules.py`, then `spikes/anime_mapping/compare_fork.py`

**Interfaces:**
- Consumes: `Node`, `ANIME_FORMATS` and `build_franchise` from `franchise.py`; `AniListClient.node`, `AniListClient.media` from `anilist.py`; `DiskCache`; `FIXTURES`.
- Produces: `ForkGroup` dataclass (`main: set[int]`, `extras: set[int]`, `warnings: list[str]`); `fork_group(fetch, root_anilist_id) -> ForkGroup`.

The fork's rules, as read from its code:

| Fork (MAL) | AniList | Effect |
| --- | --- | --- |
| sequel, prequel | SEQUEL, PREQUEL | destination is a main entry |
| side_story, summary | SIDE_STORY, SUMMARY | destination is an extra |
| parent_story, full_story | PARENT | the current entry becomes an extra; the destination is main |
| alternative_version, spin_off | ALTERNATIVE, SPIN_OFF | not followed; a warning is recorded |

The fork also keeps walking from extras (their sequel and prequel edges are followed), which `build_franchise` deliberately does not.

- [ ] **Step 1: Write the failing tests**

```python
from fork_rules import fork_group
from franchise import Node


def make(anilist_id, edges, fmt='TV', year=2020):
    return Node(
        anilist_id=anilist_id,
        mal_id=anilist_id + 1000,
        format=fmt,
        title=f'n{anilist_id}',
        episodes=12,
        start=(year, 1, 1),
        edges=edges,
    )


def fetcher(nodes):
    return {node.anilist_id: node for node in nodes}.get


def test_sequel_is_main_and_side_story_is_extra():
    nodes = [
        make(1, [('SEQUEL', 2, 1002, 'TV', 'ANIME'), ('SIDE_STORY', 3, 1003, 'OVA', 'ANIME')]),
        make(2, [('PREQUEL', 1, 1001, 'TV', 'ANIME')]),
        make(3, [('PARENT', 1, 1001, 'TV', 'ANIME')], fmt='OVA'),
    ]
    group = fork_group(fetcher(nodes), 1)
    assert group.main == {1, 2}
    assert group.extras == {3}


def test_alternative_and_spin_off_are_skipped_with_a_warning():
    nodes = [
        make(1, [('ALTERNATIVE', 2, 1002, 'TV', 'ANIME'), ('SPIN_OFF', 3, 1003, 'TV', 'ANIME')]),
        make(2, []),
        make(3, []),
    ]
    group = fork_group(fetcher(nodes), 1)
    assert group.main == {1}
    assert group.extras == set()
    assert len(group.warnings) == 2


def test_parent_edge_marks_the_current_entry_as_extra():
    nodes = [
        make(1, [('PARENT', 2, 1002, 'TV', 'ANIME')], fmt='OVA'),
        make(2, []),
    ]
    group = fork_group(fetcher(nodes), 1)
    assert group.extras == {1}
    assert group.main == {2}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest tests/test_fork_rules.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fork_rules'`.

- [ ] **Step 3: Implement `fork_rules.py`**

```python
"""The Yamtrack fork's relation rules, re-expressed over AniList relation types.

The fork walks MAL relations (``anime_series_builder._discover_group``). AniList
relation types derive from MAL, so the mapping is: sequel/prequel -> SEQUEL and
PREQUEL; side_story/summary -> SIDE_STORY and SUMMARY; parent_story/full_story ->
PARENT; alternative_version/spin_off -> ALTERNATIVE and SPIN_OFF.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from franchise import ANIME_FORMATS, Node

MAIN = {'SEQUEL', 'PREQUEL'}
EXTRA = {'SIDE_STORY', 'SUMMARY'}
PARENT = {'PARENT'}
DUBIOUS = {'ALTERNATIVE', 'SPIN_OFF'}


@dataclass
class ForkGroup:
    """Result of the fork's traversal: main ids, extra ids, and warnings."""

    main: set[int] = field(default_factory=set)
    extras: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def fork_group(fetch: Callable[[int], Node | None], root_anilist_id: int) -> ForkGroup:
    """Walk the relation graph the way the fork's ``_discover_group`` does.

    Unlike ``build_franchise``, extras are expanded too (their sequel and
    prequel edges are followed), which is how the fork behaves.

    Args:
        fetch: Returns the ``Node`` for an AniList id, or None if unavailable.
        root_anilist_id: AniList id to start from.

    Returns:
        The ids classified as main or extra, plus warnings for skipped edges.
    """
    is_extra: dict[int, bool] = {}
    warnings: list[str] = []
    visited = {root_anilist_id}
    queue: list[tuple[int, bool]] = [(root_anilist_id, False)]
    while queue:
        current, extra = queue.pop(0)
        node = fetch(current)
        if node is None:
            continue
        is_extra[current] = is_extra.get(current, False) or extra
        for relation, other, _mal, fmt, media_type in node.edges:
            if media_type != 'ANIME' or fmt not in ANIME_FORMATS:
                continue
            if relation in DUBIOUS:
                warnings.append(f'{node.anilist_id} -{relation}-> {other}')
                continue
            target_extra = relation in EXTRA
            if relation in PARENT:
                is_extra[current] = True
            if (relation in MAIN or target_extra or relation in PARENT) and other not in visited:
                visited.add(other)
                queue.append((other, target_extra))
    return ForkGroup(
        main={i for i, extra in is_extra.items() if not extra},
        extras={i for i, extra in is_extra.items() if extra},
        warnings=warnings,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest -q`
Expected: all tests pass (12).

- [ ] **Step 5: Write `compare_fork.py`**

```python
"""Compare ``build_franchise`` with the fork's rules on every fixture."""

from pathlib import Path

from anilist import AniListClient
from cache import DiskCache
from fixtures import FIXTURES
from fork_rules import fork_group
from franchise import build_franchise

CACHE_DIR = Path(__file__).parent / '.cache'


def main() -> None:
    """Print, per fixture, the entries on which the two groupings disagree."""
    client = AniListClient(DiskCache(CACHE_DIR / 'anilist'))
    print('| Title | ours main | fork main | only ours | only fork | extras ours/fork | skipped edges |')
    print('| --- | --- | --- | --- | --- | --- | --- |')
    for fixture in FIXTURES:
        root = client.media(mal_id=fixture.mal_id)
        if root is None:
            print(f'| {fixture.label} | not found | | | | | |')
            continue
        ours = build_franchise(client.node, root['id'])
        theirs = fork_group(client.node, root['id'])
        ours_main = {n.anilist_id for n in ours.main}
        ours_extra = {e[0] for e in ours.extras}
        print(
            f'| {fixture.label} | {len(ours_main)} | {len(theirs.main)} '
            f'| {sorted(ours_main - theirs.main)} | {sorted(theirs.main - ours_main)} '
            f'| {len(ours_extra)}/{len(theirs.extras)} | {len(theirs.warnings)} |'
        )


if __name__ == '__main__':
    main()
```

- [ ] **Step 6: Run the comparison**

Run: `cd spikes/anime_mapping && ../.venv/bin/python compare_fork.py > ../../fork-table.md && cat ../../fork-table.md`
Expected: a 12-row table. The fork's walk expands extras, so it reaches entries the main run never fetched; the first run makes extra AniList requests (a few minutes with rate limiting). Keep `fork-table.md` out of git.

Reference result from 2026-10-10 (your numbers may differ if AniList data changed): 9 of 12 fixtures have identical main chains. Attack on Titan: the fork adds two entries (AniList 20691, 20692) by walking through extras. Steins;Gate and Jujutsu Kaisen: `build_franchise` has one entry the fork lacks (11577, 204066). Fate/stay night: the fork skips 37 ALTERNATIVE/SPIN_OFF edges.

- [ ] **Step 7: Classify every disagreement**

For each id in the "only ours" and "only fork" columns, open `https://anilist.co/anime/<id>` and decide which grouping is right: a real season of the franchise, an extra, or an unrelated work. Write one line per id (id, title, format, verdict) into the notes for the report.

- [ ] **Step 8: Commit**

```bash
git add spikes/anime_mapping/fork_rules.py spikes/anime_mapping/compare_fork.py spikes/anime_mapping/tests/test_fork_rules.py
git commit -m "spike: compare franchise grouping with the Yamtrack fork rules"
```

---

### Task 7: Episode source without Simkl (TMDB-only variant)

Question: can episode lists for anime come from TMDB alone, bridged by Fribb and AniList, closely enough that Simkl is not needed? Two allocations are scored against the AniList start date of each TV/ONA entry:

- `concat`: glue every non-special TMDB season into one stream and slice it by the AniList episode counts in release order.
- `fribb`: use the Fribb `season` and `episode_offset`.

Simkl is scored with the same guard. The guard checks the first episode's air date (within 2 days) and the allocation length against the AniList count; a pass still does not prove that every episode lines up. TMDB season 0 (specials) is fetched so that Fribb mappings to specials can be scored.

**Files:**
- Create: `spikes/anime_mapping/tests/test_episodes_tmdb.py`, then `spikes/anime_mapping/episodes_tmdb.py`

**Interfaces:**
- Consumes: `results.json` from Task 5 (needs `TMDB_TOKEN` and `SIMKL_CLIENT_ID` set); `DiskCache`; `simkl.SimklClient.episodes`.
- Produces: `allocate_concat(flat, counts) -> list[list[dict]]`, `allocate_fribb(seasons, season, offset, count) -> list[dict]`, `date_matches(air_date, start, days=14) -> bool`, and a markdown table on stdout.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

from episodes_tmdb import allocate_concat, allocate_fribb, date_matches


def ep(n, day):
    return {'episode_number': n, 'air_date': f'2020-01-{day:02d}'}


def test_concat_slices_by_cumulative_counts_and_gives_the_rest_to_open_ended():
    flat = [ep(i, i) for i in range(1, 8)]
    parts = allocate_concat(flat, [3, 2, None])
    assert [len(p) for p in parts] == [3, 2, 2]
    assert parts[1][0]['episode_number'] == 4


def test_concat_runs_short_when_tmdb_has_fewer_episodes():
    flat = [ep(i, i) for i in range(1, 4)]
    parts = allocate_concat(flat, [2, 5])
    assert [len(p) for p in parts] == [2, 1]


def test_fribb_uses_season_and_offset():
    seasons = {1: [ep(i, i) for i in range(1, 13)], 2: [ep(i, i) for i in range(1, 5)]}
    assert [e['episode_number'] for e in allocate_fribb(seasons, 1, 8, 3)] == [9, 10, 11]
    assert allocate_fribb(seasons, 9, 0, 3) == []
    assert allocate_fribb(seasons, None, 0, 3) == []


def test_date_matches_within_tolerance():
    assert date_matches('2020-01-10', (2020, 1, 1), days=14)
    assert not date_matches('2020-03-10', (2020, 1, 1), days=14)
    assert not date_matches(None, (2020, 1, 1), days=14)
    assert not date_matches('2020-01-10', (2020, None, None), days=14)


def test_accepts_only_tight_date_and_matching_length():
    from episodes_tmdb import accepts

    part = [ep(i, i) for i in range(1, 13)]
    assert accepts(part, 12, (2020, 1, 1))
    assert accepts(part, None, (2020, 1, 1))
    assert accepts(part, 12, (2019, 12, 30))  # two days earlier
    assert not accepts(part, 12, (2019, 12, 20))  # eleven days earlier
    assert not accepts(part, 13, (2020, 1, 1))  # one episode short
    assert not accepts([], 12, (2020, 1, 1))


def test_first_accepted_picks_the_first_candidate_that_passes():
    from episodes_tmdb import first_accepted

    bad = [ep(i, 20 + i) for i in range(1, 4)]
    good = [ep(i, i) for i in range(1, 4)]
    assert first_accepted([bad, good], 3, (2020, 1, 1)) == 1
    assert first_accepted([bad, bad], 3, (2020, 1, 1)) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest tests/test_episodes_tmdb.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'episodes_tmdb'`.

- [ ] **Step 3: Implement `episodes_tmdb.py`**

```python
"""Variant 1: build anime episode lists from TMDB alone and score them.

Two allocations are compared against the AniList start date of each entry:
``concat`` glues every non-special TMDB season into one stream and slices it by
the AniList episode counts; ``fribb`` uses the Fribb season and offset.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
import simkl
from cache import DiskCache

HERE = Path(__file__).parent
BASE = 'https://api.themoviedb.org/3'


def allocate_concat(flat: list[dict], counts: list[int | None]) -> list[list[dict]]:
    """Slice one episode stream by consecutive entry counts.

    Args:
        flat: Episodes in watching order.
        counts: Episode count per entry in release order; None takes the rest.

    Returns:
        One list of episodes per entry (shorter when the stream runs out).
    """
    parts = []
    pos = 0
    for count in counts:
        end = len(flat) if count is None else pos + count
        parts.append(flat[pos:end])
        pos = min(end, len(flat))
    return parts


def allocate_fribb(
    seasons: dict[int, list[dict]], season: int | None, offset: int, count: int | None
) -> list[dict]:
    """Take ``count`` episodes of a TMDB season starting after ``offset``."""
    if season is None or season not in seasons:
        return []
    episodes = seasons[season][offset:]
    return episodes if count is None else episodes[:count]


def date_matches(air_date: str | None, start: tuple, days: int = 14) -> bool:
    """Return True when ``air_date`` is within ``days`` of a (y, m, d) start."""
    if not air_date or None in start:
        return False
    return abs(date.fromisoformat(air_date) - date(*start)) <= timedelta(days=days)


def accepts(
    part: list[dict], expected: int | None, start: tuple, days: int = 2
) -> bool:
    """Return True when an allocation passes the guard.

    The first episode must air within ``days`` of the AniList start date, and the
    allocation must have exactly ``expected`` episodes when that is known. The
    length check catches off-by-one shifts that a date window alone lets through.
    """
    if not part:
        return False
    if expected is not None and len(part) != expected:
        return False
    return date_matches(part[0].get('air_date'), start, days)


def first_accepted(
    candidates: list[list[dict]], expected: int | None, start: tuple, days: int = 2
) -> int | None:
    """Return the index of the first candidate that passes ``accepts``."""
    for index, part in enumerate(candidates):
        if accepts(part, expected, start, days):
            return index
    return None


class Tmdb:
    """Cached TMDB season fetcher."""

    def __init__(self, token: str, cache: DiskCache) -> None:
        self._http = httpx.Client(timeout=30, headers={'Authorization': f'Bearer {token}'})
        self._cache = cache

    def seasons(self, tv_id: int) -> dict[int, list[dict]]:
        """Return ``{season_number: episodes}``, season 0 (specials) included."""
        key = f'tmdb_tv_all_{tv_id}'
        cached = self._cache.get(key)
        if cached is None:
            response = self._http.get(f'{BASE}/tv/{tv_id}')
            response.raise_for_status()
            show = response.json()
            cached = {}
            for s in show.get('seasons', []):
                resp = self._http.get(f'{BASE}/tv/{tv_id}/season/{s["season_number"]}')
                resp.raise_for_status()
                cached[str(s['season_number'])] = resp.json().get('episodes', [])
            self._cache.put(key, cached)
        return {int(k): v for k, v in cached.items()}


def main() -> None:
    """Score the allocations for every TV/ONA entry in ``results.json``.

    Columns: ``concat`` and ``fribb`` count the loose check (first air date within
    14 days, the original measure); ``hybrid`` is the guard (Fribb, then concat,
    each accepted only by ``accepts``: first episode within 2 days and the
    allocation length equal to the AniList count); ``simkl`` applies the same
    guard to the Simkl episode list; ``fit`` counts Fribb season/offset pairs that
    fit inside the TMDB season length.
    """
    results = json.loads((HERE / 'results.json').read_text(encoding='utf-8'))
    cache = DiskCache(HERE / '.cache' / 'variant1')
    tmdb = Tmdb(os.environ['TMDB_TOKEN'], cache)
    sk = simkl.SimklClient(os.environ['SIMKL_CLIENT_ID'])
    keys = ('n', 'concat14', 'fribb14', 'hybrid', 'simkl', 'fit', 'fit_known')
    totals = dict.fromkeys(keys, 0)
    print('| Title | entries | concat (14d) | fribb (14d) | hybrid (guard) | simkl (guard) | fribb fits season | unresolved by hybrid |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- |')
    for fixture in results:
        groups: dict[int, list[dict]] = {}
        for entry in fixture.get('main') or []:
            if entry['format'] in ('TV', 'ONA') and entry['tmdb'] and entry['tmdb'][0] == 'tv':
                groups.setdefault(entry['tmdb'][1], []).append(entry)
        row = dict.fromkeys(keys, 0)
        unresolved = []
        for tv_id, entries in groups.items():
            seasons = tmdb.seasons(tv_id)
            flat = [e for s in sorted(seasons) if s >= 1 for e in seasons[s]]
            entries.sort(key=lambda e: tuple(x or 0 for x in e['start']))
            concat = allocate_concat(flat, [e['episodes'] for e in entries])
            for entry, cpart in zip(entries, concat):
                start = tuple(entry['start'])
                expected = entry['episodes']
                season = (entry['season'] or {}).get('tmdb')
                offset = ((entry['episode_offset'] or {}).get('tmdb')) or 0
                fpart = allocate_fribb(seasons, season, offset, expected)
                row['n'] += 1
                row['concat14'] += bool(cpart) and date_matches(cpart[0].get('air_date'), start)
                row['fribb14'] += bool(fpart) and date_matches(fpart[0].get('air_date'), start)
                if expected and season in seasons:
                    row['fit_known'] += 1
                    row['fit'] += offset + expected <= len(seasons[season])
                if first_accepted([fpart, cpart], expected, start) is not None:
                    row['hybrid'] += 1
                else:
                    unresolved.append(entry['title'][:26])
                eps = cache.get(f'simkl_{entry["simkl_id"]}') if entry['simkl_id'] else []
                if eps is None:
                    eps = sk.episodes(entry['simkl_id'])
                    cache.put(f'simkl_{entry["simkl_id"]}', eps)
                real = [e for e in eps if e.get('type') == 'episode']
                spart = [{'air_date': (e.get('date') or '')[:10] or None} for e in real]
                row['simkl'] += accepts(spart, expected, start)
        for key in keys:
            totals[key] += row[key]
        print(
            f'| {fixture["label"]} | {row["n"]} | {row["concat14"]} | {row["fribb14"]} '
            f'| {row["hybrid"]} | {row["simkl"]} | {row["fit"]}/{row["fit_known"]} '
            f'| {"; ".join(unresolved)} |'
        )
    print(
        f'| TOTAL | {totals["n"]} | {totals["concat14"]} | {totals["fribb14"]} '
        f'| {totals["hybrid"]} | {totals["simkl"]} | {totals["fit"]}/{totals["fit_known"]} | |'
    )


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd spikes/anime_mapping && ../.venv/bin/python -m pytest -q`
Expected: all tests pass (18).

- [ ] **Step 5: Run the scoring**

Run: `cd spikes/anime_mapping && ../.venv/bin/python episodes_tmdb.py > ../../variant1-table.md && cat ../../variant1-table.md`
Expected: a table with one row per fixture and a TOTAL row. The first run downloads every TMDB season of the fixtures and takes about a minute. Keep `variant1-table.md` out of git.

Reference result from 2026-10-18 (38 TV/ONA entries): loose 14-day check, `concat` 29 and `fribb` 28; the guard (first episode within 2 days and allocation length equal to the AniList count), `hybrid` 29 and Simkl 28; Fribb season and offset fit the TMDB season length for 29 of 34 checkable entries. The loose check overstates the result: an independent review found that it accepts allocations shifted by one episode or of the wrong length, so the report quotes the guard numbers.

- [ ] **Step 6: Inspect every miss**

For each name in the "notes" column, look at the TMDB season list and the AniList page and write one line (title, cause, whether a rule would fix it, for example "reorder by air date" or "needs manual override") for the report.

- [ ] **Step 7: Commit**

```bash
git add spikes/anime_mapping/episodes_tmdb.py spikes/anime_mapping/tests/test_episodes_tmdb.py
git commit -m "spike: score TMDB-only episode allocation against Simkl"
```

---

### Task 8: Write the report and update the spec

**Files:**
- Create: `docs/superpowers/spikes/2026-10-10-anime-source-and-id-mapping.md`
- Modify: `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 4 and 13)

- [ ] **Step 1: Write the report**

Create the report with exactly these headings, filling each from the run:

1. `## Question`: which providers supply anime franchise structure, episode lists with air dates and TMDB bridges.
2. `## Method`: the 12 fixtures, the sources probed, which keys were available, the run date.
3. `## Results`: paste the table from `table.md`, then the hand-check notes from Task 5, Step 4, then the fork comparison from Task 6 (`fork-table.md` and the per-id verdicts), then the TMDB-only scoring from Task 7 (`variant1-table.md` and the notes on every miss).
4. `## Findings`: answer each of these in one or two sentences with numbers from the table:
   - What share of main-chain entries has a Fribb record, a Simkl id, a TMDB id? Which fixtures are weakest?
   - Does the Fribb `season` and `episode_offset` data let an anime cour map onto a TMDB season (Attack on Titan is the test)?
   - Is the AniList franchise chain right for the four hand-checked titles? Where does it differ from the Yamtrack fork's rules (Task 6), and which side is right per entry?
   - Where do episode air dates come from for aired and for upcoming episodes: AniList `airingSchedule` (upcoming only), Simkl `date`, TMDB season episodes? Which are missing?
5. `## Decision rules and outcome`: apply these rules and state the result.
   - Franchise source: use AniList if its chain matches the real franchise for at least 10 of the 12 fixtures; otherwise use the better of AniList and Simkl `relations`.
   - Simkl necessity: list every field the product needs per episode (title, overview, air date, still) and the source that supplies it without Simkl. Simkl is only worth keeping if some field for a meaningful share of main-chain entries has no other source.
   - Episode source: prefer TMDB episodes for entries Fribb maps to a TMDB TV season; use Simkl or AniList only for entries without a TMDB mapping. Simkl may only be used if the project also offers Simkl login and sync (Simkl API rule 2), which is outside the MVP scope, so a Simkl dependency needs its own explicit decision.
   - ID bridge: the Fribb dataset, refreshed on a schedule, is the bridge unless coverage is below 90% of main-chain entries.
6. `## Consequences for the spec`: the exact edits to spec sections 4 and 13, or "none" with the reason.

- [ ] **Step 2: Apply the spec edits**

Edit section 4 of the spec (the provider table, the id-mapping paragraph) and section 13 (remove the finished spike item, add anything the spike opened) to match the report's outcome. Keep the spec self-consistent: if Simkl or MAL is dropped, remove them from sections 1, 2 and 6 as well.

- [ ] **Step 3: Check the spec for stale provider mentions**

Run: `grep -n -i "simkl\|MAL\b\|MyAnimeList" docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`
Expected: every remaining mention matches the report's decision.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/spikes docs/superpowers/specs
git commit -m "docs: record anime source spike outcome"
```

- [ ] **Step 5: Ask for review**

Tell the user the report path, the recommendation, and the exact spec edits made; wait for approval before plan 2 is written.
