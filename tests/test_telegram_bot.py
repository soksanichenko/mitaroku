import logging
from datetime import UTC, datetime

import pytest
from accounts.models import Profile
from notifications import telegram
from notifications.errors import Unreachable
from notifications.telegram_bot import handle_update
from notifications.telegram_link import Outcome, connect, disconnect, issue_link_token

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
SETTINGS_URL = 'https://m.example/settings/'

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(bot):
    """Return a Bot API wrapper bound to the fake bot."""
    with telegram.bot_api() as api:
        yield api


def profile_of(user) -> Profile:
    """Return the freshly loaded profile of ``user``."""
    return Profile.objects.get(user=user)


def russian(user) -> None:
    """Switch the interface of ``user`` to Russian."""
    profile = user.profile
    profile.interface_language = 'ru'
    profile.save()


def test_start_with_a_token_connects_and_answers_in_the_account_language(
    alice, bot, api
):
    russian(alice)
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'/start {token}'), api, NOW)

    assert profile_of(alice).telegram_chat_id == 42
    assert len(bot.sent) == 1
    assert bot.sent[0]['chat_id'] == 42
    assert bot.sent[0]['text'] == (
        'Подключено. Новые уведомления mitaroku будут приходить в этот чат.'
    )


def test_the_connected_reply_is_english_for_an_english_account(alice, bot, api):
    token = issue_link_token(alice, NOW)

    # The account language wins over the Telegram language code.
    handle_update(bot.say(42, f'/start {token}', language_code='ru'), api, NOW)

    assert bot.sent[0]['text'] == (
        'Connected. New mitaroku notifications will arrive in this chat.'
    )


def test_the_botname_suffix_is_accepted(alice, bot, api):
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'/start@mitaroku_bot {token}'), api, NOW)

    assert profile_of(alice).telegram_chat_id == 42


def test_surrounding_whitespace_around_the_token_is_ignored(alice, bot, api):
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'/start \t {token}\n '), api, NOW)

    assert profile_of(alice).telegram_chat_id == 42


@pytest.mark.parametrize('command', ['/START', '/Start', 'start', '/starting'])
def test_only_the_exact_start_command_connects(alice, bot, api, command):
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'{command} {token}'), api, NOW)

    assert profile_of(alice).telegram_chat_id is None
    assert 'To connect Telegram' in bot.sent[0]['text']
    assert connect(token, 42, NOW)[0] is Outcome.CONNECTED


def test_a_token_with_embedded_spaces_is_invalid(alice, bot, api):
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'/start {token} extra'), api, NOW)

    assert profile_of(alice).telegram_chat_id is None
    assert 'expired or was already used' in bot.sent[0]['text']


def test_a_bare_start_changes_nothing_and_points_to_settings(alice, bot, api):
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, '/start'), api, NOW)

    assert profile_of(alice).telegram_chat_id is None
    assert bot.sent == [
        {
            'chat_id': 42,
            'text': (
                'To connect Telegram, open mitaroku Settings and press '
                f'"Connect Telegram": {SETTINGS_URL}'
            ),
            'parse_mode': 'HTML',
            'link_preview_options': {'is_disabled': True},
        }
    ]
    assert connect(token, 42, NOW)[0] is Outcome.CONNECTED


def test_the_reply_language_follows_the_telegram_language_code(bot, api):
    handle_update(bot.say(42, '/start', language_code='ru'), api, NOW)
    handle_update(bot.say(43, '/start', language_code='ru-RU'), api, NOW)
    handle_update(bot.say(44, '/start', language_code='de'), api, NOW)
    handle_update(bot.say(45, '/start'), api, NOW)

    texts = [message['text'] for message in bot.sent]
    assert 'Чтобы подключить Telegram' in texts[0]
    assert 'Чтобы подключить Telegram' in texts[1]
    assert 'To connect Telegram' in texts[2]
    assert 'To connect Telegram' in texts[3]


def test_a_missing_language_code_means_english(bot, api):
    update = bot.say(42, '/start')
    del update['message']['from']

    handle_update(update, api, NOW)

    assert 'To connect Telegram' in bot.sent[0]['text']


def test_an_unknown_token_gets_the_expired_reply(alice, bot, api):
    handle_update(bot.say(42, '/start nonsense'), api, NOW)

    assert bot.sent[0]['text'] == (
        'That link has expired or was already used. Press "Connect Telegram" in '
        f'mitaroku Settings to get a new one: {SETTINGS_URL}'
    )


def test_the_expired_reply_in_russian(bot, api):
    handle_update(bot.say(42, '/start nonsense', language_code='ru'), api, NOW)

    assert 'Ссылка устарела или уже использована' in bot.sent[0]['text']
    assert bot.sent[0]['text'].endswith(SETTINGS_URL)


def test_an_oversized_or_odd_token_gets_the_expired_reply(alice, bot, api):
    handle_update(bot.say(42, '/start ' + 'a' * 500), api, NOW)
    handle_update(bot.say(42, '/start <b>&'), api, NOW)

    assert [m['text'].startswith('That link has expired') for m in bot.sent] == [
        True
    ] * 2


def test_a_used_token_cannot_connect_a_second_chat(alice, bot, api):
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'/start {token}'), api, NOW)
    handle_update(bot.say(77, f'/start {token}'), api, NOW)

    assert profile_of(alice).telegram_chat_id == 42
    assert bot.sent[-1]['chat_id'] == 77
    assert 'expired or was already used' in bot.sent[-1]['text']


def test_a_replayed_start_from_a_connected_chat_gets_the_connected_reply(
    alice, bot, api
):
    russian(alice)
    token = issue_link_token(alice, NOW)
    handle_update(bot.say(42, f'/start {token}'), api, NOW)
    bot.sent.clear()

    # The account's language wins over the Telegram language code.
    handle_update(bot.say(42, f'/start {token}', language_code='en'), api, NOW)

    assert [m['chat_id'] for m in bot.sent] == [42]
    assert bot.sent[0]['text'] == (
        'Подключено. Новые уведомления mitaroku будут приходить в этот чат.'
    )
    assert profile_of(alice).telegram_chat_id == 42


def test_an_unknown_token_from_a_connected_chat_gets_the_connected_reply(
    alice, bot, api
):
    handle_update(bot.say(42, f'/start {issue_link_token(alice, NOW)}'), api, NOW)
    bot.sent.clear()

    handle_update(bot.say(42, '/start nonsense'), api, NOW)

    assert bot.sent[0]['text'].startswith('Connected.')


@pytest.mark.parametrize('chat_type', ['group', 'supergroup', 'channel'])
def test_a_group_message_is_ignored_and_the_token_stays_valid(
    alice, bot, api, chat_type
):
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(-100, f'/start {token}', chat_type=chat_type), api, NOW)

    assert bot.sent == []
    assert profile_of(alice).telegram_chat_id is None
    assert connect(token, 42, NOW)[0] is Outcome.CONNECTED


def test_other_updates_and_malformed_shapes_are_ignored(bot, api):
    private = bot.say(42, '/start')['message']
    updates = [
        {'update_id': 1, 'edited_message': private},
        {'update_id': 2, 'my_chat_member': {'chat': {'id': 42, 'type': 'private'}}},
        {'update_id': 3, 'message': {**private, 'text': None}},
        {'update_id': 4, 'message': {k: v for k, v in private.items() if k != 'text'}},
        {'update_id': 5, 'message': 'x'},
        {'update_id': 6, 'message': {**private, 'chat': None}},
        {'update_id': 7, 'message': {k: v for k, v in private.items() if k != 'chat'}},
        {
            'update_id': 8,
            'message': {**private, 'chat': {'id': '42', 'type': 'private'}},
        },
        {
            'update_id': 9,
            'message': {**private, 'chat': {'id': True, 'type': 'private'}},
        },
        {'update_id': 10, 'message': {**private, 'chat': {'id': 42}}},
        {'update_id': 12, 'message': {**private, 'text': 5}},
        {'update_id': 13},
        {},
    ]
    bot.sent.clear()

    for update in updates:
        handle_update(update, api, NOW)

    assert bot.sent == []


def test_other_private_text_gets_the_help_reply(bot, api):
    handle_update(bot.say(42, 'hello'), api, NOW)
    handle_update(bot.say(42, ''), api, NOW)
    handle_update(bot.say(42, '   '), api, NOW)

    assert len(bot.sent) == 3
    assert all('To connect Telegram' in m['text'] for m in bot.sent)


def test_a_chat_of_another_account_gets_the_refusal(alice, bob, bot, api):
    connect(issue_link_token(bob, NOW), 42, NOW)
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'/start {token}'), api, NOW)

    assert bot.sent[0]['text'] == (
        'This Telegram account is already connected to another mitaroku account. '
        'Disconnect it there first, then try again.'
    )
    assert profile_of(alice).telegram_chat_id is None
    assert profile_of(bob).telegram_chat_id == 42
    disconnect(profile_of(bob))
    assert connect(token, 42, NOW)[0] is Outcome.CONNECTED


def test_the_refusal_in_russian(alice, bob, bot, api):
    connect(issue_link_token(bob, NOW), 42, NOW)
    token = issue_link_token(alice, NOW)

    handle_update(bot.say(42, f'/start {token}', language_code='ru'), api, NOW)

    assert 'уже подключён к другой учётной записи' in bot.sent[0]['text']


def test_an_ampersand_in_the_settings_url_cannot_break_the_html(bot, api, settings):
    settings.PUBLIC_URL = 'https://m.example/?a=1&b=<2>'

    handle_update(bot.say(42, '/start'), api, NOW)

    text = bot.sent[0]['text']
    assert 'a=1&amp;b=&lt;2&gt;' in text
    assert '<2>' not in text


def test_a_block_right_after_start_is_swallowed_and_the_connection_stands(
    alice, bot, api, caplog
):
    token = issue_link_token(alice, NOW)
    bot.fail('sendMessage', 403, 'Forbidden: bot was blocked by the user')

    with caplog.at_level(logging.INFO):
        handle_update(bot.say(42, f'/start {token}'), api, NOW)

    assert profile_of(alice).telegram_chat_id == 42
    assert 'PermanentFailure' in caplog.text
    assert 'blocked by the user' not in caplog.text


def test_an_unreachable_telegram_propagates(alice, bot, api):
    token = issue_link_token(alice, NOW)
    bot.down = True

    with pytest.raises(Unreachable):
        handle_update(bot.say(42, f'/start {token}'), api, NOW)


def test_other_send_errors_propagate(bot, api):
    from notifications.telegram import TelegramError

    bot.fail('sendMessage', 500, 'Internal')

    with pytest.raises(TelegramError):
        handle_update(bot.say(42, '/start'), api, NOW)


def test_the_token_and_the_text_are_never_logged(alice, bot, api, caplog):
    token = issue_link_token(alice, NOW)

    with caplog.at_level(logging.DEBUG):
        handle_update(bot.say(42, f'/start {token}', update_id=77), api, NOW)
        handle_update(bot.say(43, '/start secret-guess', update_id=78), api, NOW)

    assert token not in caplog.text
    assert 'secret-guess' not in caplog.text
    assert '77' in caplog.text
    assert 'CONNECTED' in caplog.text
    assert 'INVALID' in caplog.text
