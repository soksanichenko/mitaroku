# Plan 4a: on-site notifications (design)

Part of the mitaroku requirements spec, section 7 (Notifications). Plan 4 is split in two:
this document covers 4a, the notification core and the on-site channel. Plan 4b (email,
Telegram, `Delivery`, digests) gets its own design after 4a is merged.

## 1. Intent

A user who tracks titles in mitaroku should learn on the site, without checking each
title, that an episode they follow has aired, that a planned title premieres or is
released, or that a release date was announced or moved. The user sets a time zone, and
every notification arrives at a sensible local moment.

Success: with a Watching series whose next episode airs today, the bell in the header
shows an unread count at 09:00 in the user's zone, the Notifications screen lists it, and nothing is ever notified twice.

## 2. Goals, non-goals, anti-goals

Goals:

- The five types of spec section 7 and the on-site channel (always on).
- A `timezone` on the user profile, chosen on Settings.
- Detection that works for the whole library, idempotent and safe to rerun.
- The bell with an unread count, and the Notifications screen of the design
  (`docs/design/Notifications.dc.html`): grouped by day, "Unread only", "Mark all as
  read", loading, empty and error states, Russian and English.

Non-goals (4b or later): email, Telegram, `Delivery` rows, digests, per-title or per-type
settings, a configurable notification hour, push or browser notifications, notifications
for removed dates or for date changes of single episodes.

Anti-goals: a notification twice for the same event; a flood after the deploy or after
adding a long-running show; notifying for events that happened before the user cared
(before the library entry or before the feature existed).

## 3. Approaches

Chosen by the user (variant 1):

1. Two producers writing one `Notification` table. A scanner finds what became due
   (episode aired, premiere, release reached) for each user in the user's zone; the title
   sync detects date changes (announced, changed) and writes rows straight away. Rows are
   one per user, title, kind and local day, so several episodes of one title merge.
2. A title-level event log fanned out to users. Rejected: the due moment depends on each
   user's zone, so the event is recomputed per user anyway; it only adds a layer.
3. Compute on read, store nothing. Rejected: unread state, the bell count and 4b's
   `Delivery` need stored rows.

Also decided with the user: no per-user scheduled rows queued ahead; one scan job asks
"what became due". A date or zone change therefore needs no rescheduling. Different
titles produce different rows (grouping is per title per day); 4b will combine the rows
of one pass into a digest for email and Telegram.

## 4. Behaviour

### Time and zone

- `Profile.timezone`: an IANA name, default `UTC`. An unknown or removed name falls back
  to UTC at use time.
- A release is due at **09:00 in the user's zone on its date**. The hour is a constant.
- Every release is due at that 09:00, anime included. Providers give dates, not times;
  AniList's exact `airingAt` is read only on the sync's fallback path (most anime take
  their episodes from TMDB), so 4a does not store it. An exact-time field is a possible
  later refinement, not part of this plan.
- A notification's `day` is its due moment's date in the user's zone (used for grouping
  and for the day headings on the screen).

### Types and recipients

| Kind | Recipients (library status) | Rule |
|------|-----------------------------|------|
| `episode_aired` | Watching | an episode of the title became due |
| `premiere` | Planned | the first episode of the title, or of a later season, became due |
| `movie_released` | Planned, Watching | a movie's `release_date` became due |
| `release_announced` | Planned | `Title.release_date` went from empty to a date |
| `release_changed` | Planned | `Title.release_date` moved from one date to another |

Calls I made, open to correction:

- `release_*` concern `Title.release_date` only (first air date for series). Moves of
  single episode dates are not notified: they are frequent and would be noise.
- A date correction in the past (both old and new date earlier than today) is not
  notified. A removed date never is.
- Regular episodes are not announced to Planned titles and premieres not to Watching
  titles; the Watching user gets `episode_aired` for every episode including the first
  of a new season.
- All anime parts with episodes (TV cours, ONA, OVA, specials) count as episodes.
- Completed and Dropped entries receive nothing.
- `premiere` counts only a TMDB season with number above 0 and, for anime, a part of
  kind TV, whose first episode became due. Movie, OVA, ONA and special parts and TMDB
  season 0 never produce a premiere.
- `release_announced` is written only when the new date is later than today (a date
  announced straight into the past is not news); a title that is already released sends
  `movie_released` or `episode_aired` through the scanner instead. `release_changed`
  needs the old or the new date to be later than today. For these two kinds "today" is
  the UTC date at sync time; the scanner kinds are judged in the user's zone.
- Episodes the user already has a `WatchEvent` for are skipped (an import must not
  produce "new episode" notifications for watched ones). One anti-join in the scan.
- `LibraryEntry.added_at` is the gate even after a Planned to Watching switch: an
  episode that aired within the 48 h window just before the switch may notify. That is
  acceptable and needs no extra field.

### Producing rows

- **Scanner** (`notify_due`, Huey periodic task every 15 minutes). Looks at due moments in
  a window `[now - 48 h, now]` and writes missing rows with `bulk_create(ignore_conflicts)`.
  The unique key makes reruns and overlapping windows harmless; the 48 h look-back
  survives a worker that was down for up to two days. An event counts only if its due
  moment is after both `LibraryEntry.added_at` and `Profile.notify_since` (set to the
  deploy time for existing profiles and to creation time for new ones), so nothing
  floods after the deploy or when a show is added.
- Changing the time zone does not touch `Profile.notify_since`; it only guards against
  events from before the feature or the profile existed. The dedupe key uses the release
  date, which no zone changes, so a zone change cannot repeat a notification. A move to
  the east can deliver an event that is already due in the new zone at the next scan;
  that is intended.
- Several episodes of the same title due on the same local day merge into one row: the
  later run appends them to the row's payload and marks it unread again.
- **Sync hook.** The title sync compares `Title.release_date` before and after writing. A
  first sync (no prior `synced_at`) records a baseline and notifies nobody. A repeated
  change on the same local day updates the existing row (`to` replaced, `from` kept).
- Rows are inserted per user through `bulk_create`, never one query per user.

### Model

`Notification` (in the new `notifications` app, scoped manager `for_user`, not in the
admin): `user`, `kind`, `title`, `day` (date in the user's zone), `payload` (JSON: episode
numbers and season for episode kinds, `from`/`to` dates for date kinds, no display
strings), `dedupe_key` (unique with `user`), `created_at`, `read_at` (null = unread).
Index on `(user, read_at, -created_at)`. Display text is built at render time in the
user's interface language and metadata language, so a language switch changes old rows.

### Screens

- **Bell** in the header: unread count (capped at "99+"), links to the screen. Rendered on
  each full page load; no live polling in 4a.
- **Notifications** (`/notifications/`): rows grouped by local day, each with kind label,
  title, a short text, and `from → to` for date kinds; an unread marker. "Unread only"
  (`?unread=1`), "Mark all as read" (POST), "Load more" paging in the style of Library,
  empty and error states, a skeleton loading state. Opening a row marks it read and goes
  to the title page; a single-row mark-read is also available without navigation.
- **Settings**: a time zone select. When the profile still holds the default `UTC` and
  the browser reports another zone, the select is preselected with the browser's zone
  and a hint says where it came from; nothing is saved until the user presses save. The
  "Notifications come with later plans" text is replaced: on-site is always on, and
  email and Telegram are shown as coming (no controls in 4a).

### Retention

A weekly Huey job deletes read notifications older than 90 days and any older than 180
days.

## 5. Constraints

- All queries on `Notification` go through `for_user`. Mutating endpoints are POST with
  CSRF and touch only the requester's rows (404 otherwise). Payload and titles render
  escaped.
- Query count per page and per scan is constant, not proportional to users or titles.
- New strings in Russian and English. Local-accounts mode behaves the same (the zone is
  on `Profile`).
- Work in the existing apps' style: Django 6.1, Huey, HTMX, Alpine, pytest; new app
  `notifications` as spec section 2 lists it.
- After implementation the spec is amended: section 3 (`Profile.timezone`,
  `Notification`) and section 7 (the rules above, 09:00, grouping).

## 6. Left to the builder

Names of functions and templates, the exact payload layout, the Notifications row
markup (follow the design file), how the zone list is presented (searchable select or
plain), the migration's data step (`notify_since` for existing profiles), how to freeze time in tests, and the exact
indexes beyond the one named.
