# Plan 4c: the Telegram channel (design)

Part of the mitaroku requirements spec, section 7 (Notifications). Plan 4a built the
notification core and the on-site channel, plan 4b the `Delivery` machinery and email;
this document covers 4c, Telegram as the second delivery channel.

## 1. Intent

A user who lives in Telegram rather than in a mailbox still learns that an episode aired,
a planned title premieres or is released, or a release date moved. The operator sets up
one bot when deploying the service; each user connects their own Telegram account to
their mitaroku account in two taps and from then on gets their own notifications, about
the titles in their own library, from that bot.

Success: a user who connected Telegram and has a Watching series whose next episode airs
today gets one Telegram message shortly after 09:00 in their zone, listing that episode
(and anything else due in the same pass), in their interface language. Blocking the bot
switches the channel off. Nothing is sent twice, and nothing reaches a chat that the owner
of the mitaroku account did not connect.

## 2. Goals, non-goals, anti-goals

Goals:

- Telegram as a second value of `Delivery.channel`, with the sender shared between email
  and Telegram so that a further channel is one more channel and not a rewrite.
- Operator setup by configuration only: the bot token and the bot name.
- Per-user connection from Settings, a switch, disconnect, and the "bot blocked" state.
- Messages and the bot's replies in Russian and English, in the user's own language.

Non-goals (later, or never): bot commands beyond `/start` (stopping is the Settings
switch or blocking the bot), notification settings inside the bot, groups and channels,
inline keyboards, a webhook, one bot shared by several installations, per-type or
per-title settings, a configurable hour, interactive replies to the user's messages.

Anti-goals: connecting a chat without an action by the mitaroku account owner (a bare
`/start`, or a chat id typed or guessed by someone); a message twice for the same
notification; mail-bomb-like floods after an outage; the bot token in any log, error text
or page; a language different from the user's profile.

## 3. Approaches

Chosen by the user: a **centralised bot** set up at deploy, which each user starts to get
their own notifications. Telegram sends only a chat id and a name on `/start`, and neither
says which mitaroku account is meant, so the bot link carries a one-time token issued to a
signed-in user. This is the same as the spec's "one bot, one-time
`t.me/<bot>?start=<token>` link".

How the bot hears the user (decided with the user): the service polls Telegram's
`getUpdates` from a Huey periodic task every minute.

- Polling: no public endpoint, so no second exemption in the nginx SSO check next to
  `/email/`; a link is confirmed within a minute. Chosen.
- Webhook: instant, but needs a public route past the SSO and a secret check. Rejected.
- A long-polling process: instant and no webhook, but one more container and one more
  process to keep alive. Rejected for now; it can replace the periodic task later without
  changing the rest.

Also decided: the sender from 4b is made channel-generic here (it was built for email
only), and Telegram is added to it. The notification producers and the `Delivery` table do
not change.

## 4. Behaviour

### Operator setup

- Config block `telegram` with `bot_token` (a `SecretStr`) and `bot_username` (the name
  the `t.me` link needs; the operator knows it, so no network call is needed to find it).
  Env names `MITAROKU_TELEGRAM__BOT_TOKEN` and `MITAROKU_TELEGRAM__BOT_USERNAME`. One of the
  two without the other is a config error; both empty means the channel is off.
- The channel is available only when the token, the username and `public_url` are all
  set (the links in messages are absolute, like in mail); otherwise the Telegram block is
  absent from Settings and the poll and the sender do nothing.
- The bot must be dedicated to this service: a second consumer of `getUpdates`, or a
  webhook set on the bot, makes Telegram refuse the poll. The README says so.

### Connecting

- Settings > Notifications gets a "Telegram" block next to "Email". It has five states,
  checked in this order: connected and paused (a chat, the switch on or off, with a
  "Disconnect" button), waiting for Start (a live link token, no chat), stopped because
  the bot was blocked, and not connected.
- "Connect Telegram" is a POST. On an account that already has a chat it does nothing.
  Otherwise it issues a one-time token for that user (a new token replaces an
  older one, 30 minutes of life) and shows an "Open in Telegram" link
  `https://t.me/<bot_username>?start=<token>` in that response only (a reload hides it;
  "Get a new link" issues another). The token fits Telegram's `start` payload
  (letters, digits, `_`, `-`, up to 64 characters). While the state is "waiting", the block
  asks a status URL every 3 seconds (HTMX); the answer is 204 until a chat exists, then
  200 with `HX-Redirect` to Settings, so a reload never re-sends the POST.
- The poll reads `/start <token>` from a private chat. A valid token connects that chat
  to the token's user, switches the channel on (`telegram_since` is set to that moment,
  so nothing earlier is ever sent) and the bot answers "Connected. New mitaroku
  notifications will arrive in this chat." in the user's interface language. The token
  is used up.
- A `/start` without a token, an unknown, used or expired token, and every other message
  get a short reply that says to connect from mitaroku Settings (in Russian when the
  Telegram `language_code` is `ru`, else English). They change nothing. Groups, channels
  and other chat types are ignored.
- One chat belongs to one account: a chat that is already connected to another account is
  refused, with a reply saying to disconnect it there first. An account that has a chat
  moves to another one by "Disconnect" and a new connect.
- The switch off clears `telegram_since`, on sets it, like email. "Disconnect" forgets the
  chat and switches the channel off.

### The message

- One message per user per pass, in `Profile.interface_language`; titles in `Profile.metadata_language`, as on the
  Notifications screen and in the mail.
- Telegram HTML: items grouped by local day, each a line with the kind label, the title as
  a link to its page, and the short text (and `from -> to` for date kinds), the same words
  as the screen. The last line links to the Notifications screen. Every value is escaped
  for Telegram's HTML; link previews are off.
- Telegram's limit is 4096 characters per message: the list is cut at the last item that
  fits, and the rest becomes a line "and N more" with the screen link. The cap of 50 items
  of the digest still holds.
- There is no unsubscribe link: the user stops it in Settings or by blocking the bot.
- The message is built under `translation.override` with the recipient's language, as the
  mail is.

### Sending, retries and blocking

- The shared sender runs for each channel in the same pass, in order (email, then
  Telegram). For Telegram: materialise rows for users with the channel on and a chat,
  settle (expire first, then skip), claim and send one message per user, with the same
  retry pauses, expiry, savepoint and per-user locking as email. The channel's own
  "enabled", "since" and "destination" fields replace the email ones.
- A permanent refusal (the bot was blocked or kicked, the chat is gone, the user is
  deactivated: Telegram's 403, and 400 "chat not found") switches the channel off for that
  user at once, forgets the chat, marks the queued deliveries `skipped` with the reason,
  and the block shows the "stopped, the bot was blocked" state. No retry.
- A 429 answer is honoured: the next attempt is not earlier than its `retry_after`. Other
  errors follow the usual pauses and become `failed` at the fifth.
- A connection-level failure (timeout, refused, DNS) ends the Telegram pass at once instead
  of going through every user, because the next user will fail the same way and the single
  worker must not stall for 10 s per user. The same stop applies to email's connection
  errors (this closes a deferred item of 4b).
- The bot token appears only in the URL of the request; it never reaches a log line,
  `Delivery.last_error` or a page. Exception text is scrubbed of it before use, like the
  address in 4b.

### Polling

- The periodic task `poll_telegram` runs every minute and does nothing when the channel is
  off. It asks `getUpdates` with an offset and a short timeout and handles each update;
  the offset is stored so that an update is handled once, and handling is idempotent
  (a used token cannot connect twice) so a lost offset does no harm. Overlapping runs
  must not handle the same update twice.
- An error from Telegram is logged without the token and the next minute retries; a
  failed poll never changes any user's state.

## 5. Model and configuration

`Delivery.Channel` gains `telegram`. `Profile` gains `telegram_enabled`, `telegram_since`
(null), `telegram_chat_id` (a big integer, null, unique) and what the one-time token needs
(its value or hash and its expiry; the builder chooses where it lives), plus a way to show
"stopped because the bot was blocked". The poll's offset is kept in Redis, with a lock that
keeps runs from overlapping.

Config (Pydantic, YAML with environment override): a `telegram` block as above. HTTP calls
to Telegram go through the project's existing HTTP approach, with a timeout.

## 6. Constraints

- All `Delivery` reads go through `for_user` (the sender and the poll are system jobs and
  say so). State changes are POST. The poll is the only entry from Telegram, and it acts
  only on a valid token.
- Message text is built from fixed text, counts, escaped values and links built from
  `public_url` and signed or random tokens, never from raw title text in markup.
- New strings in Russian and English, including the bot's replies. Local-accounts mode
  behaves the same.
- Work in the existing apps' style: Django, Huey, HTMX, Alpine, pytest. Tests never reach
  the network (a fake for the Telegram API).
- After implementation the spec is amended: section 3 (the new `Profile` fields), section 7
  (Telegram as built) and section 13 (the polling decision); the README gains the Telegram
  configuration, the dedicated-bot note and the Notifications text; the Settings text
  "Telegram notifications are coming later" is replaced by the block.

## 7. Left to the builder

The shape of the channel abstraction in the sender; names of functions, templates and
URLs; where the one-time token and the poll offset are stored; how overlapping polls are
excluded; how Settings words and lays out the four states; how the message is cut to the
limit; which HTTP client calls the Bot API.

## 8. Verification

Besides tests, a live pass on a real bot: the operator (the user) provides a test bot
token outside the repo, and it is never printed or logged. The pass covers connecting
from Settings, the digest message, a blocked bot, and a Telegram outage.
