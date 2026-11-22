# Delivery Core and Email Implementation Plan (plan 4b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user who enables email in Settings gets one localised digest mail per pass for the notifications of plan 4a, can confirm a different address, and can switch the channel off from the mail itself, with no repeats, no backlog, and no mail to an unconfirmed address.

**Architecture:** The `notifications` app gains a `Delivery` table and a Huey job `send_deliveries` that materialises `Delivery` rows for users with email enabled (the producers of plan 4a do not change), settles stale or already-read ones, and sends one message per user. A `Notification` display layer is extracted from the web view so the mail and the screen share their wording. Signed tokens drive the unsubscribe and address-confirmation pages, which work without a session. Settings gets an Email block. Telegram is plan 4c.

**Tech Stack:** Django 6.1 (`EmailMultiAlternatives`, `django.core.signing`), Huey, Pydantic settings, HTMX, pytest (locmem mail backend).

**Spec:** `docs/superpowers/specs/2026-11-22-plan-4b-email-delivery-design.md` (binding), argued from `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` sections 3, 7, 10, 11 and the 4a design `docs/superpowers/specs/2026-11-21-plan-4a-on-site-notifications-design.md`.

## Global Constraints

- Django on the latest stable release (`Django==6.1.3`), Python 3.12, PostgreSQL, Redis, Huey; UI is HTMX + Alpine, no build step (spec section 2).
- "Email: admin-configured SMTP, per-user opt-in, address from `X-Discord-Email` or entered manually, one-click unsubscribe." and "Mechanics: dedupe through a unique key, grouping per title per day, one `Delivery` row per channel with retries." (requirements spec section 7).
- "All user-data queries go through a model manager that scopes them to the user." (spec section 3). `Delivery` uses `UserScopedManager` (`for_user`); the sender job is a system job spanning users and says so in a comment. The scoping guard `tests/test_tracking_scoping.py` covers `Delivery` and `.deliveries` after Task 1.
- "CSRF protection covers HTMX requests." (spec section 10). Every state change is a POST. The only routes that work without a session are the token pages and the one-click unsubscribe, and they act only on the user a valid signed token names.
- "The UI ships in Russian and English on the Django i18n foundation." (spec section 11). Every new string, including mail text, goes through `gettext` / `{% trans %}` and gets a Russian translation in `sources/locale/ru/LC_MESSAGES/django.po` (additions only; the existing `(msgid, msgstr)` set, obsolete `#~` entries included, stays intact; check with `msgfmt --check -o /dev/null`).
- Every mail, confirmation mail and token page is rendered in the recipient's `Profile.interface_language`, titles in `Profile.metadata_language`; the worker has no request, so rendering runs under `translation.override(language)`.
- The recipient address, the SMTP password and the tokens never reach the logs.
- Single quotes for Python strings (docstrings use triple double quotes); type annotations on public functions; Google-style docstrings; PEP 8 with 88 columns preferred; log with `logging` and %-formatting.
- Everything committed (code, comments, commit messages, docs) is in English. Commit messages end with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; commits are GPG-signed (never bypass).
- Run every command from the repository root with `source .venv/bin/activate`; tests need the dev containers (`docker compose -f docker-compose.dev.yml up -d --wait`). Run `pre-commit run --all-files` and `python sources/manage.py makemigrations --check` before the last commit of a task that changes models.
- Time is never read from the clock inside logic: functions take `now: datetime` and tests pass fixed values; there is no time-freezing library.

## Decisions

Rulings the implementer cannot make alone; they restate the design and are binding.

1. **Enabled means configured.** The Email block and the sender exist only when `settings.EMAIL_ENABLED` is true: `config.email.host` and `config.public_url` are both non-empty. A host without `from_address` is a config error.
2. **Address.** `channel.effective_address(user)` is `Profile.notify_email` when set, else `User.email` (portal-supplied or operator-set, trusted), else `''`. The switch cannot be turned on while it is `''`. A different address goes to `Profile.pending_email` and becomes `notify_email` only through the confirmation link; until then the previous address stays in use.
3. **Opt-in guard.** Turning the switch on sets `Profile.email_since = now`, off sets it to `None` and `email_enabled = False`. Only notifications with `created_at > email_since` are mailed.
4. **Delivery statuses.** `pending`, `sent`, `failed`, `expired` (the notification is older than 48 h), `skipped` (the notification is already read on the site). Unique `(notification, channel)`: a notification merged after it was mailed is never mailed again.
5. **Retry.** After failure number 1, 2, 3, 4 the next attempt waits 15 min, 1 h, 6 h, 6 h; failure number 5 sets `failed`. A pending delivery is not attempted before `next_attempt_at`.
6. **Digest.** One message per user per run, at most 50 items listed (the rest as "and N more" with the screen link); all items of the message become `sent` together. The subject is fixed text plus the count, never title text. Plain text plus an HTML alternative; every value escaped; links absolute from `settings.PUBLIC_URL`.
7. **Tokens.** Built with `django.core.signing`. Unsubscribe token: payload is the user id, salt `mitaroku.email.unsubscribe`, no expiry. Confirmation token: payload is the user id and the address, salt `mitaroku.email.confirm`, `max_age` 3 days. Both pages answer GET with a page holding a button and change state only on POST; a bad, expired or mismatched token is a plain "link is not valid" page with status 400 and no state change.
8. **Public routes.** `/email/unsubscribe/<token>/` (GET page, POST acts, `csrf_exempt` because a mail client posts it without a token) and `/email/confirm/<token>/` (GET page, POST acts, normal CSRF). Both `@login_not_required`. In portal mode nginx must let `/email/` through without the SSO check: plan 6 carry-over.
9. **Sender schedule.** `send_deliveries` is a Huey periodic task, minutes 2, 17, 32, 47 of every hour.
10. **Out of scope** (design non-goals): Telegram, per-type or per-title settings, a configurable hour, bounce processing, DKIM/SPF, digest frequency, an admin screen for deliveries.

## Review Focus

The inputs and failures the spec implies but its straight-line tests would miss, most likely first; each has a pinning test in the task named.

1. Two `send_deliveries` runs overlap: never two mails for the same delivery (Task 5: `test_overlapping_runs_never_send_twice`). A crash between sending and committing may repeat one mail; the design accepts that at-least-once case and no test pins it.
2. A mail scanner or a link preview opens the unsubscribe or confirmation link: GET changes nothing (Task 3).
3. A hostile title name (`<script>`, header-injection text with newlines) or a long list: escaped in the body, absent from subject and headers, capped at 50 items (Task 4).
4. SMTP down for days, then back: no flood of stale mail (`expired` after 48 h), retries follow Decision 5 (Task 5).
5. A user enables email while a backlog exists, or toggles off and on: nothing before `email_since` is mailed (Tasks 5 and 6).

## File Structure

- Create `sources/notifications/`: `channel.py` (`email_configured`, `effective_address`, `absolute_url`), `display.py` (rows extracted from the web view), `tokens.py`, `digest.py` (`build_digest`), `deliver.py` (`send_deliveries` logic), `confirmation.py` (`send_confirmation`), `templates/notifications/email/` (`digest.txt`, `digest.html`, `confirm.txt`, `confirm.html`), `migrations/0002_delivery.py`.
- Modify `sources/mitaroku/config.py`, `sources/mitaroku/settings.py`, `sources/accounts/models.py` (+ migration `0005`), `sources/notifications/models.py` (`Delivery`), `sources/notifications/tasks.py` (`send_deliveries`), `sources/web/views/notifications.py` (use `display`), `sources/web/views/settings.py`, `sources/web/views/email.py` (new), `sources/web/urls.py`, templates (`settings.html`, `email_unsubscribe.html`, `email_confirm.html`, `email_invalid.html`), `app.css`, the `ru` `django.po`, `README.md`, the requirements spec (sections 3 and 7).
- Tests (create): `tests/test_email_channel.py`, `test_delivery_models.py`, `test_notification_display.py`, `test_email_tokens.py`, `test_email_pages.py`, `test_notification_digest.py`, `test_deliver.py`, `test_web_settings_email.py`; extend `tests/test_config.py`, `tests/test_tracking_scoping.py`, `tests/test_notification_tasks.py` only where a task says so.

---

### Task 1: Config, address helpers, `Profile` fields and `Delivery`

**Files:**
- Create: `sources/notifications/channel.py`, `sources/notifications/migrations/0002_delivery.py` and `sources/accounts/migrations/0005_profile_email.py` (generated)
- Modify: `sources/mitaroku/config.py`, `sources/mitaroku/settings.py`, `sources/accounts/models.py`, `sources/notifications/models.py`
- Test: `tests/test_config.py` (extend), `tests/test_email_channel.py`, `tests/test_delivery_models.py`, `tests/test_tracking_scoping.py` (extend)

**Interfaces:**
- Produces, `mitaroku.config`: `EmailConfig(host: str = '', port: int = 587, user: str = '', password: SecretStr | None = None, starttls: bool = True, from_address: str = '')` with a model validator that rejects a non-empty `host` with an empty `from_address`; `Settings.email: EmailConfig = EmailConfig()`; `Settings.public_url: str = ''` (validator: empty, or starts with `http://` or `https://`; trailing slashes stripped). A blank password counts as `None`.
- Produces, Django settings (Django 6.1 deprecates the `EMAIL_*` settings in favour of `MAILERS`, so use `MAILERS`): `MAILERS = {'default': {'BACKEND': 'django.core.mail.backends.smtp.EmailBackend', 'OPTIONS': {'host': <email.host or 'localhost'>, 'port', 'username', 'password' (secret value or ''), 'use_tls': <starttls>, 'timeout': 15}}}` (always defined, so tests that send mail find a mailer the test runner swaps for the in-memory one; nothing sends while email is disabled), `DEFAULT_FROM_EMAIL` (= `from_address` when set), `PUBLIC_URL`, `EMAIL_ENABLED: bool` (Decision 1). No `EMAIL_HOST*` / `EMAIL_USE_TLS` / `EMAIL_TIMEOUT` settings and no `get_connection()` calls anywhere.
- Produces, `accounts.models.Profile`: `email_enabled = BooleanField(default=False)`, `email_since = DateTimeField(null=True, blank=True)`, `notify_email = EmailField(blank=True)`, `pending_email = EmailField(blank=True)`.
- Produces, `notifications.models.Delivery` (not in the admin): `user` FK `related_name='deliveries'`, `notification` FK `Notification` cascade `related_name='deliveries'`, `channel` CharField(10) with `Channel` TextChoices (`EMAIL = 'email'`), `status` CharField(10) with `Status` TextChoices (Decision 4) default `pending`, `attempts` PositiveSmallIntegerField default 0, `next_attempt_at` DateTimeField null, `sent_at` DateTimeField null, `last_error` CharField(200, blank), `created_at` DateTimeField default `timezone.now`; `UniqueConstraint(fields=['notification', 'channel'], name='unique_delivery_per_channel')`; `Index(fields=['channel', 'status', 'next_attempt_at'], name='delivery_due_idx')`; `objects = UserScopedManager()`.
- Produces, `notifications.channel`: `email_configured() -> bool` (`settings.EMAIL_ENABLED`); `effective_address(user: User) -> str` (Decision 2, stripped); `absolute_url(path: str) -> str` (`settings.PUBLIC_URL + path`, `path` starts with `/`).

- [ ] **Step 1: Write the failing tests.** `tests/test_config.py`: `test_the_email_block_is_off_by_default`; `test_a_host_without_a_from_address_is_rejected` (`MITAROKU_EMAIL__HOST` set alone → `ValidationError`); `test_public_url_loses_its_trailing_slash_and_must_be_http` (`https://m.example/` → `https://m.example`; `ftp://x` rejected; empty allowed); `test_a_blank_email_password_is_none`; `test_the_email_password_is_not_in_the_repr`. `tests/test_email_channel.py` (`django_db` where users are needed, `override_settings(EMAIL_ENABLED=..., PUBLIC_URL=...)`): `test_email_configured_follows_the_setting`; `test_effective_address_prefers_the_confirmed_address` (`notify_email` over `user.email`); `test_effective_address_falls_back_to_the_account_email`; `test_effective_address_is_empty_without_any` (empty `user.email`, empty `notify_email`, and whitespace-only values); `test_absolute_url_joins_the_public_url`; `test_the_default_test_settings_leave_email_disabled`; `test_the_default_mailer_carries_the_smtp_options` (`django.core.mail.mailers` configured from the config values: build the options dict through a small function in `settings.py` or a helper you can import, and assert host, port, username, `use_tls` and timeout; the password value is passed but never appears in any `repr` of the settings object). `tests/test_delivery_models.py`: `test_one_delivery_per_notification_and_channel` (second insert raises `IntegrityError`); `test_for_user_returns_only_own_deliveries`; `test_deleting_a_notification_deletes_its_deliveries`; `test_new_profile_email_fields_are_off_and_empty`. `tests/test_tracking_scoping.py`: add `Delivery` to `MANAGER` and `deliveries` to `RELATED`, add parametrised cases `'Delivery.objects.filter(a=1)'` and `'user.deliveries.count()'` to the unscoped list and `'Delivery.objects.for_user(u).filter(a=1)'` to the scoped list, as 4a did for `Notification`.
- [ ] **Step 2: Run them to see them fail.** `pytest tests/test_config.py tests/test_email_channel.py tests/test_delivery_models.py tests/test_tracking_scoping.py -v` → failures or import errors.
- [ ] **Step 3: Implement** the config block and settings (read `config.email` and `config.public_url`; `EMAIL_ENABLED = bool(config.email.host and config.public_url)`; use `SecretStr.get_secret_value()` only to fill `EMAIL_HOST_PASSWORD`), the `Profile` fields, `Delivery`, `channel.py`; `python sources/manage.py makemigrations accounts notifications`. Do not register `Delivery` in the admin.
- [ ] **Step 4: Run tests and checks.** The same command → PASS; `python sources/manage.py makemigrations --check` → "No changes detected"; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: add the email config, the Delivery table and the profile email fields"`.

---

### Task 2: Extract the row display from the web view

A behaviour-preserving move so the mail and the screen share their wording. No new behaviour; the existing screen tests are the regression gate.

**Files:**
- Create: `sources/notifications/display.py`
- Modify: `sources/web/views/notifications.py`
- Test: `tests/test_notification_display.py`; `tests/test_web_notifications.py` must pass unchanged

**Interfaces:**
- Consumes: the current private helpers of `web/views/notifications.py` (`KINDS`, `EPISODE_KINDS`, `Row`, `_payload_date`, `_payload_episodes`, `_payload`, `_episodes`, `_text`, `_row`), `web.templatetags.web_extras.episode_code`, `catalog.translations` (`localized_title`, `localized_episode`, `FALLBACK_LANGUAGE`). Importing `episode_code` from `web` into `notifications` is accepted: `display.py` is presentation code.
- Produces, `notifications.display`: `KINDS`, `EPISODE_KINDS`, `Row` (moved unchanged, same fields); `prepare_rows(notifications: list[Notification], language: str) -> list[Row]` (rows in the input order; prefetches the title translations for `{language, FALLBACK_LANGUAGE}` and loads the episodes with a constant number of queries; labels and sentences use the language active at call time, so a caller outside a request activates it); `display_name(title: Title, language: str) -> str` (the localized name, `gettext('Untitled')` when none). The view imports `Row` and `prepare_rows` from here and keeps paging and grouping.

- [ ] **Step 1: Write the failing tests** in `tests/test_notification_display.py`: `test_one_episode_shows_code_and_name` (`'S1E3 · Name'`); `test_several_episodes_show_codes` (`'S1E3, S1E4'`); `test_the_movie_sentence_is_translated_under_the_active_language` (`translation.override('ru')` gives the Russian sentence); `test_date_kinds_carry_old_and_new`; `test_a_deleted_episode_is_skipped_and_bad_payloads_are_tolerated` (payload `{'episodes': 'x'}`, a non-dict payload, a bool in the id list); `test_prepare_rows_keeps_the_input_order_and_the_unread_flag`; `test_the_query_count_does_not_grow_with_rows` (3 rows versus 12 rows: equal); `test_display_name_falls_back_to_the_english_name_then_untitled`.
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_notification_display.py -v` → import errors.
- [ ] **Step 3: Move** the helpers into `display.py` and make the view call `prepare_rows`; leave the view's grouping and paging unchanged. Delete the moved code from the view (no copies).
- [ ] **Step 4: Run.** `pytest tests/test_notification_display.py tests/test_web_notifications.py -v` → PASS (the screen tests untouched); `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "refactor: move the notification row display out of the web view"`.

---

### Task 3: Signed tokens and the two public pages

**Files:**
- Create: `sources/notifications/tokens.py`, `sources/web/views/email.py`, templates `sources/web/templates/web/email_unsubscribe.html`, `email_confirm.html`, `email_invalid.html`
- Modify: `sources/web/urls.py`
- Test: `tests/test_email_tokens.py`, `tests/test_email_pages.py`

**Interfaces:**
- Consumes: Task 1 (`Profile` fields, `channel`); the public-view pattern of `accounts/views.py` (`@login_not_required`).
- Produces, `notifications.tokens`: `unsubscribe_token(user: User) -> str`; `unsubscribe_user(token: str) -> User | None`; `confirmation_token(user: User, address: str) -> str`; `confirmation_target(token: str) -> tuple[User, str] | None` (`None` for a bad signature, an expired token, or an unknown user). `MAX_AGE_CONFIRM = timedelta(days=3)`.
- Produces, URLs: `email_unsubscribe` at `email/unsubscribe/<str:token>/` and `email_confirm` at `email/confirm/<str:token>/`.
- Page behaviour: the unsubscribe page names no address; POST sets `Profile.email_enabled=False` and `email_since=None` and shows "You will not get email from mitaroku any more" with a link to Settings; repeating it is harmless. The confirm page names the address it will confirm; POST sets `notify_email = address` and clears `pending_email` **only when `pending_email == address`** (a stale link for a replaced address is "not valid"). Each page renders under `translation.override(profile.interface_language)` of the token's user, extends `web/base.html` like the login page (no bottom bar), and is a standalone view for anonymous visitors. An invalid token renders `email_invalid.html` with status 400 in the cookie or default language.

- [ ] **Step 1: Write the failing tests.** `tests/test_email_tokens.py`: `test_an_unsubscribe_token_round_trips`; `test_a_tampered_token_is_rejected`; `test_an_unsubscribe_token_has_no_expiry` (a token made long ago still reads: build it with `signing.dumps` and a patched `time.time`, or check `max_age` is not passed); `test_a_confirmation_token_carries_the_address`; `test_an_expired_confirmation_token_is_rejected` (patch `time.time` past 3 days); `test_tokens_of_one_kind_do_not_read_as_the_other` (different salts); `test_a_token_of_a_deleted_user_reads_as_none`. `tests/test_email_pages.py` (anonymous `Client()`, `django_db`): `test_get_unsubscribe_changes_nothing` (user stays enabled); `test_post_unsubscribe_needs_no_login_and_no_csrf_token` (`Client(enforce_csrf_checks=True)` posts without a token: 200, channel off, `email_since is None`); `test_post_unsubscribe_twice_is_harmless`; `test_the_unsubscribe_page_speaks_the_profile_language` (Russian profile, English browser: Russian heading); `test_a_bad_token_is_a_400_page_and_changes_nothing`; `test_get_confirm_changes_nothing`; `test_post_confirm_moves_the_pending_address` (`notify_email` set, `pending_email == ''`); `test_post_confirm_with_a_replaced_pending_address_is_invalid` (profile's `pending_email` now differs from the token's address → 400, `notify_email` unchanged); `test_confirm_post_requires_csrf` (`enforce_csrf_checks=True` without a token → 403); `test_the_pages_work_in_portal_mode_without_headers` (portal middleware set, no `HTTP_X_DISCORD_*`: 200, not 401).
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_email_tokens.py tests/test_email_pages.py -v` → FAIL.
- [ ] **Step 3: Implement** the tokens (`signing.dumps({'u': user.pk}, salt=...)` / `signing.loads(..., max_age=...)`; catch `signing.BadSignature` and a missing user), the views (`@login_not_required`, `@require_http_methods(['GET', 'POST'])`; `@csrf_exempt` only on the unsubscribe view), the templates and the two URLs. Reading a user for a token is a lookup by primary key on `User`, not a scoped model.
- [ ] **Step 4: Run.** The same command → PASS; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: signed unsubscribe and address-confirmation pages that need no session"`.

---

### Task 4: The digest message

**Files:**
- Create: `sources/notifications/digest.py`, `sources/notifications/templates/notifications/email/digest.txt`, `digest.html`
- Test: `tests/test_notification_digest.py`

**Interfaces:**
- Consumes: Task 1 (`channel.absolute_url`, `channel.effective_address`), Task 2 (`display.prepare_rows`, `display.display_name`), Task 3 (`tokens.unsubscribe_token`, URL names `email_unsubscribe`, `notifications`, and the title page URL name `title`, `reverse('title', args=[title.pk])`).
- Produces: `notifications.digest.MAX_ITEMS: int = 50`; `build_digest(user: User, notifications: list[Notification], now: datetime) -> EmailMultiAlternatives` — the message for `effective_address(user)` with: subject `Notifications: N` / `Уведомления: N` style (fixed localized text with the total count, plural-aware via `ngettext`); a plain-text body and an HTML alternative listing at most `MAX_ITEMS` items grouped by local day (`notification.day`, headed "Today", "Yesterday" or a short date like the screen), each with kind label, title name (linked to its page), text line and `old -> new` for date kinds, then "and N more" when cut, the Notifications screen link, and the unsubscribe link; headers `List-Unsubscribe: <https://…/email/unsubscribe/<token>/>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. The whole build runs under `translation.override(profile.interface_language)`; items are ordered newest day first, then `created_at` descending, as on the screen. `now` decides "Today" and "Yesterday" through `moments.local_day`.

- [ ] **Step 1: Write the failing tests.** `test_the_subject_is_fixed_text_and_the_count` (English and Russian; contains no title name); `test_the_body_lists_each_item_in_the_profile_language` (Russian profile: Russian kind labels, title in the metadata language, which may differ from the interface language); `test_links_are_absolute_and_point_to_the_title_and_the_screen`; `test_the_unsubscribe_headers_and_link_share_one_valid_token` (the header URL's token reads back to the user with `unsubscribe_user`); `test_items_are_grouped_by_local_day_with_today_and_yesterday`; `test_more_than_fifty_items_are_cut_with_an_and_n_more_line` (60 items → 50 listed, "10 more" and the screen link present); `test_a_hostile_title_is_escaped_in_html_and_never_in_subject_or_headers` (title name `'<script>x</script>\nBcc: evil@example.com'` as a `TitleTranslation`: HTML has `&lt;script&gt;`; subject has no title text; no header contains a newline-injected line; `message.bcc == []`); `test_the_html_alternative_is_attached_and_the_text_body_has_no_tags`; `test_the_message_goes_to_the_effective_address` (`notify_email` over `user.email`); `test_building_does_not_change_the_active_language` (the language before and after the call is the same); `test_the_query_count_does_not_grow_with_items`.
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_notification_digest.py -v` → import errors.
- [ ] **Step 3: Implement** `build_digest` with Django templates under `notifications/email/` (autoescape on in the HTML template; the text template marks its output with `autoescape off` only for plain text and never for headers). Use `ngettext` for the subject count and for "and N more". Sort and group items in Python from `prepare_rows` output paired with the notifications.
- [ ] **Step 4: Run.** `pytest tests/test_notification_digest.py -v` → PASS; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: build the localised email digest with one-click unsubscribe"`.

---

### Task 5: The sender job

**Files:**
- Create: `sources/notifications/deliver.py`
- Modify: `sources/notifications/tasks.py`
- Test: `tests/test_deliver.py`; extend `tests/test_notification_tasks.py`

**Interfaces:**
- Consumes: Tasks 1 and 4 (`Delivery`, `Profile.email_enabled/email_since`, `channel.effective_address`, `channel.email_configured`, `digest.build_digest`); `Notification`.
- Produces: `notifications.deliver.EXPIRY: timedelta = timedelta(hours=48)`; `RETRY_PAUSES: tuple[timedelta, ...] = (15 min, 1 h, 6 h, 6 h)`; `MAX_ATTEMPTS: int = 5`; `send_deliveries(now: datetime) -> int` (the number of messages sent; does nothing and returns 0 when `email_configured()` is false); `notifications.tasks.send_deliveries_task() -> int` (`@db_periodic_task(crontab(minute='2,17,32,47'))`, calls `send_deliveries(timezone.now())`, logs the count at INFO; name it so it does not shadow the function).

Algorithm (Decisions 3 to 6; the query count must not grow with users or notifications, apart from one send and one update per user): (a) **materialise**: notifications of users with `Profile.email_enabled` and `email_since` set, having `created_at > email_since` and `created_at > now - EXPIRY`, and no `Delivery` for channel email, become `Delivery(status=pending)` through `bulk_create(ignore_conflicts=True)`; users with no effective address are skipped (comment why); (b) **settle**: pending deliveries whose notification is older than `now - EXPIRY` become `expired`; pending ones whose notification has `read_at` set become `skipped`; (c) **claim and send**: due pending deliveries (`next_attempt_at` null or `<= now`) are taken with `select_for_update(skip_locked=True)` inside a transaction, grouped by user, one `build_digest(...)` and `send()` per user; (d) on success: `status=sent`, `sent_at=now`, `attempts` plus one; on any exception: `attempts` plus one, `last_error = f'{type(exc).__name__}: {str(exc)[:150]}'` with the recipient address replaced by `<address>`, `next_attempt_at = now + RETRY_PAUSES[attempts - 1]`, or `failed` at `MAX_ATTEMPTS`; one user's failure never stops the others. Comment on the unscoped reads: system job spanning users. The claim: deliveries are marked `sent` in the same transaction that holds the lock, and a delivery is sent only by the run that holds it; a crash after `send()` and before commit can repeat one mail (at-least-once), which the design accepts; two runs never send the same delivery concurrently.

- [ ] **Step 1: Write the failing tests** (fixed `now = datetime(2026,11,21,12,0,tzinfo=UTC)`; `override_settings(EMAIL_ENABLED=True, PUBLIC_URL='https://m.example')`; read the outbox from `django.core.mail.outbox`; build rows with `Notification.objects.create`): `test_one_digest_per_user_with_all_items` (3 notifications of alice, 1 of bob → 2 messages; alice's has 3 items; all 4 deliveries `sent`); `test_nothing_is_sent_when_email_is_not_configured`; `test_a_user_who_did_not_enable_email_gets_nothing`; `test_nothing_before_email_since` (notification created before `email_since` → no delivery row, no mail); `test_toggling_off_and_on_skips_the_backlog` (`email_since` moved forward: the older notification is not mailed); `test_a_second_run_sends_nothing_new` (rerun: outbox length unchanged); `test_a_notification_merged_after_it_was_mailed_is_not_mailed_again` (mail, then mark the row unread and extend its payload, run again: still one message); `test_a_read_notification_is_skipped` (`status == 'skipped'`, not mailed); `test_an_old_notification_expires` (created 49 h ago, `email_since` older: `expired`, not mailed); `test_a_failed_send_retries_with_backoff` (patch `EmailMultiAlternatives.send` or use a backend that raises: attempts 1 → `next_attempt_at = now + 15 min`; a run before that moment does not retry; at that moment it does; failures 2, 3, 4 give 1 h, 6 h, 6 h; failure 5 sets `failed`); `test_last_error_has_no_address` (the exception text contains the recipient: stored text has `<address>`); `test_one_users_failure_does_not_stop_the_others`; `test_a_user_without_an_address_is_skipped_without_a_delivery`; `test_the_pending_address_is_never_used` (`pending_email` set, `notify_email` empty, `user.email` set: mail goes to `user.email`); `test_overlapping_runs_never_send_twice` (a `django_db(transaction=True)` test: two threads each call `send_deliveries(now)` while a patched `build_digest` waits on a `threading.Barrier(2, timeout=5)` and a `BrokenBarrierError` is tolerated, so the second run, which skips the locked row, simply finishes; assert exactly one message for the user and one `sent` delivery; close each thread's DB connection at the end); `test_the_query_count_does_not_grow_with_users` (materialise and settle for 1 user versus 4 users: the counts differ only by the per-user send and update). `tests/test_notification_tasks.py`: `test_send_deliveries_task_calls_the_sender_with_now_and_returns_the_count` and extend `test_both_tasks_are_registered` to the three tasks.
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_deliver.py tests/test_notification_tasks.py -v` → import errors.
- [ ] **Step 3: Implement** `send_deliveries` per the algorithm and the task wrapper.
- [ ] **Step 4: Run.** The same command → PASS; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: send one email digest per pass with retries"`.

---

### Task 6: The Email block on Settings and the confirmation mail

**Files:**
- Create: `sources/notifications/confirmation.py`, `sources/notifications/templates/notifications/email/confirm.txt`, `confirm.html`
- Modify: `sources/web/views/settings.py`, `sources/web/urls.py`, `sources/web/templates/web/settings.html`, `sources/web/static/web/app.css`
- Test: `tests/test_web_settings_email.py`

**Interfaces:**
- Consumes: Tasks 1 to 3 (`channel`, `tokens.confirmation_token`, URL `email_confirm`), `build_digest`'s language rule.
- Produces: `notifications.confirmation.send_confirmation(user: User, address: str) -> None` (one mail to `address` in the user's interface language, fixed subject, a link built from `absolute_url` of `email_confirm`; text plus HTML). Settings views: `settings_email` (POST `settings/email/`, URL name `settings_email`) handling one form with `action` in `toggle`, `address`, `use_portal`:
  - `toggle`: field `enabled` (checkbox). On: allowed only when `effective_address(user)` is non-empty and the channel is configured, sets `email_enabled=True`, `email_since=now`; with no address the page re-renders with an error "Add an email address first". Off: Decision 3. Saving "on" while already on keeps `email_since`.
  - `address`: field `email` (validated with `forms.EmailField`, max 254). It becomes `pending_email`, the confirmation mail is sent, and the page shows "Waiting for confirmation of <address>". An address equal to the current effective address is a no-op with a note. A failed send shows "We could not send the confirmation. Try again later." and leaves `pending_email` empty.
  - `use_portal`: clears `notify_email` and `pending_email`.
  - The Email block is rendered only when `email_configured()`; when not configured the three actions answer 404. State changes go through POST with CSRF, to the requester's profile only.
  - The Settings text "Email and Telegram notifications are coming later." becomes "Telegram notifications are coming later." (English) with the Russian counterpart.

- [ ] **Step 1: Write the failing tests** (portal and local-mode clients as `tests/test_web_settings.py` builds them; `override_settings(EMAIL_ENABLED=True, PUBLIC_URL=...)`; outbox via `django.core.mail.outbox`): `test_the_email_block_is_absent_when_email_is_not_configured` (and the three actions give 404); `test_the_block_shows_the_effective_address_and_the_switch`; `test_turning_on_sets_email_since_and_turning_off_clears_it`; `test_turning_on_without_any_address_is_refused_with_a_message`; `test_turning_on_again_keeps_email_since`; `test_a_new_address_goes_pending_and_a_confirmation_mail_is_sent_to_it` (mail only to the new address, in the profile language, link points to `/email/confirm/…`, `notify_email` unchanged); `test_the_pending_address_is_shown_as_waiting`; `test_an_invalid_address_is_rejected_without_a_mail`; `test_a_failed_confirmation_send_leaves_nothing_pending` (patch `send_confirmation` to raise); `test_use_portal_address_clears_both_custom_addresses`; `test_another_users_profile_is_never_touched` (post as alice, bob unchanged); `test_the_email_forms_need_csrf`; `test_the_settings_page_keeps_working_when_the_timezone_form_is_used` (regression: the timezone form still saves); `test_the_coming_later_note_mentions_only_telegram`. Confirmation-mail unit tests in the same file or `tests/test_email_pages.py`: `test_the_confirmation_mail_is_localised_and_has_a_fixed_subject`.
- [ ] **Step 2: Run to see them fail.** `pytest tests/test_web_settings_email.py -v` → FAIL.
- [ ] **Step 3: Implement** the view, the form handling (reuse the Settings page's style: one small form per action, a section `Email` with a status line and the controls; touch targets at least 44 px; error and note texts through `{% trans %}`), `send_confirmation`, the templates and the CSS. Keep `_render_page` as the single renderer.
- [ ] **Step 4: Run.** `pytest tests/test_web_settings_email.py tests/test_web_settings.py -v` → PASS; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "feat: let users enable email and confirm another address in Settings"`.

---

### Task 7: Documentation and translation sweep

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`, `sources/locale/ru/LC_MESSAGES/django.po`
- Test: none new; the whole suite and `msgfmt --check`

- [ ] **Step 1: Update the requirements spec** section 3 (`Delivery`; the `Profile` fields `email_enabled`, `email_since`, `notify_email`, `pending_email`) and section 7 (email as built: digest per pass, 50 items, retries 15 min / 1 h / 6 h / 6 h and `failed` at the fifth, `expired` after 48 h, `skipped` when read, opt-in guard, confirmation of a manual address, one-click unsubscribe; Telegram stays plan 4c).
- [ ] **Step 2: Update `README.md`**: the Notifications section (email channel, the Email block in Settings) and the configuration section (`email.*` keys with the `MITAROKU_EMAIL__*` environment names, `public_url`, "email is off unless `email.host` and `public_url` are set").
- [ ] **Step 3: Translation sweep.** Run `python sources/manage.py makemessages -l ru` from the repository root with the env vars the tests set, check that no new `msgid` is untranslated or fuzzy, then restore the generated churn with `git checkout` and add the missing entries by hand (additions only). `msgfmt --check -o /dev/null sources/locale/ru/LC_MESSAGES/django.po` → OK.
- [ ] **Step 4: Run.** `pre-commit run --all-files` → clean; `pytest -q` → all green.
- [ ] **Step 5: Commit.** `git commit -m "docs: document the email channel and the Delivery table; translate the new strings"`.

---

### Task 8: Live pass and browser pass (controller only)

Not delegated; run by the controller in this session after the final review, as in plan 4a. Scratch Postgres database and Redis db 9 only, never the dev database `mitaroku`; the TMDB token is read inside the process and never printed.

- [ ] **Step 1: Mail sink.** Start a throwaway SMTP sink on a free local port from the scratchpad (a short script that accepts and stores messages), and run the app with `MITAROKU_EMAIL__HOST=127.0.0.1`, the sink's port, `MITAROKU_EMAIL__STARTTLS=false`, `MITAROKU_EMAIL__FROM_ADDRESS`, `MITAROKU_PUBLIC_URL=http://127.0.0.1:<port>`.
- [ ] **Step 2: Live pass.** With real titles: enable email through Settings, create a due notification (the 4a live-pass recipe), run `send_deliveries` once through `call_local`, check the stored message (subject, both parts, absolute links, headers), POST its `List-Unsubscribe` URL with `curl`, check the channel is off, run the sender again and see no new mail; confirm a second address through the confirmation link; stop the sink and see a retry row with `next_attempt_at`.
- [ ] **Step 3: Browser pass** (Playwright in the scratchpad venv, 390 and 960 px, light and dark, English and Russian): the Email block states (off, on, pending, error), the unsubscribe and confirm pages (GET shows a button, POST acts), no horizontal scroll, controls at least 44 px. Record defects, fix them through the normal loop.
- [ ] **Step 4: Clean up** the scratch database, Redis db 9, the sink and the server, without `pkill -f` patterns that match this shell.

## Self-review notes

- **Spec coverage:** goals 1 to 3 → Tasks 1, 5 (core), 3, 4, 6 (email), 2, 3, 4, 6 (localisation); opt-in guard → Tasks 5 and 6; confirmation of a manual address → Tasks 3 and 6; one-click unsubscribe → Tasks 3 and 4; retries, expiry, skip → Task 5; config → Task 1; docs → Task 7.
- **Type consistency:** `Row`, `prepare_rows`, `display_name` are defined in Task 2 and used in Task 4; `unsubscribe_token`, `unsubscribe_user`, `confirmation_token`, `confirmation_target` are defined in Task 3 and used in Tasks 4 and 6; `effective_address`, `email_configured`, `absolute_url` are defined in Task 1 and used in Tasks 4 to 6; `send_deliveries` (function) and `send_deliveries_task` (Huey task) are distinct names.
- **Proportion:** tests are named with their assertions; algorithm text appears only where the signature and tests leave a choice (Task 5).
