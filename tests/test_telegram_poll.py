import logging
from datetime import UTC, datetime

import httpx
import pytest
from accounts.models import Profile
from django.test import override_settings
from notifications import telegram, telegram_poll
from notifications.errors import Unreachable
from notifications.telegram_link import issue_link_token
from notifications.telegram_poll import poll_updates
from providers.clients import connect_redis

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
OFFSET_KEY = 'test:telegram:offset'
LOCK_KEY = 'test:telegram:poll'

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def test_keys(monkeypatch, redis_client):
    """Keep the poll's Redis keys under the prefix the fixture cleans up."""
    monkeypatch.setattr(telegram_poll, 'KEY_PREFIX', 'test:telegram:')


def chat_of(user) -> int | None:
    """Return the chat id the account is connected to."""
    return Profile.objects.get(user=user).telegram_chat_id


def test_an_unconfigured_telegram_polls_nothing(bot, redis_client):
    with override_settings(TELEGRAM_ENABLED=False):
        assert poll_updates(NOW) == 0

    assert bot.calls == []
    assert redis_client.get(OFFSET_KEY) is None


def test_the_poll_handles_updates_and_remembers_the_offset(alice, bot, redis_client):
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=10)

    assert poll_updates(NOW) == 1

    assert chat_of(alice) == 42
    assert redis_client.get(OFFSET_KEY) == '11'
    assert poll_updates(NOW) == 0
    assert bot.calls[-1] == ('getUpdates', {'offset': 11, 'timeout': 0})


def test_the_first_poll_asks_without_an_offset(bot):
    poll_updates(NOW)

    assert bot.calls == [('getUpdates', {'timeout': 0})]


def test_a_lost_offset_replays_harmlessly(alice, bot, redis_client):
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=10)
    assert poll_updates(NOW) == 1
    redis_client.delete(OFFSET_KEY)

    assert poll_updates(NOW) == 1

    assert chat_of(alice) == 42
    assert Profile.objects.filter(telegram_chat_id=42).count() == 1
    assert bot.sent[-1]['text'].startswith('Connected.')
    assert redis_client.get(OFFSET_KEY) == '11'


def test_a_connected_reply_that_timed_out_is_answered_on_the_replay(
    alice, bot, redis_client, monkeypatch
):
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=10)
    real = telegram.BotApi.send_message

    def timed_out(self, chat_id, text):
        raise Unreachable('ReadTimeout')

    monkeypatch.setattr(telegram.BotApi, 'send_message', timed_out)
    assert poll_updates(NOW) == 0
    monkeypatch.setattr(telegram.BotApi, 'send_message', real)
    # The connection was committed before the reply failed.
    assert chat_of(alice) == 42
    assert redis_client.get(OFFSET_KEY) is None

    assert poll_updates(NOW) == 1

    assert chat_of(alice) == 42
    assert Profile.objects.get(user=alice).telegram_enabled is True
    assert [m['chat_id'] for m in bot.sent] == [42]
    assert bot.sent[0]['text'].startswith('Connected.')
    assert 'expired' not in bot.sent[0]['text']
    assert redis_client.get(OFFSET_KEY) == '11'


def test_a_malformed_update_is_skipped_and_the_next_one_is_handled(
    alice, bot, redis_client
):
    bot.updates.append({'update_id': 10, 'message': 'not a message'})
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=11)

    assert poll_updates(NOW) == 2

    assert chat_of(alice) == 42
    assert redis_client.get(OFFSET_KEY) == '12'


def test_an_update_without_an_id_is_skipped_without_moving_the_offset(
    bot, redis_client
):
    bot.updates.append({'message': {}})

    assert poll_updates(NOW) == 0

    assert redis_client.get(OFFSET_KEY) is None


def test_an_unexpected_error_in_one_update_does_not_wedge_the_poll(
    alice, bot, redis_client, monkeypatch, caplog
):
    real = telegram_poll.handle_update

    def flaky(update, api, now):
        if update['update_id'] == 10:
            raise ValueError('secret message text')
        real(update, api, now)

    monkeypatch.setattr(telegram_poll, 'handle_update', flaky)
    bot.say(41, 'hello', update_id=10)
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=11)

    assert poll_updates(NOW) == 1

    assert chat_of(alice) == 42
    assert redis_client.get(OFFSET_KEY) == '12'
    assert 'ValueError' in caplog.text
    assert 'secret message text' not in caplog.text


def test_an_unreachable_telegram_mid_batch_keeps_the_offset_at_the_last_handled_update(
    alice, bot, redis_client, monkeypatch
):
    real = telegram_poll.handle_update

    def cut_off(update, api, now):
        if update['update_id'] == 11:
            raise Unreachable('ConnectError')
        real(update, api, now)

    monkeypatch.setattr(telegram_poll, 'handle_update', cut_off)
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=10)
    bot.say(43, 'hello', update_id=11)
    bot.say(44, 'hello', update_id=12)

    assert poll_updates(NOW) == 1

    assert chat_of(alice) == 42
    assert redis_client.get(OFFSET_KEY) == '11'


def test_a_conflict_is_logged_without_the_token_and_changes_nothing(
    alice, bot, redis_client, caplog
):
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=10)
    bot.fail(
        'getUpdates',
        409,
        'Conflict: terminated by other getUpdates request; '
        'make sure that only one bot instance is running',
    )

    assert poll_updates(NOW) == 0

    assert bot.token not in caplog.text
    assert '409' in caplog.text
    assert 'another consumer or a webhook is set on this bot' in caplog.text
    assert 'terminated by other' not in caplog.text
    assert [r.levelno for r in caplog.records if '409' in r.getMessage()] == [
        logging.ERROR
    ]
    assert chat_of(alice) is None
    assert redis_client.get(OFFSET_KEY) is None


def test_another_failed_answer_logs_only_the_class_and_the_code(
    bot, redis_client, caplog
):
    bot.fail('getUpdates', 502, 'Bad Gateway echoing /start secret-link-token')

    assert poll_updates(NOW) == 0

    assert 'TelegramError' in caplog.text
    assert '502' in caplog.text
    assert 'secret-link-token' not in caplog.text
    assert 'another consumer' not in caplog.text


@pytest.mark.parametrize(
    ('status', 'retry_after', 'name'),
    [(429, 30, 'RetryAfter'), (403, None, 'PermanentFailure')],
)
def test_a_429_or_a_403_from_getupdates_is_logged_and_returns_zero(
    bot, redis_client, caplog, status, retry_after, name
):
    bot.fail('getUpdates', status, 'echo secret-link-token', retry_after=retry_after)

    assert poll_updates(NOW) == 0

    assert name in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert bot.token not in caplog.text
    assert 'secret-link-token' not in caplog.text
    assert redis_client.get(OFFSET_KEY) is None
    assert redis_client.get(LOCK_KEY) is None


def test_a_decoding_error_from_getupdates_is_logged_and_returns_zero(
    bot, redis_client, caplog, monkeypatch
):
    def broken_gzip(request):
        raise httpx.DecodingError(f'bad gzip body of {request.url}')

    monkeypatch.setattr(
        'notifications.telegram.make_client',
        lambda: httpx.Client(transport=httpx.MockTransport(broken_gzip)),
    )

    assert poll_updates(NOW) == 0

    assert 'TelegramError' in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert bot.token not in caplog.text
    assert 'bad gzip' not in caplog.text
    assert redis_client.get(OFFSET_KEY) is None
    assert redis_client.get(LOCK_KEY) is None


def test_getupdates_unreachable_is_logged_and_returns_zero(bot, redis_client, caplog):
    bot.down = True

    assert poll_updates(NOW) == 0

    assert 'Unreachable' in caplog.text
    assert bot.token not in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert redis_client.get(OFFSET_KEY) is None


def test_a_held_lock_skips_the_poll(bot, redis_client):
    lock = connect_redis().lock(LOCK_KEY, timeout=60)
    assert lock.acquire(blocking=False)
    try:
        assert poll_updates(NOW) == 0
        assert bot.calls == []
    finally:
        lock.release()


def test_the_lock_is_released_after_a_poll(bot, redis_client):
    poll_updates(NOW)

    assert redis_client.get(LOCK_KEY) is None


def test_the_lock_is_released_when_the_poll_fails(bot, redis_client):
    bot.down = True

    poll_updates(NOW)

    assert redis_client.get(LOCK_KEY) is None


def test_a_lock_that_expired_during_the_poll_does_not_break_the_release(
    alice, bot, redis_client, monkeypatch
):
    real = telegram_poll.handle_update

    def slow(update, api, now):
        # The lock's 55 s run out while the update is handled.
        redis_client.delete(LOCK_KEY)
        real(update, api, now)

    monkeypatch.setattr(telegram_poll, 'handle_update', slow)
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=10)

    assert poll_updates(NOW) == 1
    assert redis_client.get(OFFSET_KEY) == '11'


def test_one_client_serves_the_whole_poll(alice, bot):
    token = issue_link_token(alice, NOW)
    bot.say(42, f'/start {token}', update_id=10)
    bot.say(43, 'hello', update_id=11)

    poll_updates(NOW)

    assert len(bot.clients) == 1
    assert bot.clients[0].is_closed
