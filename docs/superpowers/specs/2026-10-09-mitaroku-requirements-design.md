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

Modular monolith (Django LTS, PostgreSQL, HTMX + Alpine + Tailwind), two
processes: web (gunicorn) and worker (Huey on Redis).

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
- `Profile`: interface language, metadata language, notification preferences.

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
loading, empty and error states. Touch targets are at least 44 px.

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

- Plan 3b: Browse, Collection and the title-page enrichment (genres, country, cast,
  trailers, similar titles, "See also" links) need new provider data.
- Whether a Simkl provider is added later together with Simkl login and sync.
- Telegram: webhook or polling.
- Subdomain or subpath.
- Verify the exact export format of each supported service.
