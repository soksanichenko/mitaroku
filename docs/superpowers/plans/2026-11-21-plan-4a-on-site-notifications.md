# On-site Notifications Implementation Plan (plan 4a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user sets a time zone, and the site tells them (bell with an unread count and a Notifications screen) when a followed episode airs, a planned title premieres or a movie is released, and when a release date is announced or moved, each at 09:00 in their zone, without ever repeating or flooding.

**Architecture:** A new `notifications` app owns one `Notification` table (one row per user, kind, title and local day). Two producers write it: a Huey scanner every 15 minutes finds releases that became due in each user's zone, and the title sync records changes of `Title.release_date`. The web layer adds the bell, the `/notifications/` screen with mark-read actions, and a time-zone section on Settings. Email, Telegram and digests are plan 4b.

**Tech Stack:** Django 6.1, PostgreSQL 17, Redis 7, Huey, HTMX, Alpine, `zoneinfo`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-plan-4a-on-site-notifications-design.md` (binding), argued from `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` sections 3, 5, 7, 11. The visual reference is `docs/design/Notifications.dc.html` and the notifications part of `docs/design/Settings.dc.html` (the mockups use inline demo data: take layout, states and wording, not logic).

## Global Constraints

- Django on the latest stable release (`Django==6.1.3` is pinned), Python 3.12, PostgreSQL, Redis, Huey on Redis; UI is HTMX + Alpine, no JavaScript build step (spec sections 2 and 5).
- "Five types: episode aired (titles in Watching), first episode of a new season or premiere (Planned), movie release date reached, release date announced, release date changed." (spec section 7). On-site is always on; there is no per-title or per-type setting.
- "Mechanics: dedupe through a unique key, grouping per title per day ... The first sync records a baseline only. A removed date produces no notification. Providers give dates, not times." (spec section 7).
- "All user-data queries go through a model manager that scopes them to the user." (spec section 3). `Notification` uses `for_user`; every web read of it goes through `Notification.objects.for_user(request.user)`. The scanner and the sync hook are system jobs that span users and say so in a comment.
- "Every screen has loading, empty and error states. Touch targets are at least 44 px." (spec section 5).
- "The UI ships in Russian and English on the Django i18n foundation." (spec section 11). Every new string goes through `{% trans %}` or `gettext` and gets a Russian translation in `sources/locale/ru/LC_MESSAGES/django.po` (additions only; the existing `(msgid, msgstr)` set, obsolete `#~` entries included, stays intact; check with `msgfmt --check -o /dev/null`).
- "CSRF protection covers HTMX requests." (spec section 10). Everything that writes is a POST with the CSRF token and touches only the requester's rows (another user's row is a 404).
- Single quotes for Python strings (docstrings use triple double quotes); type annotations on public functions; Google-style docstrings; PEP 8 with 88 columns preferred; log with `logging` and %-formatting.
- Everything committed (code, comments, commit messages, docs) is in English. Commit messages end with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; commits are GPG-signed (never bypass).
- Run every command from the repository root with `source .venv/bin/activate`; tests need the dev containers (`docker compose -f docker-compose.dev.yml up -d --wait`). Run `pre-commit run --all-files` and `python sources/manage.py makemigrations --check` before the last commit of a task that changes models.
- Time is never read from the clock inside logic: functions take `now: datetime` (as `sync_anime` does) and tests pass fixed values; there is no time-freezing library.

## Decisions

Rulings the implementer cannot make alone; they restate the design and are binding.

1. **Due moment.** A release is due at `NOTIFY_HOUR = 9` o'clock in the user's zone on its date. Anime too: AniList's exact `airingAt` is not stored. There is no `Episode.aired_at`.
2. **Zone.** `Profile.timezone` is an IANA name, default `UTC`; an unknown or removed name behaves as UTC at use time. `Profile.notify_since` (default: creation time; set to the migration time for existing profiles) is a lower bound for every scanned event.
3. **Kinds and recipients.**

   | Kind (`Notification.Kind`) | Recipients | Rule |
   |---|---|---|
   | `episode_aired` | Watching | an episode of the title became due |
   | `premiere` | Planned | episode number 1 of a season with `kind == 'tv'` and `is_extra == False` became due |
   | `movie_released` | Planned, Watching | a movie title's `release_date` became due |
   | `release_announced` | Planned | `Title.release_date` went from empty to a date later than today |
   | `release_changed` | Planned | `Title.release_date` moved between two dates and at least one is later than today |

   Completed and Dropped entries receive nothing. Single-episode date moves, removed dates and corrections wholly in the past never notify. Episodes the user already has a `WatchEvent` for are skipped.
4. **Scanner window and guards.** Due moments in `(now - 48 h, now]` count, and only when later than both `LibraryEntry.added_at` and `Profile.notify_since`. The 48 h look-back survives a stopped worker; `unique(user, dedupe_key)` makes reruns harmless.
5. **Rows.** One row per `(user, kind, title, local day)`: `dedupe_key = f'{kind}:{title_id}:{day.isoformat()}'` (`day` is the due moment's date in the user's zone; for sync-written rows it is `now`'s date in the user's zone). Later events of the same key merge into the row and mark it unread again. `payload` holds no display strings: `{'episodes': [<episode ids, ascending>]}` for `episode_aired` and `premiere`; `{}` for `movie_released`; `{'to': 'YYYY-MM-DD'}` for `release_announced`; `{'from': 'YYYY-MM-DD', 'to': 'YYYY-MM-DD'}` for `release_changed` (a repeat on the same day replaces `to` and keeps `from`).
6. **First sync.** The sync hook writes nothing when the title had no `synced_at` before the run.
7. **Display.** Text is built when the page renders, in the interface language (labels) and metadata language (title and episode names), so switching language changes old rows. Kind labels (English): "New episode", "Premiere", "Released", "Date announced", "Date changed". Groups are headed by the user's local day ("Today", "Yesterday", then a short date).
8. **Out of scope** (design non-goals): email, Telegram, `Delivery`, digests, per-title or per-type settings, a configurable hour, live polling of the bell, push notifications, `Episode.aired_at`.

## Review Focus

The inputs and failures the spec implies but its straight-line tests would miss, most likely first; each has a pinning test in the task named.

1. The scan runs twice, or two runs overlap: no duplicate row, no second unread flip (Task 2: `test_a_second_scan_changes_nothing`).
2. Deploy day and a freshly added long-running show: no flood from episodes older than the entry or `notify_since` (Task 1 migration test, Task 2: `test_events_before_the_entry_or_notify_since_are_ignored`).
3. A stored zone name that no longer exists, or a half-hour or far-east zone (`Asia/Kolkata`, `Pacific/Auckland`): never a crash, and the 09:00 moment is right (Task 1 `test_due_moment_*`, Task 2 `test_an_unknown_zone_counts_as_utc`).
4. Another user's notification: never listed, opened, marked read, and never counted in the bell (Tasks 4 and 5).
5. A failing notification write must not fail or retry a title sync (Task 3: `test_a_failing_hook_does_not_fail_the_sync`).

## File Structure

- Create `sources/notifications/`: `__init__.py`, `apps.py`, `models.py` (`Notification`), `moments.py` (zone and 09:00 math), `scan.py` (`scan_due`), `changes.py` (`record_release_change`), `tasks.py` (`notify_due`, `gc_notifications`), `migrations/`.
- Modify `sources/accounts/models.py` (+ migration `0004`), `sources/mitaroku/settings.py`, `sources/catalog/sync/__init__.py`, `sources/web/` (views `notifications.py` and `settings.py`, `urls.py`, templatetags, templates `notifications.html`, `_notification_*.html`, `_header.html`, `settings.html`, `app.css`), the `ru` `django.po`, `README.md`, and the requirements spec (sections 3 and 7).
- Tests (create): `tests/test_moments.py`, `test_notification_models.py`, `test_notification_scan.py`, `test_notification_changes.py`, `test_notification_tasks.py`, `test_web_notifications.py`; extend `tests/test_web_settings.py` and `tests/test_tracking_scoping.py` only where a task says so.

---

### Task 1: The `notifications` app, `Notification`, and the zone fields

**Files:**
- Create: `sources/notifications/{__init__,apps,models,moments}.py`, `sources/notifications/migrations/` (generated), `sources/accounts/migrations/0004_profile_timezone.py` (generated)
- Modify: `sources/accounts/models.py`, `sources/mitaroku/settings.py` (add `'notifications'` after `'tracking'`)
- Test: `tests/test_moments.py`, `tests/test_notification_models.py`

**Interfaces:**
- Produces, `notifications.moments`: `NOTIFY_HOUR: int = 9`; `user_zone(name: str) -> ZoneInfo` (UTC for an empty, unknown or malformed name, never raises); `due_moment(day: date, zone_name: str) -> datetime` (aware, 09:00 local on `day`); `local_day(moment: datetime, zone_name: str) -> date`; `zone_names() -> list[str]` (sorted IANA names, `UTC` first, only names that `user_zone` accepts).
- Produces, `notifications.models`: `Notification` (fields and constraint per Decision 5: `user` FK `related_name='notifications'`, `kind` `TextChoices` `Kind` with the five values of Decision 3, `title` FK `catalog.Title` cascade, `day` DateField, `payload` JSONField default dict, `dedupe_key` CharField(80), `created_at` DateTimeField default `timezone.now`, `read_at` DateTimeField null; `UniqueConstraint(fields=['user','dedupe_key'], name='unique_notification_key')`; `Index(fields=['user','read_at','-created_at'], name='notification_unread_idx')`; `objects = tracking.models.UserScopedManager()`); `dedupe_key(kind: str, title_id: int, day: date) -> str`.
- Produces, `accounts.models.Profile`: `timezone = CharField(max_length=64, default='UTC')`, `notify_since = DateTimeField(default=timezone.now)` (name the import so it does not shadow the field).

- [ ] **Step 1: Write the failing tests.** `tests/test_moments.py`: `test_due_moment_is_nine_local` (`due_moment(date(2026,11,21),'Europe/Kyiv')` equals `datetime(2026,11,21,7,0,tzinfo=UTC)` — UTC+2 in November); `test_due_moment_half_hour_and_far_east_zones` (`Asia/Kolkata` gives 03:30 UTC, `Pacific/Auckland` on 2026-11-21 gives 20:00 UTC the day before); `test_due_moment_follows_daylight_saving` (`Europe/Kyiv` on 2026-07-01 gives 06:00 UTC); `test_local_day_crosses_midnight` (`local_day(datetime(2026,11,21,23,30,tzinfo=UTC),'Asia/Tokyo') == date(2026,11,22)`); `test_user_zone_never_raises` (parametrised: `''`, `'Nope/Zone'`, `'../etc'`, `'a'*200`, `None`-like empty string → `ZoneInfo('UTC')`); `test_zone_names_start_with_utc_and_are_valid`. `tests/test_notification_models.py`: `test_dedupe_key_format` (`'episode_aired:7:2026-11-21'`); `test_the_key_is_unique_per_user_not_across_users` (same key for `alice` and `bob` is fine, twice for `alice` raises `IntegrityError`); `test_for_user_returns_only_own_rows`; `test_new_profile_notify_since_is_now_and_zone_is_utc`; `test_migration_gives_existing_profiles_the_migration_time` (use `MigrationExecutor`: migrate accounts to `0003`, create a user and its profile row, migrate to `0004`, assert `notify_since` is within the last minute and `timezone == 'UTC'`; migrate forward again in `finally`).
- [ ] **Step 2: Run them to see them fail.** `pytest tests/test_moments.py tests/test_notification_models.py -v` → collection or import errors.
- [ ] **Step 3: Implement** the app (`apps.py`: `NotificationsConfig`, `name = 'notifications'`), the model, the helpers and the `Profile` fields; register the app; `python sources/manage.py makemigrations accounts notifications`. `zone_names` filters `zoneinfo.available_timezones()` by `user_zone`-acceptance and drops names starting with `Etc/`, `posix/`, `right/`, `SystemV/`. Do not register `Notification` in the admin (user data; same rule as `tracking`).
- [ ] **Step 4: Run tests and checks.** `pytest tests/test_moments.py tests/test_notification_models.py -v` → PASS; `python sources/manage.py makemigrations --check` → "No changes detected"; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git add sources/notifications sources/accounts sources/mitaroku/settings.py tests && git commit -m "feat: add the notifications app, the zone fields and the 09:00 moment helpers"`.

---

### Task 2: The scanner: releases that became due

**Files:**
- Create: `sources/notifications/scan.py`, `sources/notifications/tasks.py` (only `notify_due` here)
- Test: `tests/test_notification_scan.py`, `tests/test_notification_tasks.py`

**Interfaces:**
- Consumes: Task 1 (`Notification`, `dedupe_key`, `moments.due_moment`, `moments.local_day`, `Profile.timezone`, `Profile.notify_since`); `catalog.models` (`Episode`, `Season`, `Title`); `tracking.models` (`LibraryEntry`, `WatchEvent`); helpers in `tests/tracking_helpers.py` (`make_show`) and `tests/conftest.py` (`alice`, `bob`).
- Produces: `notifications.scan.WINDOW: timedelta = timedelta(hours=48)`; `scan_due(now: datetime) -> int` (rows created or changed); `notifications.tasks.notify_due() -> int` (`@db_periodic_task(crontab(minute='*/15'))`, calls `scan_due(timezone.now())`, logs the count at INFO).

Algorithm the tests do not determine (follow it, since the query count must not grow with users or titles): (a) `lo = (now - WINDOW).date() - 1 day`, `hi = now.date() + 1 day` is the coarse date range (zones span UTC-12 to UTC+14); (b) one query for candidate episodes (`air_date` in range, with season kind, `is_extra`, title id) and one for candidate movies (`kind='movie'`, `release_date` in range); (c) one query for the library entries of those titles with status Watching or Planned, `select_related('user__profile')`; (d) one query for the user's `WatchEvent` pairs for the candidate episodes; (e) in Python, keep an event when its due moment (`due_moment(date, user's zone)`) is in `(now - WINDOW, now]` and later than `max(entry.added_at, profile.notify_since)` and, for episodes, not watched; group into rows per Decision 5; (f) one query for the existing rows by key; `bulk_create(ignore_conflicts=True)` for new rows, `bulk_update` for merged ones (payload extended, `read_at=None`). Comment on the unscoped `LibraryEntry`/`WatchEvent` reads: system job spanning users.

- [ ] **Step 1: Write the failing tests** (fixed `now = datetime(2026,11,21,12,0,tzinfo=UTC)`; build titles with the helpers or `Episode.objects.create`; give entries an old `added_at` via `LibraryEntry.objects.filter(pk=...).update(added_at=...)` and set `profile.notify_since` old). `test_an_aired_episode_notifies_a_watching_user` (episode air date `2026-11-21`, user zone `Europe/Kyiv` → one `episode_aired` row, `day == date(2026,11,21)`, `payload == {'episodes': [episode.pk]}`, unread); `test_a_planned_user_gets_a_premiere_only_for_episode_one_of_a_tv_season` (episode 1 of season 2 → `premiere`; episode 2 → nothing; an `is_extra` season's episode 1, a `kind='ova'` season's episode 1 → nothing; a Watching user gets `episode_aired` for the same episode 1, not `premiere`); `test_a_movie_release_notifies_planned_and_watching` (not Completed or Dropped); `test_nothing_before_the_moment` (episode dated 2026-11-21, zone `Pacific/Auckland` is due 20:00 UTC on 11-20 and so notified, while zone `America/Los_Angeles` (due 17:00 UTC on 11-21) is not yet due at 12:00 UTC); `test_events_before_the_entry_or_notify_since_are_ignored` (entry `added_at` after the due moment; and separately `notify_since` after it → no rows); `test_the_window_is_forty_eight_hours` (due 47 h ago → row; due 49 h ago → none); `test_a_second_scan_changes_nothing` (run twice: row count equal, `read_at` stays set when the user read the row between the runs); `test_two_episodes_of_one_day_merge_and_unread_again` (second episode appears after the first scan, the user read the row: one row, payload has both ids ascending, `read_at is None`); `test_watched_episodes_are_skipped` (a `WatchEvent` of the user for the episode; another user's event does not suppress this user's row); `test_completed_and_dropped_entries_get_nothing`; `test_an_unknown_zone_counts_as_utc` (`profile.timezone = 'Gone/Zone'`); `test_rows_are_per_user` (alice and bob both watching: one row each); `test_the_query_count_does_not_grow` (compare `CaptureQueriesContext` counts for 1 user with 1 title versus 4 users with 4 titles each: equal). `tests/test_notification_tasks.py`: `test_notify_due_scans_now_and_returns_the_count` (patch `notifications.tasks.scan_due`, call `notify_due.call_local()`).
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_notification_scan.py tests/test_notification_tasks.py -v` → import errors.
- [ ] **Step 3: Implement** `scan_due` and `notify_due` per the algorithm above; keep helpers private and small (`_candidates`, `_rows`). Huey finds `notifications/tasks.py` by autodiscovery.
- [ ] **Step 4: Run.** The same command → PASS; then `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: scan for releases that became due in each user's zone"`.

---

### Task 3: Release-date changes recorded by the sync

**Files:**
- Create: `sources/notifications/changes.py`
- Modify: `sources/catalog/sync/__init__.py` (`sync_title`)
- Test: `tests/test_notification_changes.py`

**Interfaces:**
- Consumes: Task 1; `catalog.sync.sync_title(title, clients=None, now=None)` which fills `title` in place.
- Produces: `notifications.changes.record_release_change(title: Title, old_date: date | None, was_synced: bool, now: datetime) -> int` (rows created or changed). `sync_title` reads `old_date = title.release_date` and `was_synced = title.synced_at is not None` before the kind-specific sync, and calls it afterwards with `now`; an exception from it is logged (`logger.exception`) and swallowed.

Rules (Decisions 3, 5, 6): nothing when `not was_synced`, when the new date equals the old one or is empty; `release_announced` when old is empty and the new date is later than `now.date()`; `release_changed` when both exist and `max(old, new) > now.date()`; recipients are the Planned entries of the title (`LibraryEntry.objects`, system read); `day` is `local_day(now, zone)`; a repeat with the same key updates `to`, keeps `from` and marks the row unread.

- [ ] **Step 1: Write the failing tests.** `test_first_sync_is_a_baseline` (`was_synced=False` → 0 rows); `test_an_announced_date_notifies_planned_users_only` (Watching and Completed entries get nothing); `test_a_date_announced_in_the_past_is_not_news`; `test_a_changed_date_records_from_and_to`; `test_a_correction_wholly_in_the_past_is_ignored`; `test_a_removed_date_notifies_nobody`; `test_an_unchanged_date_notifies_nobody`; `test_a_second_change_on_the_same_day_keeps_from_and_replaces_to` (one row, `read_at is None`); `test_the_day_follows_the_users_zone` (`now = 2026-11-21T23:30Z`, zone `Asia/Tokyo` → `day == 2026-11-22`); `test_sync_title_records_the_change_end_to_end` (use `FakeTmdb` through the `fake_clients` fixture as `tests/test_sync_tmdb.py` does: sync a movie once, change the fake's release date to a future date, sync again → one `release_changed` row for a Planned user); `test_a_failing_hook_does_not_fail_the_sync` (monkeypatch `catalog.sync.record_release_change` to raise: `sync_title` returns normally and the title is saved).
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_notification_changes.py -v` → FAIL.
- [ ] **Step 3: Implement** `record_release_change` (one query for entries, one for existing keys, `bulk_create(ignore_conflicts=True)` plus `bulk_update`) and wire `sync_title`; import `record_release_change` at module level in `catalog/sync/__init__.py` so the test can patch it there.
- [ ] **Step 4: Run.** `pytest tests/test_notification_changes.py tests/test_sync_tmdb.py tests/test_sync_anime.py -v` → PASS; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: record release date announcements and changes during the sync"`.

---

### Task 4: The bell and the Notifications screen

**Files:**
- Create: `sources/web/views/notifications.py`, `sources/web/templates/web/notifications.html`, `_notification_group.html`, `_notification_row.html`, `_notifications_more.html`
- Modify: `sources/web/urls.py`, `sources/web/templatetags/web_extras.py`, `sources/web/templates/web/_header.html`, `sources/web/static/web/app.css`, `sources/locale/ru/LC_MESSAGES/django.po`
- Test: `tests/test_web_notifications.py`

**Interfaces:**
- Consumes: Task 1 (`Notification`, `moments.local_day`, `Profile.timezone`); the existing `title_name`, `episode_name`, `episode_code`, `short_date` tags and the paging and fragment pattern of `web/views/library.py` and `web/views/browse.py` (Load more).
- Produces: URL `notifications` → `GET /notifications/` (`?unread=1`, `?page=N`, fragment requests for Load more); view `notifications_page(request) -> HttpResponse`; simple tag `{% unread_notifications %}` returning the user's unread count; header bell link to `notifications` with a badge (`99+` above 99) and an accessible name that includes the count; `PAGE_SIZE = 30`.

Behaviour: rows ordered `-day, -created_at, -pk`; headings are the user's local day ("Today", "Yesterday", else `short_date`); each row shows the kind icon (from the design), the uppercase kind label, the title name, a text line — episode kinds list codes with `episode_code` and, for one episode, its name; `movie_released` says the movie was released; date kinds show `to` in bold and `from` struck through before an arrow, as in the design — and an unread dot with a visually hidden "unread" label. Episode ids in payloads are loaded in one query with their seasons, title and episode translations are prefetched like the Upcoming view does; a deleted episode id is skipped. States: loading (skeleton, the existing `_state_loading` component, for the Load more request), error (`_state_error` with a retry link), empty ("You're all caught up" with the design's text; "No unread notifications" with "Turn off “Unread only” to see earlier notifications." when the filter hides rows). "Unread only" is a plain link that toggles `?unread=1`. This task renders only; the mark-read actions come in Task 5 (leave the row as a link to the title page for now).

- [ ] **Step 1: Write the failing tests** (client logged in through `portal_headers`; create rows directly). `test_the_page_lists_only_own_notifications` (bob's row is absent); `test_rows_are_grouped_under_local_day_headings` (a row dated the user's today shows "Today", the day before "Yesterday", an older one a date); `test_unread_only_filters_read_rows`; `test_the_empty_states_differ_with_the_filter`; `test_each_kind_renders_its_label_and_text` (parametrised over the five kinds with English labels of Decision 7; the change row contains both dates and an arrow); `test_the_page_renders_in_russian` (`Новая серия` for `episode_aired` with `interface_language='ru'`); `test_load_more_pages_by_thirty` (31 rows → 30 on page 1, a Load more link, the fragment request returns the rest and no further link); `test_page_clamps_garbage` (`?page=abc`, `?page=-3`, `?page=99999` → 200); `test_the_bell_shows_the_unread_count_only_for_the_user` (alice has 2 unread, bob 5 → alice's header says 2; 100+ unread → `99+`; none → no badge); `test_the_bell_count_is_one_query`; `test_anonymous_is_sent_to_login` (follow the project's existing behaviour for other pages); `test_the_page_query_count_is_constant` (10 rows versus 30 rows, same count). Also run `tests/test_tracking_scoping.py`: it must stay green; if it needs `web/` rules for `Notification`, add `Notification` to its pattern the way the other user-scoped models are listed.
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_web_notifications.py -v` → FAIL (no URL).
- [ ] **Step 3: Implement** the view, templates, tag, header bell and CSS following the design file; add Russian translations for every new msgid, including the five kind labels (`Новая серия`, `Премьера`, `Вышел`, `Дата объявлена`, `Дата изменена`) and the state texts; `msgfmt --check -o /dev/null sources/locale/ru/LC_MESSAGES/django.po`.
- [ ] **Step 4: Run.** `pytest tests/test_web_notifications.py tests/test_translations.py tests/test_tracking_scoping.py -v` → PASS; `pytest -q` → all green (if an existing test asserts a fixed query count for a page that now renders the bell, raise it by exactly one and say so in the report).
- [ ] **Step 5: Commit.** `git commit -m "feat: add the notification bell and the Notifications screen"`.

---

### Task 5: Mark read: open a row, mark one, mark all

**Files:**
- Modify: `sources/web/views/notifications.py`, `sources/web/urls.py`, `_notification_row.html`, `notifications.html`, `app.css`, the `ru` `django.po`
- Test: `tests/test_web_notifications.py`

**Interfaces:**
- Consumes: Task 4's view and templates.
- Produces (all POST, CSRF-protected, `@require_POST`): `notification_open` → `POST /notifications/<int:pk>/open/` marks the row read (keeps an earlier `read_at`) and answers a 303 redirect to `title` of the row's title; `notification_read` → `POST /notifications/<int:pk>/read/` marks it read and redirects back to the list (keeping `unread` and `page` from a `next` hidden field only when it is a local path, otherwise the plain list); `notifications_read_all` → `POST /notifications/read-all/` marks every unread row of the user read and redirects to the list. Another user's pk is a 404 for the first two. The row's main control becomes a `<form method="post">` with a full-width button (it works without JavaScript); unread rows also carry a small "Mark as read" button; the header of the page carries "Mark all as read", disabled when there is nothing unread.

- [ ] **Step 1: Write the failing tests.** `test_opening_marks_read_and_redirects_to_the_title`; `test_opening_keeps_the_first_read_time`; `test_marking_one_keeps_the_user_on_the_list`; `test_next_must_be_a_local_path` (`next=https://evil.example/` → redirect to the list); `test_mark_all_read_marks_only_own_rows` (bob's rows stay unread); `test_another_users_row_is_a_404_for_open_and_read` (and stays unread); `test_get_is_not_allowed_on_the_mutating_urls` (405); `test_post_without_csrf_is_rejected` (`Client(enforce_csrf_checks=True)`); `test_mark_all_is_disabled_when_nothing_is_unread`; `test_the_bell_updates_after_marking` (count drops on the next page load).
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_web_notifications.py -v` → the new tests FAIL.
- [ ] **Step 3: Implement** the three views and the row markup; keep touch targets at least 44 px; translations for "Mark as read", "Mark all as read" (`Отметить как прочитанное`, `Отметить все как прочитанные`).
- [ ] **Step 4: Run.** `pytest tests/test_web_notifications.py tests/test_translations.py -v` → PASS; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: mark notifications read from the list and when opened"`.

---

### Task 6: Time zone on Settings

**Files:**
- Modify: `sources/web/views/settings.py`, `sources/web/urls.py`, `sources/web/templates/web/settings.html`, `sources/web/static/web/app.css`, the `ru` `django.po`
- Test: `tests/test_web_settings.py`

**Interfaces:**
- Consumes: Task 1 (`zone_names`, `user_zone`, `Profile.timezone`, `Profile.notify_since`).
- Produces: URL `settings_timezone` → `POST /settings/timezone/` with field `timezone`; a form class `TimezoneForm` (`ChoiceField` over `zone_names()`); a "Notifications" section on the settings page. On a valid change the view sets `profile.timezone` and `profile.notify_since = timezone.now()`, sets the existing saved flag and redirects to `settings`; saving the same zone changes nothing (including `notify_since`); an invalid value re-renders the page with the error. The section says: on-site notifications are always on (the bell shows unread items); notifications arrive at 09:00 in the chosen zone; email and Telegram are "coming later" (text only, no controls). The select is grouped by region (`<optgroup>` by the part before the first `/`, with `UTC` alone first). When the profile still holds `UTC` and the browser reports another listed zone (`Intl.DateTimeFormat().resolvedOptions().timeZone`), Alpine preselects it and shows a hint "Detected from your browser"; nothing is saved until the user presses Save, and the page works with plain `UTC` when JavaScript is off. The old template comment and text that say notifications come with later plans are updated.

- [ ] **Step 1: Write the failing tests.** `test_the_settings_page_lists_zones_and_the_current_one_is_selected`; `test_a_valid_zone_is_saved_and_notify_since_moves` (assert `notify_since` increased and the redirect carries the saved flag); `test_saving_the_same_zone_keeps_notify_since`; `test_an_invalid_zone_is_rejected_and_nothing_changes` (`'Mars/Base'`, `''`, `'../x'`); `test_the_timezone_form_edits_only_the_requesters_profile` (bob's zone untouched); `test_get_on_the_timezone_url_is_405`; `test_the_section_has_no_email_or_telegram_controls`; `test_the_page_renders_in_russian` (section heading `Уведомления`, hint text translated); `test_the_browser_hint_markup_is_present_only_for_the_default_zone` (the Alpine detection block exists when `timezone == 'UTC'` and is absent otherwise).
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_web_settings.py -v` → new tests FAIL.
- [ ] **Step 3: Implement** the form, the view, the template section and Alpine detection (no new script file: inline `x-data` as the header menu does); translations; `msgfmt --check -o /dev/null`.
- [ ] **Step 4: Run.** `pytest tests/test_web_settings.py tests/test_translations.py -v` → PASS; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: let users choose the time zone that sets their notification hour"`.

---

### Task 7: Retention, documentation and translation sweep

**Files:**
- Modify: `sources/notifications/tasks.py` (add `gc_notifications`), `README.md`, `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 3 and 7), the `ru` `django.po`
- Test: `tests/test_notification_tasks.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `notifications.tasks.gc_notifications(now: datetime | None = None) -> int` (`@db_periodic_task(crontab(day_of_week='0', hour='5', minute='0'))`; deletes read rows with `read_at` older than 90 days and any rows with `created_at` older than 180 days; returns the number deleted).

- [ ] **Step 1: Write the failing tests.** `test_gc_deletes_old_read_rows` (read 91 days ago → gone; read 89 days ago → kept); `test_gc_deletes_any_row_older_than_180_days`; `test_gc_keeps_recent_unread_rows`; `test_gc_returns_the_count`; `test_both_tasks_are_registered` (the periodic tasks `notify_due` and `gc_notifications` are in Huey's registry).
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_notification_tasks.py -v` → FAIL.
- [ ] **Step 3: Implement** the task. Docs, surgically: README gains a short "Notifications" section (what is notified, the 09:00 rule, the zone setting, the 15-minute scanner, the 48 h look-back, retention, and that email and Telegram are plan 4b); spec section 3 gets `Profile.timezone`, `Profile.notify_since` and `Notification`; spec section 7 gets the 09:00 rule, the kind-to-status table of Decision 3, per-title-per-day rows, the merge-and-unread rule, the retention windows and a line that the digest for email and Telegram is decided in plan 4b. Sweep: `python sources/manage.py makemessages -l ru --no-obsolete` must not be used (additions only); instead confirm no new msgid lacks a Russian entry with `msgfmt --statistics` on the `.po` and `pytest tests/test_translations.py`.
- [ ] **Step 4: Run.** `pytest tests/test_notification_tasks.py tests/test_translations.py -v` → PASS; `pytest -q` → all green; `pre-commit run --all-files` and `python sources/manage.py makemigrations --check` → clean.
- [ ] **Step 5: Commit.** `git commit -m "feat: delete old notifications; document notifications and the zone setting"`.

---

### Task 8: Live pass and browser pass (controller only)

Run by the controller after the final whole-branch review, not by an implementer, as in plan 3b. Use a scratch database and Redis db (never the dev ones), the real sync against TMDB and AniList with the token from `~/.tmdb-credentials.json` kept out of logs, and a real browser.

- [ ] **Step 1: Live data.** Sync one airing series, one upcoming movie and one anime; put them into a user's library as Watching, Planned and Watching; backdate `LibraryEntry.added_at` and `Profile.notify_since`; set an episode's `air_date` to today; run `notify_due.call_local()` and confirm the rows, then run it again and confirm no change. Change a Planned title's `release_date` and run a forced sync with the provider stubbed to confirm `release_changed` (do not mutate real provider data).
- [ ] **Step 2: Browser.** At 390 and 960 px, light and dark, English and Russian: bell badge, the Notifications screen (live, empty, unread-only empty, error via a forced failure), opening a row, mark one, mark all, Load more with more than 30 rows, the Settings zone section with and without a detected browser zone, keyboard focus and 44 px targets.
- [ ] **Step 3: Record** findings in the ledger; anything that is a defect goes to the single fix wave, minors to the deferred list. Then clean up scratch resources (database, Redis db, servers, worker).

---

## Self-review notes

- Spec coverage: five kinds (Tasks 2, 3), zone and 09:00 (Tasks 1, 2, 6), baseline and no flood (Tasks 1–3), grouping and dedupe (Task 2), bell and screen with states (Tasks 4, 5), Settings (Task 6), retention and spec amendments (Task 7), live verification (Task 8). Email, Telegram, `Delivery` and digests are plan 4b by design.
- Names used across tasks: `Notification`, `Notification.Kind`, `dedupe_key`, `due_moment`, `local_day`, `user_zone`, `zone_names`, `scan_due`, `notify_due`, `record_release_change`, `gc_notifications`, URL names `notifications`, `notification_open`, `notification_read`, `notifications_read_all`, `settings_timezone`.
