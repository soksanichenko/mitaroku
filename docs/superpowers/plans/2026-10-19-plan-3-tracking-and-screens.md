# Tracking and Screens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in user can find a title, add it to their library, check off episodes, and see progress and the next air date on the Library, Search, Title and Upcoming screens (plus a language Settings page).

**Architecture:** A new `tracking` app owns the per-user data (`LibraryEntry`, `WatchEvent`, `AddRequest`), a write side (`services.py`, every function acts for one user) and a read side (`queries.py`, constant query counts). The `web` app renders the screens with Django templates, HTMX and Alpine; every action is a small POST that returns an HTML fragment. Adding a title runs as a background job (`AddRequest` plus a Huey task) because an anime franchise load takes tens of seconds; the page polls the request. The catalog (plan 2) stays shared and read-only for users.

**Tech Stack:** Django 5.2, PostgreSQL 17, Redis 7, Huey, HTMX, Alpine, the Modernist design system in `web/static/web/ds.css`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 3, 4, 5, 10, 11, 13). The visual reference is `docs/design/` (open `Library.dc.html`, `Search.dc.html`, `Title Page.dc.html`, `Upcoming.dc.html`, `Settings.dc.html`, `Poster Card.dc.html`, `Status Picker.dc.html`, `Rating Badge.dc.html`); the mockups use inline demo data, so take layout, states and wording from them, not their logic.

## Global Constraints

- Django 5.2 LTS, Python 3.12, PostgreSQL, Redis, Huey on Redis; UI is HTMX + Alpine, no JavaScript build step (spec sections 2 and 5).
- "All user-data queries go through a model manager that scopes them to the user." (spec section 3). In this plan: every read of `LibraryEntry`, `WatchEvent` and `AddRequest` is `Model.objects.for_user(user)`.
- "CSRF protection covers HTMX requests." (spec section 10). The base template already sends `X-CSRFToken`; every action is a POST.
- "Every screen has loading, empty and error states. Touch targets are at least 44 px." (spec section 5).
- "The UI ships in Russian and English on the Django i18n foundation. Metadata language comes from the profile and is separate from the interface language." (spec section 11). Every new string goes through `{% trans %}` or `gettext` and gets a Russian translation in `sources/locale/ru/LC_MESSAGES/django.po`.
- `LibraryEntry`: status watching, planned, completed or dropped; "not watching" means no entry. Score stored 0 to 10 and shown as 5 stars. Private note (spec section 3).
- `WatchEvent`: user, title, nullable episode, `watched_at`. Progress is derived. A rewatch is an additional event (spec section 3).
- Translation fallback: user language, original, English; empty values are never stored (spec section 3).
- Single quotes for Python strings (docstrings use triple double quotes); type annotations on public functions; Google-style docstrings; PEP 8 with 88 columns preferred.
- Log with `logging` using %-formatting, not `print()`.
- Everything committed (code, comments, commit messages, docs) is in English.
- Run every command from the repository root; tests need the dev containers (`docker compose -f docker-compose.dev.yml up -d --wait`).

## Decisions

These are rulings the implementer cannot make alone; they are binding for this plan.

1. **Scope.** This plan covers tracking, Library, Upcoming, Search and add, Title pages and a Settings page with the language section. Browse, Collection and the title-page enrichment (genres, country, cast, trailers, similar titles, "See also" links) need new provider data and move to **plan 3b**; the title page of this plan shows only what the catalog stores. Notifications, Import and Export belong to plans 4 and 5. The Settings page reserves no placeholder for them.
2. **Progress counts main seasons only.** Seasons with `is_extra` (anime OVAs, movies, specials, TV season 0) can be checked off but never change progress, "next episode", "continue watching" or the auto-complete rule.
3. **Idempotent actions.** Episode, season and title actions carry the wanted state (`watched=1` or `watched=0`), not "toggle"; sending the same request twice changes nothing. `again=1` records one more viewing. Unwatching (`watched=0`) deletes all of the user's viewings of that episode or movie, so repeating it changes nothing; `again=1` is the only way to add a second viewing.
4. **Only aired episodes can be watched.** "Aired" means `air_date` is set and not after `timezone.localdate()` (UTC). Amendment: an episode with no `air_date` also counts as aired when its season has a `start_date` that is today or earlier and the title is `ended` or `canceled` (a finished show whose dates were never filled in); every other undated episode stays unaired (`tracking.queries.has_aired` is the one implementation). Whole-season and whole-show actions skip episodes that have not aired; a single unaired episode is refused (`NotAired`).
5. **Status transitions.** Watching an episode of a title that is planned, dropped or not in the library makes it Watching (a new entry is created as Watching). Setting Completed on a series marks every aired main episode watched; on a movie it adds a viewing if there is none. A Watching series whose status is ended or canceled and whose main episodes are all watched becomes Completed automatically. Completed is otherwise left alone, so a rewatch does not reopen it.
6. **Removing from the library deletes the entry and keeps the watch events**, so adding the title again restores its progress. The UI asks for confirmation.
7. **Add flow.** The browser sends only `provider`, `external_id` and `status`; the server derives everything else, so a forged request cannot put data into the shared catalog. A title that is already linked and synced is added at once; anything else becomes an `AddRequest` that a Huey job completes while the page polls it. A failed first sync removes the placeholder title (plan 2 follow-up recorded in spec section 13). At most 5 pending requests per user.
8. **Scheduled sync follows the library.** `sync_due_titles` syncs titles that are in at least one library (watching and planned first) and titles never synced; other titles sync when someone opens their page and they are stale.
9. **Translations stay lazy.** The title page shows the best available text and queues a job for the user's metadata language when it is missing; a weekly job deletes translations in languages nobody uses (English is always kept).
10. **Posters are hotlinked** from the provider CDN with `referrerpolicy="no-referrer"` and `loading="lazy"`; a missing or failing poster shows a tinted placeholder. A server proxy is not built (spec section 13, resolved).
11. **Degraded episode mode (spec section 13, resolved).** An entry whose `episode_source` is `anilist` shows rows named "Episode N" with a date or "Date not announced" and a one-line note ("Episode titles are not available for this entry"). An entry with source `none` and no rows shows the note "Episodes are not known yet" and no checkboxes.
12. **Not built although the mockups show them:** per-episode ratings, bookmarks, the "Design notes" panel, the numbering and layout switches of the title page (accordion and "both" numbering are the defaults), "Continue where you left off" across devices.
13. **Shared provider clients in the web process.** Views use one process-wide pair of clients (`web.clients.shared_clients()`), not a new `httpx.Client` per request.

## Review Focus

These are the inputs a person using the service is most likely to hit and that no happy-path test covers; each line names the test that pins it.

- Another user's data must never be readable or writable through any endpoint (status, score, note, watch events, add requests): `test_actions_only_touch_the_requesting_user` (Task 10), `test_title_page_never_shows_another_users_note` (Task 9), `test_reads_and_writes_go_through_for_user` (Task 2).
- A double click, a stale page or an unaired episode must not create duplicate events or a wrong state: `test_marking_twice_with_the_same_state_adds_one_event`, `test_unaired_and_undated_episodes_are_refused` (Task 4), `test_unaired_episode_is_refused_by_the_endpoint` (Task 10).
- Adding a title must never block a request on a provider, leave a placeholder behind when it fails, or take data from the browser: `test_failed_first_sync_removes_the_placeholder` and `test_forged_values_are_rejected` (Task 5), `test_add_button_returns_a_polling_fragment` (Task 8).
- A title with no, partial or undated episode data (announced, degraded, source `none`, zero episodes) must not break the title page, the library or upcoming, and progress must not divide by zero: `test_title_without_episodes_has_zero_progress` (Task 3), `test_title_page_for_a_degraded_entry` and `test_title_page_for_an_announced_title` (Task 9), `test_library_with_an_announced_title` (Task 11), `test_upcoming_without_dates` (Task 12).
- A provider outage while searching must give partial results with a notice (or an error state if both are down), never a 500, and a user who types nothing must not trigger a provider call: `test_search_with_one_provider_down` and `test_short_query_calls_no_provider` (Task 8).

---

## File Structure

```
sources/tracking/                   new app: per-user data and rules
  models.py                         LibraryEntry, WatchEvent, AddRequest, UserScopedManager
  services.py                       write side (status, score, note, watching)
  queries.py                        read side (progress, library rows, title page state, upcoming)
  adding.py                         request_add, add_title_task, cleanup_add_requests
  migrations/0001_initial.py        LibraryEntry, WatchEvent
  migrations/0002_addrequest.py     AddRequest
sources/catalog/tasks.py            sync priority, on-demand sync, language job, translation GC
sources/catalog/franchise.py        Franchise.anchors
sources/providers/http.py           post_json(validate=...)
sources/providers/anilist.py        validated responses, sort tie-break
sources/web/views/                  package: shell.py, library.py, upcoming.py, search.py, titles.py, actions.py, settings.py
sources/web/clients.py              shared_clients()
sources/web/templatetags/web_extras.py   title_name, episode_name, poster tint, status labels
sources/web/templates/web/          pages (library, upcoming, search, title, settings) and _partials
sources/web/static/web/app.css      screen styles (mt- prefix)
sources/locale/ru/LC_MESSAGES/django.po   Russian strings
tests/                              test_tracking_*.py, test_catalog_policy.py, test_web_*.py
```

`web/views.py` becomes the package `web/views/` in Task 7; `web/urls.py` imports from it.

## Definition of done

- `python -m pytest -q` is green; `pre-commit run --all-files` is clean; `python sources/manage.py makemigrations --check --dry-run` reports no changes.
- With `sync_title` run once for a movie, a TV series and an anime (needs a TMDB token), a user can search, add, check off episodes, change status, score and note, see progress on the Library and the next release on Upcoming, in Russian and English, light and dark, at 390 and 960 px (the browser pass in Task 14).
- No screen reads another user's data; a failed add leaves no placeholder title.
- CI (`Lint`, `Tests`, `CodeQL`) is green after the user pushes.

---

### Task 1: Catalog follow-ups from the plan 2 reviews

The add flow depends on four things the plan 2 reviews left open: the franchise match must not swallow alternative versions that share a side story, a GraphQL error body must not poison the cache, a null AniList payload must be a `ProviderError`, and entries with equal dates must have a stable order.

**Files:**
- Modify: `sources/providers/http.py`, `sources/providers/anilist.py`, `sources/catalog/franchise.py`, `sources/catalog/sync/__init__.py`
- Test: `tests/test_providers_http.py`, `tests/test_anilist.py`, `tests/test_franchise.py`, `tests/test_sync_anime.py`

**Interfaces:**
- Produces: `ProviderHttp.post_json(url, *, json, ttl=None, validate=None)` where `validate: Callable[[object], None] | None` is called with each parsed 200 body and raises `ProviderError` for a body that must not be used. That happens before anything is written to the cache and without retries, so the previous stale copy survives and is served instead; with no stale copy the `ProviderError` reaches the caller.
- Produces: `Franchise.anchors -> list[AniListMedia]`: the main chain plus the extras that are compilations (`is_compilation`). `find_franchise_title` matches only these; linking still uses `Franchise.own`.
- Produces: `AniListMedia.sort_key -> tuple[int, int, int, int]` with `anilist_id` last.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_providers_http.py
def test_an_invalid_body_is_not_cached_and_the_stale_copy_survives(
    provider_http, redis_client, redis_prefix
):
    calls = []

    def handler(request):
        calls.append(1)
        body = {'data': {'ok': 1}} if len(calls) == 1 else {'errors': [{'message': 'boom'}]}
        return httpx.Response(200, json=body)

    def validate(body):
        if not body.get('data'):
            raise ProviderError('boom')

    http = provider_http(handler)
    url = 'https://x/graphql'
    assert http.post_json(url, json={'q': 1}, validate=validate) == {'data': {'ok': 1}}
    for key in redis_client.scan_iter(redis_prefix + 'test:*'):
        if ':stale:' not in key and ':lock:' not in key:
            redis_client.delete(key)  # the fresh copy expires, the stale one stays
    # the provider now answers with an error body: the stale copy is served
    assert http.post_json(url, json={'q': 1}, validate=validate) == {'data': {'ok': 1}}
    # with no stale copy the same body is an error, and it is not cached
    with pytest.raises(ProviderError, match='boom'):
        http.post_json(url, json={'q': 2}, validate=validate)
    with pytest.raises(ProviderError, match='boom'):
        http.post_json(url, json={'q': 2}, validate=validate)
    assert len(calls) == 4
```

```python
# tests/test_anilist.py
def test_null_page_is_a_provider_error(provider_http):
    handler = lambda request: httpx.Response(200, json={'data': {'Page': None}})  # noqa: E731
    client = AniListClient(provider_http(handler, name='anilist'))
    with pytest.raises(ProviderError):
        client.media_many([1])
    with pytest.raises(ProviderError):
        client.search('x')

def test_an_error_body_without_data_is_a_provider_error_and_not_cached(provider_http):
    bodies = iter([{'errors': [{'message': 'boom'}]}, {'data': {'Page': {'media': []}}}])
    client = AniListClient(provider_http(lambda r: httpx.Response(200, json=next(bodies)), name='anilist'))
    with pytest.raises(ProviderError):
        client.media_many([1])
    assert client.media_many([1]) == []   # the error was not cached
```

```python
# tests/test_franchise.py
def test_equal_dates_sort_by_anilist_id():
    first, second = media(20, year=2020), media(10, year=2020)
    assert sorted([first, second], key=lambda m: m.sort_key)[0].anilist_id == 10
```

```python
# tests/test_sync_anime.py (uses the module's add_hit helper)
def test_alternative_versions_sharing_a_side_story_are_separate_titles():
    anilist = FakeAniList(
        media(1, [edge('ALTERNATIVE', 5), edge('SIDE_STORY', 9, fmt='OVA')], year=2000),
        media(5, [edge('ALTERNATIVE', 1), edge('SIDE_STORY', 9, fmt='OVA')], year=2010),
        media(9, [edge('PARENT', 1), edge('PARENT', 5)], fmt='OVA', year=2011),
    )
    clients_ = Clients(None, anilist)
    first, _ = add_hit(1, clients_)
    second, created = add_hit(5, clients_)
    assert created is True and second.pk != first.pk
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_providers_http.py tests/test_anilist.py tests/test_franchise.py tests/test_sync_anime.py -q`
Expected: the five new tests FAIL (unexpected keyword `validate`, `TypeError` on the null page, an error body raised after being cached, wrong order, `created is False`).

- [ ] **Step 3: Implement**

- `post_json` gains `validate`; the check runs on the parsed body inside the request path, before the cache write, and raises `ProviderError` without retrying (a permanent error is not worth the backoff).
- `AniListClient._query` passes a validator that accepts a dict whose `data` is a non-empty dict (partial data with `errors` is accepted, `errors` without `data` is not). `media_many` and `search` read `data['Page']['media']` through one helper that raises `ProviderError` when `data`, `Page` or `media` is missing or null. `schedule` keeps returning an empty schedule on a 404.
- `AniListMedia.sort_key` appends `self.anilist_id`.
- Add `Franchise.anchors`; make `find_franchise_title` use `franchise.anchors` instead of `franchise.own`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the earlier franchise and sync tests are unchanged).

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "fix: keep alternative versions apart, validate AniList responses, order equal dates"
```

---

### Task 2: Tracking models

**Files:**
- Create: `sources/tracking/__init__.py`, `sources/tracking/apps.py`, `sources/tracking/models.py`, `sources/tracking/migrations/__init__.py`, `sources/tracking/migrations/0001_initial.py` (generated)
- Modify: `sources/mitaroku/settings.py` (add `'tracking'` to `INSTALLED_APPS` after `'accounts'`)
- Test: `tests/test_tracking_models.py`, `tests/test_tracking_scoping.py`

**Interfaces:**
- Produces: `tracking.models.UserScopedManager.for_user(user) -> QuerySet` (rows with `user=user`).
- Produces: `LibraryEntry(user, title, status, score, note, added_at, updated_at)`; `LibraryEntry.Status` is `watching`, `planned`, `completed`, `dropped`; unique per `(user, title)`; check constraint `0 <= score <= 10`; `objects = UserScopedManager()`.
- Produces: `WatchEvent(user, title, episode, watched_at, created_at)` with a nullable `episode`; unique per `(user, episode, watched_at)` when `episode` is set and per `(user, title, watched_at)` when it is empty; index on `(user, title)`; `objects = UserScopedManager()`.
- Neither model is registered in the admin: users' viewing data stays out of `/admin/`.

- [ ] **Step 1: Write the shared test helpers and the failing tests**

Add to `tests/conftest.py` the fixtures `alice` (a user with `username='1001'` and `discord_id='1001'`, the same account `portal_headers` signs in as), `bob` (`'1002'`) and `other_headers` (a copy of `PORTAL` with `HTTP_X_DISCORD_USER_ID='1002'`). Add `tests/tracking_helpers.py` with `TODAY = date(2026, 10, 19)`, `make_show(name='Show', status='airing', dated=(-30, -23, -16, 7, None), extra=True) -> (title, episodes, extra_episode)` (a TV `Title` with main season 1 whose episodes are dated `TODAY` plus the given day offsets, `None` meaning undated, and, when `extra`, a season 2 with `is_extra=True` holding one episode dated 100 days ago), `make_movie(name='Movie', release_offset=-10, status='ended') -> Title`, and `watch(user, *episodes, at=None)` which creates `WatchEvent` rows directly (so query tests do not depend on the services).

`tests/test_tracking_models.py`:

- `test_one_entry_per_user_and_title`: a second `LibraryEntry` for the same user and title raises `IntegrityError`; a second user may have one.
- `test_score_range_is_enforced`: score 11 raises `IntegrityError`.
- `test_watch_event_identity_is_user_episode_time`: the same user, episode and time twice raises; another user with the same values is fine; a movie event (no episode) is unique per user, title and time.
- `test_for_user_returns_only_own_rows`: `LibraryEntry.objects.for_user(alice)` and `WatchEvent.objects.for_user(alice)` never return bob's rows.
- `test_tracking_data_is_not_in_the_admin`: neither model is in `django.contrib.admin.site._registry`.

`tests/test_tracking_scoping.py`:

```python
import re
from pathlib import Path

READS = r'\b(LibraryEntry|WatchEvent|AddRequest)\.objects\.(all|filter|get|exclude|count|first|last|values|values_list|select_related|annotate|select_for_update|exists|in_bulk)\b'

def test_reads_and_writes_go_through_for_user():
    """Only create/get_or_create/bulk_create may bypass for_user: reads must be scoped."""
    root = Path('sources')
    offenders = [
        f'{path}: {match.group(0)}'
        for path in list(root.glob('tracking/**/*.py')) + list(root.glob('web/**/*.py'))
        if path.name != 'models.py' and 'migrations' not in path.parts
        for match in re.finditer(READS, path.read_text())
    ]
    assert offenders == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tracking_models.py tests/test_tracking_scoping.py -q`
Expected: collection errors (`No module named 'tracking'`).

- [ ] **Step 3: Implement** the models as described in Interfaces; generate the migration with `python sources/manage.py makemigrations tracking`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tracking_models.py tests/test_tracking_scoping.py -q; .venv/bin/python sources/manage.py makemigrations --check --dry-run`
Expected: PASS; `No changes detected`.

- [ ] **Step 5: Commit**

```bash
git add sources/tracking sources/mitaroku/settings.py tests
git commit -m "feat: add the tracking models"
```

---

### Task 3: Tracking queries (read side)

**Files:**
- Create: `sources/tracking/queries.py`
- Test: `tests/test_tracking_queries.py`

**Interfaces:**
- Consumes: Task 2 models.
- Produces (all in `tracking.queries`):
  - `main_episodes(title) -> QuerySet[Episode]` (non-extra seasons, ordered by season number then episode number); `main_episodes_for(titles) -> QuerySet[Episode]`.
  - `watched_episode_ids(user, title) -> set[int]`.
  - `Progress(watched: int, total: int)` with `.percent` (rounded down; 0 when `total` is 0; 100 only when everything is watched) and `progress(user, title) -> Progress` (main seasons only, distinct episodes).
  - `next_episode(user, title) -> Episode | None` (first unwatched main episode, aired or not, with `season` loaded).
  - `LibraryRow(entry, watched, total, next, last_watched_at)` with `.percent`; `library_rows(user, *, kind=None, status=None, query='', language='en') -> list[LibraryRow]` in **three queries** whatever the number of titles (entries with annotated totals, next episodes in bulk, translations for `language` and English in bulk). `query` matches the original title or any translation name, case-insensitively.
  - `continue_watching(user, *, today=None, language='en') -> list[LibraryRow]`: Watching entries that have a next episode; those whose next episode has aired first (most recently watched first), then the not yet aired by date, undated last.
  - `UpcomingItem(title, episode, date)` and `upcoming(user, today=None) -> list[UpcomingItem]`: for each Watching or Planned title the next release: a series' earliest unwatched main episode dated today or later (an undated one only when the title is airing or upcoming), a movie's release date when it is today or later and the user has not watched it (or none yet when the title is upcoming). Sorted by date, undated last.
  - `TitlePageState` and `title_page_state(user, title, today=None, language='en') -> TitlePageState`: the entry (or None), `groups: list[GroupState]` (one per season in order, each with `season`, `rows: list[EpisodeRow(episode, aired, views)]` where `views` is the number of the user's events for it, `watched`, `total`, `aired_total`), `progress`, `next_episode`, `movie_views` (number of the user's viewings of a movie), in a constant number of queries.

- [ ] **Step 1: Write the failing tests** in `tests/test_tracking_queries.py` (helpers from Task 2):

```python
def test_title_without_episodes_has_zero_progress(alice):
    title = Title.objects.create(kind='anime', original_title='Announced', status='upcoming')
    assert queries.progress(alice, title).percent == 0
    assert queries.next_episode(alice, title) is None

def test_progress_and_next_episode_ignore_extras(alice):
    title, eps, ex = make_show()
    watch(alice, eps[0], eps[1], ex)
    progress = queries.progress(alice, title)
    assert (progress.watched, progress.total, progress.percent) == (2, 5, 40)
    assert queries.next_episode(alice, title) == eps[2]

def test_library_rows_use_constant_queries(alice, bob, django_assert_num_queries):
    ...  # one title, then thirty; each time exactly 3 queries; bob's rows never appear in alice's

def test_library_filters_by_kind_status_and_query(alice): ...
def test_library_query_matches_translations(alice): ...
def test_continue_watching_orders_aired_first(alice): ...
def test_upcoming_orders_dated_first_and_skips_ended_undated(alice): ...
    # 'Soon' (+2 days), a movie (+30), 'Undated airing' last; a completed title and an ended
    # title with an undated episode do not appear
def test_title_page_state_counts_views_and_uses_constant_queries(alice, django_assert_num_queries): ...
    # a rewatched episode has views == 2; group watched/total/aired_total are right
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tracking_queries.py -q`
Expected: collection error (`tracking.queries` missing).

- [ ] **Step 3: Implement.** Counts are `Subquery` annotations keyed on `OuterRef('title')` (a join of episodes and events multiplies rows). The next episode id is a subquery that excludes the user's watched episodes, ordered by `season__number, number`, fetched in bulk with `Episode.objects.select_related('season').in_bulk(ids)`. Sorting by title, rating and next date happens in Python in the view layer (Task 11).

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tracking_queries.py tests/test_tracking_services.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources/tracking tests
git commit -m "feat: add the tracking queries"
```

---

### Task 4: Tracking services (write side)

**Files:**
- Create: `sources/tracking/services.py`
- Test: `tests/test_tracking_services.py`

**Interfaces:**
- Consumes: Task 2 models; Task 3 `main_episodes` and `watched_episode_ids`; `catalog.models.Title/Season/Episode`.
- Produces (all in `tracking.services`; `today` defaults to `timezone.localdate()`, `now` to `timezone.now()`):
  - `class NotAired(ValueError)`, `class NotInLibrary(LookupError)`.
  - `is_aired(episode, today) -> bool`.
  - `set_status(user, title, status: str | None, *, now=None, today=None) -> LibraryEntry | None`: creates or updates the entry; `None` removes it (events kept, Decision 6) and returns None. Completed follows Decision 5.
  - `set_score(user, title, score: int) -> LibraryEntry` (0 clears; outside 0..10 raises `ValueError`; no entry raises `NotInLibrary`).
  - `set_note(user, title, note: str) -> LibraryEntry` (more than 5000 characters raises `ValueError`; no entry raises `NotInLibrary`).
  - `set_episode_watched(user, episode, watched: bool, *, again=False, now=None, today=None) -> None` (Decisions 3 to 5; `watched=False` deletes every viewing of the episode).
  - `mark_episodes(user, title, episodes, *, now=None, today=None) -> int`: adds a viewing for each aired episode the user has not watched; returns how many.
  - `mark_up_to(user, episode, *, now=None, today=None) -> int`: the episode and every earlier one in its group (all earlier main seasons for a main episode; the same season for an extra).
  - `set_season_watched(user, season, watched: bool, *, now=None, today=None) -> int`: True marks the aired unwatched episodes and returns how many it added; False deletes all the user's events of the season's episodes and returns how many events it deleted.
  - `set_title_watched(user, title, watched: bool, *, now=None, today=None) -> int`: every season including extras, with the return values of `set_season_watched` summed; for a movie it delegates to `set_movie_watched` and returns 1 or 0.
  - `set_movie_watched(user, title, watched: bool, *, again=False, now=None, today=None) -> None`: a movie must have `release_date <= today` (else `NotAired`); watching sets the entry to Completed (creating it if needed); unwatching deletes every viewing and leaves the status alone.
  - `mark_next(user, title, *, now=None, today=None) -> Episode | None`: marks `queries.next_episode(user, title)` (the first unwatched main episode) when it has aired and returns it; returns None and writes nothing when that episode has not aired or there is none.
- Every mutation runs in `transaction.atomic()` and locks the user's entry row (`get_or_create`, then `select_for_update` through `for_user`), so two simultaneous requests for one user and title cannot both pass the "already watched" check.

- [ ] **Step 1: Write the failing tests** in `tests/test_tracking_services.py`, using the helpers from Task 2. `TODAY = date(2026, 10, 19)`; `make_show()` gives main episodes dated `TODAY` minus 30, 23, 16 days, plus 7 days ahead, plus one undated, and an extra season with one old episode.

```python
def test_marking_twice_with_the_same_state_adds_one_event(alice):
    title, eps, _ = make_show()
    for _ in range(2):
        services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert LibraryEntry.objects.for_user(alice).get(title=title).status == 'watching'

def test_unaired_and_undated_episodes_are_refused(alice):
    title, eps, _ = make_show()
    for episode in (eps[3], eps[4]):
        with pytest.raises(services.NotAired):
            services.set_episode_watched(alice, episode, True, today=TODAY)
    assert not WatchEvent.objects.exists() and not LibraryEntry.objects.exists()

def test_again_adds_a_rewatch_and_unwatch_removes_the_latest(alice):
    title, eps, _ = make_show()
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    services.set_episode_watched(alice, eps[0], True, again=True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).count() == 2
    services.set_episode_watched(alice, eps[0], False)
    assert WatchEvent.objects.for_user(alice).count() == 1

def test_mark_up_to_skips_unaired_and_other_users(alice, bob):
    title, eps, _ = make_show()
    assert services.mark_up_to(alice, eps[2], today=TODAY) == 3
    assert services.mark_up_to(alice, eps[2], today=TODAY) == 0
    assert not WatchEvent.objects.for_user(bob).exists()

def test_up_to_inside_an_extra_stays_in_its_season(alice):
    title, eps, extra = make_show()
    assert services.mark_up_to(alice, extra, today=TODAY) == 1
    assert queries.progress(alice, title).watched == 0

def test_last_episode_of_an_ended_show_completes_it(alice):
    title, eps, _ = make_show(status='ended', dated=(-30, -23, -16))
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'
    services.mark_up_to(alice, eps[2], today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'

@pytest.mark.parametrize('before', ['planned', 'dropped'])
def test_watching_a_planned_or_dropped_title_resumes_it(alice, before):
    title, eps, _ = make_show()
    services.set_status(alice, title, before, today=TODAY)
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert LibraryEntry.objects.for_user(alice).get().status == 'watching'

def test_completed_status_marks_every_aired_main_episode(alice):
    title, eps, extra = make_show()
    services.set_status(alice, title, 'completed', today=TODAY)
    assert queries.watched_episode_ids(alice, title) == {eps[0].pk, eps[1].pk, eps[2].pk}
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'

def test_removing_from_the_library_keeps_the_events(alice):
    title, eps, _ = make_show()
    services.set_episode_watched(alice, eps[0], True, today=TODAY)
    assert services.set_status(alice, title, None) is None
    assert not LibraryEntry.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(alice).count() == 1

def test_set_season_watched_false_deletes_only_this_users_events(alice, bob):
    title, eps, _ = make_show()
    season = eps[0].season
    for user in (alice, bob):
        services.set_season_watched(user, season, True, today=TODAY)
    assert services.set_season_watched(alice, season, False) == 3
    assert not WatchEvent.objects.for_user(alice).exists()
    assert WatchEvent.objects.for_user(bob).count() == 3

def test_score_and_note_validation(alice):
    title, _, _ = make_show()
    with pytest.raises(services.NotInLibrary):
        services.set_score(alice, title, 5)
    services.set_status(alice, title, 'planned')
    for bad in (11, -1):
        with pytest.raises(ValueError):
            services.set_score(alice, title, bad)
    with pytest.raises(ValueError):
        services.set_note(alice, title, 'x' * 5001)
    assert services.set_score(alice, title, 7).score == 7

def test_movie_watched_completes_and_rewatch_counts(alice):
    future = make_movie(release_offset=5)
    with pytest.raises(services.NotAired):
        services.set_movie_watched(alice, future, True, today=TODAY)
    movie = make_movie(release_offset=-5)
    services.set_movie_watched(alice, movie, True, today=TODAY)
    services.set_movie_watched(alice, movie, True, again=True, today=TODAY)
    assert WatchEvent.objects.for_user(alice).filter(title=movie).count() == 2
    assert LibraryEntry.objects.for_user(alice).get(title=movie).status == 'completed'

def test_mark_next_marks_the_first_unwatched_aired_episode(alice):
    title, eps, _ = make_show()
    assert services.mark_next(alice, title, today=TODAY) == eps[0]
    assert services.mark_next(alice, title, today=TODAY) == eps[1]
    services.mark_up_to(alice, eps[2], today=TODAY)
    assert services.mark_next(alice, title, today=TODAY) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tracking_services.py -q`
Expected: collection error (`tracking.services` missing).

- [ ] **Step 3: Implement** `tracking/services.py` with the signatures above.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tracking_services.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources/tracking tests
git commit -m "feat: add the tracking services"
```

---

### Task 5: The add flow (AddRequest and job)

**Files:**
- Modify: `sources/tracking/models.py` (add `AddRequest`), generate `sources/tracking/migrations/0002_addrequest.py`
- Create: `sources/tracking/adding.py`
- Test: `tests/test_tracking_adding.py`

**Interfaces:**
- Consumes: `catalog.sync.get_or_create_title(hit, clients)`, `catalog.sync.sync_title(title, clients)`, `catalog.sync.common.default_clients`, `ExternalId`, `LibraryEntry`.
- Produces: `AddRequest(user, provider, external_id, status, state, title, error, created_at, finished_at)`; `AddRequest.State` is `pending`, `done`, `failed`; `error` is `''`, `provider` or `timeout`; unique per `(user, provider, external_id)` while `state='pending'`; `objects = UserScopedManager()`.
- Produces in `tracking.adding`:
  - `PROVIDER_KIND = {'tmdb_movie': 'movie', 'tmdb_tv': 'tv', 'anilist': 'anime'}`, `MAX_PENDING = 5`.
  - `class TooManyPending(Exception)`.
  - `request_add(user, provider: str, external_id: str, status: str) -> LibraryEntry | AddRequest`: raises `ValueError` for an unknown provider, a non-numeric or longer than 10 digit `external_id`, or an unknown status; raises `TooManyPending` at `MAX_PENDING`. If the id is linked to a title that has `synced_at` set, it returns the user's entry at once (created with `status` if missing; an existing entry keeps its status). Otherwise it returns the existing pending request for the same id, or creates one and queues `add_title_task` after the transaction commits.
  - `add_title_task(request_id: int) -> str` (Huey `db_task`, no Huey retries: the provider layer already retries): builds `SearchHit(provider, external_id, kind=PROVIDER_KIND[...], title='', original_title='', year=None, poster_url='', overview='')`, calls `get_or_create_title`, `sync_title`, then creates the library entry and marks the request `done` with its title. A `ProviderError` marks it `failed` with `error='provider'` and deletes the title when `created` is true, it was never synced and no library entry points at it. Returns `done`, `failed` or `skipped` (request missing or not pending).
  - `cleanup_add_requests() -> int`: periodic daily at 04:10; deletes finished requests older than 7 days and marks pending ones older than 1 hour `failed` with `error='timeout'`; returns how many it touched.

- [ ] **Step 1: Write the test fixtures and the failing tests** in `tests/test_tracking_adding.py`. Fixtures (put the ones the web tests also use in `tests/conftest.py`): `fake_clients` returns `Clients(FakeTmdb(...), FakeAniList(...))` holding a TMDB movie 603, a TMDB show 1396 with two seasons, and an anime franchise 70 and 71 (built with the helpers of `tests/fakes.py`) and patches `tracking.adding.default_clients` to return it; `failing_clients` is the same patch with a TMDB and an AniList fake whose every method raises `ProviderError`; `queued` patches `tracking.adding.add_title_task` with a recorder that appends each request pk to a list and returns it; `capture_on_commit` wraps `django_capture_on_commit_callbacks(execute=True)`; `synced_movie` is a movie `Title` linked to `tmdb_movie:603` with `synced_at` set.

```python
def test_a_synced_title_is_added_at_once(alice, synced_movie):
    entry = adding.request_add(alice, 'tmdb_movie', '603', 'planned')
    assert isinstance(entry, LibraryEntry) and entry.status == 'planned'
    assert not AddRequest.objects.exists()

def test_a_new_title_becomes_a_pending_request_and_is_queued(alice, capture_on_commit, queued):
    request = adding.request_add(alice, 'tmdb_tv', '1396', 'watching')
    assert request.state == 'pending'
    capture_on_commit()  # run the on_commit callbacks
    assert queued == [request.pk]

def test_the_same_pending_request_is_reused(alice): ...
def test_pending_limit(alice): ...  # the 6th distinct id raises TooManyPending

def test_forged_values_are_rejected(alice):
    for provider, external_id, status in [('imdb', '1', 'planned'), ('tmdb_tv', 'abc', 'planned'),
                                          ('tmdb_tv', '1' * 11, 'planned'), ('tmdb_tv', '1', 'hacked')]:
        with pytest.raises(ValueError):
            adding.request_add(alice, provider, external_id, status)
    assert not AddRequest.objects.exists() and not Title.objects.exists()

def test_job_syncs_and_adds_the_title(alice, fake_clients): ...
    # request pending -> add_title_task.call_local(pk) == 'done'; request.title is synced;
    # the library entry has the requested status
def test_failed_first_sync_removes_the_placeholder(alice, failing_clients):
    request = AddRequest.objects.create(user=alice, provider='tmdb_movie', external_id='424242', status='planned')
    assert adding.add_title_task.call_local(request.pk) == 'failed'
    request.refresh_from_db()
    assert (request.state, request.error) == ('failed', 'provider')
    assert not Title.objects.exists() and not LibraryEntry.objects.exists()

def test_a_failed_job_keeps_a_title_someone_else_tracks(alice, bob, failing_clients): ...
def test_new_entry_of_a_known_franchise_adds_the_existing_title(alice, fake_clients): ...
def test_cleanup_times_out_stale_requests_and_deletes_old_ones(alice): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tracking_adding.py -q`
Expected: FAIL (missing `AddRequest` / `tracking.adding`).

- [ ] **Step 3: Implement** the model, the migration and `adding.py`. Pending uniqueness is the partial unique constraint; `request_add` catches `IntegrityError` and returns the winner's row.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tracking_adding.py tests/test_tracking_scoping.py -q; .venv/bin/python sources/manage.py makemigrations --check --dry-run`
Expected: PASS; `No changes detected`.

- [ ] **Step 5: Commit**

```bash
git add sources/tracking tests
git commit -m "feat: add titles through a background request"
```

---

### Task 6: Sync policy, language job and translation cleanup

**Files:**
- Modify: `sources/catalog/tasks.py`
- Test: `tests/test_catalog_policy.py`

**Interfaces:**
- Consumes: `catalog.translations.ensure_title_translation`, `ensure_episode_translations`, `catalog.sync.common.default_clients`, `providers.clients.connect_redis`, `tracking.models.LibraryEntry`, `accounts.models.Profile`.
- Produces in `catalog.tasks`:
  - `claim(key: str, ttl: int) -> bool`: `SET NX EX` on Redis; true only for the first caller within `ttl` seconds.
  - `enqueue_sync_once(title) -> bool`: if the title was never synced or `next_sync_at` has passed, and `claim(f'sync-queued:{pk}', 300)` succeeds, queue a non-forced `sync_title_task` and return True.
  - `ensure_language_task(title_id: int, language: str) -> str` (Huey `db_task`, 2 retries 60 seconds apart): ensures the title translation and the episode translations of every season in `language` through `default_clients().tmdb`; returns `done`, `missing` or `no tmdb`. `enqueue_language_once(title, language) -> bool` queues it behind `claim(f'language-queued:{pk}:{language}', 3600)`; `language == 'en'` returns False.
  - `gc_translations() -> int`: periodic weekly (Sunday 04:30); deletes `TitleTranslation` and `EpisodeTranslation` rows whose language is neither `en` nor the `metadata_language` of any profile; returns the number deleted.
  - `sync_due_titles()` changes: a title is due when it was never synced or it is in at least one library and `next_sync_at` is null or past; ordering is never synced first, then titles with a Watching or Planned entry, then the rest, then `next_sync_at`, then `pk` (the lease and `DUE_BATCH` stay).

- [ ] **Step 1: Write the failing tests** in `tests/test_catalog_policy.py`:

```python
def test_untracked_titles_are_not_scheduled(db):
    never = make_title(synced=False)
    tracked = make_title(synced=True, due=True, library_status='planned')
    untracked = make_title(synced=True, due=True)
    assert catalog_tasks.sync_due_titles() == 2
    queued = {pk for pk in (never.pk, tracked.pk, untracked.pk) if leased(pk)}
    assert queued == {never.pk, tracked.pk}

def test_watching_titles_are_queued_before_others(db, monkeypatch): ...
    # DUE_BATCH = 1: a watching title wins over a planned-only and a completed-only title
def test_claim_is_true_once_per_window(redis_client): ...
def test_enqueue_sync_once_skips_a_fresh_title_and_a_second_call(db): ...
def test_language_job_fetches_title_and_episode_translations(db, fake_clients): ...
def test_language_job_without_tmdb_is_a_noop(db): ...
def test_english_never_needs_a_language_job(db): ...
def test_gc_keeps_english_and_languages_in_use(db):
    # profile metadata languages {'en', 'ru'}; a 'de' title translation and a 'de' episode translation
    # are deleted, 'en' and 'ru' stay
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_catalog_policy.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** in `catalog/tasks.py`. Existing plan 2 tests of `sync_due_titles` (`tests/test_catalog_tasks.py`) must stay green except where they assume untracked titles are scheduled: update those tests to create a library entry, and say so in the commit message.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_catalog_policy.py tests/test_catalog_tasks.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources/catalog tests
git commit -m "feat: schedule syncs by library, add the language job and translation cleanup"
```

---

### Task 7: UI foundation

**Files:**
- Modify: `sources/web/views.py` becomes the package `sources/web/views/` (`__init__.py`, `shell.py` with `index` and `healthz`, `library.py`, `upcoming.py` holding the existing placeholders for now); `sources/web/urls.py`; `sources/web/templatetags/web_extras.py`; `sources/web/static/web/app.css`; `sources/web/templates/web/_header.html`, `_bottom_nav.html`
- Create: `sources/web/clients.py`, templates `sources/web/templates/web/components/` (`_poster.html`, `_rating_badge.html`, `_status_picker.html`, `_progress.html`, `_state_loading.html`, `_state_empty.html`, `_state_error.html`)
- Test: `tests/test_web_foundation.py`

**Interfaces:**
- Produces: `web.clients.shared_clients() -> Clients` (cached for the process with `functools.cache`; tests patch it).
- Produces template tags in `web_extras`: `{% title_name title %}` and `{% title_overview title %}` (use `catalog.translations.localized_title` with the signed-in user's `metadata_language`, from `context['request']`), `{% episode_name episode %}` (`localized_episode`), `poster_hue` filter (a stable hue 0 to 359 from the title pk, used for the placeholder tint), `status_label` filter (the translated label of a `LibraryEntry.Status`).
- Produces components, each an `{% include %}` with documented variables: `_poster.html` (`title`, optional `variant`), `_rating_badge.html` (`title`; shows `rating_source` and value, nothing when `rating` is null), `_status_picker.html` (`title`, `entry`, `action` (the URL the choice is posted to), `fields` (extra hidden inputs as a dict), `align`, `target` (the `hx-target`); an Alpine dropdown with the four statuses and, when `entry` is set, a Remove item; a button reading "Add" when `entry` is None; `aria-expanded`; Escape and an outside click close it; each option posts `status=<value>` (`remove` for Remove) with `hx-post` to `action`), `_progress.html` (`watched`, `total`; a bar plus "watched/total", 0 when `total` is 0), and the three state blocks (loading skeleton with `aria-busy`, empty, error with a retry button taking `retry_url`).
- The header gets a search form (`GET /search/`, field `q`) and the bottom nav a Search item; `current_nav` already marks the active page.

- [ ] **Step 1: Write the failing tests** in `tests/test_web_foundation.py` (client with `portal_headers`):

- `test_existing_pages_still_resolve_after_the_views_package`: `/`, `/library/`, `/upcoming/`, `/healthz/` respond as in `tests/test_shell.py` and `tests/test_health_and_tasks.py`.
- `test_header_has_a_search_form_and_bottom_nav_a_search_item`: the HTML of `/library/` contains `action="/search/"` and an `href="/search/"` in `.mt-bottom-nav`.
- `test_title_name_uses_the_metadata_language`: a title with a Russian `TitleTranslation`; render `{% title_name title %}` through `Template` with a request for a user whose `metadata_language='ru'` gives the Russian name; for `en` it gives the English translation, falling back to `original_title` when none exists.
- `test_poster_without_url_renders_a_tinted_placeholder`; `test_poster_with_url_is_lazy_and_sends_no_referrer` (`loading="lazy"`, `referrerpolicy="no-referrer"`).
- `test_rating_badge_is_empty_without_a_rating`.
- `test_status_picker_offers_add_when_not_in_the_library` and `test_status_picker_marks_the_current_status` (`aria-checked="true"` on exactly one option, a Remove item only when in the library, every option at least 44 px via the `mt-touch` class).
- `test_progress_with_no_episodes_renders_zero`.
- `test_shared_clients_is_built_once`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_foundation.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement.** Move the views without changing behaviour; add the tags and components following `docs/design/Poster Card.dc.html`, `Status Picker.dc.html`, `Rating Badge.dc.html` (light and dark, grayscale poster that gains colour on hover, 44 px targets); add the CSS classes with the `mt-` prefix. Add the Russian strings.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the UI foundation: views package, components, shared clients"
```

---

### Task 8: Search and add screen

**Files:**
- Create: `sources/web/views/search.py`, templates `search.html`, `_search_results.html`, `_add_result.html`
- Modify: `sources/web/urls.py`, `app.css`, `django.po`
- Test: `tests/test_web_search.py`

**Interfaces:**
- Consumes: `providers.search.search`, `web.clients.shared_clients`, `tracking.adding.request_add/TooManyPending`, `tracking.models.AddRequest`.
- Produces URL names and views:
  - `search` `GET /search/?q=&all=`: full page, or only `_search_results.html` for an HTMX request (`HX-Request` header). `all` is `tv`, `anime` or `movie` and lifts the 5-per-group limit for that group.
  - `search_add` `POST /search/add/` with `provider`, `external_id`, `status`: returns `_add_result.html` (always status 200 for handled outcomes): the status label and an "Open" link when added at once; a polling element for a pending request; a message for `TooManyPending`; 400 for invalid input.
  - `add_status` `GET /add/<int:pk>/`: the request's fragment, only for the signed-in user's own requests (404 otherwise). While pending it polls itself (`hx-get`, `hx-trigger="load delay:2s"`, `hx-swap="outerHTML"`); done shows "Added" and a link to `title`; failed shows "Couldn't add this title" and a retry button posting to `search_add`.
- Results are grouped Shows, Anime, Movies (groups hidden when empty), each row: poster, name, original name when different, year and kind, and either the library status or the add picker.
- States: idle (query empty: a short hint), typing (one character: "Type at least 2 characters", no provider call), loading (HTMX indicator skeleton), results, none ("No results for ..."), partial (a notice naming the provider that failed), error (both providers failed: the error block with a retry link).

- [ ] **Step 1: Write the fixtures and the failing tests** in `tests/test_web_search.py`. `patched_search` replaces `web.views.search.search` with a recorder: it appends `(query, language)` to `.calls` and returns `.result` (a `SearchResults`, empty by default); `shared_clients` is patched to return `Clients(None, FakeAniList())`. `anime_hit(name)` builds a `SearchHit('anilist', '1', 'anime', name, name, 2023, '', '')`. `queued` is the fixture from Task 5.

```python
def test_short_query_calls_no_provider(client, portal_headers, patched_search):
    client.get('/search/?q=a', **portal_headers)
    client.get('/search/', **portal_headers)
    assert patched_search.calls == []

def test_search_with_one_provider_down(client, portal_headers, patched_search):
    patched_search.result = SearchResults(hits=[anime_hit('Frieren')], failed=['tmdb'])
    html = client.get('/search/?q=frieren', **portal_headers).content.decode()
    assert 'Frieren' in html and 'TMDB' in html and 'incomplete' in html.lower()

def test_search_with_both_providers_down_shows_the_error_state(...): ...  # status 200, role="alert", retry link
def test_results_are_grouped_and_limited_to_five_per_group(...): ...  # 7 shows: 5 rows + "Show all 7"; ?all=tv shows 7
def test_hits_already_in_the_library_show_their_status(...): ...  # no add button for them
def test_htmx_request_returns_only_the_results_fragment(...): ...  # no <html>, no header
def test_add_button_returns_a_polling_fragment(client, portal_headers, queued):
    response = client.post('/search/add/', {'provider': 'tmdb_tv', 'external_id': '1396', 'status': 'planned'}, **portal_headers)
    assert response.status_code == 200
    assert 'hx-trigger="load delay:2s"' in response.content.decode()
def test_add_of_a_synced_title_returns_the_open_link(...): ...
def test_forged_values_are_rejected(client, portal_headers):
    for data in ({'provider': 'imdb', 'external_id': '1', 'status': 'planned'},
                 {'provider': 'tmdb_tv', 'external_id': 'x', 'status': 'planned'},
                 {'provider': 'tmdb_tv', 'external_id': '1', 'status': 'hacked'}):
        assert client.post('/search/add/', data, **portal_headers).status_code == 400
    assert not Title.objects.exists()
def test_add_status_is_visible_only_to_its_owner(...): ...  # another user gets 404
def test_add_status_fragment_for_done_and_failed(...): ...
def test_search_requires_login(client): ...  # 401 without headers
def test_search_page_is_translated_to_russian(...): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_search.py -q`
Expected: FAIL (404 for `/search/`).

- [ ] **Step 3: Implement** following `docs/design/Search.dc.html`. The search input is `type="search"` with `hx-get`, `hx-trigger="input changed delay:400ms, search"`, `hx-target="#search-results"`, `hx-push-url="true"`; the user's `metadata_language` goes to `search(...)`. Cap the query at 100 characters.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_search.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the search and add screen"
```

---

### Task 9: Title page: display, status, score and note

**Files:**
- Create: `sources/web/views/titles.py`, templates `title.html`, `_title_body.html`, `_title_header.html`, `_library_panel.html`, `_episode_group.html`, `_episode_row.html`, `_episode_check.html`
- Modify: `sources/web/urls.py`, `app.css`, `django.po`
- Test: `tests/test_web_title.py`

**Interfaces:**
- Consumes: `tracking.queries.title_page_state`, `tracking.services.set_status/set_score/set_note`, `catalog.tasks.enqueue_sync_once/enqueue_language_once`, `web_extras` tags.
- Produces URL names and views:
  - `title` `GET /titles/<int:pk>/`: the page (404 for an unknown title). It queues `enqueue_sync_once(title)` and, when the user's metadata language is not English and the translation is missing, `enqueue_language_once`.
  - `title_refresh` `POST /titles/<int:pk>/refresh/`: queues a forced sync behind a 60 second claim and returns the syncing fragment.
  - `title_status` `POST /titles/<int:pk>/status/` with `status` (one of the four, or `remove`), `title_score` `POST .../score/` (`score` 0 to 10), `title_note` `POST .../note/` (`note`): each applies the service for `request.user` and returns `_library_panel.html` (status picker, five-star score, note editor) with `outerHTML` swap; invalid input is 400; score and note without an entry are 409.
- `_episode_check.html` is the seam for Task 10: here it renders a non-interactive state icon (watched, partly, not watched); Task 10 replaces it with the interactive control.
- Page content: header (poster, name, original name, kind tag, status tag, release date or year, runtime, rating badge), library panel, description (localized), then the episodes section: nothing for a movie; for a series one group per season (a "Specials" label for season 0), for an anime one group per franchise entry with a chip row (`Season.name`, else "Season N" for TV, else the kind label: OVA, Movie, Special) and a kind tag on single-episode groups. Episode rows show the number (`S1E3`, or the absolute number on a continuation season), the localized name or "Episode N", the air date (or "Date not announced", or "airs in N days" for a future date) and the check seam.
- States: syncing (never synced, shown for a title with `synced_at` empty: skeleton plus a poll of the page region every 3 seconds that replaces itself with the content once synced; after 15 minutes without a sync it shows the error block with a "Try again" button posting to `title_refresh`), degraded (Decision 11), announced (no episodes: "No episodes announced yet").

- [ ] **Step 1: Write the fixtures and the failing tests** in `tests/test_web_title.py`. `synced_show` (in `tests/conftest.py`, reused by Tasks 10 to 12) is `make_show()` from `tests/tracking_helpers.py` with `synced_at` set to now and `next_sync_at` an hour ahead, so no sync is queued by the page; `queued_language` patches `catalog.tasks.ensure_language_task` and `catalog.tasks.sync_title_task` with recorders and clears the Redis claim keys (`claim` uses the test Redis). `first_episode(title)` and `future_episode(title)` return the first aired and the first future main episode.

```python
def test_title_page_never_shows_another_users_note(client, portal_headers, other_headers, synced_show):
    LibraryEntry.objects.create(user=bob, title=synced_show, status='watching', note='bob secret', score=9)
    html = client.get(f'/titles/{synced_show.pk}/', **portal_headers).content.decode()
    assert 'bob secret' not in html and 'Add' in html     # alice is not tracking it

def test_title_page_for_a_degraded_entry(...):
    # season with episode_source 'anilist', rows without names: "Episode 1", a dated and an undated row,
    # the degraded note, and the page returns 200
def test_title_page_for_an_announced_title(...):  # no seasons: "No episodes announced yet", 200
def test_title_page_for_a_source_none_season_has_no_checks(...): ...
def test_never_synced_title_shows_the_polling_skeleton(...): ...  # hx-trigger every 3s, no 500
def test_stuck_title_offers_a_retry(...): ...  # synced_at empty and created 20 minutes ago
def test_movie_page_has_no_episode_section(...): ...
def test_anime_page_has_franchise_chips_and_kind_tags(...): ...
def test_localized_text_and_language_job(client, ..., queued_language):  # ru profile, missing translation:
    # the English text is shown and exactly one language job is queued (second visit queues none)
def test_unknown_title_is_404(...): ...
def test_status_score_note_update_the_panel(...): ...
    # POST status=watching -> panel shows Watching; score=8 -> 4 filled stars; note saved
def test_status_remove_deletes_the_entry_and_keeps_events(...): ...
def test_invalid_status_score_note_are_400_and_score_without_entry_is_409(...): ...
def test_title_page_is_translated_to_russian(...): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_title.py -q`
Expected: FAIL (404).

- [ ] **Step 3: Implement** following `docs/design/Title Page.dc.html` (header, description, facts that the catalog has, the episodes section). Score is shown as five stars where each star is two points (a half star is a score of 1, 3, ...); the control is a row of ten buttons grouped by star, or a `<select>` for keyboard users: keep it a `<select>` with options 0 to 10 labelled "Not rated", "1/10" ... on narrow screens and stars above 600 px (a single form either way).

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_title.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the title page with status, score and note"
```

---

### Task 10: Episode, season and title watch actions

**Files:**
- Create: `sources/web/views/actions.py`, template `_title_progress.html`, `_continue_bar.html`, `_hint_mark_up_to.html`
- Modify: `sources/web/urls.py`, `_episode_check.html`, `_episode_group.html`, `_episode_row.html`, `app.css`, `django.po`
- Test: `tests/test_web_actions.py`

**Interfaces:**
- Consumes: Task 4 services, Task 3 `title_page_state`.
- Produces URL names (all POST, all for `request.user`):
  - `episode_watch` `/episodes/<int:pk>/watch/` with `watched` (`1` or `0`) and optional `again=1`: returns the group fragment `_episode_group.html` for the episode's season plus out-of-band `_title_progress.html` and `_continue_bar.html`.
  - `episode_up_to` `/episodes/<int:pk>/up-to/`: same response after `mark_up_to`.
  - `season_watch` `/seasons/<int:pk>/watch/` with `watched`: the season's group fragment plus the out-of-band parts.
  - `title_watch_all` `/titles/<int:pk>/watch-all/` with `watched`: the whole `#episodes` section plus out-of-band parts.
  - `title_movie_watch` `/titles/<int:pk>/movie/` with `watched` and optional `again=1`: the library panel and the movie block ("Watched 2 times").
  - `title_continue` `/titles/<int:pk>/continue/`: `mark_next`; returns the out-of-band parts and the group that contains the episode; the same endpoint serves the "Continue" button on the Library cards (Task 11 posts here with `hx-target` set to its own card).
- An unaired episode is refused with status 409 and no change; an unknown or foreign object is 404 (episodes and seasons are catalog rows, visible to everyone; the effect is always scoped to `request.user`).
- Interactive control: `_episode_check.html` becomes a button with `aria-pressed`, `aria-label` ("Mark as watched: S1E3 · name"), a 44 px target and `hx-post` with the wanted `watched` value; unaired rows are disabled. A group header has a "mark all aired" control with `aria-pressed="mixed"` when partly watched; each row has an overflow menu (Alpine dropdown, also opened by long press) with "Mark all up to here as watched" and, for a watched episode, "Watched again" (shows "×2" after two viewings). After the first single-episode mark the page shows a one-time hint about "up to here"; dismissing it, or using the action, stores `mitaroku.hint.markUpTo` in `localStorage` (all `localStorage` calls in `try/catch`).

- [ ] **Step 1: Write the failing tests** in `tests/test_web_actions.py`:

```python
def test_actions_only_touch_the_requesting_user(client, portal_headers, other_headers, synced_show):
    ep = first_episode(synced_show)
    client.post(f'/episodes/{ep.pk}/watch/', {'watched': '1'}, **portal_headers)
    assert WatchEvent.objects.for_user(alice).count() == 1
    assert WatchEvent.objects.for_user(bob).count() == 0
    client.post(f'/episodes/{ep.pk}/watch/', {'watched': '0'}, **other_headers)  # bob unwatches
    assert WatchEvent.objects.for_user(alice).count() == 1                         # alice is untouched

def test_unaired_episode_is_refused_by_the_endpoint(client, portal_headers, synced_show):
    response = client.post(f'/episodes/{future_episode(synced_show).pk}/watch/', {'watched': '1'}, **portal_headers)
    assert response.status_code == 409 and not WatchEvent.objects.exists()

def test_posting_the_same_state_twice_adds_one_event(...): ...
def test_watch_fragment_has_the_group_and_out_of_band_progress(...):
    # response contains the group id and an element with hx-swap-oob for #title-progress
def test_again_adds_a_viewing_and_shows_the_count(...): ...
def test_up_to_marks_earlier_aired_episodes_only(...): ...
def test_season_and_whole_title_actions(...): ...  # watched=1 marks aired only; watched=0 clears
def test_extras_can_be_checked_without_changing_progress(...): ...
def test_movie_watch_and_rewatch(...): ...
def test_continue_marks_the_next_aired_episode(...): ...
def test_unknown_episode_is_404_and_get_is_405(...): ...
def test_actions_require_login_and_csrf(client): ...  # 401 without headers
def test_controls_are_44px_and_labelled(...): ...  # class mt-touch, aria-pressed and aria-label on the row control
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_actions.py -q`
Expected: FAIL (404 for the URLs).

- [ ] **Step 3: Implement** the views as thin wrappers: parse and validate the POST (`watched` must be `1` or `0`), call the service, rebuild `title_page_state` for the title, render only the needed fragments. Group fragments carry a stable id (`grp-<season pk>`) and the progress parts the ids `title-progress` and `continue-bar`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_actions.py tests/test_web_title.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the episode, season and title watch actions"
```

---

### Task 11: Library screen

**Files:**
- Modify: `sources/web/views/library.py`, `sources/web/templates/web/library.html`; create `_library_results.html`, `_continue_watching.html`, `_library_card.html`, `_library_row.html`
- Modify: `app.css`, `django.po`
- Test: `tests/test_web_library.py`

**Interfaces:**
- Consumes: `tracking.queries.library_rows/continue_watching`, `title_name`, the components of Task 7, `title_continue` of Task 10.
- Produces: `library` `GET /library/?tab=&status=&q=&sort=&view=&page=`:
  - `tab`: `all`, `tv` (Shows), `anime`, `movie`; `status`: `all` or one of the four; `q`: text (at most 100 characters); `sort`: `recent`, `title`, `rating`, `next`; `view`: `grid` or `list`; `page`: 1-based, 60 rows per page. Unknown values fall back to the defaults.
  - Sorting happens in Python on the rows from `library_rows`: `recent` is last watched descending then added descending; `title` is the localized name (case-insensitive); `rating` is the title rating descending with unrated last; `next` is the next episode's air date ascending, aired first, undated and finished last.
  - A request with `HX-Request` returns only `_library_results.html` (filters, results, pagination); the full page also includes "Continue watching" (a horizontal strip of cards with a one-tap "mark next as watched" button, enabled only when the next episode has aired).
  - Pagination is a "Show more" button that appends the next page (`hx-get`, `hx-swap="beforeend"`), no page numbers.
- States: loading (skeleton while an HTMX request is in flight), onboarding (no entries: the three-step explanation and a "Find a title" button to `search`), no match ("No titles match" with a "Clear filters" link), error (a database error is not caught; the error block is for a failed HTMX request via `htmx:responseError`, shown by a small Alpine handler with a retry button).
- List view is a table (Title, Type on wide screens, Progress, Next on wide screens, Status picker); grid view uses the poster card with progress and the next episode line.

- [ ] **Step 1: Write the failing tests** in `tests/test_web_library.py`:

```python
def test_library_with_an_announced_title(client, portal_headers, alice_entries):
    # an anime with no seasons in the library: 200, progress shows 0, the next line says no upcoming episodes
def test_library_shows_only_my_titles(...): ...
def test_empty_library_shows_onboarding_with_a_find_a_title_link(...): ...
def test_tabs_status_and_search_filter_the_results(...): ...
def test_sort_orders(...): ...  # recent, title, rating (unrated last), next
def test_unknown_filter_values_fall_back_to_defaults(...): ...
def test_htmx_request_returns_only_the_results_fragment(...): ...
def test_continue_watching_lists_watching_titles_and_enables_aired_next(...): ...
def test_continue_button_is_disabled_when_the_next_episode_has_not_aired(...): ...
def test_pagination_appends_the_next_page(...): ...  # 65 entries: 60 then 5
def test_library_query_count_does_not_grow_with_titles(django_assert_max_num_queries): ...
def test_grid_and_list_views(...): ...
def test_library_is_translated_to_russian(...): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_library.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** following `docs/design/Library.dc.html`, with the tabs, status chips, search, sort and view switch as one `<form method="get">` that HTMX submits (`hx-trigger="change, input delay:300ms from:[name=q]"`, `hx-push-url="true"`) and that works without JavaScript.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_library.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the library screen"
```

---

### Task 12: Upcoming screen

**Files:**
- Modify: `sources/web/views/upcoming.py`, `sources/web/templates/web/upcoming.html`; create `_upcoming_group.html`
- Modify: `app.css`, `django.po`
- Test: `tests/test_web_upcoming.py`

**Interfaces:**
- Consumes: `tracking.queries.upcoming`.
- Produces: `upcoming` `GET /upcoming/`: groups Today (date equals today), Tomorrow, This week (2 to 6 days), Later (7 days or more) and "Date not announced" ("We'll notify you when a date is set" is plan 4's promise; for now the line reads "No date yet"), each item showing the poster, name, "S1E4 · name" or "Movie premiere", the date with weekday, and the user's status label. Empty groups are not shown. States: loading skeleton, empty ("Nothing coming up" with a link to the library), error block.

- [ ] **Step 1: Write the failing tests** in `tests/test_web_upcoming.py`:

```python
def test_upcoming_without_dates(client, portal_headers, tracked_undated_airing_show):
    html = client.get('/upcoming/', **portal_headers).content.decode()
    assert 'Date not announced' in html and client.get('/upcoming/', **portal_headers).status_code == 200

def test_groups_by_distance(...): ...      # today, tomorrow, +3, +20 days land in the right groups
def test_only_watching_and_planned_titles_appear(...): ...
def test_other_users_titles_do_not_appear(...): ...
def test_empty_state_links_to_the_library(...): ...
def test_movie_premiere_label(...): ...
def test_upcoming_is_translated_to_russian(...): ...  # group headings and dates in Russian
```

Freeze "today" with `django.utils.timezone.localdate` patched (`monkeypatch.setattr('web.views.upcoming.timezone.localdate', lambda: TODAY)` or the `today` argument through a small module-level hook).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_upcoming.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** following `docs/design/Upcoming.dc.html`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_upcoming.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the upcoming screen"
```

---

### Task 13: Settings screen (account and language)

**Files:**
- Create: `sources/web/views/settings.py`, template `settings.html`
- Modify: `sources/web/urls.py`, `_header.html` (an Account menu "Settings" item), `app.css`, `django.po`
- Test: `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `accounts.models.Profile`.
- Produces: `settings_page` `GET/POST /settings/` (`name='settings'`): sections Account (name, email, sign-in method; read only) and Language (interface language and metadata language selects, both from `settings.LANGUAGES`). A valid POST saves the profile and redirects to `/settings/` (post/redirect/get) with the interface language already active; an invalid language value re-renders with an error and saves nothing.

- [ ] **Step 1: Write the failing tests** in `tests/test_web_settings.py`:

```python
def test_settings_shows_the_account_and_language_sections(...): ...
def test_changing_languages_saves_the_profile_and_switches_the_ui(client, portal_headers):
    client.get('/settings/', **portal_headers)
    response = client.post('/settings/', {'interface_language': 'ru', 'metadata_language': 'ru'}, **portal_headers)
    assert response.status_code == 302
    profile = User.objects.get(discord_id='1001').profile
    assert (profile.interface_language, profile.metadata_language) == ('ru', 'ru')
    assert 'Настройки' in client.get('/settings/', **portal_headers).content.decode()
def test_invalid_language_is_rejected_and_nothing_is_saved(...): ...
def test_metadata_language_changes_the_title_text(...): ...  # a title with ru and en translations
def test_settings_requires_login(client): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_settings.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** with a `ModelForm` over `Profile` (fields `interface_language`, `metadata_language`), following `docs/design/Settings.dc.html` for the Account and Language sections only.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_web_settings.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the settings screen with the language section"
```

---

### Task 14: Documentation, the spec, and a browser pass

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`

- [ ] **Step 1: Document tracking in the README** (a "Tracking and screens" section: what the screens do, that adding a title queues a job so `run_huey` must be running, the sync policy of Decision 8).

- [ ] **Step 2: Update the spec.** Section 13: remove the resolved items (degraded-mode UX, add-flow latency, deleting a placeholder, posters) and add "Plan 3b: Browse, Collection and title-page enrichment (genres, cast, trailers, similar titles)". Section 5: no change to the screen list.

- [ ] **Step 3: Run the whole suite and the linters.**

Run: `.venv/bin/python -m pytest -q; pre-commit run --all-files; .venv/bin/python sources/manage.py makemigrations --check --dry-run`
Expected: all green; `No changes detected`.

- [ ] **Step 4: Browser pass** (needs a TMDB token and `run_huey`; use a scratch database). Run `refresh_mapping` and `sync_title` for one anime, one series and one movie, start `runserver` and `run_huey`, and check Library, Search, Title (series, anime, movie), Upcoming and Settings at 390 px and 960 px, in light and dark, in English and Russian: touch targets at least 44 px, no horizontal scroll, focus visible, the loading, empty and error states, and the add flow end to end (search, Add, the polling fragment, Open). Fix the CSS issues found and record anything deferred in the pull request description.

- [ ] **Step 5: Commit**

```bash
git add README.md docs
git commit -m "docs: document tracking and the screens, update the spec"
```

---

## Spec coverage

| Spec section | Where |
| --- | --- |
| 3 Data model: `LibraryEntry`, `WatchEvent`, manager scoping | Tasks 2 to 4 |
| 3 `Profile` (languages) | Task 13 |
| 3 Orphan translations garbage-collected | Task 6 |
| 4 Scheduled sync prioritises watching and planned titles | Task 6 |
| 5 Library, Upcoming, Search and add, Title pages, Settings (language) | Tasks 8 to 13 |
| 5 Loading, empty and error states; 44 px targets | Tasks 7 to 12 |
| 10 Scoping through a manager; CSRF for HTMX | Tasks 2, 7, 10 |
| 11 Interface and metadata language | Tasks 7, 9, 13 |
| 13 Degraded-mode UX, add-flow latency, placeholder cleanup, posters | Tasks 5, 8, 9, 14 |

Not in this plan: Browse, Collection and title-page enrichment (plan 3b); Notifications (plan 4); Import and Export (plan 5); production deployment (plan 6).
