# Plan 4b: delivery core and the email channel (design)

Part of the mitaroku requirements spec, section 7 (Notifications). Plan 4 is split in
three: 4a (merged) built the notification core and the on-site channel; this document
covers 4b, the `Delivery` machinery and the email channel; plan 4c (Telegram) gets its own
design after 4b is merged.

## 1. Intent

A user who does not open the site every day still learns, by email, that an episode aired,
a planned title premieres or is released, or a release date moved. Mail arrives as one
message per pass, in the user's own language, and can be switched off from the message
itself.

Success: a user who enabled email and has a Watching series whose next episode airs today
gets one email shortly after 09:00 in their zone, listing that episode (and anything else
due in the same pass), in their interface language. Pressing "unsubscribe" in the mail
client turns the channel off. Nothing is mailed twice, and nothing is mailed to an address
the user has not confirmed.

## 2. Goals, non-goals, anti-goals

Goals:

- The `Delivery` table and the sender job, designed so plan 4c adds Telegram as one more
  channel without touching the producers of notifications.
- The email channel: admin-configured SMTP, per-user opt-in, the address from the SSO
  portal or entered manually (with confirmation), a digest per pass, retries,
  one-click unsubscribe.
- Mail, confirmation mail and the two token pages fully localised (Russian and English).

Non-goals (4c or later): Telegram, per-type or per-title settings, a configurable hour,
bounce or complaint processing, DKIM or SPF setup (the operator's SMTP does that),
user-selectable digest frequency, an admin screen for deliveries.

Anti-goals: an email twice for the same notification; mail for notifications that existed
before the user enabled the channel; mail to an address that is not confirmed by its owner
(or trusted from the portal); a flood of stale mail after a long outage; a language
different from the user's profile.

## 3. Approaches

Chosen by the user (variant C):

- A. The producers (scanner and sync hook) write a `Delivery` for every enabled channel
  next to each `Notification`. Rejected: both producers, just reviewed and merged, would
  learn about channels, every new channel edits them again, and enabling a channel later
  needs a backfill rule.
- B. A "sent up to" cursor on `Profile` per channel. Rejected: no per-item retries, one
  failing address blocks the cursor, and the spec asks for a `Delivery` row per channel.
- C. **A sender job creates the `Delivery` rows itself.** It finds notifications of users
  with the channel enabled that have no `Delivery` yet, creates the rows, and sends one
  digest per user. The producers do not change; the unique key on `(notification,
  channel)` makes re-runs harmless; a notification merged after it was mailed does not
  trigger a second mail (it is unread again on the site).

Also decided: Telegram is a separate plan (4c). It brings its own risks (bot token, the
linking flow, polling, blocked-bot handling), and the core plus email can be reviewed and
merged first.

## 4. Behaviour

### Settings and the address

- A new "Email" block under Settings > Notifications. It is shown only when the operator
  configured `email.host` and `public_url`; otherwise it is absent.
- A switch "Send me email". Turning it on sets `Profile.email_since` to the current
  moment, turning it off clears it, so nothing earlier is ever mailed.
- The address is `Profile.notify_email` when set, otherwise `User.email`. `User.email` is
  trusted: the SSO portal supplies it (`X-Discord-Email`, refreshed at each login), or the
  operator set it in local mode. The switch cannot be turned on while there is no address.
- A different address is entered in the form; it goes to `Profile.pending_email`, and a
  confirmation mail with a signed link is sent to it. The channel keeps using the previous
  address until the link is confirmed; the block shows "waiting for confirmation of
  <address>". Confirming moves the address to `notify_email`. "Use the portal address"
  clears `notify_email` and `pending_email`.
- The confirmation link has a 3-day life. Its page answers GET with a button and changes
  state only on POST, so a mail scanner that prefetches links cannot confirm an address on
  the owner's behalf.

### The message

- One mail per user per pass, in `Profile.interface_language`; titles in
  `Profile.metadata_language`, as on the Notifications screen.
- The subject is built from the item count only (no title text), localised. The body lists
  the items grouped by local day, with the kind label, the title (a link to its page), the
  short text and `from -> to` for date kinds, the same wording as the screen. A link to the
  Notifications screen and an unsubscribe link close it. At most 50 items are listed; a
  line "and N more" with the screen link covers the rest.
- Plain text plus a simple HTML alternative. All values are escaped. Links are absolute,
  built from `public_url`.
- The worker has no request, so each message is built under `translation.override` with
  the recipient's language. Confirmation mail and the two token pages use the same rule;
  the pages open without a session, so their language comes from the profile named in the
  token, not from the browser.

### Unsubscribe

- Every mail carries `List-Unsubscribe` (an https link) and `List-Unsubscribe-Post:
  List-Unsubscribe=One-Click`.
- The link holds a signed token naming the user, with no expiry (old mail must keep
  working). POST turns the channel off at once and needs no login and no CSRF token (the
  token is the proof). GET shows a page with the confirm button (to survive link
  prefetching) and a way back to Settings. The token can only switch email off.

### The sender job

`send_deliveries`, a Huey periodic task every 15 minutes at minutes 2, 17, 32, 47 (after
the scanner at 0, 15, 30, 45). One run:

1. Materialise: for users with email enabled and an address, notifications with
   `created_at` after `email_since`, not older than 48 h, without a `Delivery`, get a
   `Delivery(status=pending)` (`bulk_create` with `ignore_conflicts`).
2. Settle: a pending delivery whose notification is older than 48 h becomes `expired`; one
   whose notification is already read on the site becomes `skipped`.
3. Send: due pending deliveries (`next_attempt_at` empty or past) are grouped by user and
   one message per user is sent; its deliveries become `sent` with `sent_at`.
4. On an error: `attempts` grows and `next_attempt_at` moves by 15 min, 1 h, 6 h, 6 h; the
   fifth failure sets `failed`. `last_error` holds a short class-and-message text with no
   address or secret. A user's failure does not stop the other users.
5. Overlapping runs never send the same delivery twice; the locking method is the
   builder's, the requirement is fixed.

Query count is constant per run apart from one send and one update per user.

## 5. Model and configuration

`Delivery` (in `notifications`, scoped manager `for_user`, not in the admin): `user`,
`notification` (FK, cascade), `channel` (`email` in 4b; 4c adds `telegram`), `status`
(`pending`, `sent`, `failed`, `expired`, `skipped`), `attempts`, `next_attempt_at`,
`sent_at`, `last_error`, `created_at`. Unique `(notification, channel)`; an index for the
due lookup. Deliveries are deleted with their notifications by the existing retention job
(cascade).

`Profile` gains `email_enabled`, `email_since` (null), `notify_email`, `pending_email`.

Config (Pydantic, YAML with environment override): a new `email` block (`host`, `port`,
`user`, `password` as `SecretStr`, `starttls`, `from_address`) and a top-level
`public_url` (the base for links in mail). The Django mail backend is configured from
them; tests use the in-memory backend.

## 6. Constraints

- All `Delivery` reads go through `for_user` (the sender job is a system job and says so,
  like the retention job). State changes are POST; the token pages and the one-click
  endpoint are the only routes that work without a session, and they act only on the
  user named by a valid signed token.
- Subject and headers are built from fixed text and counts, never from title text. Body
  values are escaped. The recipient address and the SMTP password never reach the logs.
- New strings in Russian and English. Local-accounts mode behaves the same.
- Work in the existing apps' style: Django, Huey, HTMX, Alpine, pytest.
- After implementation the spec is amended: section 3 (`Delivery`, the new `Profile`
  fields) and section 7 (email as built); the README gains the email and `public_url`
  configuration and the Notifications text; the Settings text "Email and Telegram
  notifications are coming later" becomes "Telegram is coming later".

## 7. Left to the builder

Names of functions, templates and URLs; the mail template layout (follow the screen's
wording); how overlapping runs are excluded; how the signed tokens are built (Django's
`signing` is the expected tool); the exact index; how Settings presents the pending-address
state.
