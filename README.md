# mitaroku (見た録)

A self-hosted tracker for movies, TV series and anime. Tracking follows the
MyShows and Yamtrack model; notifications about new episodes and release dates,
and import/export from other trackers, are on top.

- Requirements and design: `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`
- UI mockup (open `docs/design/export/Mitaroku.html`): `docs/design/`
- Implementation plans: `docs/superpowers/plans/`

## Development

You need Python 3.12, Docker with Compose, and `gettext` (for the translations).
Run every command from the repository root: `config.yaml` is resolved relative to
the working directory.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
docker compose -f docker-compose.dev.yml up -d --wait   # PostgreSQL :55432, Redis :56379
cp config.example.yaml config.yaml                      # local mode, git-ignored
.venv/bin/python sources/manage.py migrate
.venv/bin/python sources/manage.py compilemessages
.venv/bin/python sources/manage.py createsuperuser
.venv/bin/python sources/manage.py runserver            # http://127.0.0.1:8000
.venv/bin/python sources/manage.py run_huey             # background worker, second terminal
```

Run the tests with `.venv/bin/python -m pytest`. They need the same PostgreSQL and
Redis containers.

## Configuration

Settings come from `config.yaml` (override the path with `MITAROKU_CONFIG`) and from
environment variables prefixed `MITAROKU_`, with `__` between nested keys, for example
`MITAROKU_DATABASE__PASSWORD`. The environment wins over the file. See
`config.example.yaml` for every key.

- `secret_key` and `database.password` have no defaults: set `MITAROKU_SECRET_KEY` and
  `MITAROKU_DATABASE__PASSWORD` (or the YAML keys).
- List-valued environment variables must be JSON, for example
  `MITAROKU_ALLOWED_HOSTS='["mitaroku.example.com"]'`.
- Behind a TLS proxy, `csrf_trusted_origins` must list the https origin (for example
  `['https://mitaroku.example.com']`), or every POST gets 403.
- Email (optional) uses `email.host`, `email.port` (587), `email.user`, `email.password`,
  `email.starttls` (true), `email.ssl` (false) and `email.from_address`, or
  `MITAROKU_EMAIL__HOST`, `MITAROKU_EMAIL__PORT`, `MITAROKU_EMAIL__USER`,
  `MITAROKU_EMAIL__PASSWORD`, `MITAROKU_EMAIL__STARTTLS`, `MITAROKU_EMAIL__SSL` and
  `MITAROKU_EMAIL__FROM_ADDRESS`. For a provider that offers only implicit TLS
  (port 465) set `ssl: true` and `port: 465`; `starttls` is then ignored, and setting
  both to true explicitly is rejected. `public_url`
  (`MITAROKU_PUBLIC_URL`, for example `https://mitaroku.example.com`) is the address
  used in the links inside the mail. Email is off unless both `email.host` and
  `public_url` are set, and a host needs `from_address`. The worker (`run_huey`) must be
  running for mail to go out. In portal mode nginx must let `/email/` through without
  the SSO check: the one-click unsubscribe is posted by the recipient's mail provider.
- Telegram (optional) uses `telegram.bot_token` and `telegram.bot_username` (without the
  `@`), or `MITAROKU_TELEGRAM__BOT_TOKEN` and `MITAROKU_TELEGRAM__BOT_USERNAME`. Set both
  or neither: one without the other is a startup error, and so is a token that is not
  `<digits>:<characters>` or a username that is not 5 to 32 letters, digits or
  underscores starting with a letter. The channel also needs `public_url`, for the links
  in the messages. Without all three the Telegram block is absent from Settings and
  nothing is polled or sent. The bot must be dedicated to this service (see the Telegram
  section). The worker (`run_huey`) and Redis must be running: the worker polls the bot
  and sends the messages. The poll is outbound HTTPS to `api.telegram.org` only, so no
  nginx change and no public endpoint are needed. The token is only in the request URL,
  and the log filter keeps the `httpx` request line out of the logs.
- `python sources/manage.py collectstatic` writes to `staticfiles/` (git-ignored).
  Serving `/static/` in production is deferred to plan 6.

## Authentication

- `auth.mode: portal` (production): nginx authenticates through the SSO portal and
  passes `X-Discord-User-Id`, `X-Discord-Email`, `X-Discord-Avatar-Url` and
  `X-Discord-Access-Level`. They are trusted only from addresses in
  `auth.trusted_proxies`, which defaults to loopback only (`127.0.0.1/32`, `::1/128`).
  List the exact address of nginx; a Docker-based nginx needs its container address or
  the bridge gateway listed.
- `auth.mode: local` (development, or when the portal is down): Django accounts and
  the `/login/` page.

An `admin` access level makes the user staff and a member of the `app-admins` group;
it never grants superuser rights. An admin is `is_staff` plus `app-admins`, with view
access to users and view/change access to profiles in `/admin/` (no permission to
change users, so nobody can promote themselves). Password login and `createsuperuser`
work only in local mode; in portal mode the portal header is the only login.

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

## Tracking and screens

Signed-in users have a library, a watch history and four screens: Library (continue
watching, filters, sort, grid and list), Upcoming (what your watching and planned titles
release next), Search and add, and the title pages (status, score, private note, episodes
you can check off). Settings has the interface and metadata languages.

- Adding a title is a background job: the search page shows "Adding..." and polls it, so
  `run_huey` must be running. A franchise that is not in the catalog yet is loaded from
  AniList, which takes tens of seconds for a long one. A failed add leaves nothing behind.
- Episodes count towards progress only in the main seasons; OVAs, movies and specials of
  an anime can be checked off but do not change progress, the next episode or the
  automatic "completed" status.
- The worker syncs titles that are in someone's library (watching and planned first) and
  titles never synced; a title nobody tracks is refreshed when someone opens its page.
  Translations are fetched lazily in the user's metadata language and cleaned up weekly.
- Posters are loaded from the provider's CDN by the browser.

## Notifications

The bell in the header and the Notifications screen (`/notifications/`) tell you when
something you follow changes. Email and Telegram can carry the same news. There are
five kinds: a new episode of a title you are watching, the premiere of a planned
series, the release of a movie you plan or watch, and a release date announced or
changed for a planned title. Completed and dropped titles are silent, and so are
episodes you have already checked off.

- A release is due at 09:00 in your time zone on its date, for anime too. Choose the
  time zone in Settings (default UTC). Changing it does not move any earlier cut-off: a
  release that is already due in the new zone shows up at the next scan, once.
- `run_huey` scans every 15 minutes, looking back 48 hours, so a stopped worker catches
  up without repeating anything. Releases from before you added the title are ignored.
- There is one notification per title, kind and day; a later event the same day updates
  it and marks it unread. A first sync of a title and removed dates notify nobody, and a
  date that moves and comes back the same day leaves nothing. A release date announced
  or changed counts only when it is later than today, judged against the UTC date at
  sync time (the other kinds are judged in your zone).
- Read notifications are deleted after 90 days and all of them after 180 days.
- Email and Telegram are optional second channels, described below.

### Email

Email is a second channel next to the site, available once the operator has configured
outgoing mail (see Configuration). The Email block in Settings appears only then.

- It is off until you switch it on in Settings. Only notifications created after that
  moment are mailed, so switching on never sends a backlog.
- The address is your account address (from the portal, or your local account). To use
  another one, enter it in Settings: it is held as pending and a confirmation link is
  mailed to it (valid for 3 days); the previous address stays in use until you confirm.
  "Use the portal address" goes back.
- The worker sends one digest per user every 15 minutes (minutes 2, 17, 32 and 47), in
  your interface language, with at most 50 items.
- A failed send is retried after 15 minutes, 1 hour, 6 hours and 6 hours; the fifth
  failure marks the delivery `failed`. A notification older than 48 hours is no longer
  mailed (`expired`), and one you have already read on the site is skipped.
- Every mail has a one-click unsubscribe (`List-Unsubscribe` and `List-Unsubscribe-Post`)
  and a link at the bottom. Unsubscribing switches email off and stops what is queued.
- A notification that is updated after it was mailed is not mailed again.
- If the mail server cannot be reached (connection refused, timeout, DNS, SMTP
  disconnect), the pass ends there: the other users wait for the next pass instead of
  failing one by one. A refused login or a refused address still fails only that mail.

### Telegram

Telegram is a third channel, available once the operator has configured a bot (see
Configuration). The Telegram block in Settings appears only then.

Setting up the bot (operator):

1. In Telegram, talk to @BotFather, send `/newbot` and follow the questions. Copy the
   token and the bot's username.
2. Put them in `telegram.bot_token` and `telegram.bot_username` (or the two
   `MITAROKU_TELEGRAM__` variables) next to `public_url`, and restart the web and
   worker processes.
3. Use a bot made for this service alone. No webhook may be set on it (while one is
   set, `getUpdates` answers 409), no other program may read its updates, and only one
   instance of the service may poll it. The log then says "another consumer or a
   webhook is set on this bot".

Connecting (user):

- Settings > Telegram > "Connect Telegram" shows an "Open in Telegram" link,
  `t.me/<bot>?start=<token>`. It works once and for 30 minutes. It is shown only in
  the response to the button: leave the page and the link is gone, press "Get a new
  link" for another one.
- Press Start in the private chat with the bot. The worker polls the bot once a
  minute, so the chat is connected within about a minute, and the Settings page moves
  on by itself a few seconds after that. The channel is then on and only notifications
  created from that moment are sent: nothing older, so there is no backlog.
- One chat belongs to one account; a chat that is connected to another account is
  refused. To move to another chat, press "Disconnect" and connect again.
- The switch in the block pauses and resumes the channel; "Disconnect" forgets the chat.
- The bot has no commands except `/start` with a link. A bare `/start` and any other
  message get a short reply that points to Settings and change nothing. Groups and
  channels are ignored. To stop, use the switch in Settings or block the bot.

What is sent:

- One message per user per 15-minute pass (minutes 2, 17, 32 and 47), in your
  interface language, with the titles in your metadata language, at most 50 items,
  newest first. A message is cut to Telegram's 4096 characters: what does not fit
  becomes an "and N more" line, and the last line links to the Notifications screen.
- The retries, the 48-hour expiry and the skip of what you have already read are the
  same as for email. A "too many requests" answer from Telegram is honoured: the next
  attempt waits for the time Telegram asks.
- If the bot is blocked or the chat is gone, the channel is switched off, the chat is
  forgotten and what is queued is marked `skipped`. It is noticed at the next message.
  The Settings block then says so, and "Connect Telegram" resumes it.
- If Telegram cannot be reached (connection refused, timeout, DNS), the pass ends for
  Telegram and the other users wait for the next pass. The same rule now holds for the
  mail server.

## Browse and title facts

Browse (`/browse/`) lists movies, TV series and anime by type, genre, country and year.
It is not a bottom-bar tab: it opens from "See also", from the genre and country tags
on a title page and from the link under the idle Search page. The filters live in the
query string, so a filtered list can be bookmarked:

- `type`: `movie`, `tv` or `anime`; `genre`: a genre slug; `country`: one of 30
  curated two-letter origin country codes; `year`: 1950 to next year; `sort`:
  `popularity` (default), `date`, `title` or `rating`; `page`: 1 to 200 (a larger
  number is clamped to 200). A value that is not valid is ignored.
- Movies and TV series come from TMDB, anime from AniList, interleaved. Browse needs a
  TMDB token; without one the movies and series are missing and the page says so, and
  anime still work. AniList can only filter anime by JP, KR, CN and TW and by its own
  genres, so a country or genre outside those leaves the anime out; likewise a genre
  without a TMDB equivalent (Sports, Mecha, ...) leaves the movies and series out.
  Japanese animation found by TMDB is dropped, because anime come from AniList.
- The providers' totals are estimates (AniList caps at 5000, TMDB at 500 pages), so
  Browse shows a count of titles only for a list that has ended and "Showing N" while
  there is more to load.
- Pages and collections are cached by the provider layer: a Browse page for 3 hours, a
  TMDB movie collection for 7 days and the AniList details of a title for 24 hours.
  When a provider is down the last copy is served.
- The title page shows the genres, countries, cast, trailers, the movie collection it
  belongs to (`/collections/<tmdb id>/`) and similar titles. These facts arrive with
  the normal sync, so they cost no extra TMDB request and one AniList request per anime.
- A card of a title nobody has added yet links to `/open/<provider>/<id>/`. It queues
  a background job that creates the title in the catalog (no library entry) and syncs
  it, so `run_huey` must be running. The page polls every 2 seconds and replaces itself
  with the title page once the title is synced (a visitor without JavaScript gets a
  page that reloads every 2 seconds). The job holds a claim on the id for 10 minutes,
  and when the claim is gone without a title the page shows "Try again".

## License

[MIT](https://opensource.org/licenses/MIT)
