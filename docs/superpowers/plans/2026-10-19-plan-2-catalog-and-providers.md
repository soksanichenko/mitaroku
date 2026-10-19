# Plan 2: Catalog and Providers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill a shared catalog of movies, TV series and anime franchises from TMDB, AniList and the Fribb id bridge, with episodes, air dates and translations, kept fresh by background jobs. Nothing user-facing yet: this plan ends at services, tasks and management commands that the screens of plan 3 call.

**Architecture:** Two new Django apps. `providers` holds everything that talks to the outside: a resilient HTTP layer (Redis cache with a stale copy, single-flight lock, rate limiter, retries, stale-on-error), typed TMDB and AniList clients, the Fribb dataset in a table, and merged search. `catalog` holds the models and the sync that turns provider data into rows. An anime `Title` is a whole franchise; its `Season` rows are the AniList entries, and TMDB episodes are accepted for an entry only when they pass the date-and-length guard measured in the spike. Every sync fetches first and writes in one transaction, so a provider outage leaves the catalog untouched.

**Tech Stack:** Python 3.12, Django 5.2.18, PostgreSQL 17, Redis 7, Huey 3.4.0, httpx 0.28.1, pydantic-settings 2.15.0, pytest 9.1.1, ruff 0.17.0.

**Spec:** `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 3 and 4; section 11 for translations). Source of the anime rules: `docs/superpowers/spikes/2026-10-10-anime-source-and-id-mapping.md`, including its "Requirements carried to plan 2".

## Global Constraints

- Django 5.2 LTS, Python 3.12, PostgreSQL, Redis, Huey on Redis (spec sections 2 and 12).
- Providers: TMDB (movies, TV, anime episodes, MVP rating), AniList (anime entries, relations, schedule, anime rating; no key), Fribb/anime-lists (id bridge with season and episode offset). Simkl, MAL and TVDB are not used (spec sections 1, 4 and 6).
- API keys are global server-side settings; the TMDB token is a `SecretStr` and never appears in logs, cache keys or `repr` output.
- Cache layers (spec section 4): Postgres is the durable store; Redis holds response TTL copies; a per-provider rate limiter; a single-flight lock; stale-on-error with Huey retry.
- A TMDB allocation of episodes to an AniList entry is accepted only when the first episode airs within 2 days of the AniList start date and the allocation length equals the AniList episode count (when known). Everything else gets the degraded mode: episode count, start date and rating from AniList, per-episode dates only when the airing schedule has one node per episode, no titles (spec section 4).
- `TitleTranslation` is a lazily filled persistent cache; search results are never persisted; empty values are never stored; the fallback chain is user language, original, English (spec sections 3 and 11).
- Single quotes for Python strings (docstrings use triple double quotes); type annotations on public functions; Google-style docstrings; PEP 8 with 88 columns preferred.
- Log with `logging` using %-formatting, not `print()` (management commands write to `self.stdout`).
- Everything committed (code, comments, commit messages, docs) is in English.
- Every YAML file ends with `...` and a blank line, except GitHub workflow files.

## Decisions

- **No screens in this plan.** Search, "Add", title pages and the degraded-episode UX belong to plan 3 with tracking, because adding a title and showing progress are one flow. This plan exposes what they need: `providers.search.search`, `catalog.sync.get_or_create_title`, `catalog.tasks.enqueue_sync`, `catalog.translations.localized_title`, and `Season.episode_source`.
- **An anime `Title` is a franchise.** Its seasons are the AniList entries: the sequel/prequel chain first (chronological, unknown dates last), then extras. Every AniList id of the franchise becomes an `ExternalId`, so adding any entry finds the existing title. TMDB movie and TV ids are separate `ExternalId` providers because their number spaces collide.
- **The PARENT rule (spec section 13) is decided.** A `MOVIE` or `SPECIAL` with a PARENT edge to an anime entry is an extra, and the walk still passes through it, because AniList sometimes links one season to the next only through such a film. On the spike fixtures this changes two entries: Jujutsu Kaisen 204066 (a recap, correctly demoted) and Steins;Gate 11577 (a canon film, now listed as an extra instead of lost). Known miss: AniList 192809 (the other Jujutsu Kaisen compilation) is linked by ALTERNATIVE and stays in the main chain.
- **Cost asymmetry behind that rule.** A demoted film is still listed under extras; a recap promoted into the main chain pollutes the season list and the progress percentage.
- **Fribb lives in Postgres** (`providers.AnimeMapping`, keyed by AniList id), refreshed by the weekly Huey task (Monday 03:30 UTC) and by `manage.py refresh_mapping`. A download that fails or looks truncated (fewer than 10,000 entries) never replaces the table. Resolves the "refresh schedule" item of spec section 13.
- **Episodes and seasons are never deleted by a sync.** Plan 3's watch events will point at episodes. Seasons are matched by AniList id, renumbered through unique negative numbers, and a season that leaves the franchise is kept as the last extras.
- **The degraded mode is data only.** `Season.episode_source` is `tmdb`, `anilist` or `none`. A TMDB allocation that is later rejected removes that season's episode translations (never the episodes). A TMDB outage does not degrade anything: the sync raises and Huey retries it.
- **Dates.** AniList start dates and airing timestamps are turned into the day in Japan (UTC+9, fixed offset, no tzdata dependency). Providers give dates, not times (spec section 7).
- **Languages.** A sync stores English text. Another language is fetched from TMDB the first time `ensure_title_translation` or `ensure_episode_translations` is called for it. Anime titles use TMDB translations only when Fribb maps the franchise to a TMDB show. The anime `original_title` is the romaji of the first main entry.
- **Ratings.** TMDB `vote_average` for movies and TV (none until someone voted); the mean of the main entries' AniList scores divided by 10 for anime.
- **Poster URLs are stored as full URLs.** Whether the browser hotlinks the CDN or a server proxy is still open (spec section 13) and is decided in plan 3.
- **Not here:** garbage collection of orphaned translations (needs library entries, plan 3), notification baselines and "release date changed" detection (plan 4), priority of watching and planned titles in the sync queue (plan 3 adds it to `sync_due_titles`).

## Review Focus

These are the inputs a person running the service is most likely to hit. Each line names the test that pins it.

- A provider outage during a sync must leave the catalog exactly as it was and must not turn episode titles into the degraded mode: `test_a_provider_outage_leaves_the_catalog_untouched` (Task 8); the HTTP layer serves a stale copy instead of failing: `test_stale_copy_is_served_when_the_provider_is_down` (Task 2).
- An allocation TMDB gets wrong (shifted dates, off-by-one length, a season Fribb invented) must never show another episode's title: `test_guard_rejects_an_off_by_one_length`, `test_tmdb_having_fewer_episodes_than_anilist_is_rejected` (Task 7), `test_a_rejected_allocation_removes_the_tmdb_titles_but_keeps_the_episodes` (Task 8).
- Adding any entry of a franchise must find the existing title, and ids that already belong to another title must not be taken over: `test_any_entry_finds_the_franchise_title`, `test_ids_that_belong_to_another_title_are_not_stolen` (Task 8).
- A freshly announced entry (no format, a start date with only a year, no episode count) must not crash the sync or vanish: `test_edge_with_a_null_format_is_kept_as_an_unresolved_extra`, `test_announced_entry_with_partial_data` (Task 5), `test_unknown_start_date_means_no_allocation` (Task 7).
- A re-sync must keep the episode rows (watch events will refer to them), and renumbering seasons must not trip the unique constraint: `test_resync_is_idempotent`, `test_a_new_entry_in_the_middle_renumbers_without_touching_existing_rows`, `test_an_entry_that_left_the_franchise_is_parked_as_an_extra` (Task 8), `test_an_episode_tmdb_stops_listing_is_kept` (Task 8).
- A missing TMDB token must not stop anime from syncing (degraded) and must give a clear error for movies and TV; a truncated Fribb download must not replace the table; two workers must not sync one title at once: `test_without_a_tmdb_client_every_entry_is_degraded`, `test_missing_tmdb_token_is_a_provider_error` (Task 8), `test_a_truncated_dataset_is_refused` (Task 6), `test_a_title_being_synced_elsewhere_is_left_alone` (Task 9).

## File Structure

| Path | Responsibility |
| --- | --- |
| `sources/mitaroku/config.py` | `ProvidersConfig`: TMDB token, contact, request intervals, Fribb URL |
| `sources/providers/http.py` | `ProviderHttp`: Redis cache with stale copy, single-flight lock, rate limiter, retries |
| `sources/providers/clients.py` | Factories: Redis connection, `get_tmdb`, `get_anilist` |
| `sources/providers/types.py` | `SearchHit`, shared by both search clients |
| `sources/providers/tmdb.py` | TMDB client and its dataclasses |
| `sources/providers/anilist.py` | AniList GraphQL client and its dataclasses |
| `sources/providers/models.py`, `mapping.py`, `tasks.py` | Fribb table, loader, weekly refresh task |
| `sources/providers/search.py` | Merged search over TMDB and AniList |
| `sources/providers/management/commands/refresh_mapping.py` | Load the Fribb dataset on demand |
| `sources/catalog/models.py` | `Title`, `ExternalId`, `Season`, `Episode`, translations |
| `sources/catalog/admin.py`, `migrations/0002_*.py` | Read-only admin for the `app-admins` group |
| `sources/catalog/franchise.py` | Group AniList entries into main chain and extras |
| `sources/catalog/episodes.py` | The guard and the TMDB-to-AniList episode allocation |
| `sources/catalog/sync/` | `common.py` helpers, `movies.py`, `tv.py`, `anime.py`, `__init__.py` entry points |
| `sources/catalog/translations.py` | Fallback chain and lazy translation fill |
| `sources/catalog/tasks.py` | Huey tasks: `sync_title_task`, `sync_due_titles` |
| `sources/catalog/management/commands/sync_title.py` | Add and sync a title by provider id |
| `tests/` | One module per concern; `fakes.py` holds in-memory provider clients |

All commands run from the repository root with the virtualenv active (`source .venv/bin/activate`) and the development containers up (`docker compose -f docker-compose.dev.yml up -d --wait`). `manage.py` reads `config.yaml` from the working directory, so run it from the root too. Tests never touch the network.

---

### Task 1: Dependencies and provider configuration

**Files:**
- Modify: `sources/requirements.txt`, `sources/mitaroku/config.py`, `config.example.yaml`, `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.providers: ProvidersConfig` with `tmdb_token: SecretStr | None` (default None), `contact: str` (default `''`), `tmdb_min_interval: float` (0.05), `anilist_min_interval: float` (2.0, AniList allows 30 requests a minute), `fribb_url: str`. Environment names follow the existing scheme, for example `MITAROKU_PROVIDERS__TMDB_TOKEN`. `httpx` becomes a dependency.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_provider_defaults_need_no_key():
    providers = Settings().providers
    assert providers.tmdb_token is None
    assert providers.anilist_min_interval == 2.0
    assert providers.fribb_url.endswith('/anime-list-full.json')


def test_provider_token_comes_from_the_environment_and_stays_secret(monkeypatch):
    monkeypatch.setenv('MITAROKU_PROVIDERS__TMDB_TOKEN', 'tmdb-secret')
    providers = Settings().providers
    assert providers.tmdb_token.get_secret_value() == 'tmdb-secret'
    assert 'tmdb-secret' not in repr(providers)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_config.py
```

Expected: 2 failed (`AttributeError: 'Settings' object has no attribute 'providers'`), 6 passed.

- [ ] **Step 3: Add the dependency**

In `sources/requirements.txt` add this line after `Django==5.2.18` (the file stays sorted):

```text
httpx==0.28.1
```

Then install it:

```bash
pip install -r requirements.txt
```

- [ ] **Step 4: Add the configuration model**

In `sources/mitaroku/config.py`, insert this class between `RedisConfig` and `AuthConfig`:

```python
class ProvidersConfig(BaseModel):
    """Settings for the external metadata providers.

    Attributes:
        tmdb_token: TMDB API read access token (Bearer). Without it the TMDB
            client refuses to start; AniList and Fribb need no key.
        contact: Address or URL sent in the ``User-Agent`` so providers can reach
            the operator. Empty sends the bare application name.
        tmdb_min_interval: Minimum seconds between TMDB requests.
        anilist_min_interval: Minimum seconds between AniList requests.
        fribb_url: Where the Fribb/anime-lists dataset is downloaded from.
    """

    tmdb_token: SecretStr | None = None
    contact: str = ''
    tmdb_min_interval: float = 0.05
    anilist_min_interval: float = 2.0
    fribb_url: str = (
        'https://raw.githubusercontent.com/Fribb/anime-lists/master/'
        'anime-list-full.json'
    )
```

and add the field to `Settings`, after `redis`:

```python
    providers: ProvidersConfig = ProvidersConfig()
```

In `config.example.yaml` add this block after the `redis:` block and before the closing `...`:

```yaml

providers:
  tmdb_token: null  # TMDB API read access token; required to search or sync TMDB titles
  contact: ''  # e-mail or URL for the User-Agent, so providers can reach you
  tmdb_min_interval: 0.05  # seconds between TMDB requests
  anilist_min_interval: 2.0  # seconds between AniList requests (its limit is 30 a minute)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_config.py
```

Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources/requirements.txt sources/mitaroku/config.py config.example.yaml tests/test_config.py
git commit -m "feat: add provider configuration and the httpx dependency"
```

---

### Task 2: Resilient provider HTTP

**Files:**
- Create: `sources/providers/__init__.py`, `sources/providers/apps.py`, `sources/providers/http.py`, `sources/providers/clients.py`, `tests/test_providers_http.py`
- Modify: `sources/mitaroku/settings.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `settings.REDIS_*` from plan 1.
- Produces:
  - `providers.http.ProviderHttp(name, *, client, redis, min_interval=0.0, ttl=21600, stale_ttl=604800, max_attempts=4, sleep=time.sleep, prefix='mitaroku:providers:')` with `get_json(url, *, params=None, ttl=None) -> object | None` and `post_json(url, *, json, ttl=None) -> object | None`. Both return the parsed JSON, or None on HTTP 404, and raise `ProviderError` when the request fails and no stale copy exists.
  - `providers.http.ProviderError(Exception)` and `parse_retry_after(value, now=None) -> float | None` (seconds or an HTTP date).
  - `providers.clients.connect_redis() -> Redis` (decoded responses).
  - Test fixtures `redis_client`, `redis_prefix` and `provider_http(handler, name='test', **kwargs)` (a `ProviderHttp` over `httpx.MockTransport` with a no-op sleep).

Behaviour: a fresh response is served from Redis for `ttl` seconds; a second copy lives `stale_ttl` seconds and is served when the provider fails. Concurrent callers for one key share one request (Redis lock, re-checked after acquiring). Requests to one provider are at least `min_interval` apart. 429, 5xx and network errors are retried with a 1, 2, 4 second backoff, or for `Retry-After` the stated delay (capped at 120 s); other 4xx errors are not retried.

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_providers_http.py`:**

```python
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from providers.http import ProviderError, ProviderHttp, parse_retry_after

URL = 'https://provider.example/items/1'


def make_http(handler, redis_client, redis_prefix, sleeps=None, **kwargs):
    """Build a ProviderHttp over a mock transport; ``sleeps`` records waits."""
    sleeps = sleeps if sleeps is not None else []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ProviderHttp(
        'test',
        client=client,
        redis=redis_client,
        prefix=redis_prefix,
        sleep=sleeps.append,
        **kwargs,
    )


def test_second_call_is_served_from_the_cache(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 1})

    http = make_http(handler, redis_client, redis_prefix)
    assert http.get_json(URL) == {'id': 1}
    assert http.get_json(URL) == {'id': 1}
    assert len(calls) == 1


def test_query_parameters_are_part_of_the_cache_key(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'q': request.url.params['q']})

    http = make_http(handler, redis_client, redis_prefix)
    assert http.get_json(URL, params={'q': 'a'}) == {'q': 'a'}
    assert http.get_json(URL, params={'q': 'b'}) == {'q': 'b'}
    assert len(calls) == 2


def test_not_found_returns_none_and_is_cached(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    http = make_http(handler, redis_client, redis_prefix)
    assert http.get_json(URL) is None
    assert http.get_json(URL) is None
    assert len(calls) == 1


def test_post_body_is_part_of_the_cache_key(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(request.content)
        return httpx.Response(200, json={'ok': True})

    http = make_http(handler, redis_client, redis_prefix)
    http.post_json(URL, json={'query': 'a'})
    http.post_json(URL, json={'query': 'a'})
    http.post_json(URL, json={'query': 'b'})
    assert len(calls) == 2


def test_rate_limited_request_waits_for_retry_after_seconds(redis_client, redis_prefix):
    responses = iter(
        [
            httpx.Response(429, headers={'Retry-After': '7'}),
            httpx.Response(200, json=[1]),
        ]
    )
    sleeps = []
    http = make_http(
        lambda request: next(responses), redis_client, redis_prefix, sleeps
    )
    assert http.get_json(URL) == [1]
    assert sleeps == [7.0]


def test_server_errors_are_retried_with_backoff(redis_client, redis_prefix):
    responses = iter(
        [httpx.Response(503), httpx.Response(502), httpx.Response(200, json='ok')]
    )
    sleeps = []
    http = make_http(
        lambda request: next(responses), redis_client, redis_prefix, sleeps
    )
    assert http.get_json(URL) == 'ok'
    assert sleeps == [1.0, 2.0]


def test_timeouts_are_retried(redis_client, redis_prefix):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectTimeout('slow', request=request)
        return httpx.Response(200, json='ok')

    sleeps = []
    http = make_http(handler, redis_client, redis_prefix, sleeps)
    assert http.get_json(URL) == 'ok'
    assert len(attempts) == 3


def test_client_errors_are_not_retried(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    http = make_http(handler, redis_client, redis_prefix)
    with pytest.raises(ProviderError, match='HTTP 401'):
        http.get_json(URL)
    assert len(calls) == 1


def test_a_body_that_is_not_json_is_a_provider_error(redis_client, redis_prefix):
    http = make_http(
        lambda request: httpx.Response(200, text='<html>maintenance</html>'),
        redis_client,
        redis_prefix,
    )
    with pytest.raises(ProviderError, match='not JSON'):
        http.get_json(URL)


def test_gives_up_after_max_attempts(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    http = make_http(handler, redis_client, redis_prefix, max_attempts=3)
    with pytest.raises(ProviderError, match='after 3 tries'):
        http.get_json(URL)
    assert len(calls) == 3


def test_stale_copy_is_served_when_the_provider_is_down(redis_client, redis_prefix):
    state = {'up': True}

    def handler(request):
        if state['up']:
            return httpx.Response(200, json={'v': 1})
        return httpx.Response(503)

    http = make_http(handler, redis_client, redis_prefix, ttl=1)
    assert http.get_json(URL) == {'v': 1}
    for key in redis_client.scan_iter(redis_prefix + 'test:*'):
        if ':stale:' not in key and ':lock:' not in key:
            redis_client.delete(key)  # the fresh copy expires, the stale one stays
    state['up'] = False
    assert http.get_json(URL) == {'v': 1}


def test_concurrent_callers_share_one_request(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(1)
        time.sleep(0.3)
        return httpx.Response(200, json='shared')

    http = make_http(handler, redis_client, redis_prefix)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(http.get_json(URL)))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ['shared'] * 4
    assert len(calls) == 1


def test_requests_are_spaced_by_the_minimum_interval(redis_client, redis_prefix):
    def handler(request):
        return httpx.Response(200, json=str(request.url))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = ProviderHttp(
        'test', client=client, redis=redis_client, prefix=redis_prefix, min_interval=0.3
    )
    started = time.monotonic()
    http.get_json(URL + 'a')
    http.get_json(URL + 'b')
    assert time.monotonic() - started >= 0.28


def test_retry_after_accepts_seconds_and_http_dates():
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after('30') == 30.0
    assert parse_retry_after('Mon, 19 Oct 2026 12:01:30 GMT', now) == 90.0
    assert parse_retry_after('Mon, 19 Oct 2026 11:00:00 GMT', now) == 0.0
    assert parse_retry_after('soon') is None
    assert parse_retry_after(None) is None
```

Replace the whole file `tests/conftest.py` (it gains the Redis fixtures and the `provider_http` factory):

**Replace the whole file `tests/conftest.py`:**

```python
"""Shared fixtures."""

import uuid

import httpx
import pytest
from django.core.management import call_command
from providers.clients import connect_redis
from providers.http import ProviderHttp

PORTAL = {
    'HTTP_X_DISCORD_USER_ID': '1001',
    'HTTP_X_DISCORD_EMAIL': 'kira@example.com',
    'HTTP_X_DISCORD_AVATAR_URL': 'https://cdn.example.com/a.png',
    'HTTP_X_DISCORD_ACCESS_LEVEL': 'member',
}


@pytest.fixture(scope='session', autouse=True)
def compiled_messages() -> None:
    """Compile the .po files so translations can be asserted."""
    call_command('compilemessages', verbosity=0)


@pytest.fixture
def portal_headers() -> dict[str, str]:
    """Return a fresh copy of valid portal headers for a member."""
    return dict(PORTAL)


@pytest.fixture
def redis_client():
    """Return a Redis connection and delete the keys of this test afterwards."""
    client = connect_redis()
    yield client
    for key in client.scan_iter('test:*'):
        client.delete(key)


@pytest.fixture
def redis_prefix() -> str:
    """Return a Redis key prefix unique to the test."""
    return f'test:{uuid.uuid4().hex}:'


@pytest.fixture
def provider_http(redis_client, redis_prefix):
    """Return a factory for a ``ProviderHttp`` over a mock transport."""

    def build(handler, name: str = 'test', **kwargs) -> ProviderHttp:
        return ProviderHttp(
            name,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            redis=redis_client,
            prefix=redis_prefix,
            sleep=lambda seconds: None,
            **kwargs,
        )

    return build
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_providers_http.py
```

Expected: an error during collection, `ModuleNotFoundError: No module named 'providers'`.

- [ ] **Step 3: Create the app and the HTTP layer**

**Create the empty file `sources/providers/__init__.py`.**

**Create `sources/providers/apps.py`:**

```python
from django.apps import AppConfig


class ProvidersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'providers'
```

**Create `sources/providers/http.py`:**

```python
"""HTTP access to the metadata providers.

``ProviderHttp`` wraps one ``httpx.Client`` with the layers the spec asks for:
a Redis response cache with a longer-lived stale copy, a single-flight lock so
concurrent callers share one request, a per-provider rate limiter, retries on
429, 5xx and network errors, and stale-on-error.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from redis import Redis
from redis.exceptions import LockError

logger = logging.getLogger(__name__)

MAX_RETRY_DELAY = 120.0
NOT_FOUND_TTL = 300


class ProviderError(Exception):
    """A provider request failed and no stale copy was available."""


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a ``Retry-After`` header into seconds to wait.

    Args:
        value: The header value: delta-seconds or an HTTP date.
        now: Current time, for tests.

    Returns:
        Seconds to wait (never negative), or None when absent or unparsable.
    """
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - (now or datetime.now(UTC))).total_seconds())


class ProviderHttp:
    """Cached, rate-limited, retrying JSON access to one provider."""

    def __init__(
        self,
        name: str,
        *,
        client: httpx.Client,
        redis: Redis,
        min_interval: float = 0.0,
        ttl: int = 6 * 3600,
        stale_ttl: int = 7 * 24 * 3600,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        prefix: str = 'mitaroku:providers:',
    ) -> None:
        """Create the wrapper.

        Args:
            name: Provider name, used in cache keys and log lines.
            client: The ``httpx.Client`` to send requests with.
            redis: Redis connection created with ``decode_responses=True``.
            min_interval: Minimum seconds between two requests to this provider.
            ttl: Seconds a response is served without asking the provider again.
            stale_ttl: Seconds the stale copy survives for stale-on-error.
            max_attempts: Tries per request before giving up.
            sleep: Replaceable sleep, so tests do not wait.
            prefix: Redis key prefix.
        """
        self.name = name
        self._client = client
        self._redis = redis
        self._min_interval = min_interval
        self._ttl = ttl
        self._stale_ttl = stale_ttl
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._prefix = f'{prefix}{name}:'

    def get_json(
        self, url: str, *, params: dict | None = None, ttl: int | None = None
    ) -> object | None:
        """GET a JSON document.

        Args:
            url: Absolute URL.
            params: Query parameters.
            ttl: Cache lifetime override in seconds.

        Returns:
            The parsed JSON, or None when the provider answers 404.

        Raises:
            ProviderError: The request failed and no stale copy exists.
        """
        key = self._key('GET', url, params)
        return self._cached(key, ttl, lambda: self._client.get(url, params=params))

    def post_json(
        self, url: str, *, json: dict, ttl: int | None = None
    ) -> object | None:
        """POST a JSON body (GraphQL) and return the parsed JSON response.

        Args:
            url: Absolute URL.
            json: Request body; it is part of the cache key.
            ttl: Cache lifetime override in seconds.

        Returns:
            The parsed JSON, or None when the provider answers 404.

        Raises:
            ProviderError: The request failed and no stale copy exists.
        """
        key = self._key('POST', url, json)
        return self._cached(key, ttl, lambda: self._client.post(url, json=json))

    def _key(self, method: str, url: str, extra: object) -> str:
        """Return a stable cache key for a request."""
        raw = json.dumps([method, url, extra], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _read(self, key: str) -> tuple[object | None] | None:
        """Return ``(value,)`` for a cached entry, or None when absent."""
        raw = self._redis.get(self._prefix + key)
        return None if raw is None else (json.loads(raw),)

    def _store(self, key: str, value: object | None, ttl: int) -> None:
        """Write the fresh copy and the longer-lived stale copy."""
        raw = json.dumps(value)
        self._redis.set(self._prefix + key, raw, ex=ttl)
        self._redis.set(self._prefix + 'stale:' + key, raw, ex=self._stale_ttl)

    def _cached(
        self, key: str, ttl: int | None, send: Callable[[], httpx.Response]
    ) -> object | None:
        """Serve from cache, or fetch once under a lock, or fall back to stale."""
        hit = self._read(key)
        if hit is not None:
            return hit[0]
        lock = self._redis.lock(self._prefix + 'lock:' + key, timeout=180)
        locked = lock.acquire(blocking=True, blocking_timeout=120)
        try:
            hit = self._read(key)
            if hit is not None:
                return hit[0]
            try:
                value = self._send(send)
            except ProviderError:
                stale = self._read('stale:' + key)
                if stale is None:
                    raise
                logger.warning('%s unavailable, serving a stale copy', self.name)
                return stale[0]
            self._store(
                key, value, NOT_FOUND_TTL if value is None else ttl or self._ttl
            )
            return value
        finally:
            if locked:
                try:
                    lock.release()
                except LockError:
                    logger.warning('%s lock expired before release', self.name)

    def _throttle(self) -> None:
        """Wait until this provider's minimum request interval has passed."""
        if self._min_interval <= 0:
            return
        slot = self._prefix + 'slot'
        while not self._redis.set(slot, 1, nx=True, px=int(self._min_interval * 1000)):
            self._sleep(min(self._min_interval / 4, 0.05))

    def _send(self, send: Callable[[], httpx.Response]) -> object | None:
        """Send with retries on 429, 5xx and network errors."""
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            delay = min(2.0 ** (attempt - 1), MAX_RETRY_DELAY)
            try:
                response = send()
            except httpx.TransportError as error:
                logger.info('%s network error: %s', self.name, error)
                reason = type(error).__name__
            else:
                status = response.status_code
                if status == 404:
                    return None
                if status < 400:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise ProviderError(
                            f'{self.name} sent a body that is not JSON'
                        ) from error
                if status != 429 and status < 500:
                    raise ProviderError(f'{self.name} answered HTTP {status}')
                retry_after = parse_retry_after(response.headers.get('Retry-After'))
                if retry_after is not None:
                    delay = min(retry_after, MAX_RETRY_DELAY)
                reason = f'HTTP {status}'
            if attempt == self._max_attempts:
                raise ProviderError(
                    f'{self.name} failed after {attempt} tries: {reason}'
                )
            logger.info('%s retry %s in %.1f s (%s)', self.name, attempt, delay, reason)
            self._sleep(delay)
        raise ProviderError(f'{self.name}: no attempts made')
```

**Create `sources/providers/clients.py`:**

```python
"""Factories that wire the provider clients to Django settings."""

from django.conf import settings
from redis import Redis


def connect_redis() -> Redis:
    """Return a Redis connection that decodes responses to ``str``."""
    return Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )
```

In `sources/mitaroku/settings.py` add the app to `INSTALLED_APPS`, after `'accounts'`:

```python
    'providers',
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_providers_http.py
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add a cached, rate-limited, retrying HTTP layer for providers"
```

---

### Task 3: Catalog models

**Files:**
- Create: `sources/catalog/__init__.py`, `sources/catalog/apps.py`, `sources/catalog/models.py`, `sources/catalog/admin.py`, `sources/catalog/migrations/__init__.py`, `sources/catalog/migrations/0001_initial.py` (generated), `sources/catalog/migrations/0002_app_admins_catalog_permissions.py`, `tests/test_catalog_models.py`
- Modify: `sources/mitaroku/settings.py`, `tests/test_admin_access.py`

**Interfaces:**
- Produces (all in `catalog.models`):
  - `Title(kind, original_title, original_language, release_date, status, runtime_minutes, poster_url, backdrop_url, rating, rating_votes, rating_source, synced_at, next_sync_at, created_at)`; `Title.Kind` is `movie`, `tv`, `anime`; `Title.Status` is `upcoming`, `airing`, `ended`, `canceled`, `unknown`.
  - `ExternalId(title, provider, external_id)`, unique per `(provider, external_id)`; `ExternalId.Provider` is `tmdb_movie`, `tmdb_tv`, `anilist`, `mal`, `imdb`.
  - `Season(title, number, kind, name, is_extra, anilist_id, tmdb_tv_id, start_date, episode_count, episode_source)`, ordered by `number`, unique per `(title, number)` and per `(title, anilist_id)` when set; `Season.Kind` is `tv`, `ona`, `ova`, `movie`, `special`; `Season.EpisodeSource` is `tmdb`, `anilist`, `none`. `number` is a signed small integer because a sync parks seasons on negative numbers while renumbering.
  - `Episode(season, number, absolute_number, tmdb_season_number, tmdb_episode_number, air_date)`, unique per `(season, number)`.
  - `TitleTranslation(title, language, name, overview)` and `EpisodeTranslation(episode, language, name, overview)`, unique per language, and a row must have a name or an overview.
- The `app-admins` group gets view permission on all six models (the catalog is filled by the providers, so admins look but do not edit).

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_catalog_models.py`:**

```python
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
```

Append to `tests/test_admin_access.py`:

```python
@pytest.mark.parametrize(
    'path',
    [
        '/admin/catalog/title/',
        '/admin/catalog/season/',
        '/admin/catalog/episode/',
    ],
)
def test_portal_admin_can_browse_the_catalog(client, portal_headers, path):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    assert client.get(path, **portal_headers).status_code == 200


def test_portal_admin_cannot_edit_the_catalog(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    assert client.get('/admin/catalog/title/add/', **portal_headers).status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_catalog_models.py tests/test_admin_access.py
```

Expected: errors, `ModuleNotFoundError: No module named 'catalog'`.

- [ ] **Step 3: Create the app, the models and the admin**

**Create the empty file `sources/catalog/__init__.py`.**

**Create `sources/catalog/apps.py`:**

```python
from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'catalog'
```

**Create the empty file `sources/catalog/migrations/__init__.py`.**

**Create `sources/catalog/models.py`:**

```python
"""Catalog models: titles, their external ids, seasons, episodes and translations.

The catalog is shared by all users and filled by the providers; nothing here
belongs to a single user (library entries and watch events arrive with tracking).
"""

from django.db import models
from django.db.models import Q


class Title(models.Model):
    """A movie, a TV series or an anime franchise.

    An anime ``Title`` is a whole franchise: its seasons are the AniList entries
    (TV cours, ONAs, OVAs, movies and specials). A movie has no seasons.
    """

    class Kind(models.TextChoices):
        MOVIE = 'movie'
        TV = 'tv'
        ANIME = 'anime'

    class Status(models.TextChoices):
        UPCOMING = 'upcoming'
        AIRING = 'airing'
        ENDED = 'ended'
        CANCELED = 'canceled'
        UNKNOWN = 'unknown'

    kind = models.CharField(max_length=8, choices=Kind)
    original_title = models.CharField(max_length=300)
    original_language = models.CharField(max_length=8, blank=True)
    release_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.UNKNOWN)
    runtime_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    poster_url = models.URLField(max_length=500, blank=True)
    backdrop_url = models.URLField(max_length=500, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    rating_votes = models.PositiveIntegerField(null=True, blank=True)
    rating_source = models.CharField(max_length=16, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    next_sync_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.original_title


class ExternalId(models.Model):
    """An id of a title at a provider.

    TMDB movie and TV ids share a number space, so they are separate providers.
    An anime franchise carries the AniList id of every one of its entries, which
    is how adding any entry finds the existing franchise.
    """

    class Provider(models.TextChoices):
        TMDB_MOVIE = 'tmdb_movie'
        TMDB_TV = 'tmdb_tv'
        ANILIST = 'anilist'
        MAL = 'mal'
        IMDB = 'imdb'

    title = models.ForeignKey(
        Title, on_delete=models.CASCADE, related_name='external_ids'
    )
    provider = models.CharField(max_length=12, choices=Provider)
    external_id = models.CharField(max_length=32)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'external_id'], name='unique_external_id'
            )
        ]

    def __str__(self) -> str:
        return f'{self.provider}:{self.external_id}'


class Season(models.Model):
    """A season of a TV title, or one entry of an anime franchise.

    ``number`` orders the seasons inside the title (it is briefly negative while
    a sync renumbers them). ``episode_source`` records
    where the episodes came from: ``tmdb`` (accepted by the guard), ``anilist``
    (degraded mode: counts and some dates only, no titles) or ``none``.
    """

    class Kind(models.TextChoices):
        TV = 'tv'
        ONA = 'ona'
        OVA = 'ova'
        MOVIE = 'movie'
        SPECIAL = 'special'

    class EpisodeSource(models.TextChoices):
        TMDB = 'tmdb'
        ANILIST = 'anilist'
        NONE = 'none'

    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name='seasons')
    number = models.SmallIntegerField()
    kind = models.CharField(max_length=8, choices=Kind)
    name = models.CharField(max_length=300, blank=True)
    is_extra = models.BooleanField(default=False)
    anilist_id = models.PositiveIntegerField(null=True, blank=True)
    tmdb_tv_id = models.PositiveIntegerField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    episode_count = models.PositiveIntegerField(null=True, blank=True)
    episode_source = models.CharField(
        max_length=8, choices=EpisodeSource, default=EpisodeSource.NONE
    )

    class Meta:
        ordering = ['number']
        constraints = [
            models.UniqueConstraint(
                fields=['title', 'number'], name='unique_season_number'
            ),
            models.UniqueConstraint(
                fields=['title', 'anilist_id'],
                condition=Q(anilist_id__isnull=False),
                name='unique_season_anilist_id',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} / {self.name or self.number}'


class Episode(models.Model):
    """One episode.

    ``absolute_number`` counts across the main seasons of a title. When the
    episode came from TMDB, ``tmdb_season_number`` and ``tmdb_episode_number``
    say which TMDB episode it is, so other languages can be fetched later.
    """

    season = models.ForeignKey(
        Season, on_delete=models.CASCADE, related_name='episodes'
    )
    number = models.PositiveIntegerField()
    absolute_number = models.PositiveIntegerField(null=True, blank=True)
    tmdb_season_number = models.PositiveSmallIntegerField(null=True, blank=True)
    tmdb_episode_number = models.PositiveIntegerField(null=True, blank=True)
    air_date = models.DateField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['number']
        constraints = [
            models.UniqueConstraint(
                fields=['season', 'number'], name='unique_episode_number'
            )
        ]

    def __str__(self) -> str:
        return f'{self.season} #{self.number}'


class TitleTranslation(models.Model):
    """Lazily filled name and overview of a title in one language.

    Search results are never stored here, and a row with neither a name nor an
    overview is never written.
    """

    title = models.ForeignKey(
        Title, on_delete=models.CASCADE, related_name='translations'
    )
    language = models.CharField(max_length=8)
    name = models.CharField(max_length=300, blank=True)
    overview = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['title', 'language'], name='unique_title_translation'
            ),
            models.CheckConstraint(
                condition=~Q(name='') | ~Q(overview=''),
                name='title_translation_not_empty',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} [{self.language}]'


class EpisodeTranslation(models.Model):
    """Lazily filled name and overview of an episode in one language."""

    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name='translations'
    )
    language = models.CharField(max_length=8)
    name = models.CharField(max_length=300, blank=True)
    overview = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['episode', 'language'], name='unique_episode_translation'
            ),
            models.CheckConstraint(
                condition=~Q(name='') | ~Q(overview=''),
                name='episode_translation_not_empty',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.episode} [{self.language}]'
```

**Create `sources/catalog/admin.py`:**

```python
"""Admin registrations: the catalog is read-only for the admins' group."""

from catalog.models import Episode, ExternalId, Season, Title
from django.contrib import admin


class ExternalIdInline(admin.TabularInline):
    model = ExternalId
    extra = 0


@admin.register(Title)
class TitleAdmin(admin.ModelAdmin):
    """Titles with their provider ids."""

    list_display = ('original_title', 'kind', 'status', 'synced_at')
    list_filter = ('kind', 'status')
    search_fields = ('original_title', 'external_ids__external_id')
    inlines = [ExternalIdInline]


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    """Seasons and anime franchise entries."""

    list_display = ('title', 'number', 'kind', 'name', 'is_extra', 'episode_source')
    list_filter = ('kind', 'is_extra', 'episode_source')
    search_fields = ('title__original_title', 'name')


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    """Episodes."""

    list_display = ('season', 'number', 'air_date')
    search_fields = ('season__title__original_title',)
```

In `sources/mitaroku/settings.py` add the app to `INSTALLED_APPS`, after `'accounts'` and before `'providers'`:

```python
    'catalog',
```

- [ ] **Step 4: Generate the initial migration and add the permissions migration**

```bash
python sources/manage.py makemigrations catalog
ruff format sources
```

Expected: `sources/catalog/migrations/0001_initial.py` creating the six models and their constraints. Do not edit it. Then create the data migration, which mirrors `accounts/migrations/0003_app_admins_permissions.py`:

**Create `sources/catalog/migrations/0002_app_admins_catalog_permissions.py`:**

```python
"""Give the ``app-admins`` group read access to the catalog.

The catalog is filled by the providers, so the group may look but not edit.
"""

from collections.abc import Iterator

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Model

GROUP_NAME = 'app-admins'
MODELS = [
    'title',
    'externalid',
    'season',
    'episode',
    'titletranslation',
    'episodetranslation',
]


def _permissions(apps: Apps) -> Iterator[Model]:
    """Return the view permissions, creating missing ones on a fresh database."""
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    for model in MODELS:
        content_type, _ = ContentType.objects.get_or_create(
            app_label='catalog', model=model
        )
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=f'view_{model}',
            defaults={'name': f'Can view {model}'},
        )
        yield permission


def grant(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Add the permissions to the group."""
    Group = apps.get_model('auth', 'Group')
    group = Group.objects.get(name=GROUP_NAME)
    group.permissions.add(*_permissions(apps))


def revoke(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Remove the permissions from the group on rollback."""
    Group = apps.get_model('auth', 'Group')
    group = Group.objects.get(name=GROUP_NAME)
    group.permissions.remove(*_permissions(apps))


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0001_initial'),
        ('accounts', '0003_app_admins_permissions'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [migrations.RunPython(grant, revoke)]
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_catalog_models.py tests/test_admin_access.py
python sources/manage.py makemigrations --check --dry-run
```

Expected: 21 passed (11 + 10); `No changes detected`.

- [ ] **Step 6: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add the catalog models and a read-only admin"
```

---

### Task 4: TMDB client

**Files:**
- Create: `sources/providers/types.py`, `sources/providers/tmdb.py`, `tests/test_tmdb.py`
- Modify: `sources/providers/clients.py`

**Interfaces:**
- Consumes: `ProviderHttp` and `ProviderError` from Task 2; `settings`-driven `connect_redis`.
- Produces:
  - `providers.types.SearchHit(provider, external_id, kind, title, original_title, year, poster_url, overview, anime_like=False)`: `provider` is an `ExternalId.Provider` value (`tmdb_movie`, `tmdb_tv`, `anilist`), `kind` a `Title.Kind` value.
  - `providers.tmdb.TmdbClient(http)` with `search(query, language='en') -> list[SearchHit]` (movies and TV only; `anime_like` is True for Japanese animation), `movie(tmdb_id, language='en') -> TmdbMovie | None`, `tv(tmdb_id, language='en') -> TmdbShow | None`, `season(tmdb_id, number, language='en') -> TmdbSeason | None`, `seasons(show, language='en') -> dict[int, TmdbSeason]` (every season, specials as 0).
  - `TmdbEpisode(season_number, number, name, overview, air_date)`; `TmdbSeason(number, name, episodes)`; `TmdbShow(..., seasons: list[TmdbSeasonSummary])`; `TmdbMovie(...)`. Missing dates are None, missing text is `''`, a rating is None until somebody voted.
  - `providers.clients.get_tmdb() -> TmdbClient`, which raises `ProviderError` when `providers.tmdb_token` is not set.

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_tmdb.py`:**

```python
from datetime import date

import httpx
import pytest
from providers.clients import get_tmdb
from providers.http import ProviderError
from providers.tmdb import TmdbClient

SEARCH = {
    'results': [
        {
            'media_type': 'tv',
            'id': 209867,
            'name': 'Frieren: Beyond Journey’s End',
            'original_name': '葬送のフリーレン',
            'original_language': 'ja',
            'genre_ids': [16, 18, 10765],
            'first_air_date': '2023-09-29',
            'poster_path': '/frieren.jpg',
            'overview': 'An elf mage.',
        },
        {
            'media_type': 'movie',
            'id': 603,
            'title': 'The Matrix',
            'original_title': 'The Matrix',
            'original_language': 'en',
            'genre_ids': [28, 878],
            'release_date': '1999-03-31',
            'poster_path': None,
            'overview': '',
        },
        {'media_type': 'person', 'id': 1, 'name': 'Somebody'},
    ]
}
MOVIE = {
    'id': 603,
    'title': 'The Matrix',
    'original_title': 'The Matrix',
    'original_language': 'en',
    'overview': 'A hacker learns the truth.',
    'release_date': '1999-03-31',
    'runtime': 136,
    'status': 'Released',
    'poster_path': '/matrix.jpg',
    'backdrop_path': '/matrix-bg.jpg',
    'vote_average': 8.2,
    'vote_count': 25000,
    'external_ids': {'imdb_id': 'tt0133093'},
}
SHOW = {
    'id': 209867,
    'name': 'Frieren',
    'original_name': '葬送のフリーレン',
    'original_language': 'ja',
    'overview': 'An elf mage.',
    'first_air_date': '2023-09-29',
    'status': 'Returning Series',
    'poster_path': '/frieren.jpg',
    'backdrop_path': None,
    'vote_average': 0.0,
    'vote_count': 0,
    'external_ids': {'imdb_id': None},
    'seasons': [
        {'season_number': 0, 'name': 'Specials', 'episode_count': 2},
        {'season_number': 1, 'name': 'Season 1', 'episode_count': 28},
    ],
}
SEASON = {
    'season_number': 1,
    'name': 'Season 1',
    'episodes': [
        {
            'episode_number': 1,
            'name': 'The Journey’s End',
            'overview': 'Start.',
            'air_date': '2023-09-29',
        },
        {'episode_number': 2, 'name': 'Next', 'overview': '', 'air_date': ''},
    ],
}


@pytest.fixture
def tmdb(provider_http):
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path
        routes = {
            '/3/search/multi': SEARCH,
            '/3/movie/603': MOVIE,
            '/3/tv/209867': SHOW,
            '/3/tv/209867/season/1': SEASON,
            '/3/tv/209867/season/0': {
                **SEASON,
                'season_number': 0,
                'name': 'Specials',
                'episodes': [],
            },
        }
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404)

    client = TmdbClient(provider_http(handler, name='tmdb'))
    client.requests = seen
    return client


def test_search_keeps_movies_and_shows_and_drops_people(tmdb):
    hits = tmdb.search('frieren', 'ru')
    assert [(h.provider, h.external_id, h.kind) for h in hits] == [
        ('tmdb_tv', '209867', 'tv'),
        ('tmdb_movie', '603', 'movie'),
    ]
    assert tmdb.requests[0].url.params['language'] == 'ru-RU'


def test_search_hit_fields(tmdb):
    show, movie = tmdb.search('x')
    assert show.title == 'Frieren: Beyond Journey’s End'
    assert show.original_title == '葬送のフリーレン'
    assert show.year == 2023
    assert show.poster_url == 'https://image.tmdb.org/t/p/w185/frieren.jpg'
    assert movie.poster_url == ''


def test_japanese_animation_is_marked_anime_like(tmdb):
    show, movie = tmdb.search('x')
    assert show.anime_like is True
    assert movie.anime_like is False


def test_movie_details(tmdb):
    movie = tmdb.movie(603)
    assert movie.title == 'The Matrix'
    assert movie.release_date == date(1999, 3, 31)
    assert movie.runtime == 136
    assert movie.rating == 8.2
    assert movie.imdb_id == 'tt0133093'
    assert movie.poster_url == 'https://image.tmdb.org/t/p/w342/matrix.jpg'


def test_show_without_votes_has_no_rating_and_no_imdb_id(tmdb):
    show = tmdb.tv(209867)
    assert show.rating is None
    assert show.imdb_id == ''
    assert show.backdrop_url == ''
    assert [(s.number, s.episode_count) for s in show.seasons] == [(0, 2), (1, 28)]


def test_unknown_id_returns_none(tmdb):
    assert tmdb.movie(1) is None
    assert tmdb.tv(1) is None
    assert tmdb.season(209867, 9) is None


def test_season_episodes_with_missing_dates(tmdb):
    season = tmdb.season(209867, 1)
    assert season.episodes[0].air_date == date(2023, 9, 29)
    assert season.episodes[0].season_number == 1
    assert season.episodes[1].air_date is None
    assert season.episodes[1].overview == ''


def test_seasons_fetches_every_season_including_specials(tmdb):
    show = tmdb.tv(209867)
    seasons = tmdb.seasons(show)
    assert sorted(seasons) == [0, 1]
    assert len(seasons[1].episodes) == 2


def test_get_tmdb_requires_a_token(monkeypatch):
    from mitaroku.config import load_settings

    monkeypatch.delenv('MITAROKU_PROVIDERS__TMDB_TOKEN', raising=False)
    load_settings.cache_clear()
    try:
        with pytest.raises(ProviderError, match='tmdb_token'):
            get_tmdb()
    finally:
        load_settings.cache_clear()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_tmdb.py
```

Expected: an error during collection, `ModuleNotFoundError: No module named 'providers.tmdb'`.

- [ ] **Step 3: Write the client**

**Create `sources/providers/types.py`:**

```python
"""Types shared by the provider clients."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    """One search result. Search results are shown but never stored.

    Attributes:
        provider: ``ExternalId`` provider value: ``tmdb_movie``, ``tmdb_tv`` or
            ``anilist``.
        external_id: The id at that provider, as a string.
        kind: ``movie``, ``tv`` or ``anime``: the ``Title`` kind it would create.
        title: Title in the requested language.
        original_title: Title in the original language.
        year: Release or first air year, when known.
        poster_url: Poster image URL, empty when there is none.
        overview: Short description, possibly empty.
        anime_like: TMDB hit that is animation from Japan; the AniList search
            covers these, so the merged search drops them.
    """

    provider: str
    external_id: str
    kind: str
    title: str
    original_title: str
    year: int | None
    poster_url: str
    overview: str
    anime_like: bool = False
```

**Create `sources/providers/tmdb.py`:**

```python
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
```

Replace the whole file `sources/providers/clients.py`:

**Replace the whole file `sources/providers/clients.py`:**

```python
"""Factories that wire the provider clients to Django settings."""

import httpx
from django.conf import settings
from mitaroku.config import load_settings
from providers.http import ProviderError, ProviderHttp
from providers.tmdb import TmdbClient
from redis import Redis

TIMEOUT = 30.0


def connect_redis() -> Redis:
    """Return a Redis connection that decodes responses to ``str``."""
    return Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )


def user_agent() -> str:
    """Return the ``User-Agent`` header, naming the operator when configured."""
    contact = load_settings().providers.contact
    return f'mitaroku/0.1 ({contact})' if contact else 'mitaroku/0.1'


def get_tmdb() -> TmdbClient:
    """Return a TMDB client.

    Raises:
        ProviderError: No TMDB token is configured.
    """
    config = load_settings().providers
    if config.tmdb_token is None:
        raise ProviderError('TMDB is not configured: set providers.tmdb_token')
    client = httpx.Client(
        timeout=TIMEOUT,
        headers={
            'Authorization': f'Bearer {config.tmdb_token.get_secret_value()}',
            'User-Agent': user_agent(),
        },
    )
    http = ProviderHttp(
        'tmdb',
        client=client,
        redis=connect_redis(),
        min_interval=config.tmdb_min_interval,
    )
    return TmdbClient(http)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_tmdb.py
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add a typed TMDB client"
```

---

### Task 5: AniList client and franchise grouping

**Files:**
- Create: `sources/providers/anilist.py`, `sources/catalog/franchise.py`, `tests/fakes.py`, `tests/test_anilist.py`, `tests/test_franchise.py`
- Modify: `sources/providers/clients.py`

**Interfaces:**
- Consumes: `ProviderHttp`, `ProviderError` (Task 2); `SearchHit` (Task 4).
- Produces:
  - `providers.anilist.AniListClient(http)` with `media_many(ids) -> list[AniListMedia]` (50 ids per request; unknown ids are absent), `schedule(anilist_id) -> AniListSchedule`, `search(query) -> list[SearchHit]` (adult entries and non-anime formats dropped).
  - `AniListMedia(anilist_id, mal_id, format, status, episodes, average_score, romaji, english, native, cover_url, banner_url, start, edges)` with properties `start_date` (a `date` only when year, month and day are known) and `sort_key` (unknown dates last); `AniListEdge(relation, anilist_id, mal_id, format, media_type)` keeps edges whose `format` is None; `AniListSchedule(upcoming, all)` map an episode number to a UTC datetime; constant `ANIME_FORMATS`.
  - `catalog.franchise.load_franchise(fetch_many, root_id) -> Franchise(main, extras)` where `fetch_many(ids)` returns the known entries (pass `AniListClient.media_many`). Both lists are chronological with unknown dates last. Rules: the main chain follows SEQUEL and PREQUEL edges between anime entries (each level of the walk is one request); side stories, summaries, spin-offs, alternatives, parents and freshly announced entries with a null format are extras and are never walked; a `MOVIE` or `SPECIAL` with a PARENT edge is an extra, but the walk passes through it and a compilation reached first leads to its parent.
  - `providers.clients.get_anilist() -> AniListClient`.
  - Test helpers in `tests/fakes.py`: `edge(relation, anilist_id, fmt='TV', media_type='ANIME')` and `media(anilist_id, edges=(), fmt='TV', year=2020, month=1, day=1, episodes=12, status='FINISHED', score=80, mal_id=None, english='')`.

The franchise rules were checked against the cached AniList data of the twelve spike fixtures: ten produce the same main chain as the spike's rules, and the two that change are Steins;Gate (film 11577 becomes an extra) and Jujutsu Kaisen (recap 204066 becomes an extra).

- [ ] **Step 1: Write the failing tests**

**Create `tests/fakes.py`:**

```python
"""In-memory stand-ins for the provider clients, and data builders."""

from providers.anilist import AniListEdge, AniListMedia


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
```

**Create `tests/test_anilist.py`:**

```python
from datetime import UTC, date, datetime

import httpx
import pytest
from providers.anilist import AniListClient
from providers.http import ProviderError

FRIEREN = {
    'id': 154587,
    'idMal': 52991,
    'format': 'TV',
    'status': 'FINISHED',
    'episodes': 28,
    'averageScore': 91,
    'isAdult': False,
    'title': {
        'romaji': 'Sousou no Frieren',
        'english': 'Frieren',
        'native': '葬送のフリーレン',
    },
    'coverImage': {'large': 'https://img.example/f.jpg'},
    'bannerImage': None,
    'startDate': {'year': 2023, 'month': 9, 'day': 29},
    'relations': {
        'edges': [
            {
                'relationType': 'SEQUEL',
                'node': {'id': 182255, 'idMal': None, 'format': None, 'type': 'ANIME'},
            },
            {
                'relationType': 'ADAPTATION',
                'node': {
                    'id': 118586,
                    'idMal': 126287,
                    'format': 'MANGA',
                    'type': 'MANGA',
                },
            },
        ]
    },
}
ANNOUNCED = {
    'id': 182255,
    'idMal': None,
    'format': None,
    'status': 'NOT_YET_RELEASED',
    'episodes': None,
    'averageScore': None,
    'isAdult': False,
    'title': {
        'romaji': 'Sousou no Frieren 2nd Season',
        'english': None,
        'native': None,
    },
    'coverImage': None,
    'bannerImage': None,
    'startDate': {'year': 2026, 'month': None, 'day': None},
    'relations': None,
}


def page(*media):
    return {'data': {'Page': {'media': list(media)}}}


@pytest.fixture
def anilist(provider_http):
    bodies = []

    def handler(request):
        import json

        body = json.loads(request.content)
        bodies.append(body)
        variables = body['variables']
        if 'airingSchedule' in body['query']:
            if variables['id'] == 404:
                return httpx.Response(404, json={'errors': [{'message': 'Not Found.'}]})
            return httpx.Response(
                200,
                json={
                    'data': {
                        'Media': {
                            'upcoming': {
                                'nodes': [{'episode': 29, 'airingAt': 1798985760}]
                            },
                            'schedule': {
                                'nodes': [
                                    {'episode': 1, 'airingAt': 1695997200},
                                    {'episode': 29, 'airingAt': 1798985760},
                                ]
                            },
                        }
                    }
                },
            )
        if 'search' in variables:
            adult = {**FRIEREN, 'id': 5, 'isAdult': True}
            manga_like = {**FRIEREN, 'id': 6, 'format': 'MUSIC'}
            return httpx.Response(200, json=page(FRIEREN, adult, manga_like))
        known = {154587: FRIEREN, 182255: ANNOUNCED}
        return httpx.Response(
            200, json=page(*[known[i] for i in variables['ids'] if i in known])
        )

    client = AniListClient(provider_http(handler, name='anilist'))
    client.bodies = bodies
    return client


def test_media_fields(anilist):
    media = anilist.media_many([154587])[0]
    assert media.anilist_id == 154587
    assert media.mal_id == 52991
    assert media.english == 'Frieren'
    assert media.native == '葬送のフリーレン'
    assert media.episodes == 28
    assert media.average_score == 91
    assert media.start_date == date(2023, 9, 29)
    assert media.cover_url == 'https://img.example/f.jpg'


def test_edges_keep_a_null_format(anilist):
    media = anilist.media_many([154587])[0]
    assert [(e.relation, e.anilist_id, e.format) for e in media.edges] == [
        ('SEQUEL', 182255, None),
        ('ADAPTATION', 118586, 'MANGA'),
    ]


def test_announced_entry_with_partial_data(anilist):
    media = anilist.media_many([182255])[0]
    assert media.format is None
    assert media.edges == []
    assert media.start == (2026, None, None)
    assert media.start_date is None
    assert media.english == ''


def test_unknown_ids_are_absent(anilist):
    assert anilist.media_many([1, 2]) == []


def test_ids_are_sent_in_pages_of_fifty(anilist):
    anilist.media_many(list(range(1, 121)))
    assert [len(b['variables']['ids']) for b in anilist.bodies] == [50, 50, 20]


def test_schedule(anilist):
    schedule = anilist.schedule(154587)
    assert schedule.upcoming == {29: datetime.fromtimestamp(1798985760, UTC)}
    assert sorted(schedule.all) == [1, 29]


def test_schedule_of_an_unknown_entry_is_empty(anilist):
    schedule = anilist.schedule(404)
    assert schedule.upcoming == {} and schedule.all == {}


def test_search_drops_adult_and_non_anime_formats(anilist):
    hits = anilist.search('frieren')
    assert [(h.provider, h.external_id, h.kind) for h in hits] == [
        ('anilist', '154587', 'anime')
    ]
    assert hits[0].title == 'Frieren'
    assert hits[0].original_title == 'Sousou no Frieren'
    assert hits[0].year == 2023


def test_graphql_errors_without_data_are_a_provider_error(provider_http):
    def handler(request):
        return httpx.Response(200, json={'errors': [{'message': 'boom'}]})

    client = AniListClient(provider_http(handler, name='anilist'))
    with pytest.raises(ProviderError, match='boom'):
        client.media_many([1])
```

**Create `tests/test_franchise.py`:**

```python
from catalog.franchise import load_franchise
from fakes import edge, media


class Fake:
    """Stands in for ``AniListClient.media_many`` and counts the calls."""

    def __init__(self, *entries):
        self.by_id = {entry.anilist_id: entry for entry in entries}
        self.calls = []

    def __call__(self, ids):
        self.calls.append(list(ids))
        return [self.by_id[i] for i in ids if i in self.by_id]


def ids(entries):
    return [entry.anilist_id for entry in entries]


def test_follows_sequels_and_prequels_in_both_directions():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2018),
        media(2, [edge('PREQUEL', 1), edge('SEQUEL', 3)], year=2019),
        media(3, [edge('PREQUEL', 2)], year=2020),
    )
    assert ids(load_franchise(fetch, 2).main) == [1, 2, 3]


def test_a_cycle_does_not_loop_forever():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2018),
        media(2, [edge('SEQUEL', 1)], year=2019),
    )
    assert ids(load_franchise(fetch, 1).main) == [1, 2]


def test_each_level_is_one_request():
    fetch = Fake(media(1, [edge('SEQUEL', 2)]), media(2, [edge('SEQUEL', 3)]), media(3))
    load_franchise(fetch, 1)
    assert fetch.calls == [[1], [2], [3]]


def test_manga_and_music_edges_are_ignored():
    fetch = Fake(
        media(1, [edge('SEQUEL', 9, 'MANGA', 'MANGA'), edge('SEQUEL', 8, 'MUSIC')])
    )
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1]
    assert franchise.extras == []


def test_side_entries_are_extras_and_not_walked():
    fetch = Fake(
        media(1, [edge('SIDE_STORY', 5, 'OVA')]),
        media(5, [edge('SEQUEL', 6)], fmt='OVA'),
        media(6),
    )
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1]
    assert ids(franchise.extras) == [5]


def test_edge_with_a_null_format_is_kept_as_an_unresolved_extra():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2, None)]),
        media(2, fmt=None, year=None),
    )
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1]
    assert ids(franchise.extras) == [2]


def test_unavailable_entry_is_skipped():
    fetch = Fake(media(1, [edge('SEQUEL', 2)]))
    assert ids(load_franchise(fetch, 1).main) == [1]


def test_unknown_start_dates_sort_last():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2020),
        media(2, [edge('PREQUEL', 1)], year=None),
    )
    assert ids(load_franchise(fetch, 1).main) == [1, 2]
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=None),
        media(2, [edge('PREQUEL', 1)], year=2020),
    )
    assert ids(load_franchise(fetch, 1).main) == [2, 1]


def test_a_movie_with_a_parent_is_an_extra_but_the_chain_passes_through_it():
    # AniList 204066: a compilation film that links season 2 to season 3.
    fetch = Fake(
        media(145064, [edge('SEQUEL', 204066, 'MOVIE')], year=2023),
        media(
            204066,
            [edge('PREQUEL', 145064), edge('PARENT', 145064), edge('SEQUEL', 172463)],
            fmt='MOVIE',
            year=2025,
        ),
        media(172463, [edge('PREQUEL', 204066)], year=2026),
    )
    franchise = load_franchise(fetch, 145064)
    assert ids(franchise.main) == [145064, 172463]
    assert ids(franchise.extras) == [204066]


def test_a_canon_film_with_a_parent_is_listed_as_an_extra():
    # AniList 11577: demoted by the rule, but still part of the franchise.
    fetch = Fake(
        media(9253, [edge('SEQUEL', 10863, 'OVA')], year=2011),
        media(
            10863,
            [edge('PREQUEL', 9253), edge('SEQUEL', 11577, 'MOVIE')],
            fmt='OVA',
            year=2012,
        ),
        media(
            11577,
            [edge('PREQUEL', 10863, 'OVA'), edge('PARENT', 9253)],
            fmt='MOVIE',
            year=2013,
        ),
    )
    franchise = load_franchise(fetch, 9253)
    assert ids(franchise.main) == [9253, 10863]
    assert ids(franchise.extras) == [11577]


def test_a_special_with_a_parent_is_an_extra_too():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2, 'SPECIAL')]),
        media(2, [edge('PARENT', 1)], fmt='SPECIAL'),
    )
    assert ids(load_franchise(fetch, 1).extras) == [2]


def test_extras_are_chronological():
    fetch = Fake(
        media(1, [edge('SIDE_STORY', 5, 'OVA'), edge('SIDE_STORY', 6, 'OVA')]),
        media(5, fmt='OVA', year=2022),
        media(6, fmt='OVA', year=2019),
    )
    assert ids(load_franchise(fetch, 1).extras) == [6, 5]


def test_a_franchise_found_through_a_compilation_reaches_its_seasons():
    fetch = Fake(
        media(1, [edge('SEQUEL', 2)], year=2020),
        media(2, [edge('PREQUEL', 1)], year=2021),
        media(3, [edge('PARENT', 2, 'TV')], fmt='SPECIAL', year=2022),
    )
    franchise = load_franchise(fetch, 3)
    assert ids(franchise.main) == [1, 2]
    assert ids(franchise.extras) == [3]


def test_a_standalone_movie_is_its_own_main_entry():
    fetch = Fake(media(1, [edge('ADAPTATION', 9, 'NOVEL', 'MANGA')], fmt='MOVIE'))
    franchise = load_franchise(fetch, 1)
    assert ids(franchise.main) == [1] and franchise.extras == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_anilist.py tests/test_franchise.py
```

Expected: errors during collection, `ModuleNotFoundError: No module named 'providers.anilist'`.

- [ ] **Step 3: Write the client and the grouping**

**Create `sources/providers/anilist.py`:**

```python
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
    def sort_key(self) -> tuple[int, int, int]:
        """Chronological sort key that puts unknown dates last."""
        year, month, day = self.start
        return (year or 9999, month or 12, day or 31)


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
            URL, json={'query': query, 'variables': variables}, ttl=ttl
        )
        if body is None:
            return None
        if body.get('errors') and not body.get('data'):
            raise ProviderError(f'AniList error: {body["errors"][0].get("message")}')
        return body['data']

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
            result.extend(media_from_json(item) for item in data['Page']['media'])
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
        for item in data['Page']['media']:
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
```

**Create `sources/catalog/franchise.py`:**

```python
"""Group AniList entries into a franchise.

The main chain follows SEQUEL and PREQUEL links between anime entries. Side
stories, summaries, spin-offs, alternative versions and parents of a main entry
are listed as extras but are never walked, which keeps a franchise bounded.

A MOVIE or SPECIAL that has a PARENT edge is a compilation or side film of its
parent. It is an extra, yet the walk still passes through it, because AniList
sometimes links a season to the next one only via such a film. Known limit: a
compilation without a PARENT edge, such as AniList 192809 (the second Jujutsu
Kaisen compilation film, linked by ALTERNATIVE), is not recognised.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from providers.anilist import ANIME_FORMATS, AniListEdge, AniListMedia

MAIN_RELATIONS = {'SEQUEL', 'PREQUEL'}
EXTRA_RELATIONS = {'SIDE_STORY', 'SUMMARY', 'SPIN_OFF', 'ALTERNATIVE', 'PARENT'}


@dataclass
class Franchise:
    """A franchise split into the main chain and its extras, both chronological."""

    main: list[AniListMedia]
    extras: list[AniListMedia]


def is_compilation(media: AniListMedia) -> bool:
    """Return True for a MOVIE or SPECIAL that names a PARENT anime entry."""
    return media.format in ('MOVIE', 'SPECIAL') and any(
        edge.relation == 'PARENT' and edge.media_type == 'ANIME' for edge in media.edges
    )


def _walkable(edge: AniListEdge, source: AniListMedia) -> bool:
    """Return True for an edge the walk follows.

    That is a SEQUEL or PREQUEL to a known anime format, and also the PARENT
    edge of a compilation, so that a franchise found through a compilation film
    still reaches its seasons.
    """
    if edge.media_type != 'ANIME' or edge.format not in ANIME_FORMATS:
        return False
    if edge.relation in MAIN_RELATIONS:
        return True
    return edge.relation == 'PARENT' and is_compilation(source)


def _is_extra_edge(edge: AniListEdge) -> bool:
    """Return True for an edge that lists its target as an extra.

    Besides the extra relations this keeps sequels and prequels whose format is
    still null (freshly announced), instead of dropping them.
    """
    if edge.media_type != 'ANIME':
        return False
    if edge.relation in EXTRA_RELATIONS:
        return edge.format in ANIME_FORMATS or edge.format is None
    return edge.relation in MAIN_RELATIONS and edge.format is None


def load_franchise(
    fetch_many: Callable[[list[int]], Iterable[AniListMedia]], root_id: int
) -> Franchise:
    """Fetch and group the franchise around an entry.

    Args:
        fetch_many: Returns the entries for a list of AniList ids; ids it does
            not know are simply missing. ``AniListClient.media_many`` fits.
        root_id: AniList id of any entry of the franchise.

    Returns:
        The franchise. Unknown start dates sort last.
    """
    walked: dict[int, AniListMedia] = {}
    asked: set[int] = set()
    frontier = [root_id]
    while frontier:
        asked.update(frontier)
        found = list(fetch_many(frontier))
        walked.update({media.anilist_id: media for media in found})
        frontier = list(
            dict.fromkeys(
                edge.anilist_id
                for media in found
                for edge in media.edges
                if _walkable(edge, media) and edge.anilist_id not in asked
            )
        )
    main = [media for media in walked.values() if not is_compilation(media)]
    extra_ids = {
        edge.anilist_id
        for media in main
        for edge in media.edges
        if _is_extra_edge(edge) and edge.anilist_id not in walked
    }
    extras = [media for media in walked.values() if is_compilation(media)]
    if extra_ids:
        extras.extend(fetch_many(sorted(extra_ids)))
    main.sort(key=lambda media: media.sort_key)
    extras.sort(key=lambda media: media.sort_key)
    return Franchise(main=main, extras=extras)
```

Replace the whole file `sources/providers/clients.py` (it gains `get_anilist`):

**Replace the whole file `sources/providers/clients.py`:**

```python
"""Factories that wire the provider clients to Django settings."""

import httpx
from django.conf import settings
from mitaroku.config import load_settings
from providers.anilist import AniListClient
from providers.http import ProviderError, ProviderHttp
from providers.tmdb import TmdbClient
from redis import Redis

TIMEOUT = 30.0


def connect_redis() -> Redis:
    """Return a Redis connection that decodes responses to ``str``."""
    return Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )


def user_agent() -> str:
    """Return the ``User-Agent`` header, naming the operator when configured."""
    contact = load_settings().providers.contact
    return f'mitaroku/0.1 ({contact})' if contact else 'mitaroku/0.1'


def get_tmdb() -> TmdbClient:
    """Return a TMDB client.

    Raises:
        ProviderError: No TMDB token is configured.
    """
    config = load_settings().providers
    if config.tmdb_token is None:
        raise ProviderError('TMDB is not configured: set providers.tmdb_token')
    client = httpx.Client(
        timeout=TIMEOUT,
        headers={
            'Authorization': f'Bearer {config.tmdb_token.get_secret_value()}',
            'User-Agent': user_agent(),
        },
    )
    http = ProviderHttp(
        'tmdb',
        client=client,
        redis=connect_redis(),
        min_interval=config.tmdb_min_interval,
    )
    return TmdbClient(http)


def get_anilist() -> AniListClient:
    """Return an AniList client; it needs no key."""
    config = load_settings().providers
    client = httpx.Client(timeout=TIMEOUT, headers={'User-Agent': user_agent()})
    http = ProviderHttp(
        'anilist',
        client=client,
        redis=connect_redis(),
        min_interval=config.anilist_min_interval,
    )
    return AniListClient(http)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_anilist.py tests/test_franchise.py
```

Expected: 23 passed (9 + 14).

- [ ] **Step 5: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add the AniList client and franchise grouping"
```

---

### Task 6: The Fribb id bridge

**Files:**
- Create: `sources/providers/models.py`, `sources/providers/migrations/__init__.py`, `sources/providers/migrations/0001_initial.py` (generated), `sources/providers/mapping.py`, `sources/providers/tasks.py`, `sources/providers/management/__init__.py`, `sources/providers/management/commands/__init__.py`, `sources/providers/management/commands/refresh_mapping.py`, `tests/test_mapping.py`

**Interfaces:**
- Consumes: `ProviderError` (Task 2); `ProvidersConfig.fribb_url` (Task 1).
- Produces:
  - `providers.models.AnimeMapping(anilist_id [pk], mal_id, entry_type, tmdb_tv_id, tmdb_movie_id, imdb_id, tmdb_season, tmdb_episode_offset)`. Dataset entries without an AniList id are skipped; a missing MAL id is allowed.
  - `providers.mapping.download_dataset(url, client) -> list[dict]` (raises `ProviderError` on a failed download, invalid JSON, or fewer than 10,000 entries), `parse_entry(entry) -> AnimeMapping | None`, `load_entries(entries) -> int` (upserts by AniList id in one transaction, 2,000 rows per statement).
  - `providers.tasks.refresh_mapping_now() -> int` and the periodic task `refresh_mapping_weekly` (Monday 03:30 UTC, 2 retries).
  - `manage.py refresh_mapping`.

The dataset's shapes were checked against the real file (20,862 entries with an AniList id): `themoviedb_id` is `{'tv': int}` or `{'movie': [int]}`, `season` and `episode_offset` are `{'tmdb': int | None, 'tvdb': int}` with either key possibly missing, `imdb_id` is a list.

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_mapping.py`:**

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_mapping.py
```

Expected: an error during collection, `ModuleNotFoundError: No module named 'providers.mapping'`.

- [ ] **Step 3: Write the model, the loader, the task and the command**

**Create `sources/providers/models.py`:**

```python
"""Provider data that lives in the database."""

from django.db import models


class AnimeMapping(models.Model):
    """One row of the Fribb/anime-lists dataset, keyed by AniList id.

    The dataset bridges AniList to TMDB and IMDb ids and gives the TMDB season
    and episode offset an entry occupies. It has known errors, so the sync only
    trusts a mapping after the episode guard accepts it.
    """

    anilist_id = models.PositiveIntegerField(primary_key=True)
    mal_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    entry_type = models.CharField(max_length=8, blank=True)
    tmdb_tv_id = models.PositiveIntegerField(null=True, blank=True)
    tmdb_movie_id = models.PositiveIntegerField(null=True, blank=True)
    imdb_id = models.CharField(max_length=16, blank=True)
    tmdb_season = models.PositiveSmallIntegerField(null=True, blank=True)
    tmdb_episode_offset = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f'anilist:{self.anilist_id}'
```

**Create the empty file `sources/providers/migrations/__init__.py`.**

**Create `sources/providers/mapping.py`:**

```python
"""Load the Fribb/anime-lists dataset into the ``AnimeMapping`` table."""

import logging
from collections.abc import Iterable, Iterator

import httpx
from django.db import transaction
from providers.http import ProviderError
from providers.models import AnimeMapping

logger = logging.getLogger(__name__)

CHUNK = 2000
# The real dataset has about 40,000 entries; far fewer means a bad download.
MIN_ENTRIES = 10_000
UPDATE_FIELDS = [
    'mal_id',
    'entry_type',
    'tmdb_tv_id',
    'tmdb_movie_id',
    'imdb_id',
    'tmdb_season',
    'tmdb_episode_offset',
]


def download_dataset(url: str, client: httpx.Client) -> list[dict]:
    """Download the dataset.

    Args:
        url: Location of ``anime-list-full.json``.
        client: HTTP client to use.

    Returns:
        The parsed entries.

    Raises:
        ProviderError: The download failed or the file does not look like the
            dataset, so the previous data stays in place.
    """
    try:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
        entries = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ProviderError(f'Fribb dataset download failed: {error}') from error
    if not isinstance(entries, list) or len(entries) < MIN_ENTRIES:
        raise ProviderError('Fribb dataset looks truncated, keeping the old data')
    return entries


def parse_entry(entry: dict) -> AnimeMapping | None:
    """Convert one dataset entry, or return None when it has no AniList id."""
    anilist_id = entry.get('anilist_id')
    if not anilist_id:
        return None
    tmdb = entry.get('themoviedb_id') or {}
    movie = tmdb.get('movie')
    imdb = entry.get('imdb_id') or []
    season = (entry.get('season') or {}).get('tmdb')
    offset = (entry.get('episode_offset') or {}).get('tmdb') or 0
    return AnimeMapping(
        anilist_id=anilist_id,
        mal_id=entry.get('mal_id'),
        entry_type=entry.get('type') or '',
        tmdb_tv_id=tmdb.get('tv'),
        tmdb_movie_id=(movie[0] if isinstance(movie, list) and movie else movie),
        imdb_id=imdb[0] if isinstance(imdb, list) and imdb else '',
        tmdb_season=season,
        tmdb_episode_offset=offset,
    )


def _chunks(rows: Iterable[AnimeMapping]) -> Iterator[list[AnimeMapping]]:
    """Yield the rows in chunks of ``CHUNK``."""
    chunk = []
    for row in rows:
        chunk.append(row)
        if len(chunk) == CHUNK:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def load_entries(entries: Iterable[dict]) -> int:
    """Upsert dataset entries by AniList id.

    Args:
        entries: Parsed dataset entries; those without an AniList id are skipped.

    Returns:
        The number of rows written.
    """
    rows = (row for row in map(parse_entry, entries) if row is not None)
    written = 0
    with transaction.atomic():
        for chunk in _chunks(rows):
            AnimeMapping.objects.bulk_create(
                chunk,
                update_conflicts=True,
                unique_fields=['anilist_id'],
                update_fields=UPDATE_FIELDS,
            )
            written += len(chunk)
    logger.info('Fribb mapping refreshed: %s rows', written)
    return written
```

**Create `sources/providers/tasks.py`:**

```python
"""Background tasks of the providers app."""

import httpx
from huey import crontab
from huey.contrib.djhuey import db_periodic_task
from mitaroku.config import load_settings
from providers import mapping


def refresh_mapping_now() -> int:
    """Download the Fribb dataset and load it; return the number of rows."""
    with httpx.Client(timeout=120) as client:
        entries = mapping.download_dataset(load_settings().providers.fribb_url, client)
    return mapping.load_entries(entries)


@db_periodic_task(
    crontab(day_of_week='1', hour='3', minute='30'), retries=2, retry_delay=600
)
def refresh_mapping_weekly() -> int:
    """Refresh the Fribb mapping every Monday at 03:30 UTC."""
    return refresh_mapping_now()
```

**Create the empty file `sources/providers/management/__init__.py`.**

**Create the empty file `sources/providers/management/commands/__init__.py`.**

**Create `sources/providers/management/commands/refresh_mapping.py`:**

```python
from django.core.management.base import BaseCommand
from providers.tasks import refresh_mapping_now


class Command(BaseCommand):
    help = 'Download the Fribb/anime-lists dataset and load it into the database.'

    def handle(self, *args, **options) -> None:
        count = refresh_mapping_now()
        self.stdout.write(self.style.SUCCESS(f'Loaded {count} anime mappings.'))
```

Generate the migration:

```bash
python sources/manage.py makemigrations providers
ruff format sources
```

Expected: `sources/providers/migrations/0001_initial.py` creating `AnimeMapping`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_mapping.py
python sources/manage.py makemigrations --check --dry-run
```

Expected: 10 passed; `No changes detected`.

- [ ] **Step 5: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: load the Fribb id mapping into the database"
```

---

### Task 7: The episode guard and allocation

**Files:**
- Create: `sources/catalog/episodes.py`, `tests/test_episodes.py`

**Interfaces:**
- Consumes: `TmdbEpisode`, `TmdbSeason` (Task 4).
- Produces (all pure functions, no I/O):
  - `catalog.episodes.EntrySpec(anilist_id, format, start, expected, tmdb_season, offset, sort_key)`: `start` is the full AniList start date or None, `expected` the AniList episode count or None, `tmdb_season` and `offset` come from Fribb.
  - `passes_guard(part, expected, start) -> bool`: true only when `part` is not empty, `start` is known, the first episode is dated and within `GUARD_DAYS` (2) of `start`, and `len(part) == expected` when `expected` is known.
  - `allocate_show(seasons, specs) -> dict[int, list[TmdbEpisode] | None]`: for the entries Fribb maps to one TMDB show. For each entry in release order it tries the Fribb season and offset first, then (for `TV`, `TV_SHORT` and `ONA`) the slice of all regular seasons glued together by the stream entries' counts (an entry without a count takes the rest); the first candidate that passes the guard wins, otherwise None. Season 0 (specials) serves only Fribb's candidate, so OVAs and specials never consume the stream.

This is the "hybrid" of the spike, which resolved 29 of 38 TV and ONA entries on the twelve fixtures.

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_episodes.py`:**

```python
from datetime import date, timedelta

from catalog.episodes import EntrySpec, allocate_show, passes_guard
from providers.tmdb import TmdbEpisode, TmdbSeason

START = date(2020, 1, 1)


def episodes(count, first=START, first_number=1):
    return [
        TmdbEpisode(1, first_number + i, f'ep{i}', '', first + timedelta(days=7 * i))
        for i in range(count)
    ]


def season(number, count, first=START):
    return TmdbSeason(number, f'S{number}', episodes(count, first))


def spec(anilist_id, start, expected, fmt='TV', tmdb_season=None, offset=0):
    return EntrySpec(
        anilist_id=anilist_id,
        format=fmt,
        start=start,
        expected=expected,
        tmdb_season=tmdb_season,
        offset=offset,
        sort_key=(start.year, start.month, start.day) if start else (9999, 12, 31),
    )


def test_guard_needs_the_first_episode_close_to_the_start_date():
    part = episodes(12)
    assert passes_guard(part, 12, START)
    assert passes_guard(part, 12, START + timedelta(days=2))
    assert passes_guard(part, 12, START - timedelta(days=2))
    assert not passes_guard(part, 12, START + timedelta(days=3))


def test_guard_rejects_an_off_by_one_length():
    assert not passes_guard(episodes(12), 13, START)
    assert not passes_guard(episodes(13), 12, START)


def test_guard_ignores_the_length_while_the_count_is_unknown():
    assert passes_guard(episodes(5), None, START)


def test_guard_rejects_missing_dates_and_empty_parts():
    assert not passes_guard([], 12, START)
    assert not passes_guard(episodes(12), 12, None)
    undated = [TmdbEpisode(1, 1, 'x', '', None)]
    assert not passes_guard(undated, 1, START)


def test_fribb_season_and_offset_pick_the_right_slice():
    # One TMDB season of 24 that AniList splits into two cours of 12.
    seasons = {1: season(1, 24)}
    second_start = START + timedelta(days=7 * 12)
    result = allocate_show(
        seasons,
        [
            spec(1, START, 12, tmdb_season=1, offset=0),
            spec(2, second_start, 12, tmdb_season=1, offset=12),
        ],
    )
    assert [e.number for e in result[2]] == list(range(13, 25))
    assert len(result[1]) == 12


def test_concatenation_rescues_an_entry_fribb_maps_wrongly():
    seasons = {1: season(1, 12), 2: season(2, 12, START + timedelta(days=7 * 12))}
    result = allocate_show(
        seasons,
        [
            spec(1, START, 12, tmdb_season=1),
            # Fribb says season 5, which TMDB does not have.
            spec(2, START + timedelta(days=7 * 12), 12, tmdb_season=5),
        ],
    )
    assert result[2] is not None
    assert result[2][0].air_date == START + timedelta(days=7 * 12)


def test_an_entry_that_fails_both_candidates_is_none():
    seasons = {1: season(1, 12, START + timedelta(days=100))}
    result = allocate_show(seasons, [spec(1, START, 12, tmdb_season=1)])
    assert result == {1: None}


def test_season_zero_serves_specials_and_ovas_through_fribb_only():
    seasons = {0: season(0, 3), 1: season(1, 12)}
    ova = spec(5, START, 3, fmt='OVA', tmdb_season=0)
    result = allocate_show(seasons, [ova, spec(1, START, 12, tmdb_season=1)])
    assert len(result[5]) == 3
    # An OVA never takes episodes from the regular stream.
    no_fribb = spec(6, START, 12, fmt='OVA')
    assert allocate_show(seasons, [no_fribb]) == {6: None}


def test_ova_entries_do_not_consume_the_stream():
    seasons = {1: season(1, 12), 2: season(2, 12, START + timedelta(days=100))}
    result = allocate_show(
        seasons,
        [
            spec(1, START, 12),
            spec(9, START + timedelta(days=50), 1, fmt='OVA'),
            spec(2, START + timedelta(days=100), 12),
        ],
    )
    assert result[2] is not None and result[2][0].number == 1


def test_a_running_entry_with_an_unknown_count_takes_the_rest():
    seasons = {1: season(1, 12), 2: season(2, 5, START + timedelta(days=100))}
    result = allocate_show(
        seasons, [spec(1, START, 12), spec(2, START + timedelta(days=100), None)]
    )
    assert len(result[2]) == 5


def test_unknown_start_date_means_no_allocation():
    seasons = {1: season(1, 12)}
    assert allocate_show(seasons, [spec(1, None, 12, tmdb_season=1)]) == {1: None}


def test_tmdb_having_fewer_episodes_than_anilist_is_rejected():
    # Bakemonogatari: AniList counts 15, TMDB season 1 has 12.
    seasons = {1: season(1, 12)}
    assert allocate_show(seasons, [spec(1, START, 15, tmdb_season=1)]) == {1: None}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_episodes.py
```

Expected: an error during collection, `ModuleNotFoundError: No module named 'catalog.episodes'`.

- [ ] **Step 3: Write the module**

**Create `sources/catalog/episodes.py`:**

```python
"""Decide which TMDB episodes belong to which AniList entry.

TMDB lists one show where AniList has many cours, and the Fribb dataset that
bridges them has known errors. So an allocation is accepted only when it passes
a guard: the first episode airs within two days of the AniList start date, and
the number of episodes equals the AniList count when that is known. Anything
that fails the guard falls back to the degraded mode (see ``catalog.sync``).
"""

from dataclasses import dataclass
from datetime import date

from providers.tmdb import TmdbEpisode, TmdbSeason

GUARD_DAYS = 2
# Formats whose episodes continue each other inside one TMDB show.
STREAM_FORMATS = {'TV', 'TV_SHORT', 'ONA'}


@dataclass(frozen=True)
class EntrySpec:
    """What the allocation needs to know about one AniList entry.

    Attributes:
        anilist_id: AniList id of the entry.
        format: AniList format (TV, ONA, OVA, SPECIAL, ...).
        start: Full AniList start date, or None when any part is unknown.
        expected: AniList episode count, or None while the show is running.
        tmdb_season: TMDB season the Fribb dataset gives, or None.
        offset: Episodes of that season that belong to earlier entries.
        sort_key: AniList chronological key (unknown dates last).
    """

    anilist_id: int
    format: str
    start: date | None
    expected: int | None
    tmdb_season: int | None
    offset: int
    sort_key: tuple[int, int, int]


def passes_guard(
    part: list[TmdbEpisode], expected: int | None, start: date | None
) -> bool:
    """Return True when an allocation is trustworthy.

    Args:
        part: The episodes allocated to an entry.
        expected: AniList episode count, or None when unknown.
        start: AniList start date, or None when not fully known.

    Returns:
        True when the first episode airs within ``GUARD_DAYS`` of ``start`` and
        the length equals ``expected`` (when known).
    """
    if not part or start is None or part[0].air_date is None:
        return False
    if expected is not None and len(part) != expected:
        return False
    return abs((part[0].air_date - start).days) <= GUARD_DAYS


def fribb_part(seasons: dict[int, TmdbSeason], spec: EntrySpec) -> list[TmdbEpisode]:
    """Take the entry's episodes from the TMDB season the Fribb dataset names."""
    if spec.tmdb_season is None or spec.tmdb_season not in seasons:
        return []
    episodes = seasons[spec.tmdb_season].episodes[spec.offset :]
    return episodes if spec.expected is None else episodes[: spec.expected]


def concatenated_parts(
    seasons: dict[int, TmdbSeason], specs: list[EntrySpec]
) -> dict[int, list[TmdbEpisode]]:
    """Slice all regular seasons, glued together, by the entries' counts.

    Args:
        seasons: TMDB seasons of one show; season 0 (specials) is not part of
            the stream.
        specs: The stream entries in release order.

    Returns:
        ``{anilist_id: episodes}``. An entry without a known count takes the rest.
    """
    stream = [
        episode
        for number in sorted(seasons)
        if number >= 1
        for episode in seasons[number].episodes
    ]
    parts = {}
    position = 0
    for spec in specs:
        end = len(stream) if spec.expected is None else position + spec.expected
        parts[spec.anilist_id] = stream[position:end]
        position = min(end, len(stream))
    return parts


def allocate_show(
    seasons: dict[int, TmdbSeason], specs: list[EntrySpec]
) -> dict[int, list[TmdbEpisode] | None]:
    """Allocate the episodes of one TMDB show to the AniList entries mapped to it.

    For each entry the Fribb season and offset is tried first, then (for TV, ONA
    and TV_SHORT entries) the concatenated stream; the first candidate that
    passes the guard wins.

    Args:
        seasons: All TMDB seasons of the show, season 0 included.
        specs: The entries Fribb maps to this show.

    Returns:
        ``{anilist_id: episodes}``, with None for entries no candidate satisfied.
    """
    ordered = sorted(specs, key=lambda spec: spec.sort_key)
    stream = concatenated_parts(
        seasons, [spec for spec in ordered if spec.format in STREAM_FORMATS]
    )
    result: dict[int, list[TmdbEpisode] | None] = {}
    for spec in ordered:
        candidates = [fribb_part(seasons, spec)]
        if spec.format in STREAM_FORMATS:
            candidates.append(stream[spec.anilist_id])
        result[spec.anilist_id] = next(
            (
                part
                for part in candidates
                if passes_guard(part, spec.expected, spec.start)
            ),
            None,
        )
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_episodes.py
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add the TMDB episode guard and allocation"
```

---

### Task 8: Sync movies, TV series and anime

**Files:**
- Create: `sources/catalog/sync/__init__.py`, `sources/catalog/sync/common.py`, `sources/catalog/sync/movies.py`, `sources/catalog/sync/tv.py`, `sources/catalog/sync/anime.py`, `tests/test_sync_tmdb.py`, `tests/test_sync_anime.py`
- Replace: `tests/fakes.py`

**Interfaces:**
- Consumes: `TmdbClient` (Task 4), `AniListClient` and `load_franchise` (Task 5), `AnimeMapping` (Task 6), `allocate_show` and `EntrySpec` (Task 7), the catalog models (Task 3).
- Produces (`catalog.sync`):
  - `Clients(tmdb, anilist)` dataclass (`tmdb` may be None) and `default_clients()`, which builds the configured clients and logs a warning when no TMDB token is set.
  - `sync_title(title, clients=None, now=None) -> None`: refreshes a title and everything under it; raises `ProviderError` when a provider fails, the id is unknown, or the TMDB token is missing for a movie or TV title (anime degrade instead). Network calls come first, then one transaction writes; `synced_at` and `next_sync_at` are set from the status (airing 6 h, upcoming 24 h, ended and canceled 14 days, unknown 1 day).
  - `get_or_create_title(hit: SearchHit) -> (Title, created)`: looks the provider id up first, so any entry of a known franchise returns that franchise; a new title is bare (the hit's name and poster) until synced.
  - Movie: fields, rating, IMDb id and English text from TMDB. TV: also every season including specials (season 0 is an extra) and every episode, with `absolute_number` counted across the regular seasons. Anime: described below.
  - Anime rules: seasons are matched by AniList id, ordered main chain first then extras; kinds map `TV`/`TV_SHORT` to `tv`, `ONA`, `OVA`, `MOVIE`, `SPECIAL` (an unknown format is an extra of kind `special`); a `MOVIE` entry has one episode on its release date; an entry with an accepted TMDB allocation gets those episodes with English titles and their TMDB season and episode numbers; every other entry is degraded: episodes `1..N` from the AniList count (or, for a running entry without a count, from the schedule), dated only from a complete airing schedule plus any upcoming nodes; the schedule is fetched for main entries and for running extras only. Title status: any `RELEASING` or `HIATUS` entry means airing, else any `NOT_YET_RELEASED` means upcoming, else ended or canceled. The title links every AniList id, MAL id, TMDB show and IMDb id of the franchise unless that id already belongs to another title.
  - Test helpers in `tests/fakes.py`: `FakeTmdb`, `FakeAniList`, `tmdb_movie`, `tmdb_show`, `tmdb_season`, `tmdb_episodes`, `edge`, `media`.

- [ ] **Step 1: Write the failing tests**

Replace the whole file `tests/fakes.py`:

**Replace the whole file `tests/fakes.py`:**

```python
"""In-memory stand-ins for the provider clients, and data builders."""

from datetime import date, timedelta

from providers.anilist import AniListEdge, AniListMedia, AniListSchedule
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
```

**Create `tests/test_sync_tmdb.py`:**

```python
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from catalog.models import Episode, EpisodeTranslation, ExternalId, Title
from catalog.sync import get_or_create_title, sync_title
from catalog.sync.common import Clients
from fakes import FakeAniList, FakeTmdb, tmdb_movie, tmdb_season, tmdb_show
from providers.http import ProviderError
from providers.types import SearchHit

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def clients(tmdb):
    return Clients(tmdb=tmdb, anilist=FakeAniList())


def movie_title(tmdb_id='603'):
    title = Title.objects.create(kind='movie', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id=tmdb_id)
    return title


def tv_title(tmdb_id='1000'):
    title = Title.objects.create(kind='tv', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_tv', external_id=tmdb_id)
    return title


def test_movie_is_filled_from_tmdb():
    title = movie_title()
    sync_title(title, clients(FakeTmdb(movies={603: tmdb_movie(603)})), NOW)
    title.refresh_from_db()
    assert title.original_title == 'The Matrix (original)'
    assert title.release_date == date(1999, 3, 31)
    assert title.status == Title.Status.ENDED
    assert (title.runtime_minutes, title.rating, title.rating_source) == (
        136,
        Decimal('8.2'),
        'tmdb',
    )
    assert title.rating_votes == 25000
    assert title.poster_url.endswith('/m.jpg')
    assert title.synced_at == NOW
    assert title.next_sync_at == NOW + timedelta(days=14)
    assert title.external_ids.get(provider='imdb').external_id == 'tt0133093'
    assert title.translations.get(language='en').name == 'The Matrix'


def test_an_unreleased_movie_is_upcoming_and_synced_often():
    title = movie_title()
    future = tmdb_movie(
        603, release=date(2099, 1, 1), status='Post Production', rating=None
    )
    sync_title(title, clients(FakeTmdb(movies={603: future})), NOW)
    title.refresh_from_db()
    assert title.status == Title.Status.UPCOMING
    assert title.rating is None and title.rating_source == ''
    assert title.next_sync_at == NOW + timedelta(hours=24)


def test_syncing_a_movie_twice_does_not_duplicate_anything():
    title = movie_title()
    tmdb = FakeTmdb(movies={603: tmdb_movie(603)})
    sync_title(title, clients(tmdb), NOW)
    sync_title(title, clients(tmdb), NOW)
    assert title.external_ids.count() == 2
    assert title.translations.count() == 1


def test_unknown_movie_is_a_provider_error():
    title = movie_title()
    with pytest.raises(ProviderError, match='does not know'):
        sync_title(title, clients(FakeTmdb()), NOW)


def test_missing_tmdb_token_is_a_provider_error():
    title = movie_title()
    with pytest.raises(ProviderError, match='not configured'):
        sync_title(title, Clients(tmdb=None, anilist=FakeAniList()), NOW)


def test_title_without_its_provider_id_is_a_provider_error():
    title = Title.objects.create(kind='movie', original_title='x')
    with pytest.raises(ProviderError, match='no TMDB movie id'):
        sync_title(title, clients(FakeTmdb()), NOW)


def make_show(status='Returning Series'):
    seasons = {
        0: tmdb_season(0, 2, date(2019, 12, 1), 'Specials'),
        1: tmdb_season(1, 3, date(2020, 1, 1)),
        2: tmdb_season(2, 2, date(2021, 1, 1)),
    }
    return FakeTmdb(
        shows={1000: (tmdb_show(1000, list(seasons.values()), status, 'Show'), seasons)}
    )


def test_tv_show_creates_seasons_and_episodes():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    title.refresh_from_db()
    assert title.status == Title.Status.AIRING
    assert title.next_sync_at == NOW + timedelta(hours=6)
    assert [
        (s.number, s.kind, s.is_extra, s.episode_source) for s in title.seasons.all()
    ] == [
        (0, 'special', True, 'tmdb'),
        (1, 'tv', False, 'tmdb'),
        (2, 'tv', False, 'tmdb'),
    ]
    season_one = title.seasons.get(number=1)
    assert season_one.start_date == date(2020, 1, 1)
    assert season_one.episodes.count() == 3
    assert season_one.tmdb_tv_id == 1000


def test_absolute_numbers_run_across_regular_seasons_and_skip_specials():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    absolute = {
        (e.season.number, e.number): e.absolute_number
        for e in Episode.objects.select_related('season')
    }
    assert absolute[(1, 1)] == 1 and absolute[(1, 3)] == 3
    assert absolute[(2, 1)] == 4 and absolute[(2, 2)] == 5
    assert absolute[(0, 1)] is None


def test_episode_texts_are_stored_in_english_and_remember_their_tmdb_origin():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    episode = Episode.objects.get(season__title=title, season__number=2, number=2)
    assert (episode.tmdb_season_number, episode.tmdb_episode_number) == (2, 2)
    assert (
        EpisodeTranslation.objects.get(episode=episode, language='en').name
        == 'Episode 2'
    )


def test_resync_keeps_episode_rows_and_updates_dates():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    before = {
        e.number: e.pk
        for e in Episode.objects.filter(season__title=title, season__number=1)
    }
    moved = make_show()
    episodes = moved.shows[1000][1][1].episodes
    episodes[0] = replace(episodes[0], air_date=date(2020, 2, 1))
    sync_title(title, clients(moved), NOW)
    after = {
        e.number: e.pk
        for e in Episode.objects.filter(season__title=title, season__number=1)
    }
    assert before == after
    assert Episode.objects.get(
        season__title=title, season__number=1, number=1
    ).air_date == date(2020, 2, 1)


def test_an_episode_tmdb_stops_listing_is_kept():
    title = tv_title()
    sync_title(title, clients(make_show()), NOW)
    shorter = make_show()
    shorter.shows[1000][1][2].episodes.pop()
    sync_title(title, clients(shorter), NOW)
    assert Episode.objects.filter(season__title=title, season__number=2).count() == 2


def test_an_ended_show_is_checked_rarely():
    title = tv_title()
    sync_title(title, clients(make_show('Ended')), NOW)
    title.refresh_from_db()
    assert title.status == Title.Status.ENDED
    assert title.next_sync_at == NOW + timedelta(days=14)


def test_unknown_show_is_a_provider_error():
    title = tv_title('999')
    with pytest.raises(ProviderError, match='does not know'):
        sync_title(title, clients(make_show()), NOW)


def hit(provider='tmdb_tv', external_id='1000', kind='tv'):
    return SearchHit(
        provider, external_id, kind, 'Title', 'Original', 2020, 'https://p/x.jpg', ''
    )


def test_a_new_search_hit_creates_a_bare_title():
    title, created = get_or_create_title(hit())
    assert created is True
    assert (title.kind, title.original_title, title.poster_url) == (
        'tv',
        'Original',
        'https://p/x.jpg',
    )
    assert title.external_ids.get().external_id == '1000'
    assert title.synced_at is None


def test_the_same_hit_returns_the_same_title():
    first, _ = get_or_create_title(hit())
    second, created = get_or_create_title(hit())
    assert created is False and second.pk == first.pk
    assert Title.objects.count() == 1


def test_tmdb_movie_and_tv_hits_with_equal_numbers_are_different_titles():
    get_or_create_title(hit('tmdb_tv', '42', 'tv'))
    get_or_create_title(hit('tmdb_movie', '42', 'movie'))
    assert Title.objects.count() == 2


def test_a_hit_without_a_native_title_falls_back_to_the_display_title():
    bare = SearchHit('tmdb_tv', '7', 'tv', 'Display', '', None, '', '')
    title, _ = get_or_create_title(bare)
    assert title.original_title == 'Display'
```

**Create `tests/test_sync_anime.py`:**

```python
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from catalog.models import Episode, EpisodeTranslation, ExternalId, Title
from catalog.sync import get_or_create_title, sync_title
from catalog.sync.common import Clients
from fakes import FakeAniList, FakeTmdb, edge, media, tmdb_season, tmdb_show
from providers.anilist import AniListSchedule
from providers.http import ProviderError
from providers.models import AnimeMapping
from providers.types import SearchHit

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def franchise_entries():
    """Four main entries (A, B, C, D) and three extras (movie, OVA, special)."""
    return [
        media(
            10,
            [edge('SEQUEL', 20), edge('SIDE_STORY', 80, 'SPECIAL')],
            mal_id=110,
            english='Show',
            score=80,
        ),
        media(
            20,
            [
                edge('PREQUEL', 10),
                edge('SEQUEL', 30),
                edge('SUMMARY', 50, 'MOVIE'),
                edge('SIDE_STORY', 40, 'OVA'),
            ],
            month=7,
            mal_id=120,
            score=70,
        ),
        media(
            30,
            [edge('PREQUEL', 20), edge('SEQUEL', 70)],
            year=2021,
            episodes=None,
            status='RELEASING',
            score=None,
        ),
        media(70, [edge('PREQUEL', 30)], year=2022, episodes=13),
        media(50, [edge('PARENT', 20)], fmt='MOVIE', month=10, episodes=1),
        media(40, [], fmt='OVA', month=12, episodes=2),
        media(80, [], fmt='SPECIAL', year=2022, month=6, episodes=4),
    ]


def schedule(first, count, weekly=7):
    return {
        n: datetime(first.year, first.month, first.day, 12, tzinfo=UTC)
        + timedelta(days=weekly * (n - 1))
        for n in range(1, count + 1)
    }


def tmdb_with_show():
    seasons = {
        0: tmdb_season(0, 2, date(2020, 12, 1), 'Specials'),
        1: tmdb_season(1, 12, date(2020, 1, 1)),
        2: tmdb_season(2, 12, date(2020, 7, 1)),
        3: tmdb_season(3, 5, date(2021, 1, 1)),
    }
    return FakeTmdb(shows={2000: (tmdb_show(2000, list(seasons.values())), seasons)})


@pytest.fixture
def mappings():
    AnimeMapping.objects.create(
        anilist_id=10, mal_id=110, tmdb_tv_id=2000, tmdb_season=1, imdb_id='tt0001'
    )
    # Fribb is wrong about B (a season TMDB does not have); concatenation rescues it.
    AnimeMapping.objects.create(
        anilist_id=20, mal_id=120, tmdb_tv_id=2000, tmdb_season=9
    )
    AnimeMapping.objects.create(anilist_id=30, tmdb_tv_id=2000, tmdb_season=3)
    AnimeMapping.objects.create(anilist_id=40, tmdb_tv_id=2000, tmdb_season=0)
    AnimeMapping.objects.create(anilist_id=50, tmdb_movie_id=777)


def anime_title(anilist_id='10'):
    title = Title.objects.create(kind='anime', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='anilist', external_id=anilist_id)
    return title


def clients(tmdb=None, anilist=None):
    return Clients(
        tmdb=tmdb,
        anilist=anilist
        or FakeAniList(
            *franchise_entries(),
            schedules={
                70: AniListSchedule(upcoming={}, all=schedule(date(2022, 1, 1), 13))
            },
        ),
    )


def seasons_of(title):
    return {s.anilist_id: s for s in title.seasons.all()}


def test_structure_kinds_and_order(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    assert [
        (s.number, s.anilist_id, s.kind, s.is_extra) for s in title.seasons.all()
    ] == [
        (1, 10, 'tv', False),
        (2, 20, 'tv', False),
        (3, 30, 'tv', False),
        (4, 70, 'tv', False),
        (5, 50, 'movie', True),
        (6, 40, 'ova', True),
        (7, 80, 'special', True),
    ]


def test_guarded_tmdb_episodes_carry_titles_and_remember_their_origin(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    seasons = seasons_of(title)
    for anilist_id, expected in ((10, 12), (20, 12), (30, 5), (40, 2)):
        assert seasons[anilist_id].episode_source == 'tmdb'
        assert seasons[anilist_id].episodes.count() == expected
        assert seasons[anilist_id].tmdb_tv_id == 2000
    second = seasons[20].episodes.get(number=1)
    assert (second.tmdb_season_number, second.tmdb_episode_number) == (2, 1)
    assert second.air_date == date(2020, 7, 1)
    assert (
        EpisodeTranslation.objects.get(episode=second, language='en').name
        == 'Episode 1'
    )
    ova = seasons[40].episodes.get(number=1)
    assert ova.tmdb_season_number == 0


def test_running_entry_with_an_unknown_count_takes_what_tmdb_has(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    running = seasons_of(title)[30]
    assert running.episode_count is None
    assert running.episodes.count() == 5


def test_degraded_entry_uses_the_complete_airing_schedule(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    degraded = seasons_of(title)[70]
    assert degraded.episode_source == 'anilist'
    assert degraded.episodes.count() == 13
    assert degraded.episodes.get(number=1).air_date == date(2022, 1, 1)
    assert degraded.episodes.get(number=3).air_date == date(2022, 1, 15)
    assert not EpisodeTranslation.objects.filter(episode__season=degraded).exists()
    assert degraded.episodes.get(number=1).tmdb_season_number is None


def test_extras_get_counts_but_no_schedule_lookup(mappings):
    title = anime_title()
    anilist = clients(tmdb_with_show()).anilist
    sync_title(title, Clients(tmdb_with_show(), anilist), NOW)
    special = seasons_of(title)[80]
    assert special.episodes.count() == 4
    assert all(e.air_date is None for e in special.episodes.all())
    assert anilist.schedule_calls == [70]


def test_movie_entry_has_one_episode_on_its_release_date(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    movie = seasons_of(title)[50]
    assert movie.episode_source == 'anilist'
    assert [e.air_date for e in movie.episodes.all()] == [date(2020, 10, 1)]


def test_absolute_numbers_cover_the_main_seasons_only(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    seasons = seasons_of(title)
    assert seasons[10].episodes.get(number=1).absolute_number == 1
    assert seasons[20].episodes.get(number=1).absolute_number == 13
    assert seasons[30].episodes.get(number=5).absolute_number == 29
    assert seasons[70].episodes.get(number=13).absolute_number == 42
    assert not Episode.objects.filter(
        season__is_extra=True, absolute_number__isnull=False
    ).exists()


def test_title_fields_and_provider_ids(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    title.refresh_from_db()
    assert (title.original_title, title.original_language) == ('n10', 'ja')
    assert title.status == Title.Status.AIRING
    assert title.release_date == date(2020, 1, 1)
    assert title.poster_url == 'https://img.example/10.jpg'
    # The mean of the known main scores 80, 70 and 80 is 76.7, shown as 7.7.
    assert (str(title.rating), title.rating_source) == ('7.7', 'anilist')
    assert title.translations.get(language='en').name == 'Show'
    assert title.next_sync_at == NOW + timedelta(hours=6)
    links = {(link.provider, link.external_id) for link in title.external_ids.all()}
    assert {('anilist', str(i)) for i in (10, 20, 30, 70, 50, 40, 80)} <= links
    assert {
        ('mal', '110'),
        ('mal', '120'),
        ('tmdb_tv', '2000'),
        ('imdb', 'tt0001'),
    } <= links


def test_any_entry_finds_the_franchise_title(mappings):
    title = anime_title('20')
    sync_title(title, clients(tmdb_with_show()), NOW)
    hit = SearchHit('anilist', '10', 'anime', 'Show', 'n10', 2020, '', '')
    found, created = get_or_create_title(hit)
    assert created is False and found.pk == title.pk


def test_without_a_tmdb_client_every_entry_is_degraded(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb=None), NOW)
    sources = {s.anilist_id: s.episode_source for s in title.seasons.all()}
    # The running entry has no known count and no schedule, so it has no episodes.
    assert sources.pop(30) == 'none'
    assert set(sources.values()) == {'anilist'}
    assert not EpisodeTranslation.objects.exists()
    assert seasons_of(title)[10].episodes.count() == 12


def test_running_degraded_entry_keeps_only_upcoming_dates():
    entries = franchise_entries()
    upcoming = {
        13: datetime(2026, 11, 1, 12, tzinfo=UTC),
        14: datetime(2026, 11, 8, 12, tzinfo=UTC),
    }
    anilist = FakeAniList(
        *entries, schedules={30: AniListSchedule(upcoming=upcoming, all={})}
    )
    title = anime_title()
    sync_title(title, Clients(None, anilist), NOW)
    running = seasons_of(title)[30]
    assert [(e.number, e.air_date) for e in running.episodes.all()] == [
        (13, date(2026, 11, 1)),
        (14, date(2026, 11, 8)),
    ]


def test_an_incomplete_schedule_gives_counts_without_dates():
    entries = franchise_entries()
    partial = AniListSchedule(upcoming={}, all=schedule(date(2022, 1, 1), 3))
    anilist = FakeAniList(*entries, schedules={70: partial})
    title = anime_title()
    sync_title(title, Clients(None, anilist), NOW)
    degraded = seasons_of(title)[70]
    assert degraded.episodes.count() == 13
    assert all(e.air_date is None for e in degraded.episodes.all())


def test_resync_is_idempotent(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    before = list(
        Episode.objects.order_by('pk').values_list('pk', 'season__anilist_id', 'number')
    )
    sync_title(title, clients(tmdb_with_show()), NOW)
    after = list(
        Episode.objects.order_by('pk').values_list('pk', 'season__anilist_id', 'number')
    )
    assert before == after
    assert title.seasons.count() == 7
    assert title.external_ids.count() == len(
        set(title.external_ids.values_list('provider', 'external_id'))
    )


def test_air_dates_are_the_day_in_japan():
    late_night = {
        1: datetime(2022, 1, 1, 15, 30, tzinfo=UTC)
    }  # 00:30 on 2 January in Japan
    entries = [media(30, [], episodes=None, status='RELEASING')]
    anilist = FakeAniList(
        *entries, schedules={30: AniListSchedule(upcoming=late_night, all={})}
    )
    title = anime_title('30')
    sync_title(title, Clients(None, anilist), NOW)
    assert seasons_of(title)[30].episodes.get().air_date == date(2022, 1, 2)


def test_a_new_entry_in_the_middle_renumbers_without_touching_existing_rows(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    pks = {s.anilist_id: s.pk for s in title.seasons.all()}
    entries = franchise_entries()
    entries[1] = media(
        20,
        [
            edge('PREQUEL', 10),
            edge('SEQUEL', 25),
            edge('SUMMARY', 50, 'MOVIE'),
            edge('SIDE_STORY', 40, 'OVA'),
        ],
        month=7,
        mal_id=120,
    )
    entries[2] = media(
        30,
        [edge('PREQUEL', 25), edge('SEQUEL', 70)],
        year=2021,
        episodes=None,
        status='RELEASING',
        score=None,
    )
    entries.append(
        media(25, [edge('PREQUEL', 20), edge('SEQUEL', 30)], year=2020, month=10)
    )
    anilist = FakeAniList(*entries)
    sync_title(title, clients(tmdb_with_show(), anilist), NOW)
    seasons = seasons_of(title)
    assert [seasons[i].number for i in (10, 20, 25, 30, 70)] == [1, 2, 3, 4, 5]
    assert all(seasons[i].pk == pk for i, pk in pks.items())


def test_an_entry_that_left_the_franchise_is_parked_as_an_extra(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    entries = [e for e in franchise_entries() if e.anilist_id != 70]
    entries[2] = media(
        30,
        [edge('PREQUEL', 20)],
        year=2021,
        episodes=None,
        status='RELEASING',
        score=None,
    )
    sync_title(title, clients(tmdb_with_show(), FakeAniList(*entries)), NOW)
    gone = seasons_of(title)[70]
    assert gone.is_extra is True and gone.number == 7  # after the 3 main + 3 extra
    assert gone.episodes.count() == 13
    sync_title(title, clients(tmdb_with_show(), FakeAniList(*entries)), NOW)
    assert seasons_of(title)[70].number == 7  # repeated syncs do not move it again


def test_a_rejected_allocation_removes_the_tmdb_titles_but_keeps_the_episodes(mappings):
    title = anime_title()
    sync_title(title, clients(tmdb_with_show()), NOW)
    first = seasons_of(title)[10]
    ids = set(first.episodes.values_list('pk', flat=True))
    shifted = tmdb_with_show()
    shifted.shows[2000][1][1] = tmdb_season(
        1, 12, date(2020, 2, 20)
    )  # no longer matches
    sync_title(title, clients(shifted), NOW)
    first = seasons_of(title)[10]
    assert first.episode_source == 'anilist'
    assert set(first.episodes.values_list('pk', flat=True)) == ids
    assert not EpisodeTranslation.objects.filter(episode__season=first).exists()
    assert first.episodes.get(number=1).tmdb_season_number is None


def test_a_provider_outage_leaves_the_catalog_untouched(mappings):
    class Down(FakeTmdb):
        def tv(self, tmdb_id, language='en'):
            raise ProviderError('tmdb down')

    title = anime_title()
    with pytest.raises(ProviderError, match='tmdb down'):
        sync_title(title, clients(Down()), NOW)
    title.refresh_from_db()
    assert title.synced_at is None
    assert title.seasons.count() == 0


def test_ids_that_belong_to_another_title_are_not_stolen(mappings, caplog):
    other = Title.objects.create(kind='anime', original_title='other')
    ExternalId.objects.create(title=other, provider='anilist', external_id='80')
    title = anime_title()
    with caplog.at_level(logging.WARNING):
        sync_title(title, clients(tmdb_with_show()), NOW)
    assert (
        ExternalId.objects.get(provider='anilist', external_id='80').title_id
        == other.pk
    )
    assert 'belongs to title' in caplog.text


def test_an_empty_mapping_table_is_reported(caplog):
    title = anime_title()
    with caplog.at_level(logging.WARNING):
        sync_title(title, clients(tmdb_with_show()), NOW)
    assert 'refresh_mapping' in caplog.text


def test_an_entry_anilist_does_not_know_is_a_provider_error():
    title = anime_title('999')
    with pytest.raises(ProviderError, match='does not know'):
        sync_title(title, clients(), NOW)


def test_a_title_without_an_anilist_id_is_a_provider_error():
    title = Title.objects.create(kind='anime', original_title='x')
    with pytest.raises(ProviderError, match='no AniList id'):
        sync_title(title, clients(), NOW)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_sync_tmdb.py tests/test_sync_anime.py
```

Expected: errors during collection, `ModuleNotFoundError: No module named 'catalog.sync'`.

- [ ] **Step 3: Write the shared helpers and the movie and TV sync**

**Create `sources/catalog/sync/common.py`:**

```python
"""Pieces shared by the movie, TV and anime sync."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from catalog.models import (
    Episode,
    EpisodeTranslation,
    ExternalId,
    Season,
    Title,
    TitleTranslation,
)
from providers.anilist import AniListClient
from providers.clients import get_anilist, get_tmdb
from providers.http import ProviderError
from providers.tmdb import TmdbClient

logger = logging.getLogger(__name__)

# How soon a title is looked at again, by status. Airing and announced titles
# change often; finished ones only get a slow check for corrections.
RESYNC_AFTER = {
    Title.Status.AIRING: timedelta(hours=6),
    Title.Status.UPCOMING: timedelta(hours=24),
    Title.Status.ENDED: timedelta(days=14),
    Title.Status.CANCELED: timedelta(days=14),
    Title.Status.UNKNOWN: timedelta(days=1),
}


@dataclass
class Clients:
    """The provider clients a sync needs. ``tmdb`` is None without a token."""

    tmdb: TmdbClient | None
    anilist: AniListClient


@dataclass(frozen=True)
class EpisodeRow:
    """One episode to write, from whichever provider it came from."""

    number: int
    air_date: date | None
    tmdb_season_number: int | None = None
    tmdb_episode_number: int | None = None
    name: str = ''
    overview: str = ''


def default_clients() -> Clients:
    """Build the clients from the configuration; TMDB is optional."""
    try:
        tmdb = get_tmdb()
    except ProviderError as error:
        logger.warning('%s', error)
        tmdb = None
    return Clients(tmdb=tmdb, anilist=get_anilist())


def require_tmdb(clients: Clients) -> TmdbClient:
    """Return the TMDB client, or fail when no token is configured."""
    if clients.tmdb is None:
        raise ProviderError('TMDB is not configured: set providers.tmdb_token')
    return clients.tmdb


def to_rating(value: float | None) -> Decimal | None:
    """Round a provider rating to one decimal place."""
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def schedule_next_sync(title: Title, now: datetime) -> None:
    """Set ``synced_at`` and ``next_sync_at`` according to the title's status."""
    title.synced_at = now
    title.next_sync_at = now + RESYNC_AFTER[Title.Status(title.status)]


def link_external_id(title: Title, provider: str, external_id: object) -> bool:
    """Attach a provider id to a title.

    Returns:
        False when the id already belongs to another title (it is left there).
    """
    link, _ = ExternalId.objects.get_or_create(
        provider=provider, external_id=str(external_id), defaults={'title': title}
    )
    if link.title_id != title.pk:
        logger.warning(
            'Not linking %s:%s to title %s: it belongs to title %s',
            provider,
            external_id,
            title.pk,
            link.title_id,
        )
        return False
    return True


def save_title_translation(
    title: Title, language: str, name: str, overview: str
) -> None:
    """Store a title translation unless both texts are empty."""
    if name or overview:
        TitleTranslation.objects.update_or_create(
            title=title,
            language=language,
            defaults={'name': name, 'overview': overview},
        )


def write_episodes(
    season: Season, rows: list[EpisodeRow], language: str = 'en'
) -> None:
    """Upsert the episodes of a season and their translations.

    Episodes are never deleted, because watch events will refer to them.

    Args:
        season: The season to write into.
        rows: The episodes as the provider reports them.
        language: Language of the names and overviews in ``rows``.
    """
    if not rows:
        return
    Episode.objects.bulk_create(
        [
            Episode(
                season=season,
                number=row.number,
                air_date=row.air_date,
                tmdb_season_number=row.tmdb_season_number,
                tmdb_episode_number=row.tmdb_episode_number,
            )
            for row in rows
        ],
        update_conflicts=True,
        unique_fields=['season', 'number'],
        update_fields=['air_date', 'tmdb_season_number', 'tmdb_episode_number'],
    )
    texts = [row for row in rows if row.name or row.overview]
    if not texts:
        return
    ids = dict(
        season.episodes.filter(number__in=[row.number for row in texts]).values_list(
            'number', 'pk'
        )
    )
    EpisodeTranslation.objects.bulk_create(
        [
            EpisodeTranslation(
                episode_id=ids[row.number],
                language=language,
                name=row.name,
                overview=row.overview,
            )
            for row in texts
        ],
        update_conflicts=True,
        unique_fields=['episode', 'language'],
        update_fields=['name', 'overview'],
    )


def renumber_absolute(title: Title) -> None:
    """Number the episodes of the main seasons 1, 2, 3 ... across the title.

    Extras and specials get no absolute number.
    """
    counter = 0
    changed = []
    for season in title.seasons.order_by('number'):
        for episode in season.episodes.order_by('number'):
            if season.is_extra:
                wanted = None
            else:
                counter += 1
                wanted = counter
            if episode.absolute_number != wanted:
                episode.absolute_number = wanted
                changed.append(episode)
    Episode.objects.bulk_update(changed, ['absolute_number'], batch_size=500)
```

**Create `sources/catalog/sync/movies.py`:**

```python
"""Sync a TMDB movie."""

import logging
from datetime import datetime

from catalog.models import Title
from catalog.sync.common import (
    Clients,
    link_external_id,
    require_tmdb,
    save_title_translation,
    schedule_next_sync,
    to_rating,
)
from providers.http import ProviderError

logger = logging.getLogger(__name__)

STATUS_BY_TMDB = {
    'Released': Title.Status.ENDED,
    'Canceled': Title.Status.CANCELED,
    'Rumored': Title.Status.UPCOMING,
    'Planned': Title.Status.UPCOMING,
    'In Production': Title.Status.UPCOMING,
    'Post Production': Title.Status.UPCOMING,
}


def sync_movie(title: Title, clients: Clients, now: datetime) -> None:
    """Refresh a movie title from TMDB.

    Args:
        title: A title of kind ``movie`` with a ``tmdb_movie`` external id.
        clients: The provider clients.
        now: Current time, stored as ``synced_at``.

    Raises:
        ProviderError: The id is missing, unknown to TMDB, or TMDB is down.
    """
    link = title.external_ids.filter(provider='tmdb_movie').first()
    if link is None:
        raise ProviderError(f'Title {title.pk} has no TMDB movie id')
    movie = require_tmdb(clients).movie(int(link.external_id))
    if movie is None:
        raise ProviderError(f'TMDB does not know movie {link.external_id}')
    title.original_title = movie.original_title or movie.title or title.original_title
    title.original_language = movie.original_language
    title.release_date = movie.release_date
    title.status = STATUS_BY_TMDB.get(movie.status, Title.Status.UNKNOWN)
    if (
        movie.release_date
        and movie.release_date > now.date()
        and title.status == (Title.Status.ENDED)
    ):
        title.status = Title.Status.UPCOMING
    title.runtime_minutes = movie.runtime
    title.poster_url = movie.poster_url
    title.backdrop_url = movie.backdrop_url
    title.rating = to_rating(movie.rating)
    title.rating_votes = movie.votes or None
    title.rating_source = 'tmdb' if movie.rating is not None else ''
    schedule_next_sync(title, now)
    title.save()
    if movie.imdb_id:
        link_external_id(title, 'imdb', movie.imdb_id)
    save_title_translation(title, 'en', movie.title, movie.overview)
```

**Create `sources/catalog/sync/tv.py`:**

```python
"""Sync a TMDB TV show with its seasons and episodes."""

import logging
from datetime import datetime

from catalog.models import Season, Title
from catalog.sync.common import (
    Clients,
    EpisodeRow,
    link_external_id,
    renumber_absolute,
    require_tmdb,
    save_title_translation,
    schedule_next_sync,
    to_rating,
    write_episodes,
)
from django.db import transaction
from providers.http import ProviderError

logger = logging.getLogger(__name__)

STATUS_BY_TMDB = {
    'Returning Series': Title.Status.AIRING,
    'Planned': Title.Status.UPCOMING,
    'In Production': Title.Status.UPCOMING,
    'Pilot': Title.Status.UPCOMING,
    'Ended': Title.Status.ENDED,
    'Canceled': Title.Status.CANCELED,
}


def sync_tv(title: Title, clients: Clients, now: datetime) -> None:
    """Refresh a TV title and all its seasons from TMDB.

    Season 0 (specials) is kept as an extra season. Seasons and episodes that
    TMDB no longer lists are left in place.

    Args:
        title: A title of kind ``tv`` with a ``tmdb_tv`` external id.
        clients: The provider clients.
        now: Current time, stored as ``synced_at``.

    Raises:
        ProviderError: The id is missing, unknown to TMDB, or TMDB is down.
    """
    link = title.external_ids.filter(provider='tmdb_tv').first()
    if link is None:
        raise ProviderError(f'Title {title.pk} has no TMDB TV id')
    tmdb = require_tmdb(clients)
    show = tmdb.tv(int(link.external_id))
    if show is None:
        raise ProviderError(f'TMDB does not know show {link.external_id}')
    seasons = tmdb.seasons(show)
    with transaction.atomic():
        title.original_title = show.original_name or show.name or title.original_title
        title.original_language = show.original_language
        title.release_date = show.first_air_date
        title.status = STATUS_BY_TMDB.get(show.status, Title.Status.UNKNOWN)
        title.poster_url = show.poster_url
        title.backdrop_url = show.backdrop_url
        title.rating = to_rating(show.rating)
        title.rating_votes = show.votes or None
        title.rating_source = 'tmdb' if show.rating is not None else ''
        schedule_next_sync(title, now)
        title.save()
        if show.imdb_id:
            link_external_id(title, 'imdb', show.imdb_id)
        save_title_translation(title, 'en', show.name, show.overview)
        for summary in show.seasons:
            tmdb_season = seasons.get(summary.number)
            if tmdb_season is None:
                continue
            dated = [e.air_date for e in tmdb_season.episodes if e.air_date]
            season, _ = Season.objects.update_or_create(
                title=title,
                number=summary.number,
                defaults={
                    'kind': Season.Kind.SPECIAL
                    if summary.number == 0
                    else Season.Kind.TV,
                    'name': summary.name,
                    'is_extra': summary.number == 0,
                    'tmdb_tv_id': show.tmdb_id,
                    'start_date': min(dated) if dated else None,
                    'episode_count': summary.episode_count,
                    'episode_source': Season.EpisodeSource.TMDB,
                },
            )
            write_episodes(
                season,
                [
                    EpisodeRow(
                        number=episode.number,
                        air_date=episode.air_date,
                        tmdb_season_number=episode.season_number,
                        tmdb_episode_number=episode.number,
                        name=episode.name,
                        overview=episode.overview,
                    )
                    for episode in tmdb_season.episodes
                ],
            )
        renumber_absolute(title)
```

- [ ] **Step 4: Write the anime sync and the entry points**

**Create `sources/catalog/sync/anime.py`:**

```python
"""Sync an anime franchise: AniList structure, TMDB episodes behind the guard."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean

from catalog.episodes import EntrySpec, allocate_show
from catalog.franchise import Franchise, load_franchise
from catalog.models import EpisodeTranslation, Season, Title
from catalog.sync.common import (
    Clients,
    EpisodeRow,
    link_external_id,
    renumber_absolute,
    save_title_translation,
    schedule_next_sync,
    to_rating,
    write_episodes,
)
from django.db import transaction
from providers.anilist import AniListMedia
from providers.http import ProviderError
from providers.models import AnimeMapping
from providers.tmdb import TmdbEpisode

logger = logging.getLogger(__name__)

KIND_BY_FORMAT = {
    'TV': Season.Kind.TV,
    'TV_SHORT': Season.Kind.TV,
    'ONA': Season.Kind.ONA,
    'OVA': Season.Kind.OVA,
    'MOVIE': Season.Kind.MOVIE,
    'SPECIAL': Season.Kind.SPECIAL,
}
# AniList's airing times are timestamps; entries are dated by the day in Japan.
JST = timezone(timedelta(hours=9))
RUNNING = {'RELEASING', 'NOT_YET_RELEASED'}


def title_status(main: list[AniListMedia]) -> str:
    """Derive the franchise status from its main entries."""
    states = {media.status for media in main}
    if 'RELEASING' in states or 'HIATUS' in states:
        return Title.Status.AIRING
    if 'NOT_YET_RELEASED' in states:
        return Title.Status.UPCOMING
    if states == {'CANCELLED'}:
        return Title.Status.CANCELED
    if states and states <= {'FINISHED', 'CANCELLED'}:
        return Title.Status.ENDED
    return Title.Status.UNKNOWN


def resolve_tmdb_episodes(
    clients: Clients,
    entries: list[AniListMedia],
    mappings: dict[int, AnimeMapping],
) -> dict[int, list[TmdbEpisode]]:
    """Return the TMDB episodes the guard accepts, per AniList id.

    Entries the guard rejects, movies, and entries Fribb does not map to a TMDB
    show are absent from the result and go to the degraded mode.

    Raises:
        ProviderError: TMDB is down; the sync is retried rather than degraded,
            so a short outage does not wipe episode titles.
    """
    if clients.tmdb is None:
        return {}
    by_show: dict[int, list[EntrySpec]] = defaultdict(list)
    for media in entries:
        mapping = mappings.get(media.anilist_id)
        if media.format == 'MOVIE' or mapping is None or mapping.tmdb_tv_id is None:
            continue
        by_show[mapping.tmdb_tv_id].append(
            EntrySpec(
                anilist_id=media.anilist_id,
                format=media.format or '',
                start=media.start_date,
                expected=media.episodes,
                tmdb_season=mapping.tmdb_season,
                offset=mapping.tmdb_episode_offset,
                sort_key=media.sort_key,
            )
        )
    accepted: dict[int, list[TmdbEpisode]] = {}
    for tmdb_id, specs in by_show.items():
        show = clients.tmdb.tv(tmdb_id)
        if show is None:
            continue
        allocation = allocate_show(clients.tmdb.seasons(show), specs)
        accepted.update({k: v for k, v in allocation.items() if v is not None})
    return accepted


def degraded_rows(
    media: AniListMedia, is_extra: bool, clients: Clients
) -> list[EpisodeRow]:
    """Build episodes from AniList alone: counts, and dates where they are reliable.

    Per-episode dates for finished episodes are used only when the airing
    schedule has exactly one node per episode. Dates of episodes still to air
    are always used. Times are converted to Japan time to get the air date, as
    AniList's start dates are. No titles exist in this mode.
    """
    needs_schedule = (not is_extra) or media.status in RUNNING
    schedule = clients.anilist.schedule(media.anilist_id) if needs_schedule else None
    dates = {}
    if schedule is not None:
        if media.episodes is not None and len(schedule.all) == media.episodes:
            dates.update(schedule.all)
        dates.update(schedule.upcoming)
    if media.episodes is not None:
        numbers = range(1, media.episodes + 1)
    else:
        numbers = sorted(dates)
    return [
        EpisodeRow(
            number=n, air_date=dates[n].astimezone(JST).date() if n in dates else None
        )
        for n in numbers
    ]


def episode_plan(
    media: AniListMedia,
    is_extra: bool,
    accepted: dict[int, list[TmdbEpisode]],
    clients: Clients,
) -> tuple[str, list[EpisodeRow]]:
    """Return the episode source and rows for one entry."""
    if media.format == 'MOVIE':
        return Season.EpisodeSource.ANILIST, [EpisodeRow(1, media.start_date)]
    part = accepted.get(media.anilist_id)
    if part is not None:
        rows = [
            EpisodeRow(
                number=index,
                air_date=episode.air_date,
                tmdb_season_number=episode.season_number,
                tmdb_episode_number=episode.number,
                name=episode.name,
                overview=episode.overview,
            )
            for index, episode in enumerate(part, start=1)
        ]
        return Season.EpisodeSource.TMDB, rows
    rows = degraded_rows(media, is_extra, clients)
    return (Season.EpisodeSource.ANILIST if rows else Season.EpisodeSource.NONE), rows


def sync_anime(title: Title, clients: Clients, now: datetime) -> None:
    """Refresh an anime franchise.

    Args:
        title: A title of kind ``anime`` with at least one AniList external id.
        clients: The provider clients; TMDB may be missing (everything degrades).
        now: Current time, stored as ``synced_at``.

    Raises:
        ProviderError: AniList or TMDB is down, or AniList does not know the title.
    """
    root = title.external_ids.filter(provider='anilist').order_by('pk').first()
    if root is None:
        raise ProviderError(f'Title {title.pk} has no AniList id')
    franchise = load_franchise(clients.anilist.media_many, int(root.external_id))
    if not franchise.main:
        raise ProviderError(f'AniList does not know entry {root.external_id}')
    entries = franchise.main + franchise.extras
    mappings = AnimeMapping.objects.in_bulk([media.anilist_id for media in entries])
    if not mappings and not AnimeMapping.objects.exists():
        logger.warning('The Fribb mapping is empty; run "manage.py refresh_mapping"')
    accepted = resolve_tmdb_episodes(clients, entries, mappings)
    extra_ids = {media.anilist_id for media in franchise.extras}
    plans = {
        media.anilist_id: episode_plan(
            media, media.anilist_id in extra_ids, accepted, clients
        )
        for media in entries
    }
    with transaction.atomic():
        write_seasons(title, franchise, mappings, plans)
        renumber_absolute(title)
        update_title(title, franchise, mappings, now)


def write_seasons(
    title: Title,
    franchise: Franchise,
    mappings: dict[int, AnimeMapping],
    plans: dict[int, tuple[str, list[EpisodeRow]]],
) -> None:
    """Upsert one season per franchise entry, main entries first.

    Existing seasons are matched by AniList id and keep their rows (episodes and
    later watch events hang off them). To renumber without tripping the unique
    ``(title, number)`` constraint every season first gets a unique negative
    number. Seasons the franchise no longer contains end up after the others as
    extras.
    """
    existing = list(title.seasons.all())
    for season in existing:
        season.number = -season.pk
    Season.objects.bulk_update(existing, ['number'])
    kept = []
    ordered = [(m, False) for m in franchise.main] + [
        (m, True) for m in franchise.extras
    ]
    for position, (media, is_extra) in enumerate(ordered, start=1):
        source, rows = plans[media.anilist_id]
        mapping = mappings.get(media.anilist_id)
        season, _ = Season.objects.update_or_create(
            title=title,
            anilist_id=media.anilist_id,
            defaults={
                'number': position,
                'kind': KIND_BY_FORMAT.get(media.format or '', Season.Kind.SPECIAL),
                'name': media.english or media.romaji,
                'is_extra': is_extra or media.format is None,
                'tmdb_tv_id': mapping.tmdb_tv_id
                if mapping and source == Season.EpisodeSource.TMDB
                else None,
                'start_date': media.start_date,
                'episode_count': media.episodes,
                'episode_source': source,
            },
        )
        kept.append(season.pk)
        if source != Season.EpisodeSource.TMDB:
            # Titles from an earlier, now rejected, allocation must not linger.
            season.episodes.update(tmdb_season_number=None, tmdb_episode_number=None)
            EpisodeTranslation.objects.filter(episode__season=season).delete()
        write_episodes(season, rows)
    leftovers = list(title.seasons.exclude(pk__in=kept).order_by('-number'))
    for offset, season in enumerate(leftovers, start=len(ordered) + 1):
        season.number = offset
        season.is_extra = True
    Season.objects.bulk_update(leftovers, ['number', 'is_extra'])


def update_title(
    title: Title,
    franchise: Franchise,
    mappings: dict[int, AnimeMapping],
    now: datetime,
) -> None:
    """Fill the title from the franchise and link every provider id."""
    first = franchise.main[0]
    scores = [m.average_score for m in franchise.main if m.average_score is not None]
    title.original_title = first.romaji or first.english or title.original_title
    title.original_language = 'ja'
    title.release_date = first.start_date
    title.status = title_status(franchise.main)
    title.poster_url = first.cover_url
    title.backdrop_url = first.banner_url
    title.rating = to_rating(mean(scores) / 10) if scores else None
    title.rating_votes = None
    title.rating_source = 'anilist' if scores else ''
    schedule_next_sync(title, now)
    title.save()
    save_title_translation(title, 'en', first.english or first.romaji, '')
    for media in franchise.main + franchise.extras:
        link_external_id(title, 'anilist', media.anilist_id)
        if media.mal_id:
            link_external_id(title, 'mal', media.mal_id)
    for media in franchise.main:
        mapping = mappings.get(media.anilist_id)
        if mapping and mapping.tmdb_tv_id:
            link_external_id(title, 'tmdb_tv', mapping.tmdb_tv_id)
        if mapping and mapping.imdb_id:
            link_external_id(title, 'imdb', mapping.imdb_id)
```

**Create `sources/catalog/sync/__init__.py`:**

```python
"""Fill the catalog from the providers."""

import logging
from datetime import datetime

from catalog.models import ExternalId, Title
from catalog.sync.anime import sync_anime
from catalog.sync.common import Clients, default_clients
from catalog.sync.movies import sync_movie
from catalog.sync.tv import sync_tv
from django.db import IntegrityError, transaction
from django.utils import timezone
from providers.types import SearchHit

logger = logging.getLogger(__name__)

SYNC_BY_KIND = {
    Title.Kind.MOVIE: sync_movie,
    Title.Kind.TV: sync_tv,
    Title.Kind.ANIME: sync_anime,
}


def sync_title(
    title: Title, clients: Clients | None = None, now: datetime | None = None
) -> None:
    """Refresh a title and everything under it from the providers.

    Args:
        title: The title to refresh.
        clients: Provider clients; the configured ones when omitted.
        now: Current time, for tests.

    Raises:
        ProviderError: A provider failed; callers retry later.
    """
    SYNC_BY_KIND[Title.Kind(title.kind)](
        title, clients or default_clients(), now or timezone.now()
    )


def get_or_create_title(hit: SearchHit) -> tuple[Title, bool]:
    """Return the title for a search hit, creating a bare one when it is new.

    The provider id decides: adding any entry of an anime franchise that is
    already in the catalog returns the existing franchise title.

    Args:
        hit: A search result.

    Returns:
        ``(title, created)``. A created title has only the data of the search
        result until it is synced.
    """
    lookup = {'provider': hit.provider, 'external_id': hit.external_id}
    link = ExternalId.objects.select_related('title').filter(**lookup).first()
    if link is not None:
        return link.title, False
    try:
        with transaction.atomic():
            title = Title.objects.create(
                kind=hit.kind,
                original_title=hit.original_title or hit.title,
                poster_url=hit.poster_url,
            )
            ExternalId.objects.create(title=title, **lookup)
    except IntegrityError:
        # A concurrent request created it first.
        return ExternalId.objects.select_related('title').get(**lookup).title, False
    return title, True
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_sync_tmdb.py tests/test_sync_anime.py
```

Expected: 39 passed (17 + 22).

- [ ] **Step 6: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: sync movies, TV series and anime franchises into the catalog"
```

---

### Task 9: Search, translations, background jobs and the sync command

**Files:**
- Create: `sources/providers/search.py`, `sources/catalog/translations.py`, `sources/catalog/tasks.py`, `sources/catalog/management/__init__.py`, `sources/catalog/management/commands/__init__.py`, `sources/catalog/management/commands/sync_title.py`, `tests/test_search.py`, `tests/test_translations.py`, `tests/test_catalog_tasks.py`

**Interfaces:**
- Consumes: `TmdbClient`, `AniListClient`, `SearchHit`, `sync_title`, `get_or_create_title`, the catalog models.
- Produces:
  - `providers.search.search(query, language, *, tmdb, anilist) -> SearchResults(hits, failed)`: TMDB and AniList hits interleaved; TMDB hits with `anime_like` are dropped (AniList covers them); a provider that raises `ProviderError`, or a missing TMDB client, is named in `failed` and the other provider still answers.
  - `catalog.translations.localized_title(title, language) -> (name, overview)` (name: language, original, English; overview: language, English), `localized_episode(episode, language) -> (name, overview)` (language, English, otherwise empty), `ensure_title_translation(title, language, tmdb) -> bool`, `ensure_episode_translations(season, language, tmdb) -> int`. Fetching is lazy and stored; empty texts are never stored; only seasons whose episodes came from TMDB are filled.
  - `catalog.tasks.sync_title_task(title_id, force=False) -> str` (Huey `db_task`, 3 retries 5 minutes apart; returns `synced`, `missing`, `not due` or `busy`; a per-title lock expires after 15 minutes), `enqueue_sync(title, force=False)` (queues after the transaction commits), `sync_due_titles()` (every 15 minutes: queues up to 20 titles that were never synced or are due, never-synced first).
  - `manage.py sync_title <tmdb_movie|tmdb_tv|anilist> <id>`: adds the title if new, syncs it now, prints the counts.

- [ ] **Step 1: Write the failing tests**

**Create `tests/test_search.py`:**

```python
from providers.http import ProviderError
from providers.search import search
from providers.types import SearchHit


def hit(provider, external_id, kind='tv', anime_like=False):
    return SearchHit(
        provider, external_id, kind, f'T{external_id}', '', None, '', '', anime_like
    )


class FakeTmdbSearch:
    def __init__(self, hits=(), error=None):
        self.hits, self.error, self.languages = list(hits), error, []

    def search(self, query, language='en'):
        self.languages.append(language)
        if self.error:
            raise self.error
        return self.hits


class FakeAniListSearch:
    def __init__(self, hits=(), error=None):
        self.hits, self.error = list(hits), error

    def search(self, query):
        if self.error:
            raise self.error
        return self.hits


def ids(results):
    return [h.external_id for h in results.hits]


def test_results_of_both_providers_are_interleaved():
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch(
            [hit('tmdb_tv', '1'), hit('tmdb_movie', '2', 'movie'), hit('tmdb_tv', '3')]
        ),
        anilist=FakeAniListSearch([hit('anilist', '10', 'anime')]),
    )
    assert ids(results) == ['1', '10', '2', '3']
    assert results.failed == []


def test_japanese_animation_from_tmdb_is_dropped_in_favour_of_anilist():
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch(
            [hit('tmdb_tv', '1', anime_like=True), hit('tmdb_tv', '2')]
        ),
        anilist=FakeAniListSearch([hit('anilist', '10', 'anime')]),
    )
    assert ids(results) == ['2', '10']


def test_the_site_language_is_passed_to_tmdb():
    tmdb = FakeTmdbSearch()
    search('x', 'ru', tmdb=tmdb, anilist=FakeAniListSearch())
    assert tmdb.languages == ['ru']


def test_a_failing_provider_is_reported_and_the_other_still_answers():
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch(error=ProviderError('down')),
        anilist=FakeAniListSearch([hit('anilist', '10', 'anime')]),
    )
    assert ids(results) == ['10']
    assert results.failed == ['tmdb']
    results = search(
        'x',
        'en',
        tmdb=FakeTmdbSearch([hit('tmdb_tv', '1')]),
        anilist=FakeAniListSearch(error=ProviderError('down')),
    )
    assert ids(results) == ['1'] and results.failed == ['anilist']


def test_a_missing_tmdb_token_is_reported_as_a_failure_of_tmdb():
    results = search(
        'x', 'en', tmdb=None, anilist=FakeAniListSearch([hit('anilist', '10', 'anime')])
    )
    assert ids(results) == ['10'] and results.failed == ['tmdb']
```

**Create `tests/test_translations.py`:**

```python
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
```

**Create `tests/test_catalog_tasks.py`:**

```python
from datetime import timedelta

import pytest
from catalog import tasks
from catalog.models import ExternalId, Season, Title
from django.core.management import CommandError, call_command
from django.utils import timezone
from huey.contrib.djhuey import lock_task
from providers.http import ProviderError

pytestmark = pytest.mark.django_db


@pytest.fixture
def synced(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, 'sync_title', lambda title: calls.append(title.pk))
    return calls


def make_title(**kwargs):
    return Title.objects.create(kind='tv', original_title='x', **kwargs)


def test_a_never_synced_title_is_synced(synced):
    title = make_title()
    assert tasks.sync_title_task.call_local(title.pk) == 'synced'
    assert synced == [title.pk]


def test_a_title_that_is_not_due_is_skipped_unless_forced(synced):
    title = make_title(next_sync_at=timezone.now() + timedelta(hours=1))
    assert tasks.sync_title_task.call_local(title.pk) == 'not due'
    assert synced == []
    assert tasks.sync_title_task.call_local(title.pk, force=True) == 'synced'
    assert synced == [title.pk]


def test_an_unknown_title_id_is_not_an_error(synced):
    assert tasks.sync_title_task.call_local(999999) == 'missing'


def test_a_title_being_synced_elsewhere_is_left_alone(synced):
    title = make_title()
    with lock_task(f'sync-title-{title.pk}'):
        assert tasks.sync_title_task.call_local(title.pk) == 'busy'
    assert synced == []


def test_a_provider_error_propagates_so_huey_can_retry(monkeypatch):
    def boom(title):
        raise ProviderError('down')

    monkeypatch.setattr(tasks, 'sync_title', boom)
    title = make_title()
    with pytest.raises(ProviderError):
        tasks.sync_title_task.call_local(title.pk)


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tasks, 'sync_title_task', lambda pk, force=False: calls.append((pk, force))
    )
    return calls


def test_the_scheduler_queues_unsynced_and_due_titles_only(queued):
    now = timezone.now()
    fresh = make_title(synced_at=now, next_sync_at=now + timedelta(days=1))
    never = make_title()
    due = make_title(
        synced_at=now - timedelta(days=2), next_sync_at=now - timedelta(minutes=1)
    )
    assert tasks.sync_due_titles.call_local() == 2
    assert {pk for pk, _ in queued} == {never.pk, due.pk}
    assert fresh.pk not in {pk for pk, _ in queued}


def test_the_scheduler_serves_never_synced_titles_before_due_ones(queued):
    now = timezone.now()
    for _ in range(tasks.DUE_BATCH):
        make_title(
            synced_at=now - timedelta(days=2), next_sync_at=now - timedelta(minutes=1)
        )
    never = make_title()
    tasks.sync_due_titles.call_local()
    assert never.pk in {pk for pk, _ in queued}


def test_the_scheduler_queues_at_most_one_batch(queued):
    for _ in range(tasks.DUE_BATCH + 5):
        make_title()
    assert tasks.sync_due_titles.call_local() == tasks.DUE_BATCH
    assert len(queued) == tasks.DUE_BATCH


def test_enqueue_waits_for_the_commit(queued, django_capture_on_commit_callbacks):
    title = make_title()
    with django_capture_on_commit_callbacks(execute=True):
        tasks.enqueue_sync(title, force=True)
        assert queued == []
    assert queued == [(title.pk, True)]


def test_command_adds_and_syncs_a_title(monkeypatch, capsys):
    def fake_sync(title):
        season, _ = Season.objects.get_or_create(title=title, number=1, kind='tv')
        season.episodes.get_or_create(number=1)

    monkeypatch.setattr('catalog.management.commands.sync_title.sync_title', fake_sync)
    call_command('sync_title', 'tmdb_tv', '1000')
    out = capsys.readouterr().out
    assert 'Added' in out and '1 seasons, 1 episodes' in out
    assert ExternalId.objects.get().provider == 'tmdb_tv'
    call_command('sync_title', 'tmdb_tv', '1000')
    assert 'Synced' in capsys.readouterr().out
    assert Title.objects.count() == 1


def test_command_reports_a_provider_failure(monkeypatch):
    def boom(title):
        raise ProviderError('TMDB is not configured')

    monkeypatch.setattr('catalog.management.commands.sync_title.sync_title', boom)
    with pytest.raises(CommandError, match='not configured'):
        call_command('sync_title', 'anilist', '1')
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest -q tests/test_search.py tests/test_translations.py tests/test_catalog_tasks.py
```

Expected: errors during collection, `ModuleNotFoundError: No module named 'providers.search'`.

- [ ] **Step 3: Write search, translations, tasks and the command**

**Create `sources/providers/search.py`:**

```python
"""Search across TMDB and AniList."""

import logging
from dataclasses import dataclass, field
from itertools import zip_longest

from providers.anilist import AniListClient
from providers.http import ProviderError
from providers.tmdb import TmdbClient
from providers.types import SearchHit

logger = logging.getLogger(__name__)


@dataclass
class SearchResults:
    """Merged search results.

    Attributes:
        hits: Movies, TV shows and anime, the two providers interleaved so that
            neither buries the other.
        failed: Names of the providers that could not answer; the page can say
            that the list is incomplete.
    """

    hits: list[SearchHit] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def search(
    query: str,
    language: str,
    *,
    tmdb: TmdbClient | None,
    anilist: AniListClient,
) -> SearchResults:
    """Search both providers.

    Japanese animation found by TMDB is dropped, because AniList covers it and
    anime titles come from AniList (they are franchises there, not TMDB shows).

    Args:
        query: Text typed by the user.
        language: Site language for TMDB titles.
        tmdb: TMDB client, or None when no token is configured.
        anilist: AniList client.

    Returns:
        The merged hits and the providers that failed.
    """
    results = SearchResults()
    tmdb_hits: list[SearchHit] = []
    anilist_hits: list[SearchHit] = []
    if tmdb is None:
        results.failed.append('tmdb')
    else:
        try:
            tmdb_hits = [h for h in tmdb.search(query, language) if not h.anime_like]
        except ProviderError as error:
            logger.warning('TMDB search failed: %s', error)
            results.failed.append('tmdb')
    try:
        anilist_hits = anilist.search(query)
    except ProviderError as error:
        logger.warning('AniList search failed: %s', error)
        results.failed.append('anilist')
    results.hits = [
        hit
        for pair in zip_longest(tmdb_hits, anilist_hits)
        for hit in pair
        if hit is not None
    ]
    return results
```

**Create `sources/catalog/translations.py`:**

```python
"""Read and lazily fill translations of titles and episodes.

Only the English text is stored when a title is synced. Another language is
fetched from TMDB the first time someone needs it and then kept. Empty values
are never stored. The lookup falls back from the user's language to the
original title and then to English.
"""

import logging

from catalog.models import Episode, EpisodeTranslation, Season, Title, TitleTranslation
from providers.tmdb import TmdbClient

logger = logging.getLogger(__name__)

FALLBACK_LANGUAGE = 'en'


def _first(*values: str) -> str:
    """Return the first non-empty value, or an empty string."""
    return next((value for value in values if value), '')


def localized_title(title: Title, language: str) -> tuple[str, str]:
    """Return ``(name, overview)`` of a title with the fallback chain applied.

    The name chain is the user's language, the original title, English. The
    overview has no original, so it is the user's language, then English.

    Args:
        title: The title.
        language: The user's metadata language.

    Returns:
        The name and the overview (possibly empty).
    """
    texts = {t.language: t for t in title.translations.all()}
    own = texts.get(language)
    english = texts.get(FALLBACK_LANGUAGE)
    name = _first(
        own.name if own else '',
        title.original_title,
        english.name if english else '',
    )
    overview = _first(own.overview if own else '', english.overview if english else '')
    return name, overview


def localized_episode(episode: Episode, language: str) -> tuple[str, str]:
    """Return ``(name, overview)`` of an episode: the user's language, then English.

    Both are empty for episodes without TMDB data (the degraded mode).
    """
    texts = {t.language: t for t in episode.translations.all()}
    own = texts.get(language)
    english = texts.get(FALLBACK_LANGUAGE)
    return (
        _first(own.name if own else '', english.name if english else ''),
        _first(own.overview if own else '', english.overview if english else ''),
    )


def ensure_title_translation(title: Title, language: str, tmdb: TmdbClient) -> bool:
    """Make sure a title has a translation in ``language``, fetching it if needed.

    Args:
        title: The title; it needs a TMDB id (anime get one when Fribb maps them).
        language: Site language to fetch.
        tmdb: The TMDB client.

    Returns:
        True when the title has a stored translation in that language afterwards.
    """
    if title.translations.filter(language=language).exists():
        return True
    provider = 'tmdb_movie' if title.kind == Title.Kind.MOVIE else 'tmdb_tv'
    link = title.external_ids.filter(provider=provider).order_by('pk').first()
    if link is None:
        return False
    if provider == 'tmdb_movie':
        item = tmdb.movie(int(link.external_id), language)
        name, overview = (item.title, item.overview) if item else ('', '')
    else:
        item = tmdb.tv(int(link.external_id), language)
        name, overview = (item.name, item.overview) if item else ('', '')
    if not (name or overview):
        return False
    TitleTranslation.objects.update_or_create(
        title=title, language=language, defaults={'name': name, 'overview': overview}
    )
    return True


def ensure_episode_translations(season: Season, language: str, tmdb: TmdbClient) -> int:
    """Fetch the episode texts of a season in ``language`` where they are missing.

    Only seasons whose episodes came from TMDB are filled; a degraded season has
    no titles in any language.

    Args:
        season: The season.
        language: Site language to fetch.
        tmdb: The TMDB client.

    Returns:
        The number of translations stored.
    """
    if season.episode_source != Season.EpisodeSource.TMDB or not season.tmdb_tv_id:
        return 0
    missing = list(
        season.episodes.filter(tmdb_season_number__isnull=False).exclude(
            translations__language=language
        )
    )
    stored = 0
    for number in sorted({e.tmdb_season_number for e in missing}):
        tmdb_season = tmdb.season(season.tmdb_tv_id, number, language)
        if tmdb_season is None:
            continue
        texts = {e.number: e for e in tmdb_season.episodes if e.name or e.overview}
        for episode in missing:
            text = texts.get(episode.tmdb_episode_number)
            if episode.tmdb_season_number == number and text is not None:
                EpisodeTranslation.objects.update_or_create(
                    episode=episode,
                    language=language,
                    defaults={'name': text.name, 'overview': text.overview},
                )
                stored += 1
    return stored
```

**Create `sources/catalog/tasks.py`:**

```python
"""Background tasks that keep the catalog fresh."""

import logging

from catalog.models import Title
from catalog.sync import sync_title
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task
from huey.exceptions import TaskLockedException

logger = logging.getLogger(__name__)

# At most this many titles are queued per scheduler run, so a large backlog
# cannot flood the rate-limited providers.
DUE_BATCH = 20
# A crashed worker must not block a title for ever: the lock expires.
LOCK_TTL = 900


@db_task(retries=3, retry_delay=300)
def sync_title_task(title_id: int, force: bool = False) -> str:
    """Sync one title; a failing provider is retried by Huey.

    Args:
        title_id: Primary key of the title.
        force: Sync even when the title is not due yet.

    Returns:
        ``synced``, or why nothing was done: ``missing``, ``not due``, ``busy``.
    """
    title = Title.objects.filter(pk=title_id).first()
    if title is None:
        return 'missing'
    now = timezone.now()
    if not force and title.next_sync_at and title.next_sync_at > now:
        return 'not due'
    try:
        with lock_task(f'sync-title-{title_id}', ttl=LOCK_TTL):
            sync_title(title)
    except TaskLockedException:
        return 'busy'
    return 'synced'


def enqueue_sync(title: Title, force: bool = False) -> None:
    """Queue a sync of ``title`` once the surrounding transaction commits."""
    transaction.on_commit(lambda: sync_title_task(title.pk, force))


@db_periodic_task(crontab(minute='*/15'))
def sync_due_titles() -> int:
    """Queue the titles that were never synced or whose next sync time has come.

    Returns:
        How many titles were queued.
    """
    due = list(
        Title.objects.filter(
            Q(synced_at__isnull=True) | Q(next_sync_at__lte=timezone.now())
        )
        .order_by(F('next_sync_at').asc(nulls_first=True), 'pk')
        .values_list('pk', flat=True)[:DUE_BATCH]
    )
    for pk in due:
        sync_title_task(pk)
    return len(due)
```

**Create the empty file `sources/catalog/management/__init__.py`.**

**Create the empty file `sources/catalog/management/commands/__init__.py`.**

**Create `sources/catalog/management/commands/sync_title.py`:**

```python
from catalog.sync import get_or_create_title, sync_title
from django.core.management.base import BaseCommand, CommandError
from providers.http import ProviderError
from providers.types import SearchHit

KIND_BY_PROVIDER = {'tmdb_movie': 'movie', 'tmdb_tv': 'tv', 'anilist': 'anime'}


class Command(BaseCommand):
    help = (
        'Add a title by provider id and sync it now, for example: '
        'sync_title anilist 154587, sync_title tmdb_tv 209867, sync_title tmdb_movie 603.'
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument('provider', choices=sorted(KIND_BY_PROVIDER))
        parser.add_argument('external_id')

    def handle(self, *args, provider: str, external_id: str, **options) -> None:
        hit = SearchHit(
            provider=provider,
            external_id=external_id,
            kind=KIND_BY_PROVIDER[provider],
            title=f'{provider}:{external_id}',
            original_title=f'{provider}:{external_id}',
            year=None,
            poster_url='',
            overview='',
        )
        title, created = get_or_create_title(hit)
        try:
            sync_title(title)
        except ProviderError as error:
            raise CommandError(str(error)) from error
        title.refresh_from_db()
        seasons = title.seasons.count()
        episodes = sum(season.episodes.count() for season in title.seasons.all())
        self.stdout.write(
            self.style.SUCCESS(
                f'{"Added" if created else "Synced"} {title.original_title} '
                f'({title.kind}, {title.status}): {seasons} seasons, {episodes} episodes.'
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest -q tests/test_search.py tests/test_translations.py tests/test_catalog_tasks.py
```

Expected: 25 passed (5 + 9 + 11).

- [ ] **Step 5: Commit**

```bash
ruff check --fix sources tests && ruff format sources tests
git add sources tests
git commit -m "feat: add merged search, lazy translations, sync jobs and the sync_title command"
```

---

### Task 10: Documentation, the spec, and a live check

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`

- [ ] **Step 1: Document the catalog in the README**

Append this section to `README.md`:

````markdown

## Catalog and providers

Titles come from TMDB (movies, TV series, anime episodes), AniList (anime franchises,
schedule) and the Fribb/anime-lists id mapping. Set a TMDB read access token in
`providers.tmdb_token` (or `MITAROKU_PROVIDERS__TMDB_TOKEN`); AniList and Fribb need no
key. Without a token, movies and TV series cannot be synced and anime fall back to
episode counts and dates from AniList.

```bash
.venv/bin/python sources/manage.py refresh_mapping          # load the Fribb dataset once
.venv/bin/python sources/manage.py sync_title anilist 154587   # add and sync an anime
.venv/bin/python sources/manage.py sync_title tmdb_tv 1396     # a TV series
.venv/bin/python sources/manage.py sync_title tmdb_movie 603   # a movie
```

The Huey worker (`run_huey`) refreshes the Fribb dataset every Monday at 03:30 UTC and
re-syncs every title when it is due (airing titles every 6 hours, announced ones daily,
finished ones every 14 days). An anime title is a whole franchise; its seasons are the
AniList entries. Episode titles are shown only when TMDB's episodes for an entry pass a
check against AniList's start date and episode count; other entries show counts and, when
AniList has them, dates. A sync never deletes episodes.
````

- [ ] **Step 2: Update the spec**

In `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`, section 4, add this paragraph after the paragraph that ends "no titles. Simkl is not used because its catalog terms require Simkl login and sync.":

```markdown
The franchise rule: the main chain follows SEQUEL and PREQUEL links between anime entries;
a MOVIE or SPECIAL with a PARENT edge is an extra, but the walk passes through it. The
Fribb dataset is stored in Postgres and refreshed weekly. Plan 2 records the rule's known
limits (`docs/superpowers/plans/2026-10-19-plan-2-catalog-and-providers.md`).
```

In section 13 replace the two items

```markdown
- Fribb dataset refresh schedule, and the UX of the degraded episode mode.
```

with

```markdown
- The UX of the degraded episode mode (the data is `Season.episode_source`; plan 3).
```

and delete the item that starts "The PARENT rule" (decided in plan 2).

- [ ] **Step 3: Run the whole suite and the linters**

```bash
python -m pytest -q
ruff check . && ruff format --check .
pre-commit run --all-files
python sources/manage.py makemigrations --check --dry-run
```

Expected: 207 passed; ruff clean; hooks pass; `No changes detected`.

- [ ] **Step 4: Live check against the real providers (needs a TMDB token and network)**

Skip this step when there is no token; the tests above never touch the network. Use a scratch database so the development one stays clean.

```bash
export MITAROKU_PROVIDERS__TMDB_TOKEN=...            # your TMDB read access token
python sources/manage.py migrate
python sources/manage.py refresh_mapping
python sources/manage.py sync_title anilist 154587   # Frieren
python sources/manage.py sync_title anilist 16498    # Attack on Titan
python sources/manage.py sync_title tmdb_movie 603
```

Expected (the numbers move with the providers): `Loaded` about 20,000 mappings; Frieren `6 seasons, 62 episodes`; Attack on Titan `16 seasons, 113 episodes`; The Matrix `0 seasons, 0 episodes`. In the admin (`/admin/catalog/season/`, filter by episode source) the regular Attack on Titan seasons show source `tmdb`, the two final-chapter specials show `anilist`, and Frieren season 3 shows `none`. Re-running a command must print `Synced` and leave the row counts unchanged.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md
git commit -m "docs: document the catalog and providers and record the franchise rule"
```

---

## Definition of done

- `python -m pytest -q` reports 207 passed; ruff and the pre-commit hooks are clean; `makemigrations --check` reports no changes.
- `manage.py sync_title` adds and syncs a movie, a TV series and an anime franchise; running it twice changes nothing.
- A provider outage, a truncated Fribb file, an unknown id and a missing TMDB token each end in a clear `ProviderError` or `CommandError`, never in a half-written catalog.
- The Huey worker lists `sync_title_task`, `sync_due_titles` and `refresh_mapping_weekly`.
- CI (`Lint`, `Tests`, `CodeQL`) is green after the user pushes.

## Spec coverage

| Spec section | Where |
| --- | --- |
| 3 Data model: `Title`, `ExternalId`, `TitleTranslation`, `Season`, `Episode` | Task 3 (`LibraryEntry`, `WatchEvent` and the per-user manager belong to plan 3; `Profile` exists from plan 1) |
| 3 Translations: lazy, persistent, no empty values, fallback chain | Tasks 3 and 9 (garbage collection of orphans: plan 3) |
| 4 Providers: TMDB, AniList, Fribb | Tasks 4, 5, 6 |
| 4 Cache layers: Redis TTL, rate limiter, single-flight lock, stale-on-error with Huey retry | Tasks 2 and 9 |
| 4 Anime id mapping, the guard and the degraded mode | Tasks 7 and 8 |
| 4 Scheduled sync | Task 9 (`sync_due_titles`; priority by library status: plan 3) |
| 11 Metadata language separate from interface language | Task 9 (`localized_title` takes the metadata language) |
| 13 Open items: Fribb refresh, PARENT rule | Tasks 5, 6 and 10; the degraded-mode UX and posters go to plan 3 |

Not in this plan: search and title screens, the library and watch events (plan 3), notifications (plan 4), import and export (plan 5), production deployment (plan 6).
