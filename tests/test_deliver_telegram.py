import logging
from datetime import UTC, datetime, timedelta

import pytest
from accounts.models import Profile
from catalog.models import Title
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import override_settings
from notifications import channels, deliver
from notifications.deliver import MAX_ATTEMPTS, RETRY_PAUSES, send_deliveries
from notifications.models import Delivery, Notification

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
SINCE = NOW - timedelta(days=10)

Status = Delivery.Status
Channel = Delivery.Channel
FORBIDDEN = 'Forbidden: bot was blocked by the user'


def connect(user, chat_id: int | None = 42, since: datetime = SINCE):
    """Turn the Telegram channel on for ``user``, sending to ``chat_id``."""
    profile = user.profile
    profile.telegram_enabled = True
    profile.telegram_since = since
    profile.telegram_chat_id = chat_id
    profile.save()
    return user


def enable_email(user, since: datetime = SINCE):
    """Turn the email channel on for ``user``."""
    user.email = f'{user.username}@example.com'
    user.save()
    profile = user.profile
    profile.email_enabled = True
    profile.email_since = since
    profile.save()
    return user


def notify(user, name: str = 'Show', age: timedelta = HOUR, read: bool = False):
    """Create a notification about a new title, ``age`` old at ``NOW``."""
    title = Title.objects.create(kind=Title.Kind.TV, original_title=name)
    created = NOW - age
    return Notification.objects.create(
        user=user,
        kind=Notification.Kind.EPISODE_AIRED,
        title=title,
        day=created.date(),
        dedupe_key=f'episode_aired:{title.pk}:{created.date().isoformat()}',
        created_at=created,
        read_at=NOW if read else None,
    )


def deliveries(user, channel: str | None = Channel.TELEGRAM) -> list[Delivery]:
    """Return the user's deliveries of ``channel`` (all when None), oldest first."""
    rows = Delivery.objects.for_user(user).order_by('pk')
    return list(rows if channel is None else rows.filter(channel=channel))


def test_one_message_per_user_carries_all_items(alice, bot, caplog):
    connect(alice)
    notify(alice, 'A')
    notify(alice, 'B')

    with caplog.at_level(logging.DEBUG):
        assert send_deliveries(NOW) == 1

    (sent,) = bot.sent
    assert sent['chat_id'] == 42
    assert sent['text'].count('/titles/') == 2
    assert sent['parse_mode'] == 'HTML'
    assert sent['link_preview_options'] == {'is_disabled': True}
    rows = deliveries(alice)
    assert [row.status for row in rows] == [Status.SENT] * 2
    assert {(row.sent_at, row.attempts) for row in rows} == {(NOW, 1)}
    assert send_deliveries(NOW + HOUR) == 0
    assert len(bot.sent) == 1
    assert bot.token not in caplog.text


def test_the_client_is_closed_after_a_send_and_after_a_failure(alice, bot):
    connect(alice)
    notify(alice)

    send_deliveries(NOW)
    assert len(bot.clients) == 1
    assert bot.clients[0].is_closed

    bot.fail('sendMessage', 500, 'oops')
    notify(alice, 'B')
    send_deliveries(NOW + 2 * HOUR)
    assert len(bot.clients) == 2
    assert all(client.is_closed for client in bot.clients)


def test_nothing_older_than_the_switch_on_is_sent(alice, bot):
    connect(alice, since=NOW - HOUR)
    notify(alice, age=2 * HOUR)

    assert send_deliveries(NOW) == 0

    assert bot.calls == []
    assert Delivery.objects.count() == 0


def test_a_user_without_a_chat_gets_no_rows(alice, bot):
    connect(alice, chat_id=None)
    notify(alice)

    assert send_deliveries(NOW) == 0

    assert bot.calls == []
    assert Delivery.objects.count() == 0


def test_email_and_telegram_each_get_their_own_row(alice, bot):
    enable_email(alice)
    connect(alice)
    notify(alice, 'A')
    notify(alice, 'B')

    with override_settings(EMAIL_ENABLED=True, PUBLIC_URL='https://m.example'):
        assert send_deliveries(NOW) == 2

    assert len(mail.outbox) == 1
    assert len(bot.sent) == 1
    rows = deliveries(alice, None)
    assert len(rows) == 4
    assert {(row.channel, row.status) for row in rows} == {
        (Channel.EMAIL, Status.SENT),
        (Channel.TELEGRAM, Status.SENT),
    }


def test_a_channel_makes_rows_only_for_its_own_switch(alice, bob, bot):
    enable_email(alice)
    connect(bob)
    notify(alice, 'A')
    notify(bob, 'B')

    with override_settings(EMAIL_ENABLED=True, PUBLIC_URL='https://m.example'):
        assert send_deliveries(NOW) == 2

    assert [row.channel for row in deliveries(alice, None)] == [Channel.EMAIL]
    assert [row.channel for row in deliveries(bob, None)] == [Channel.TELEGRAM]


def test_a_delivery_of_the_other_channel_does_not_block_a_row(alice, bot):
    connect(alice)
    notification = notify(alice)
    Delivery.objects.create(
        user=alice, notification=notification, channel=Channel.EMAIL
    )

    deliver._materialise(NOW, channels.TELEGRAM)

    assert [row.channel for row in deliveries(alice, None)] == [
        Channel.EMAIL,
        Channel.TELEGRAM,
    ]
    deliver._materialise(NOW, channels.TELEGRAM)
    assert Delivery.objects.count() == 2


def test_a_blocked_bot_switches_the_channel_off(alice, bot):
    connect(alice)
    notify(alice)
    notify(alice, 'B')
    bot.fail('sendMessage', 403, FORBIDDEN)

    assert send_deliveries(NOW) == 0

    profile = Profile.objects.get(user=alice)
    assert (
        profile.telegram_enabled,
        profile.telegram_since,
        profile.telegram_chat_id,
        profile.telegram_blocked,
    ) == (False, None, None, True)
    rows = deliveries(alice)
    assert [row.status for row in rows] == [Status.SKIPPED] * 2
    assert {row.attempts for row in rows} == {0}
    assert all('bot was blocked' in row.last_error for row in rows)
    bot.fail('sendMessage', None)
    assert send_deliveries(NOW + HOUR) == 0
    assert bot.sent == []


def test_a_blocked_bot_leaves_the_pending_email_alone(alice, bot):
    enable_email(alice)
    connect(alice)
    notification = notify(alice)
    # An email delivery that is not due yet stays pending through the pass.
    Delivery.objects.create(
        user=alice,
        notification=notification,
        channel=Channel.EMAIL,
        next_attempt_at=NOW + HOUR,
    )
    bot.fail('sendMessage', 403, FORBIDDEN)

    with override_settings(EMAIL_ENABLED=True, PUBLIC_URL='https://m.example'):
        send_deliveries(NOW)

    (email,) = deliveries(alice, Channel.EMAIL)
    assert (email.status, email.attempts) == (Status.PENDING, 0)
    (telegram,) = deliveries(alice)
    assert telegram.status == Status.SKIPPED
    profile = Profile.objects.get(user=alice)
    assert profile.email_enabled is True
    assert profile.telegram_enabled is False


def test_a_429_is_honoured(alice, bot):
    connect(alice)
    notify(alice)
    bot.fail('sendMessage', 429, 'Too Many Requests', retry_after=3600)

    assert send_deliveries(NOW) == 0

    (row,) = deliveries(alice)
    assert (row.status, row.attempts) == (Status.PENDING, 1)
    assert row.next_attempt_at == NOW + HOUR


@pytest.mark.parametrize('body', ['not json', '', '{"ok": false}'])
def test_an_answer_that_is_not_a_clean_success_is_retried_not_sent(alice, bot, body):
    connect(alice)
    notify(alice)
    bot.garbage = body

    assert send_deliveries(NOW) == 0

    (row,) = deliveries(alice)
    assert (row.status, row.attempts) == (Status.PENDING, 1)
    assert row.next_attempt_at == NOW + RETRY_PAUSES[0]
    assert bot.sent == []


def test_the_fifth_failure_marks_failed(alice, bot):
    connect(alice)
    notify(alice)
    bot.fail('sendMessage', 500, 'Internal Server Error')

    now = NOW
    for attempt in range(1, MAX_ATTEMPTS):
        assert send_deliveries(now) == 0
        (row,) = deliveries(alice)
        assert (row.status, row.attempts) == (Status.PENDING, attempt)
        now = row.next_attempt_at
    assert send_deliveries(now) == 0

    (row,) = deliveries(alice)
    assert (row.status, row.attempts) == (Status.FAILED, MAX_ATTEMPTS)
    calls = len(bot.calls)
    assert send_deliveries(now + 100 * HOUR) == 0
    assert len(bot.calls) == calls


def test_an_unreachable_telegram_stops_the_pass_and_hides_the_token(
    alice, bob, bot, caplog
):
    connect(alice, chat_id=42)
    connect(bob, chat_id=43)
    notify(alice)
    notify(bob)
    bot.down = True

    with caplog.at_level(logging.DEBUG):
        assert send_deliveries(NOW) == 0

    assert len(bot.calls) == 1
    (first,) = deliveries(alice)
    assert (first.status, first.attempts) == (Status.PENDING, 1)
    assert first.last_error.startswith('Unreachable: ConnectError')
    assert bot.token not in first.last_error
    assert bot.token not in caplog.text
    (second,) = deliveries(bob)
    assert (second.status, second.attempts) == (Status.PENDING, 0)


def test_an_unreachable_email_server_does_not_stop_telegram(
    alice, bob, bot, monkeypatch
):
    enable_email(alice)
    connect(bob)
    notify(alice, 'A')
    notify(bob, 'B')

    def refuse(self, fail_silently=False):
        raise ConnectionRefusedError('refused')

    monkeypatch.setattr(EmailMultiAlternatives, 'send', refuse)

    with override_settings(EMAIL_ENABLED=True, PUBLIC_URL='https://m.example'):
        assert send_deliveries(NOW) == 1

    (mailed,) = deliveries(alice, Channel.EMAIL)
    assert mailed.status == Status.PENDING
    assert mailed.last_error.startswith('Unreachable')
    assert len(bot.sent) == 1
    assert [row.status for row in deliveries(bob)] == [Status.SENT]


def test_a_failure_text_never_holds_the_token(alice, bot, caplog):
    connect(alice)
    notify(alice)
    bot.fail('sendMessage', 500, f'broken for bot{bot.token} today')

    with caplog.at_level(logging.DEBUG):
        send_deliveries(NOW)

    (row,) = deliveries(alice)
    assert bot.token not in row.last_error
    assert 'broken for bot<token>' in row.last_error
    assert '<address>' not in row.last_error
    assert bot.token not in caplog.text


def test_an_unconfigured_telegram_is_skipped(alice, bot):
    connect(alice)
    notify(alice)

    with override_settings(TELEGRAM_ENABLED=False):
        assert send_deliveries(NOW) == 0

    assert bot.calls == []
    assert Delivery.objects.count() == 0


def test_a_read_notification_is_skipped_not_sent(alice, bot):
    connect(alice)
    notify(alice, read=True)

    assert send_deliveries(NOW) == 0

    assert bot.calls == []
    (row,) = deliveries(alice)
    assert row.status == Status.SKIPPED


def test_disable_telegram_switches_the_channel_off(alice):
    connect(alice)

    channels.disable_telegram(alice)

    profile = Profile.objects.get(user=alice)
    assert profile.telegram_enabled is False
    assert profile.telegram_since is None
    assert profile.telegram_chat_id is None
    assert profile.telegram_blocked is True


def test_the_error_text_uses_the_placeholder_of_the_channel():
    error = RuntimeError('bad 123:secret url')

    assert deliver._error_text(error, '123:SECRET', '<token>') == (
        'RuntimeError: bad <token> url'
    )
    assert deliver._error_text(error, '123:SECRET') == 'RuntimeError: bad <address> url'


def test_an_empty_secret_scrubs_nothing():
    assert deliver._error_text(RuntimeError('abc'), '', '<token>') == (
        'RuntimeError: abc'
    )
