"""The bot's side of connecting a chat: what it answers to the messages it gets.

The bot talks to whoever writes to it, not to one user, so this is system code
that spans users. A message counts only when it is a private-chat text; the
chat is connected only by ``/start <token>`` with a valid link token. The token
and the message text are never logged: only the update id and the outcome are.
"""

import logging
from datetime import datetime
from html import escape

from accounts.models import Profile
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext
from notifications.channel import absolute_url
from notifications.errors import PermanentFailure
from notifications.telegram import BotApi
from notifications.telegram_link import Outcome, connect

logger = logging.getLogger(__name__)

_START = '/start'


def _parse(update: dict) -> tuple[int, str, str] | None:
    """Return ``(chat_id, text, language_code)`` of a private text message.

    Args:
        update: One update from ``getUpdates``, of any shape.

    Returns:
        None unless ``update['message']`` is a text sent in a private chat.
    """
    message = update.get('message')
    if not isinstance(message, dict):
        return None
    chat = message.get('chat')
    text = message.get('text')
    if not isinstance(chat, dict) or chat.get('type') != 'private':
        return None
    chat_id = chat.get('id')
    # ``bool`` is an ``int``, but never a chat id.
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return None
    if not isinstance(text, str):
        return None
    sender = message.get('from')
    code = sender.get('language_code') if isinstance(sender, dict) else None
    return chat_id, text, code if isinstance(code, str) else ''


def _split(text: str) -> tuple[str, str]:
    """Split a message into its command and its argument.

    Args:
        text: The message text.

    Returns:
        The first word without an ``@botname`` suffix, and the rest, stripped.
    """
    parts = text.split(maxsplit=1)
    if not parts:
        return '', ''
    command = parts[0].split('@', 1)[0]
    return command, parts[1].strip() if len(parts) > 1 else ''


def _settings_url() -> str:
    """Return the Settings page's URL, escaped for Telegram's HTML."""
    return escape(absolute_url(reverse('settings')), quote=True)


def _help_text() -> str:
    """Return the reply that points to Settings."""
    return gettext(
        'To connect Telegram, open mitaroku Settings and press "Connect Telegram": '
        '%(url)s'
    ) % {'url': _settings_url()}


def _invalid_text() -> str:
    """Return the reply for a link that is expired, used or unknown."""
    return gettext(
        'That link has expired or was already used. Press "Connect Telegram" in '
        'mitaroku Settings to get a new one: %(url)s'
    ) % {'url': _settings_url()}


def _taken_text() -> str:
    """Return the reply for a chat that belongs to another account."""
    return gettext(
        'This Telegram account is already connected to another mitaroku account. '
        'Disconnect it there first, then try again.'
    )


def _connected_text() -> str:
    """Return the reply for a successful connection."""
    return gettext('Connected. New mitaroku notifications will arrive in this chat.')


def _reply(api: BotApi, chat_id: int, language: str, make_text) -> None:
    """Send the text ``make_text`` returns in ``language``.

    A chat that refuses the bot for good is only noted: the user may have blocked
    the bot a second after pressing Start.

    Raises:
        Unreachable: Telegram cannot be reached.
        TelegramError: Any other failed answer, or ``RetryAfter``.
    """
    with translation.override(language):
        text = make_text()
    try:
        api.send_message(chat_id, text)
    except PermanentFailure as exc:
        logger.info('Telegram reply was refused: %s', type(exc).__name__)


def handle_update(update: dict, api: BotApi, now: datetime) -> None:
    """Handle one update the bot received.

    Args:
        update: One update from ``getUpdates``; any shape is tolerated.
        api: The Bot API to reply with.
        now: The current moment.

    Raises:
        Unreachable: Telegram cannot be reached while replying.
        TelegramError: Any other failed answer, or ``RetryAfter``.
    """
    if not isinstance(update, dict):
        return
    parsed = _parse(update)
    if parsed is None:
        return
    chat_id, text, code = parsed
    update_id = update.get('update_id')
    sender_language = 'ru' if code.lower().startswith('ru') else 'en'
    command, argument = _split(text)
    if command != _START or not argument:
        logger.info('Telegram update %s: HELP', update_id)
        _reply(api, chat_id, sender_language, _help_text)
        return
    outcome, user = connect(argument, chat_id, now)
    logger.info('Telegram update %s: %s', update_id, outcome.name)
    if outcome is Outcome.CONNECTED:
        _reply(api, chat_id, user.profile.interface_language, _connected_text)
    elif outcome is Outcome.INVALID:
        # A replayed /start (its reply timed out, or the offset was lost) must not
        # tell a connected user that the link is gone. This reveals nothing the
        # chat does not already know.
        owner = Profile.objects.filter(telegram_chat_id=chat_id).first()
        if owner is None:
            _reply(api, chat_id, sender_language, _invalid_text)
        else:
            _reply(api, chat_id, owner.interface_language, _connected_text)
    else:
        _reply(api, chat_id, sender_language, _taken_text)
