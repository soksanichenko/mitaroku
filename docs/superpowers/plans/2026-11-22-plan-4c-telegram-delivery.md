# Plan 4c: the Telegram channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Telegram becomes the second delivery channel: one operator-configured bot, per-user connection from Settings through a one-time `/start` link, one digest message per user per pass, and a blocked bot switches the channel off.

**Architecture:** The 4b sender is made channel-generic (a `ChannelSpec` per channel, a shared engine in `deliver.py`) and Telegram is added as the second spec. The bot API is a small `httpx` wrapper; updates arrive by a once-a-minute Huey poll of `getUpdates`, which only handles `/start <token>`. Producers of notifications and the `Delivery` table are untouched.

**Tech Stack:** Django 6.1, PostgreSQL 17, Redis 7 (poll offset and lock), Huey, httpx, HTMX, pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-plan-4c-telegram-design.md` (read it first; sections 4 and 6 bind every task). Built on `docs/superpowers/specs/2026-11-22-plan-4b-email-delivery-design.md` and the code of plan 4b in `sources/notifications/`.

## Global Constraints

- Django 6.1 latest, Python as in `pyproject.toml`. Single quotes, Google-style docstrings on public functions, type annotations, line length 88, comments say why. Logging with %-formatting.
- Config: Pydantic block `telegram` (`bot_token`: `SecretStr`, `bot_username`), env `MITAROKU_TELEGRAM__BOT_TOKEN` and `MITAROKU_TELEGRAM__BOT_USERNAME`; the channel is on only with token, username and `public_url` all set.
- Reuse the 4b rules unchanged: retry pauses 15 min, 1 h, 6 h, 6 h; `failed` at the 5th failure; `EXPIRY` 48 h; at most 50 items per digest; one message per user per pass; per-user `transaction.atomic()` with `select_for_update(skip_locked=True, of=('self',))`.
- Telegram message limit 4096 characters, `parse_mode` HTML, link previews off. Link token: one-time, 30 minutes, only its SHA-256 stored, characters `[A-Za-z0-9_-]`, at most 64.
- The bot token appears only in the request URL. It never reaches a log line, `Delivery.last_error`, an exception message that is stored or shown, or a page.
- All `Delivery` reads go through `for_user`; the sender and the poll are system jobs and say so. State changes are POST. Tests never reach the network.
- New strings in Russian and English (`sources/locale/ru/LC_MESSAGES/django.po`: add the pairs; `makemessages -l ru` is fine if the diff is additions only; `compilemessages` runs from `tests/conftest.py`).
- Run everything from the repo root with `source .venv/bin/activate`. Commits are GPG-signed through a pinentry on the user's screen: wait, never retry. Every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push. Never touch the dev database `mitaroku` (pytest uses its own). Untracked files not to commit: `SESSION.md`, `my-shows-*.png`, `Mitaroku media tracker design.zip`.

## Review Focus

Failure modes the spec implies; each has a test in the task named.

1. A `/start <token>` replayed by another Telegram user after the token was used (or after it expired) must not connect that chat (Task 5, `test_a_used_token_cannot_connect_a_second_chat`).
2. A message from a group, supergroup or channel that contains `/start <valid token>` must be ignored: no connection, no reply, the token stays valid (Task 5).
3. Telegram answers that are not a clean success (HTTP 200 with `ok: false`, an empty or non-JSON body, a missing `result`) must count as failures that are retried, never as "sent" (Task 3 and Task 4).
4. Titles with `<`, `&`, quotes or 200 characters, and fifty of them at once, must give valid, escaped HTML of at most 4096 characters with the "and N more" line (Task 4).
5. `getUpdates` refused with 409 (a webhook is set, or a second consumer polls) must be logged without the token and change nothing, and a lost poll offset must replay harmlessly (Task 6).

---

## File Structure

- `sources/mitaroku/config.py`, `settings.py`: `TelegramConfig`, `TELEGRAM_*` settings.
- `sources/accounts/models.py` + migration: the `Profile` Telegram fields. `sources/notifications/models.py` + migration: `Channel.TELEGRAM`.
- `sources/notifications/errors.py` (new): `PermanentFailure`, `RetryAfter`, `Unreachable`.
- `sources/notifications/channels.py` (new): `ChannelSpec`, `EMAIL`, `TELEGRAM` and their send/disable functions.
- `sources/notifications/deliver.py`: the shared engine, looping over `CHANNELS`.
- `sources/notifications/telegram.py` (new): the Bot API wrapper. `telegram_message.py` (new): the digest as Telegram HTML. `telegram_link.py` (new): one-time tokens and connecting. `telegram_bot.py` (new): handling one update. `telegram_poll.py` (new): the poll.
- `sources/notifications/digest.py`: `layout()` extracted and shared. `tasks.py`: `poll_telegram_task`.
- `sources/web/views/telegram.py` (new), `urls.py`, `views/settings.py`, templates `settings.html` and `_telegram_block.html` (new): the Settings block.
- Tests: new `tests/fake_telegram.py`, `tests/notification_helpers.py`, `test_telegram_*.py`, `test_deliver_telegram.py`, `test_web_settings_telegram.py`; extended `test_config.py`, `test_deliver.py`, `test_notification_tasks.py`, `test_notification_digest.py`.

---

### Task 1: Configuration, the channel value and the Profile fields

**Files:**
- Modify: `sources/mitaroku/config.py`, `sources/mitaroku/settings.py`, `sources/notifications/channel.py`, `sources/notifications/models.py`, `sources/accounts/models.py`
- Create: migrations `sources/accounts/migrations/0006_profile_telegram.py`, `sources/notifications/migrations/0003_alter_delivery_channel.py` (generated)
- Test: `tests/test_config.py`, `tests/test_telegram_channel.py` (new), `tests/test_delivery_models.py`

**Interfaces:**
- Produces:
  - `config.TelegramConfig(bot_token: SecretStr | None = None, bot_username: str = '')`; `Settings.telegram: TelegramConfig`.
  - Django settings `TELEGRAM_BOT_TOKEN: str` (empty when unset), `TELEGRAM_BOT_USERNAME: str`, `TELEGRAM_ENABLED: bool`.
  - `notifications.channel.telegram_configured() -> bool` (reads `settings.TELEGRAM_ENABLED`).
  - `Delivery.Channel.TELEGRAM = 'telegram'`.
  - `Profile`: `telegram_enabled` (bool, default False), `telegram_since` (datetime, null), `telegram_chat_id` (BigInteger, null, blank, **unique**), `telegram_link_hash` (CharField 64, blank, `db_index=True`), `telegram_link_expires` (datetime, null), `telegram_blocked` (bool, default False).

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`, after the email tests, in the file's style (`monkeypatch.setenv`, `Settings()`):

```python
def test_the_telegram_block_is_off_by_default():
    telegram = Settings().telegram
    assert telegram.bot_token is None
    assert telegram.bot_username == ''

def test_a_token_without_a_username_is_rejected_and_not_echoed(monkeypatch):
    monkeypatch.setenv('MITAROKU_TELEGRAM__BOT_TOKEN', '123456:abc-DEF_ghi')
    with pytest.raises(ValidationError) as error:
        Settings()
    assert 'bot_username' in str(error.value)
    assert 'abc-DEF_ghi' not in str(error.value)

def test_a_username_without_a_token_is_rejected(monkeypatch): ...  # mentions 'bot_token'

def test_a_complete_block_is_accepted_and_the_username_loses_its_at_sign(monkeypatch):
    # token '123456:abc-DEF_ghi', username '@mitaroku_bot'
    assert Settings().telegram.bot_username == 'mitaroku_bot'
    assert Settings().telegram.bot_token.get_secret_value() == '123456:abc-DEF_ghi'

@pytest.mark.parametrize('value', ['abc', 'has space', '1abc_bot', 'x' * 33])
def test_a_malformed_username_is_rejected(monkeypatch, value): ...

@pytest.mark.parametrize('value', ['nocolon', 'abc:def', '12:'])
def test_a_malformed_token_is_rejected_without_echoing_it(monkeypatch, value): ...

@pytest.mark.parametrize('value', ['', '   '])
def test_a_blank_token_is_none(monkeypatch, value): ...  # with a blank username too

def test_the_token_is_not_in_the_repr(monkeypatch): ...
```

`tests/test_telegram_channel.py` (follow the `EMAIL_ENABLED` tests in `tests/test_email_channel.py`, including how that file re-imports `mitaroku.settings` under a patched environment):

```python
@pytest.mark.parametrize('enabled', [True, False])
def test_telegram_configured_follows_the_setting(enabled):
    with override_settings(TELEGRAM_ENABLED=enabled):
        assert telegram_configured() is enabled

def test_the_default_test_settings_leave_telegram_disabled(): ...

@pytest.mark.parametrize(('token', 'username', 'public_url', 'enabled'), [
    ('123456:abc', 'mitaroku_bot', 'https://m.example', True),
    ('123456:abc', 'mitaroku_bot', '', False),
    ('', '', 'https://m.example', False),
])
def test_the_setting_needs_token_username_and_public_url(...): ...  # also TELEGRAM_BOT_TOKEN is the plain string
```

In `tests/test_delivery_models.py` / `test_telegram_channel.py` (db tests):

```python
def test_profile_telegram_defaults(alice): ...  # False, None, None, '', None, False
def test_two_profiles_cannot_share_a_chat(alice, bob):  # IntegrityError on the second save
def test_many_profiles_may_have_no_chat(alice, bob): ...  # both None saves fine
def test_one_notification_can_have_a_delivery_per_channel(alice):  # email and telegram rows coexist; a second telegram row for it is an IntegrityError
```

- [ ] **Step 2: Run to verify failure**

Run: `source .venv/bin/activate && pytest tests/test_config.py tests/test_telegram_channel.py tests/test_delivery_models.py -q`
Expected: FAIL (no `telegram` attribute, no `telegram_configured`, no fields).

- [ ] **Step 3: Implement**

1. `config.py`: `class TelegramConfig(BaseModel)` with the two fields. A `mode='before'` validator turns a blank token into `None` (like `EmailConfig.password`); a `model_validator(mode='after')` raises `ValueError('telegram.bot_username is required when telegram.bot_token is set')` and the mirror message; a `field_validator('bot_username')` strips whitespace and one leading `@` and requires `^[A-Za-z][A-Za-z0-9_]{4,31}$` (empty allowed); the token must match `^\d+:[\w-]+$` (checked in the after-validator so the message never contains the value). Add `telegram: TelegramConfig = TelegramConfig()` to `Settings`.
2. `settings.py`: `TELEGRAM_BOT_TOKEN = config.telegram.bot_token.get_secret_value() if config.telegram.bot_token else ''`, `TELEGRAM_BOT_USERNAME = config.telegram.bot_username`, `TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME and config.public_url)`.
3. `channel.py`: `telegram_configured() -> bool`.
4. `Delivery.Channel.TELEGRAM = 'telegram'`; the six `Profile` fields.
5. `python sources/manage.py makemigrations accounts notifications`; rename the generated files to the names above if Django chose others, and check each holds only the expected operations.

- [ ] **Step 4: Run to verify pass, and the migrations are complete**

Run: `pytest tests/test_config.py tests/test_telegram_channel.py tests/test_delivery_models.py -q` then `python sources/manage.py makemigrations --check --dry-run`
Expected: PASS; "No changes detected".

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the Telegram config block, the channel value and the Profile fields

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: A channel-generic sender

**Files:**
- Create: `sources/notifications/errors.py`, `sources/notifications/channels.py`
- Modify: `sources/notifications/deliver.py`, `tests/test_deliver.py`
- Test: `tests/test_deliver.py`

**Interfaces:**
- Consumes: Task 1 (`Delivery.Channel`, the Profile fields exist; this task uses the email ones only).
- Produces:
  - `errors.PermanentFailure(Exception)`; `errors.RetryAfter(Exception)` with `__init__(self, seconds: float)` and `.seconds`; `errors.Unreachable(Exception)`.
  - `channels.ChannelSpec` (frozen dataclass): `channel: Delivery.Channel`, `configured: Callable[[], bool]`, `enabled_field: str`, `since_field: str` (Profile field names), `destination: Callable[[User], str]` (empty when there is nowhere to send), `send: Callable[[User, list[Notification], datetime], None]`, `disable: Callable[[User], None]`, `secret: Callable[[User], str]` (the text scrubbed from `last_error`).
  - `channels.EMAIL: ChannelSpec`; `channels.send_email(user, notifications, now) -> None` (builds with `build_digest`, sends, maps connection-level errors to `Unreachable`); `channels.disable_email(user) -> None`.
  - `deliver.CHANNELS: tuple[ChannelSpec, ...] = (EMAIL,)`; `send_deliveries(now) -> int` keeps its signature and returns the messages sent over all configured channels.
  - Engine internals take the spec: `_materialise(now, spec)`, `_settle(now, spec)`, `_due(now, spec)`, `_send_to_user(user_id, now, spec)`; `_error_text(exc, secret)` is unchanged.

- [ ] **Step 1: Write the failing tests**

Edit the existing `tests/test_deliver.py` mechanically: import `channels`; patch `channels.build_digest` instead of `deliver.build_digest` in `test_overlapping_runs_never_send_twice` and its sibling; in `test_materialise_and_settle_cost_the_same_for_one_user_and_four` call `deliver._materialise(NOW, channels.EMAIL)` and `deliver._settle(NOW, channels.EMAIL)` (change `count(step)` to take a no-argument callable). Everything else stays as it is and must stay green. Add:

```python
def test_a_connection_failure_stops_the_pass_for_the_other_users(alice, bob, failing_send):
    enable(alice); enable(bob); notify(alice); notify(bob)
    failing_send.failing = {'1001@example.com'}
    failing_send.error = staticmethod(lambda address: ConnectionRefusedError('refused'))

    assert send_deliveries(NOW) == 0

    assert failing_send.calls_made == [['1001@example.com']]
    (first,) = deliveries(alice)
    assert (first.status, first.attempts) == (Status.PENDING, 1)
    assert first.next_attempt_at == NOW + RETRY_PAUSES[0]
    assert first.last_error.startswith('Unreachable: ConnectionRefusedError')
    (second,) = deliveries(bob)
    assert (second.status, second.attempts, second.next_attempt_at) == (Status.PENDING, 0, None)

def test_a_server_that_disconnects_counts_as_unreachable(...)   # smtplib.SMTPServerDisconnected, same stop
def test_an_authentication_error_does_not_stop_the_pass(...)    # smtplib.SMTPAuthenticationError: bob is still tried

@pytest.mark.parametrize(('seconds', 'expected'), [(7200, 2 * HOUR), (60, RETRY_PAUSES[0])])
def test_retry_after_pushes_the_next_attempt_out_but_never_in(..., seconds, expected):
    # failing_send.error raises RetryAfter(seconds); next_attempt_at == NOW + expected

def test_a_permanent_failure_skips_the_queue_and_switches_the_channel_off(alice, failing_send):
    # two notifications; error -> PermanentFailure('blocked')
    # both deliveries SKIPPED, last_error == 'PermanentFailure: blocked'
    # profile.email_enabled False and email_since None
    # a run 16 minutes later sends nothing: len(failing_send.calls_made) == 1

def test_the_email_pass_leaves_other_channels_alone(alice):
    # a pending Delivery(channel=TELEGRAM) for a notification already read is not settled to SKIPPED by send_deliveries(NOW)

def test_an_unconfigured_channel_is_skipped(monkeypatch):
    monkeypatch.setattr(deliver, 'CHANNELS', (dataclasses.replace(channels.EMAIL, configured=lambda: False),))
    # enabled user with a notification: send_deliveries(NOW) == 0, mail.outbox == [], no Delivery rows

def test_disable_email_switches_email_off(alice): ...
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_deliver.py -q`
Expected: FAIL (no `channels`, `errors`; signatures differ).

- [ ] **Step 3: Implement**

1. `errors.py` and `channels.py` as in Interfaces. `EMAIL`: `channel=Delivery.Channel.EMAIL`, `configured=email_configured`, fields `email_enabled`/`email_since`, `destination=effective_address`, `secret=effective_address`. `send_email` wraps `build_digest(user, notifications, now).send()`; `smtplib.SMTPServerDisconnected`, `smtplib.SMTPConnectError`, `ConnectionError`, `TimeoutError` and `socket.gaierror` are re-raised as `Unreachable(f'{type(exc).__name__}: {exc}') from exc`; nothing else is caught (note that `smtplib.SMTPException` subclasses `OSError`, so do not catch `OSError` wholesale). `disable_email` does what the unsubscribe view does (`email_enabled=False, email_since=None` through `Profile.objects.filter(user=user).update`).
2. `deliver.py`: replace the `EMAIL`/`PENDING` module constants and the hard-wired fields by the spec; filters use `f'user__profile__{spec.enabled_field}'` and `F(f'user__profile__{spec.since_field}')`; `_materialise` keeps a row only when `spec.destination(n.user)` is non-empty; `_settle`/`_due` filter `channel=spec.channel`. `_send_to_user` returns a small enum `Result` (`NOTHING`, `SENT`, `FAILED`, `UNREACHABLE`) and:
   - catches `PermanentFailure` first: in the same transaction every pending delivery of that user and channel becomes `skipped` with `last_error` (scrubbed `_error_text`), `spec.disable(user)` runs, no attempt is counted, result `FAILED`, logged with `logger.error` (no traceback);
   - catches `RetryAfter`: `_record_failure(..., minimum=timedelta(seconds=exc.seconds))` so the next attempt is `now + max(pause, minimum)`;
   - catches `Unreachable` like any error (failure recorded, attempt counted) but returns `UNREACHABLE`;
   - any other exception as before.
   `send_deliveries(now)` loops `for spec in CHANNELS`, skips an unconfigured one, runs materialise, settle, the user loop, and **breaks the channel's user loop on `UNREACHABLE`** (the other users stay untouched and pending); it returns the total of `SENT`.
3. Log messages say the channel (`'%s delivery failed for user %s: %s'`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_deliver.py tests/test_notification_tasks.py -q`
Expected: PASS (the 26 earlier tests plus the new ones).

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "refactor: make the delivery sender channel-generic and stop a pass on connection errors

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The Bot API wrapper and the test fake

**Files:**
- Create: `sources/notifications/telegram.py`, `tests/fake_telegram.py`, `tests/test_telegram_api.py`
- Modify: `tests/conftest.py` (the `bot` fixture)

**Interfaces:**
- Consumes: Task 1 settings; Task 2 `errors`.
- Produces:
  - `telegram.TelegramError(Exception)` with `.code: int | None`.
  - `telegram.BotApi(token: str, client: httpx.Client)` with `send_message(chat_id: int, text: str) -> None` (sends `{'chat_id', 'text', 'parse_mode': 'HTML', 'link_preview_options': {'is_disabled': True}}`) and `get_updates(offset: int | None, timeout: int = 0) -> list[dict]`.
  - `telegram.make_client() -> httpx.Client` (timeout 10 s) and `telegram.bot_api() -> BotApi` (built from `settings.TELEGRAM_BOT_TOKEN` and `make_client()`).
  - Error mapping: `PermanentFailure` for 403 and for 400 with `chat not found` in the description; `RetryAfter(parameters.retry_after)` for 429; `Unreachable` for any `httpx.TransportError` (message is the exception class name only); `TelegramError(code=...)` for everything else, including HTTP 200 without `ok: true`, a non-JSON body, and a missing `result` for `getUpdates`.
  - `tests/fake_telegram.py`: `FakeBot(token='123456:TEST-token')` with `.sent: list[dict]` (sendMessage payloads), `.calls: list[tuple[str, dict]]`, `.say(chat_id, text, *, update_id=None, chat_type='private', language_code='en') -> dict` (queues a message update and returns it), `.updates` (queue; `getUpdates` returns those with `update_id >= offset`), `.fail(method, status, description='', retry_after=None)` (every later call of that method answers so; `.fail(method, None)` clears), `.garbage = None | str` (200 with that raw body), `.down = bool` (raise `httpx.ConnectError` whose text contains the full URL, token included), `.client() -> httpx.Client` on `httpx.MockTransport`.
  - Fixture `bot` in `conftest.py`: creates a `FakeBot`, patches `telegram.make_client` to return `bot.client()`, applies `override_settings(TELEGRAM_BOT_TOKEN=bot.token, TELEGRAM_BOT_USERNAME='mitaroku_bot', TELEGRAM_ENABLED=True, PUBLIC_URL='https://m.example')`, yields the bot.

- [ ] **Step 1: Write the failing tests** (`tests/test_telegram_api.py`)

```python
def test_send_message_posts_the_text_with_the_html_options(bot):
    telegram.bot_api().send_message(42, '<b>Hi</b>')
    assert bot.sent == [{'chat_id': 42, 'text': '<b>Hi</b>', 'parse_mode': 'HTML',
                         'link_preview_options': {'is_disabled': True}}]

def test_get_updates_passes_the_offset_and_returns_the_newer_updates(bot):
    bot.say(1, 'a', update_id=10); bot.say(1, 'b', update_id=11)
    assert [u['update_id'] for u in telegram.bot_api().get_updates(11)] == [11]
    assert bot.calls[-1] == ('getUpdates', {'offset': 11, 'timeout': 0})

@pytest.mark.parametrize(('status', 'description', 'expected'), [
    (403, 'Forbidden: bot was blocked by the user', PermanentFailure),
    (403, 'Forbidden: user is deactivated', PermanentFailure),
    (400, 'Bad Request: chat not found', PermanentFailure),
    (400, 'Bad Request: message is too long', TelegramError),
    (401, 'Unauthorized', TelegramError),
    (500, 'Internal Server Error', TelegramError),
])
def test_error_answers_are_classified(bot, status, description, expected): ...

def test_a_429_carries_the_retry_after_seconds(bot): ...  # RetryAfter, .seconds == 17
def test_a_conflict_carries_its_code(bot): ...             # get_updates, 409 -> TelegramError, .code == 409

@pytest.mark.parametrize('body', ['not json', '', '[]', '{"ok": true}', '{"result": []}'])
def test_answers_that_are_not_a_clean_success_are_errors(bot, body): ...
# garbage body with HTTP 200; for send_message '{"ok": true}' IS success, so assert TelegramError only for get_updates there and for the other bodies on both

def test_a_transport_error_becomes_unreachable_without_the_url_or_the_token(bot):
    bot.down = True
    with pytest.raises(Unreachable) as raised: telegram.bot_api().send_message(1, 'x')
    assert bot.token not in str(raised.value) and 'api.telegram.org' not in str(raised.value)

def test_a_timeout_is_unreachable(bot): ...  # transport raising httpx.ReadTimeout
def test_no_error_text_contains_the_token(bot): ...  # for every parametrized status above, str(exc) lacks bot.token
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_telegram_api.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

`telegram.py`: one private `_call(method, payload) -> object` that POSTs JSON to `https://api.telegram.org/bot{token}/{method}`, converts transport errors, parses the body, and classifies failures by the body's `error_code` (falling back to the HTTP status) and `description`; `send_message` and `get_updates` use it. Never call `raise_for_status` and never format an `httpx` exception into a message. `FakeBot` and the fixture as in Interfaces.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_telegram_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: add the Telegram Bot API wrapper and a fake bot for tests

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The Telegram message and the channel in the sender

**Files:**
- Create: `sources/notifications/telegram_message.py`, `tests/notification_helpers.py`, `tests/test_telegram_message.py`, `tests/test_deliver_telegram.py`
- Modify: `sources/notifications/digest.py`, `sources/notifications/channels.py`, `sources/notifications/deliver.py`, `tests/test_notification_digest.py`, locale `.po`

**Interfaces:**
- Consumes: Task 1 (fields), Task 2 (`ChannelSpec`, `CHANNELS`), Task 3 (`bot_api`, the `bot` fixture).
- Produces:
  - `digest.Item`, `digest.Group` (the former `_Item`, `_Group`, renamed), `digest.Layout(count: int, groups: list[Group], hidden: int)` and `digest.layout(user, notifications, now) -> Layout`: the ordering, the 50-item cut, row preparation and day grouping that `build_digest` did inline. It must be called under the recipient's `translation.override`. `build_digest` uses it and behaves exactly as before (its tests stay unchanged).
  - `telegram_message.LIMIT = 4096` and `telegram_message.build_message(user, notifications, now) -> str`.
  - `channels.TELEGRAM: ChannelSpec` (`enabled_field='telegram_enabled'`, `since_field='telegram_since'`, `destination` = `str(chat_id)` or `''`, `configured=telegram_configured`, `secret` returns `settings.TELEGRAM_BOT_TOKEN`), `channels.send_telegram(user, notifications, now) -> None`, `channels.disable_telegram(user) -> None` (sets `telegram_enabled=False`, `telegram_since=None`, `telegram_chat_id=None`, `telegram_blocked=True`).
  - `deliver.CHANNELS = (EMAIL, TELEGRAM)`.
  - `tests/notification_helpers.py`: `make_title`, `notify`, `load` moved out of `test_notification_digest.py` (which imports them).

**Message format.** Under the recipient's interface language: a first line `<b>{subject}</b>` where the subject is the digest's count line ("2 new notifications"); then for each day group a line `<b>{heading}</b>` and one line per item: `{label}: <a href="{url}">{name}</a>`, then ` — {text}` when the row has text, then ` ({old} → {new})` for date kinds (`short_date` for the dates). The last lines are `and N more` (when `hidden` or the cut left items out; the same `ngettext` as the mail) and a link to the Notifications screen. Every value (`label`, `name`, `text`, headings) goes through `html.escape(..., quote=True)`; URLs are built from `absolute_url`. Items are added while the whole message still fits in `LIMIT` with the closing lines; the count in "and N more" covers `hidden` plus the items dropped by the cut.

- [ ] **Step 1: Write the failing tests**

Move the helpers (`git mv`-style edit: cut from `test_notification_digest.py`, paste into `tests/notification_helpers.py`, import back). `tests/test_telegram_message.py` (db tests, `PUBLIC_URL='https://m.example'`):

```python
def test_the_message_lists_the_item_with_a_link_in_the_profile_language(alice):
    alice.profile.interface_language = 'ru'; alice.profile.save()
    title = make_title('Show'); notify(alice, title)
    text = build_message(alice, load(alice), NOW)
    assert 'Новая серия' in text and 'Сегодня' in text
    assert f'<a href="{absolute_url(reverse("title", args=[title.pk]))}">Show</a>' in text
    assert absolute_url(reverse('notifications')) in text

def test_the_first_line_is_the_bold_count_in_english(alice): ...  # '<b>2 new notifications</b>'
def test_a_date_change_shows_both_dates(alice): ...               # RELEASE_CHANGED with from/to: both short dates and '→'
def test_a_hostile_title_is_escaped(alice):
    notify(alice, make_title('<b>&"x"'))
    text = build_message(alice, load(alice), NOW)
    assert '<b>&"x"' not in text and '&lt;b&gt;&amp;&quot;x&quot;' in text
def test_more_than_fifty_items_are_cut_with_an_and_n_more_line(alice): ...   # 60 items -> 50 links and 'and 10 more'
def test_a_long_message_is_cut_to_the_limit_on_whole_items(alice):
    # 50 notifications with 200-character titles
    text = build_message(...)
    assert len(text) <= LIMIT
    assert text.count('<a ') == text.count('</a>')
    assert text.rstrip().endswith('</a>') and 'and ' in text
    shown = text.count('/title/')
    assert f'and {50 - shown} more' in text
def test_building_does_not_change_the_active_language(alice): ...
def test_the_query_count_does_not_grow_with_items(alice): ...
```

`tests/test_deliver_telegram.py` (helpers `connect(user, chat_id=42, since=SINCE)` and the notify helper of `test_deliver.py`, copied or moved to `notification_helpers`); every test uses the `bot` fixture:

```python
def test_one_message_per_user_carries_all_items(alice, bot):
    connect(alice); notify(alice, 'A'); notify(alice, 'B')
    assert send_deliveries(NOW) == 1
    (sent,) = bot.sent
    assert sent['chat_id'] == 42 and sent['text'].count('/title/') == 2
    assert [d.status for d in deliveries(alice)] == [Status.SENT] * 2
    assert send_deliveries(NOW + HOUR) == 0

def test_nothing_older_than_the_switch_on_is_sent(alice, bot): ...    # notification created before telegram_since: no row
def test_a_user_without_a_chat_gets_no_rows(alice, bot): ...          # enabled but chat_id None
def test_email_and_telegram_each_get_their_own_row(alice, bot): ...   # both on: 2 rows per notification, one mail, one message
def test_a_blocked_bot_switches_the_channel_off(alice, bot):
    connect(alice); notify(alice); notify(alice, 'B')
    bot.fail('sendMessage', 403, 'Forbidden: bot was blocked by the user')
    send_deliveries(NOW)
    profile = Profile.objects.get(user=alice)
    assert (profile.telegram_enabled, profile.telegram_chat_id, profile.telegram_blocked) == (False, None, True)
    assert [d.status for d in deliveries(alice)] == [Status.SKIPPED] * 2
    bot.fail('sendMessage', None); assert send_deliveries(NOW + HOUR) == 0 and bot.sent == []
def test_a_429_is_honoured(alice, bot): ...                           # retry_after=3600 -> next_attempt_at == NOW + HOUR
@pytest.mark.parametrize('body', ['not json', '', '{"ok": false}'])
def test_an_answer_that_is_not_a_clean_success_is_retried_not_sent(alice, bot, body): ...   # PENDING, attempts 1, next +15 min
def test_the_fifth_failure_marks_failed(alice, bot): ...              # bot.fail('sendMessage', 500) over five spaced runs
def test_an_unreachable_telegram_stops_the_pass_and_hides_the_token(alice, bob, bot, caplog):
    bot.down = True  # alice and bob connected
    send_deliveries(NOW)
    assert len(bot.calls) == 1                                        # bob not tried
    assert bot.token not in deliveries(alice)[0].last_error and bot.token not in caplog.text
def test_a_failure_text_never_holds_the_token(alice, bot, caplog): ...  # bot.fail 500 with description containing the token
def test_an_unconfigured_telegram_is_skipped(alice, bot): ...         # override TELEGRAM_ENABLED=False: returns 0, bot.calls == []
def test_a_read_notification_is_skipped_not_sent(alice, bot): ...
```

Existing `tests/test_notification_digest.py` must stay green after the extraction.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_telegram_message.py tests/test_deliver_telegram.py tests/test_notification_digest.py -q`
Expected: FAIL (no `telegram_message`, no `TELEGRAM` spec).

- [ ] **Step 3: Implement**

Extract `layout()` from `build_digest` first and run the digest tests green; then `telegram_message.py` (the cut is a loop that renders the closing lines once, then adds items while `len(...)` stays within `LIMIT`); then `send_telegram` (build under `translation.override`, `telegram.bot_api().send_message(user.profile.telegram_chat_id, text)`), `disable_telegram`, the `TELEGRAM` spec and `CHANNELS`. Add the new Russian strings to the `.po` (the "and N more" and count strings already exist; only new msgids need pairs).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_telegram_message.py tests/test_deliver_telegram.py tests/test_notification_digest.py tests/test_deliver.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: send the digest through Telegram as the second delivery channel

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Connecting a chat: link tokens and the bot's replies

**Files:**
- Create: `sources/notifications/telegram_link.py`, `sources/notifications/telegram_bot.py`, `tests/test_telegram_link.py`, `tests/test_telegram_bot.py`
- Modify: locale `.po`

**Interfaces:**
- Consumes: Task 1 (Profile fields), Task 3 (`BotApi`, `bot`, `Unreachable`/`PermanentFailure`).
- Produces:
  - `telegram_link.TOKEN_LIFETIME = timedelta(minutes=30)`.
  - `issue_link_token(user, now: datetime) -> str` (random `secrets.token_urlsafe(24)`; stores the SHA-256 hex in `telegram_link_hash` and `now + TOKEN_LIFETIME` in `telegram_link_expires`; replaces any earlier token).
  - `link_url(token: str) -> str` = `https://t.me/{TELEGRAM_BOT_USERNAME}?start={token}`.
  - `class Outcome(enum.Enum)`: `CONNECTED`, `INVALID`, `CHAT_TAKEN`; `connect(token: str, chat_id: int, now: datetime) -> tuple[Outcome, User | None]`: inside one transaction the profile is found by hash and unexpired (`select_for_update`); another profile already holding the chat gives `CHAT_TAKEN` and leaves the token valid; otherwise the profile gets `telegram_chat_id`, `telegram_enabled=True`, `telegram_since=now`, `telegram_blocked=False`, and the hash and expiry are cleared. A same-account relink replaces the chat. An `IntegrityError` on the unique chat id also gives `CHAT_TAKEN`.
  - `disconnect(profile: Profile) -> None`: clears chat, enabled, since, link hash and expiry and `telegram_blocked`.
  - `telegram_bot.handle_update(update: dict, api: BotApi, now: datetime) -> None`.

**Update handling rules.** Only `update['message']` counts, with `chat.type == 'private'`, an `int` chat id and a `str` `text`; malformed shapes are ignored without raising. The command is the first word with any `@botname` suffix removed and must equal `/start`; the rest of the text, stripped, is the token. Replies go through `api.send_message(chat_id, text)`: connected → in the account's `interface_language`; every other reply → Russian when the sender's `language_code` starts with `ru`, otherwise English. Reply texts (all `gettext`, under `translation.override`):
- connected: `Connected. New mitaroku notifications will arrive in this chat.`
- help (bare `/start`, any other private text): `To connect Telegram, open mitaroku Settings and press "Connect Telegram": %(url)s` with `url = absolute_url(reverse('settings'))`.
- invalid token: `That link has expired or was already used. Press "Connect Telegram" in mitaroku Settings to get a new one: %(url)s`.
- chat taken: `This Telegram account is already connected to another mitaroku account. Disconnect it there first, then try again.`

A `PermanentFailure` while replying is logged at info level (class name only) and swallowed (the user may have blocked the bot a second after pressing Start); `Unreachable` and every other error propagate to the caller.

- [ ] **Step 1: Write the failing tests**

`tests/test_telegram_link.py` (db):

```python
def test_a_token_is_urlsafe_short_and_stored_only_as_a_hash(alice):
    token = issue_link_token(alice, NOW)
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,64}', token)
    profile = Profile.objects.get(user=alice)
    assert profile.telegram_link_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in profile.telegram_link_hash
    assert profile.telegram_link_expires == NOW + TOKEN_LIFETIME
def test_a_new_token_replaces_the_old_one(alice): ...      # old -> INVALID, new -> CONNECTED
def test_connecting_switches_the_channel_on_from_that_moment(alice):
    # CONNECTED, user is alice; chat_id, enabled True, since == NOW, blocked False (start with blocked True), hash '' and expires None
def test_a_used_token_cannot_connect_a_second_chat(alice): ...   # second connect(token, 99, NOW) -> (INVALID, None); chat stays
def test_an_expired_token_is_invalid_and_changes_nothing(alice): ...   # NOW + TOKEN_LIFETIME + 1s
def test_an_unknown_or_empty_token_is_invalid(alice): ...
def test_a_chat_of_another_account_is_refused_and_the_token_survives(alice, bob):
    # bob holds chat 42; alice's token with chat 42 -> CHAT_TAKEN; after disconnect(bob.profile) the same token works
def test_the_same_account_may_move_to_another_chat(alice): ...
def test_disconnect_forgets_everything(alice): ...
def test_link_url_names_the_bot(bot): ...    # https://t.me/mitaroku_bot?start=<token>
```

`tests/test_telegram_bot.py` (db, `bot` fixture, `api = telegram.bot_api()`):

```python
def test_start_with_a_token_connects_and_answers_in_the_account_language(alice, bot):
    alice.profile.interface_language = 'ru'; alice.profile.save()
    token = issue_link_token(alice, NOW)
    handle_update(bot.say(42, f'/start {token}'), api, NOW)
    assert Profile.objects.get(user=alice).telegram_chat_id == 42
    assert bot.sent[0]['chat_id'] == 42 and 'Подключено' in bot.sent[0]['text']
def test_the_botname_suffix_is_accepted(alice, bot): ...        # '/start@mitaroku_bot <token>'
def test_a_bare_start_changes_nothing_and_points_to_settings(alice, bot): ...
def test_the_reply_language_follows_the_telegram_language_code(bot): ...   # 'ru' -> Russian help, 'de' -> English
def test_an_unknown_token_gets_the_expired_reply(alice, bot): ...
def test_a_used_token_cannot_connect_a_second_chat(alice, bot):          # Review Focus 1
    token = ...; handle_update(say(42, '/start ' + token)); handle_update(say(77, '/start ' + token))
    assert chat_id == 42 and last reply text is the expired one and went to chat 77
@pytest.mark.parametrize('chat_type', ['group', 'supergroup', 'channel'])
def test_a_group_message_is_ignored_and_the_token_stays_valid(alice, bot, chat_type):   # Review Focus 2
    # no reply, bot.sent == [], chat_id None, and connect(token, 42, NOW) still CONNECTED afterwards
def test_other_updates_and_malformed_shapes_are_ignored(bot): ...        # edited_message, my_chat_member, message without text, message: 'x', chat missing, chat id as a string
def test_other_private_text_gets_the_help_reply(bot): ...
def test_a_chat_of_another_account_gets_the_refusal(alice, bob, bot): ...
def test_a_block_right_after_start_is_swallowed_and_the_connection_stands(alice, bot):
    bot.fail('sendMessage', 403, 'Forbidden: bot was blocked by the user')  # no exception, chat_id == 42
def test_an_unreachable_telegram_propagates(alice, bot): ...             # bot.down -> pytest.raises(Unreachable)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_telegram_link.py tests/test_telegram_bot.py -q`
Expected: FAIL (modules not found).

- [ ] **Step 3: Implement**

Both modules per Interfaces and rules. System code that spans users says so in its docstring. Add the four reply strings (and their Russian: «Подключено. Новые уведомления mitaroku будут приходить в этот чат.» and the like) to the `.po`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_telegram_link.py tests/test_telegram_bot.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: connect a Telegram chat through a one-time /start token

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The poll

**Files:**
- Create: `sources/notifications/telegram_poll.py`, `tests/test_telegram_poll.py`
- Modify: `sources/notifications/tasks.py`, `tests/test_notification_tasks.py`

**Interfaces:**
- Consumes: Task 3 (`bot_api`, `TelegramError`), Task 5 (`handle_update`), `providers.clients.connect_redis`.
- Produces:
  - `telegram_poll.KEY_PREFIX = 'mitaroku:telegram:'` (offset key `<prefix>offset`, lock key `<prefix>poll`).
  - `poll_updates(now: datetime) -> int`: returns the number of updates handled; `0` when Telegram is not configured, when the lock is held (`redis.lock(..., timeout=55, blocking=False)`), or when `getUpdates` fails. It reads the offset (`None` when absent), fetches updates, handles them **in order**, and stores `update_id + 1` after **each** update. An update whose handling raises an ordinary exception is logged (class name only) and skipped, with the offset moved past it; `Unreachable` stops the batch (the offset stays at the last handled update). A `TelegramError` or `Unreachable` from `getUpdates` is logged with `logger.error` (class and HTTP code only, never a message that could hold the token). Releasing a lock that already expired must not raise.
  - `tasks.poll_telegram_task` (`db_periodic_task(crontab(minute='*'))`) returning `poll_updates(timezone.now())` and logging the count.

- [ ] **Step 1: Write the failing tests**

`tests/test_telegram_poll.py` (db, `bot`, `redis_client`; an autouse fixture sets `monkeypatch.setattr(telegram_poll, 'KEY_PREFIX', 'test:telegram:')`):

```python
def test_an_unconfigured_telegram_polls_nothing(): ...            # override TELEGRAM_ENABLED=False; 0, bot.calls == []
def test_the_poll_handles_updates_and_remembers_the_offset(alice, bot, redis_client):
    token = issue_link_token(alice, NOW); bot.say(42, f'/start {token}', update_id=10)
    assert poll_updates(NOW) == 1
    assert Profile.objects.get(user=alice).telegram_chat_id == 42
    assert redis_client.get('test:telegram:offset') == '11'
    assert poll_updates(NOW) == 0 and bot.calls[-1] == ('getUpdates', {'offset': 11, 'timeout': 0})
def test_a_lost_offset_replays_harmlessly(alice, bot, redis_client):                    # Review Focus 5
    # connect as above, redis_client.delete(offset key), poll again:
    # profile still connected to 42, still one connection, the replay got the 'expired' reply
def test_a_malformed_update_is_skipped_and_the_next_one_is_handled(alice, bot, redis_client): ...
def test_an_unexpected_error_in_one_update_does_not_wedge_the_poll(alice, bot, monkeypatch): ...   # handle_update raises ValueError for update 1
def test_an_unreachable_telegram_mid_batch_keeps_the_offset_at_the_last_handled_update(alice, bot, redis_client): ...
def test_a_conflict_is_logged_without_the_token_and_changes_nothing(alice, bot, caplog):         # Review Focus 5
    bot.fail('getUpdates', 409, 'Conflict: terminated by other getUpdates request; make sure that only one bot instance is running')
    assert poll_updates(NOW) == 0
    assert bot.token not in caplog.text and 'getUpdates' in caplog.text or '409' in caplog.text
def test_getupdates_unreachable_is_logged_and_returns_zero(bot, caplog): ...
def test_a_held_lock_skips_the_poll(bot, redis_client):
    lock = connect_redis().lock('test:telegram:poll', timeout=60); assert lock.acquire(blocking=False)
    try: assert poll_updates(NOW) == 0 and bot.calls == []
    finally: lock.release()
```

`tests/test_notification_tasks.py`: mirror the existing `send_deliveries_task` test for `poll_telegram_task` (monkeypatch `tasks.poll_updates`, assert it is called with the current time and the count is returned); also assert the crontab fires every minute using the same technique the existing tests use for the other tasks, if one exists.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_telegram_poll.py tests/test_notification_tasks.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** as above.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_telegram_poll.py tests/test_notification_tasks.py -q`
Expected: PASS (Redis dev container on port 56379 must be up).

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: poll Telegram for /start links once a minute

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The Telegram block in Settings

**Files:**
- Create: `sources/web/views/telegram.py`, `sources/web/templates/web/_telegram_block.html`, `tests/test_web_settings_telegram.py`
- Modify: `sources/web/urls.py`, `sources/web/views/settings.py` (`_render_page` merges the block's context), `sources/web/templates/web/settings.html` (include the block after Email; remove the line "Telegram notifications are coming later."), `sources/web/static/web/app.css` if a style is missing, locale `.po` (the removed msgid moves to the obsolete block)

**Interfaces:**
- Consumes: Task 1 (`telegram_configured`, fields), Task 5 (`issue_link_token`, `link_url`, `disconnect`).
- Produces: `POST /settings/telegram/` (`settings_telegram`, hidden field `action`: `connect`, `toggle`, `disconnect`), `GET /settings/telegram/status/` (`settings_telegram_status`).

**Behaviour.**
- The block is shown only when `telegram_configured()`; otherwise the POST and the status URLs answer 404 and the page has no block. An unknown action is a 400.
- State, in this order: **connected** (chat set, channel on), **paused** (chat set, channel off), **blocked** (no chat, `telegram_blocked`), **waiting** (no chat, a token that has not expired), **idle**.
  - idle: text and a "Connect Telegram" button. blocked: the sentence "Telegram was switched off because the bot was blocked. Connect again to resume." and the same button. waiting: "Waiting for you to press Start in Telegram." and the button again ("Get a new link"). connected/paused: the switch "Send me new notifications on Telegram", a "Disconnect" button.
- `connect` issues a token (`issue_link_token(user, timezone.now())`) and **renders the page directly** (no redirect) with `telegram_link = link_url(token)` so the block shows an "Open in Telegram" link (`target="_blank" rel="noopener"`); the link exists only in that response.
- While the state is waiting, the block carries `hx-get="{% url 'settings_telegram_status' %}" hx-trigger="every 3s" hx-swap="none"`. The status view answers **204** while the profile has no chat and **200 with the header `HX-Refresh: true`** once it has one.
- `toggle` on: requires a chat (otherwise the page with the error "Connect Telegram first"); when the channel was off it sets `telegram_enabled=True` and `telegram_since=timezone.now()`; off clears both. `disconnect` calls `telegram_link.disconnect`. Every change redirects back to Settings with the saved flag, like the email actions.
- Only the signed-in user's profile is read or changed.

- [ ] **Step 1: Write the failing tests** (`tests/test_web_settings_telegram.py`, in the style of `tests/test_web_settings_email.py`: `client`, `portal_headers`, `alice`, `override_settings` through the `bot` fixture)

```python
def test_the_block_is_absent_when_telegram_is_not_configured(client, portal_headers, alice): ...   # no 'Telegram' heading; POST /settings/telegram/ -> 404; status -> 404
def test_the_old_coming_later_note_is_gone(client, portal_headers, alice, bot): ...
def test_idle_shows_the_connect_button(client, portal_headers, alice, bot): ...
def test_connect_shows_a_one_time_link_in_the_response(client, portal_headers, alice, bot):
    response = client.post(URL, {'action': 'connect'}, **portal_headers)
    assert response.status_code == 200
    link = re.search(r'https://t\.me/mitaroku_bot\?start=([A-Za-z0-9_-]+)', response.content.decode())
    assert link and Profile.objects.get(user=alice).telegram_link_hash == sha256(link.group(1))
    assert 'rel="noopener"' in html
def test_a_later_page_load_waits_without_the_link_and_polls(...):   # state waiting: 'Waiting for you to press Start', hx-trigger="every 3s", no t.me link
def test_a_second_connect_replaces_the_first_token(...): ...
def test_connected_shows_the_switch_and_disconnect(...): ...        # no hx-get in this state
def test_toggle_on_sets_the_moment_and_off_clears_it(...): ...
def test_toggle_on_without_a_chat_is_refused(...): ...
def test_disconnect_forgets_the_chat(...): ...
def test_a_blocked_account_shows_the_blocked_sentence_and_reconnecting_clears_it(...): ...
def test_the_status_is_204_while_waiting_and_refreshes_once_connected(client, portal_headers, alice, bot):
    assert client.get(STATUS, **portal_headers).status_code == 204
    # connect the chat in the DB
    response = client.get(STATUS, **portal_headers)
    assert response.status_code == 200 and response.headers['HX-Refresh'] == 'true'
def test_state_changes_are_post_only(...): ...          # GET /settings/telegram/ -> 405
def test_a_second_user_is_never_touched(alice, bob, ...): ...
def test_the_block_is_in_russian_for_a_russian_profile(...): ...
def test_local_accounts_mode_shows_the_block_too(...): ...   # LOCAL override as in test_web_settings_email
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_web_settings_telegram.py -q`
Expected: FAIL (404 for the new URLs).

- [ ] **Step 3: Implement**

`views/telegram.py` with `telegram_context(profile) -> dict` (state and flags; merged by `_render_page`), `settings_telegram`, `settings_telegram_status`. The partial template `_telegram_block.html` is included from `settings.html` and uses the existing `mt-set-*` classes (check the Email block and the CSS; add a rule only when something is truly missing). Strings in Russian: "Подключить Telegram", "Отключить", "Открыть в Telegram", and the sentences above.

- [ ] **Step 4: Run to verify pass, and the whole suite**

Run: `pytest tests/test_web_settings_telegram.py tests/test_web_settings.py tests/test_web_settings_email.py tests/test_tracking_scoping.py -q`, then `pytest -q`
Expected: PASS; the suite is green.

- [ ] **Step 5: Commit**

```bash
git add sources tests
git commit -m "feat: let users connect and switch Telegram in Settings

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`, `config.example.yaml`, `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 3, 7, 10 and 13), `docs/superpowers/specs/2026-11-22-plan-4c-telegram-design.md` (only if the code differs from it)

- [ ] **Step 1: Update the documents**

- `README.md`: the Telegram configuration (`telegram.bot_token`, `telegram.bot_username`, the two env names, both needed, plus `public_url`); how to create the bot with @BotFather; **the bot must be dedicated** (no webhook, no second `getUpdates` consumer, one instance); how a user connects; what is not in the bot (`/start` only; stop in Settings or by blocking the bot); the line "Telegram is not built yet (plan 4c)" goes away. The poll is outbound HTTPS only: no nginx change.
- `config.example.yaml`: a commented `telegram:` block next to `email:`.
- Requirements spec: section 3 (the new `Profile` fields), section 7 (a paragraph "As built for the Telegram channel (plan 4c)": opt-in by one-time link, chat per account, polling every minute, one message per pass, limit and cut, blocked bot switches off, the connection-level stop shared with email), section 10 (the webhook sentence now says polling is used and no webhook exists), section 13 (the open question "Telegram: webhook or polling" is settled: polling).
- Check that every statement matches the code (names of settings, the button labels, the 30-minute life).

- [ ] **Step 2: Verify**

Run: `pre-commit run --all-files` and `pytest -q`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add README.md config.example.yaml docs
git commit -m "docs: document the Telegram channel

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Live verification (controller only, not given to an implementer)

The user provides a test bot token outside the repo (like `~/.tmdb-credentials.json`); it is never printed, logged or passed to a subagent. Use a scratch database and Redis db, not `mitaroku`. Pass 1 (live API): connect a real chat from Settings, receive the digest, check the links and escaping on a phone-sized view, block the bot and confirm the channel switches off and the block shows it, unblock and reconnect; run with `getUpdates` unreachable and with a webhook set to see the 409 path. Pass 2 (browser, Chromium 390 and 960 px, light and dark, en and ru): the block in its five states. Record results and rulings in the ledger; the wrap-up message to the user lists the rulings with their costs.
