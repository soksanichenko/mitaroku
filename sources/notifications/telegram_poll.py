"""The poll that fetches what users wrote to the bot and hands it to the bot logic.

It is system code that spans users. Logs carry only the class name and the HTTP
code of a failure: the bot token, a message text or a link token must never reach
them, and the text of a ``TelegramError`` is Telegram's description, which may echo
anything.
"""

import logging
from datetime import datetime

from django.conf import settings
from notifications import telegram
from notifications.errors import Unreachable
from notifications.telegram import TelegramError
from notifications.telegram_bot import handle_update
from providers.clients import connect_redis
from redis.exceptions import LockError

logger = logging.getLogger(__name__)

# Read at call time, so a test can point the keys at its own prefix.
KEY_PREFIX = 'mitaroku:telegram:'
# Shorter than the one-minute period: a run that died frees the lock by itself.
LOCK_TIMEOUT = 55
CONFLICT = 409


def _describe(exc: Exception) -> str:
    """Describe a failure without any text that could hold a secret.

    Args:
        exc: The failure.

    Returns:
        The class name, and for a ``TelegramError`` its HTTP code.
    """
    if isinstance(exc, TelegramError):
        return f'{type(exc).__name__} {exc.code}'
    return type(exc).__name__


def poll_updates(now: datetime) -> int:
    """Fetch the bot's pending updates and handle them in order.

    Overlapping runs are kept out by a Redis lock, so an update is not handled
    twice at once. The offset is stored after each update; losing it only replays
    updates, which handling tolerates.

    Args:
        now: The current moment.

    Returns:
        The number of updates handled; 0 when Telegram is not configured, when
        another run holds the lock or when ``getUpdates`` fails.
    """
    if not settings.TELEGRAM_ENABLED:
        return 0
    redis = connect_redis()
    lock = redis.lock(f'{KEY_PREFIX}poll', timeout=LOCK_TIMEOUT, blocking=False)
    if not lock.acquire():
        logger.info('Telegram poll skipped: another run holds the lock')
        return 0
    try:
        return _poll(redis, now)
    finally:
        try:
            lock.release()
        except LockError:
            # The run outlasted the lock's timeout; nobody holds it now.
            pass


def _poll(redis, now: datetime) -> int:
    """Run one poll under the lock.

    Args:
        redis: The Redis connection that holds the offset.
        now: The current moment.

    Returns:
        The number of updates handled.
    """
    offset_key = f'{KEY_PREFIX}offset'
    stored = redis.get(offset_key)
    offset = int(stored) if stored is not None else None
    with telegram.bot_api() as api:
        try:
            updates = api.get_updates(offset)
        except (TelegramError, Unreachable) as exc:
            if isinstance(exc, TelegramError) and exc.code == CONFLICT:
                logger.error(
                    'Telegram getUpdates failed: %s; another consumer or a webhook '
                    'is set on this bot',
                    _describe(exc),
                )
            else:
                logger.error('Telegram getUpdates failed: %s', _describe(exc))
            return 0
        handled = 0
        for update in updates:
            update_id = update.get('update_id') if isinstance(update, dict) else None
            if not isinstance(update_id, int) or isinstance(update_id, bool):
                logger.warning('Telegram update without a usable id skipped')
                continue
            try:
                handle_update(update, api, now)
            except Unreachable as exc:
                logger.error(
                    'Telegram unreachable while handling update %s: %s',
                    update_id,
                    _describe(exc),
                )
                break
            # One bad update must not wedge the poll: log it and move past it.
            except Exception as exc:
                logger.error('Telegram update %s failed: %s', update_id, _describe(exc))
            else:
                handled += 1
            redis.set(offset_key, update_id + 1)
    return handled
