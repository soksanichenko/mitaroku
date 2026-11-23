import logging

import httpx
import pytest
from notifications import telegram
from notifications.errors import PermanentFailure, RetryAfter, Unreachable
from notifications.telegram import BotApi, TelegramError

ANSWERS = [
    (403, 'Forbidden: bot was blocked by the user', PermanentFailure),
    (403, 'Forbidden: user is deactivated', PermanentFailure),
    (400, 'Bad Request: chat not found', PermanentFailure),
    (400, 'Bad Request: message is too long', TelegramError),
    (401, 'Unauthorized', TelegramError),
    (500, 'Internal Server Error', TelegramError),
]


def test_send_message_posts_the_text_with_the_html_options(bot):
    telegram.bot_api().send_message(42, '<b>Hi</b>')

    assert bot.sent == [
        {
            'chat_id': 42,
            'text': '<b>Hi</b>',
            'parse_mode': 'HTML',
            'link_preview_options': {'is_disabled': True},
        }
    ]


def test_get_updates_passes_the_offset_and_returns_the_newer_updates(bot):
    bot.say(1, 'a', update_id=10)
    bot.say(1, 'b', update_id=11)

    updates = telegram.bot_api().get_updates(11)

    assert [u['update_id'] for u in updates] == [11]
    assert bot.calls[-1] == ('getUpdates', {'offset': 11, 'timeout': 0})


def test_get_updates_without_an_offset_omits_the_key(bot):
    bot.say(1, 'a')

    updates = telegram.bot_api().get_updates(None, timeout=5)

    assert len(updates) == 1
    assert bot.calls[-1] == ('getUpdates', {'timeout': 5})


def test_say_numbers_the_updates_and_builds_a_real_message(bot):
    first = bot.say(7, 'hi', language_code='ru')
    second = bot.say(7, 'again', chat_type='group')

    assert second['update_id'] == first['update_id'] + 1
    assert first == {
        'update_id': first['update_id'],
        'message': {
            'message_id': first['update_id'],
            'chat': {'id': 7, 'type': 'private'},
            'from': {'id': 7, 'language_code': 'ru'},
            'text': 'hi',
        },
    }
    assert second['message']['chat']['type'] == 'group'


@pytest.mark.parametrize(('status', 'description', 'expected'), ANSWERS)
def test_error_answers_are_classified(bot, status, description, expected):
    bot.fail('sendMessage', status, description)

    with pytest.raises(expected) as raised:
        telegram.bot_api().send_message(1, 'x')

    assert type(raised.value) is expected
    if expected is TelegramError:
        assert raised.value.code == status


def test_a_429_carries_the_retry_after_seconds(bot):
    bot.fail('sendMessage', 429, 'Too Many Requests: retry after 17', retry_after=17)

    with pytest.raises(RetryAfter) as raised:
        telegram.bot_api().send_message(1, 'x')

    assert raised.value.seconds == 17


def test_a_429_without_the_seconds_is_a_plain_error(bot):
    bot.fail('sendMessage', 429, 'Too Many Requests')

    with pytest.raises(TelegramError) as raised:
        telegram.bot_api().send_message(1, 'x')

    assert raised.value.code == 429


def test_a_conflict_carries_its_code(bot):
    bot.fail('getUpdates', 409, 'Conflict: terminated by other getUpdates request')

    with pytest.raises(TelegramError) as raised:
        telegram.bot_api().get_updates(0)

    assert raised.value.code == 409


def test_an_error_without_a_json_body_falls_back_to_the_http_status(bot):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(502, text='<html>'))
    )

    with pytest.raises(TelegramError) as raised:
        BotApi(bot.token, client).send_message(1, 'x')

    assert raised.value.code == 502


@pytest.mark.parametrize(
    'body', ['not json', '', '[]', '{"ok": true}', '{"result": []}']
)
def test_answers_that_are_not_a_clean_success_are_errors(bot, body):
    bot.garbage = body

    if body == '{"ok": true}':
        telegram.bot_api().send_message(1, 'x')
    else:
        with pytest.raises(TelegramError):
            telegram.bot_api().send_message(1, 'x')
    with pytest.raises(TelegramError):
        telegram.bot_api().get_updates(0)


def test_a_transport_error_becomes_unreachable_without_the_url_or_the_token(bot):
    bot.down = True

    with pytest.raises(Unreachable) as raised:
        telegram.bot_api().send_message(1, 'x')

    assert bot.token not in str(raised.value)
    assert 'api.telegram.org' not in str(raised.value)
    assert str(raised.value) == 'ConnectError'


def test_a_timeout_is_unreachable(bot):
    def handler(request):
        raise httpx.ReadTimeout(f'timed out: {request.url}')

    client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(Unreachable) as raised:
        BotApi(bot.token, client).get_updates(0)

    assert str(raised.value) == 'ReadTimeout'
    assert bot.token not in repr(raised.value)


def test_no_error_text_contains_the_token(bot):
    errors = []
    for status, description, _ in ANSWERS:
        bot.fail('sendMessage', status, description)
        with pytest.raises(Exception) as raised:
            telegram.bot_api().send_message(1, 'x')
        errors.append(raised.value)
    bot.fail('sendMessage', 429, 'Too Many Requests', retry_after=3)
    with pytest.raises(RetryAfter) as raised:
        telegram.bot_api().send_message(1, 'x')
    errors.append(raised.value)
    bot.fail('sendMessage', None)
    bot.down = True
    with pytest.raises(Unreachable) as raised:
        telegram.bot_api().send_message(1, 'x')
    errors.append(raised.value)
    bot.down = False
    bot.garbage = 'not json'
    with pytest.raises(TelegramError) as raised:
        telegram.bot_api().send_message(1, 'x')
    errors.append(raised.value)

    assert len(errors) == len(ANSWERS) + 3
    for error in errors:
        assert bot.token not in str(error)
        assert bot.token not in repr(error)
        assert bot.token.split(':')[0] not in str(error)
        assert error.__cause__ is None
        assert error.__context__ is None or error.__suppress_context__


def test_make_client_times_out_after_ten_seconds():
    with telegram.make_client() as client:
        assert client.timeout == httpx.Timeout(10.0)


def test_a_wrong_token_is_a_401(bot):
    with pytest.raises(TelegramError) as raised:
        BotApi('999:WRONG', bot.client()).send_message(1, 'x')

    assert raised.value.code == 401
    assert bot.sent == []


@pytest.mark.parametrize('level', [logging.INFO, logging.DEBUG])
def test_the_token_never_reaches_the_logs(bot, caplog, level):
    bot.say(1, 'a')

    with caplog.at_level(level):
        telegram.bot_api().send_message(1, 'x')
        telegram.bot_api().get_updates(0)
        logging.getLogger('httpx').info('HTTP Request: GET https://other.example/x')

    assert bot.token not in caplog.text
    assert 'api.telegram.org' not in caplog.text
    assert 'https://other.example/x' in caplog.text
