# mitaroku: Requirements and Design

Date: 2026-10-09
Status: draft for review

## 1. Purpose

mitaroku (見た録) is a self-hosted, multi-user tracker for movies, TV series and
anime. Its tracking UX follows MyShows and Yamtrack. Beyond tracking it adds
notifications about new episodes and dates, and import/export from and to other
tracking services.

Success criteria for the MVP:

- A user can find a title, add it to the library, mark episodes watched and see
  progress and the next air date.
- A user can import an existing library (Yamtrack CSV, which also covers MyShows;
  IMDb CSV; Letterboxd CSV) and export it back out.
- A user receives notifications (on-site, email, Telegram) about episodes,
  premieres and release dates.
- The service runs behind the existing SSO portal and deploys with the existing
  Docker and Ansible tooling.

Non-goals: social features (comments, reviews, sharing, public profiles,
popularity counters), TVDB as a data source, per-title notification settings,
curated collection pages.

UI reference: `docs/design/` (Claude Design mockup, Modernist design system,
light and dark themes, 390 and 960 px).

## 2. Architecture

Modular monolith (Django, PostgreSQL, HTMX + Alpine + Tailwind), two
processes: web (gunicorn) and worker (Huey on Redis). Django tracks the latest
stable release (not an LTS pin), so security fixes arrive through Dependabot.

Django apps: `accounts`, `catalog`, `providers`, `tracking`, `imports`/`exports`,
`notifications`, `web`.

Supporting stack: Redis (provider response cache, rate limiter, locks, Huey
broker), httpx, Pydantic Settings (YAML with environment override), Infisical
for secrets, Docker, Ansible (repo created from `service-repo-template`).

## 3. Data model

- `Title`: type movie, tv or anime. A movie is a `Title` without seasons.
- `ExternalId`: (provider, id) per title, including the IMDb id.
- `TitleTranslation`: lazily filled persistent cache per language. Search results
  are not persisted. Empty values are not stored. Orphans are garbage-collected.
  Fallback chain: user language, original, English.
- `Season`: for anime, a franchise group of type tv, ona, ova, movie or special.
- `Episode`: number, absolute number, air date.
- `LibraryEntry`: status watching, planned, completed or dropped ("not watching"
  means no entry). Score stored 0 to 10 and shown as 5 stars. Private note.
- `WatchEvent`: user, title, nullable episode, `watched_at`. Progress is derived.
  A rewatch is an additional event.
- `Profile`: interface language, metadata language, notification preferences,
  `timezone` (IANA name, default UTC; an unknown name behaves as UTC) and
  `notify_since` (nothing earlier than this moment is notified; it guards against
  events from before the feature or the profile existed, and a zone change does not
  touch it).
  The email channel adds four fields: `email_enabled` (the opt-in, off by default),
  `email_since` (set when the channel is first switched on and cleared when it is
  switched off; nothing created at or before it is ever mailed, so it is the opt-in
  guard), `notify_email` (the address the user entered and confirmed) and
  `pending_email` (an entered address that waits for its confirmation link).
- `Notification`: one row per user, kind, title and local day (unique `dedupe_key`),
  with a data-only `payload`, `created_at` and `read_at`. It is user data: read through
  the user-scoped manager and not registered in the admin.
- `Delivery`: one row per notification and channel (unique per pair; only `email`
  exists so far), with a `status` of pending, sent, failed, expired or skipped, the
  number of `attempts`, `next_attempt_at`, `sent_at` and a data-only `last_error`
  text (the exception class and a cut message, never the recipient address). It is
  user data: read through the user-scoped manager.

All user-data queries go through a model manager that scopes them to the user.

## 4. Providers and caching

| Provider | Role in the MVP |
| --- | --- |
| TMDB | Movies and TV, anime episodes (titles, overviews, stills, air dates), external ids including IMDb, MVP rating |
| AniList | Anime entries, sequel/prequel relations (franchise grouping), upcoming episode schedule, anime rating; no key |
| Fribb/anime-lists | Dataset bridging MAL, AniList, TMDB and IMDb ids, with season and episode offset |

IMDb ratings (post-MVP) come from the public datasets by `imdb_id`. API keys are
global server-side keys from Infisical.

Cache layers: Postgres (durable), Redis TTL for responses, a per-provider rate
limiter, a single-flight lock, and stale-on-error with Huey retry. Scheduled sync
prioritises watching and planned titles.

The anime id mapping was the riskiest part of the system. The spike
(`docs/superpowers/spikes/2026-10-10-anime-source-and-id-mapping.md`) settled it: Fribb is
the bridge, AniList gives the franchise, and TMDB episodes are accepted only when the first
episode airs within 2 days of the AniList start date and the allocation has exactly the
AniList episode count (the spike measured 29 of 38 TV/ONA entries passing). Entries that fail the guard
fall back to a degraded mode: the episode count, start date and rating from AniList, and per-episode dates only when its `airingSchedule` has one node per episode (3 of the 8 probed entries); no titles.
Simkl is not used because its catalog terms require Simkl login and sync.

The franchise rule: the main chain follows SEQUEL and PREQUEL links between anime entries;
a MOVIE or SPECIAL with a PARENT edge is an extra, but the walk passes through it. The
Fribb dataset is stored in Postgres and refreshed weekly. Plan 2 records the rule's known
limits (`docs/superpowers/plans/2026-10-19-plan-2-catalog-and-providers.md`).

## 5. Screens

Library (continue watching, tabs, status filter, sort, grid/list), Upcoming,
Search and add, title pages (show, anime, movie), Browse (target of every "See
also" link, filters live in the URL), Collection (TMDB movie franchises),
Notifications, Settings, Import, Export, Login (local fallback). Every screen has
loading, empty and error states. Touch targets are at least 44 px. Browse has no
bottom-bar tab: it is reached from "See also", from the genre and country tags on
the title page and from one link on the idle Search page.

Admin uses the standard Django admin; no separate design.

## 6. MVP scope

Included: everything in sections 3 to 5 and the sections below.

Excluded: social features, TVDB, per-title notification settings, imports from
MAL, Trakt, AniList, Simkl and Kitsu, IMDb ratings, curated collection pages.

## 7. Notifications

Five types: episode aired (titles in Watching), first episode of a new season or
premiere (Planned), movie release date reached, release date announced, release
date changed.

Channels:

- On-site: always on.
- Email: admin-configured SMTP, per-user opt-in, address from `X-Discord-Email` or
  entered manually, one-click unsubscribe.
- Telegram: one bot, one-time `t.me/<bot>?start=<token>` link. A blocked bot
  disables the channel.

Mechanics: dedupe through a unique key, grouping per title per day, one `Delivery`
row per channel with retries. The first sync records a baseline only. A removed
date produces no notification. Providers give dates, not times.

As built for the on-site channel (plan 4a; the email channel is plan 4b below, Telegram
is plan 4c):

- A release is due at 09:00 in the user's time zone on its date, anime included
  (AniList's exact airing time is not stored). The zone is set in Settings.
- Kinds and recipients by library status:

  | Kind | Recipients | Fires when |
  |---|---|---|
  | episode aired | Watching | an episode became due |
  | premiere | Planned | episode 1 of a regular TV season became due |
  | movie released | Planned, Watching | a movie's release date became due |
  | release announced | Planned | the date went from empty to a future date |
  | release changed | Planned | the date moved and one of the two dates is in the future |

  Completed and Dropped entries receive nothing, and neither do episodes the user
  already watched. For release announced and release changed, "in the future" and
  "later than today" are judged against the UTC date at sync time; the scanner kinds
  are judged in the user's zone.
- A scanner runs every 15 minutes over a 48-hour look-back, so a stopped worker
  catches up. Only events later than the library entry's `added_at` and the profile's
  `notify_since` count, so adding a long-running show never floods the user.
- There is one row per user, kind, title and local day. A later event of the same
  key merges into the row and marks it unread again.
- The first sync of a title records a baseline only. Removed dates never notify. A
  date that moves and returns to its original value on the same day leaves no
  notification: the row is deleted.
- Read notifications are deleted after 90 days and all notifications after 180 days.
- The digest of one scan pass is decided and built for email in plan 4b (below);
  Telegram is plan 4c.

As built for the email channel (plan 4b; Telegram is plan 4c):

- Email is available only when the operator sets both `email.host` and `public_url`;
  otherwise the Email block is absent from Settings and nothing is sent. A host
  needs `email.from_address`.
- Opt-in in Settings, off by default. The address is the account address (from the
  portal's `X-Discord-Email` or the local account) or one the user enters. An entered
  address is held as pending and mailed a confirmation link (valid for 3 days; a GET
  only shows the page, a POST confirms); until then the previous address stays in use.
  "Use the portal address" drops the entered and the pending one.
- The sender runs every 15 minutes (minutes 2, 17, 32 and 47, after the scanner) and
  sends one digest mail per user per pass, in the user's interface language, with
  title names in the metadata language and at most 50 items, newest first, plus an
  "and N more" line.
- Nothing from before opting in is mailed: only notifications created after
  `email_since` count, and only those younger than 48 hours. A user without an
  address gets no delivery until one appears.
- Retries after a failed send: 15 minutes, 1 hour, 6 hours, 6 hours; the delivery is
  `failed` at the fifth failure. A pending delivery is `expired` once its notification
  is older than 48 hours, and `skipped` when the notification was already read on the
  site (expiry is checked first).
- Every mail carries `List-Unsubscribe` and `List-Unsubscribe-Post`
  (`List-Unsubscribe=One-Click`); the link is a signed token that never expires. A GET
  of the page only shows a confirmation, a POST switches email off and clears
  `email_since`, and neither needs a session. Unsubscribing also stops what is queued.
- A notification that merges a later event after it was mailed is not mailed again:
  its `Delivery` is already `sent` and the pair is unique.

## 8. Import and export

Import pipeline: upload, Huey job, format parser, normalised records, match
(external id first, then title and year; ambiguous and unmatched lists), write,
report kept for a few days. The uploaded file is deleted after processing.

Idempotency: event identity is user, episode and time. Existing data wins on
conflict, with an "overwrite" switch.

MVP formats: Yamtrack CSV (columns `media_id, source, media_type, title, image,
season_number, episode_number, score, status, notes, start_date, end_date,
progress, progressed_at`, rows ordered anime, tv, season, episode), IMDb CSV,
Letterboxd CSV (movies only). Movie rows from MyShows have an empty `media_id`,
so matching falls back to title and year.

Export: JSON backup and Yamtrack CSV. CSV cells starting with `=`, `+`, `-` or `@`
are escaped. The dialog lists what the chosen format loses.

## 9. Authentication and authorisation

Primary mode: nginx `auth_request` against `meow-elite-club-portal` (Discord
OAuth2 SSO gate). Trusted headers: `X-Discord-User-Id` (the key),
`X-Discord-Email`, `X-Discord-Avatar-Url`, `X-Discord-Access-Level`
(member or admin), `X-Service-Slug`. Headers are trusted only from nginx; the
container is reachable only on the internal Docker network.

Admin means `is_staff` plus the group `app-admins` with limited model permissions,
not `is_superuser`. A superuser exists only through a manual `createsuperuser`.
The role is recomputed at each login.

Fallback `local` mode (Django accounts) is switched by an environment variable.

## 10. Security

User-data queries are scoped through a model manager. CSRF protection covers HTMX
requests. Uploads have size and type limits and an import rate limit. A Telegram
webhook, if used, is protected by a secret.

## 11. Localisation

The UI ships in Russian and English on the Django i18n foundation. Metadata
language comes from the profile and is separate from the interface language.

## 12. Deployment

Docker image; shared Postgres on the VDS with a dedicated user and database; a new
Redis container on the internal network with a password; nginx and the Ansible
role from the `new-service-repo` and `new-nginx-service` skills.

## 13. Open items for the implementation plan

- "Mark all as watched" on the Collection page needs a bulk add; adding one title at
  a time cannot do it within the cap of 5 requests in progress.
- Whether a Simkl provider is added later together with Simkl login and sync.
- Browse shows a count of titles only for a list that has ended: the providers'
  totals are estimates (AniList caps at 5000, TMDB at 500 pages), so a list that
  can load more shows "Showing N".
- Opening a title nobody has added yet (`/open/<provider>/<id>/`) has no per-user cap,
  unlike the add flow, which caps pending requests at 5. Known follow-up.
- Portal mode: nginx must let `/email/` through without the SSO check. The one-click
  unsubscribe is posted by the recipient's mail provider, which has no session; the
  confirmation page lives under the same prefix.
- Telegram: webhook or polling.
- Subdomain or subpath.
- Verify the exact export format of each supported service.
