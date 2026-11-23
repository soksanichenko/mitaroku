"""The channels the shared sender delivers through, one ``ChannelSpec`` each."""

import smtplib
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from accounts.models import Profile, User
from django.conf import settings
from notifications import telegram
from notifications.channel import (
    effective_address,
    email_configured,
    telegram_configured,
)
from notifications.digest import build_digest
from notifications.errors import Unreachable
from notifications.models import Delivery, Notification
from notifications.telegram_message import build_message

# Not ``OSError``: ``smtplib.SMTPException`` subclasses it, and an error such as
# a refused login is about this message, not about the server being down.
_EMAIL_UNREACHABLE = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    ConnectionError,
    TimeoutError,
    socket.gaierror,
)


@dataclass(frozen=True)
class ChannelSpec:
    """What the shared sender needs to know about one channel.

    Attributes:
        channel: The ``Delivery.channel`` value of its rows.
        configured: Whether the operator set the channel up.
        enabled_field: The ``Profile`` field that switches it on.
        since_field: The ``Profile`` field with the moment it was switched on.
        destination: Where a user's messages go; empty when there is nowhere.
        send: Sends one message with the notifications to the user; raises
            ``notifications.errors`` exceptions for the cases the sender treats
            specially.
        disable: Switches the channel off for the user.
        secret: The text that must not reach ``Delivery.last_error`` or a log.
        placeholder: What replaces the secret in ``Delivery.last_error``.
    """

    channel: Delivery.Channel
    configured: Callable[[], bool]
    enabled_field: str
    since_field: str
    destination: Callable[[User], str]
    send: Callable[[User, list[Notification], datetime], None]
    disable: Callable[[User], None]
    secret: Callable[[User], str]
    placeholder: str = '<address>'


def send_email(user: User, notifications: list[Notification], now: datetime) -> None:
    """Mail the digest of ``notifications`` to ``user``.

    Raises:
        Unreachable: The mail server cannot be reached.
    """
    try:
        build_digest(user, notifications, now).send()
    except _EMAIL_UNREACHABLE as exc:
        raise Unreachable(f'{type(exc).__name__}: {exc}') from exc


def disable_email(user: User) -> None:
    """Switch email off for ``user``, as the unsubscribe link does."""
    Profile.objects.filter(user=user).update(email_enabled=False, email_since=None)


EMAIL = ChannelSpec(
    channel=Delivery.Channel.EMAIL,
    configured=email_configured,
    enabled_field='email_enabled',
    since_field='email_since',
    destination=effective_address,
    send=send_email,
    disable=disable_email,
    secret=effective_address,
)


def telegram_destination(user: User) -> str:
    """Return the chat the user's messages go to, or an empty string."""
    chat_id = user.profile.telegram_chat_id
    return '' if chat_id is None else str(chat_id)


def telegram_token(user: User) -> str:
    """Return the bot token; it is the same for every user."""
    return settings.TELEGRAM_BOT_TOKEN


def send_telegram(user: User, notifications: list[Notification], now: datetime) -> None:
    """Send the digest of ``notifications`` to the chat of ``user``.

    Raises:
        PermanentFailure: The chat refuses the bot for good.
        RetryAfter: Telegram asks to wait.
        Unreachable: Telegram cannot be reached.
        TelegramError: Any other failed answer.
    """
    text = build_message(user, notifications, now)
    with telegram.bot_api() as api:
        api.send_message(user.profile.telegram_chat_id, text)


def disable_telegram(user: User) -> None:
    """Switch Telegram off for ``user``, forget the chat and note the block."""
    Profile.objects.filter(user=user).update(
        telegram_enabled=False,
        telegram_since=None,
        telegram_chat_id=None,
        telegram_blocked=True,
    )


TELEGRAM = ChannelSpec(
    channel=Delivery.Channel.TELEGRAM,
    configured=telegram_configured,
    enabled_field='telegram_enabled',
    since_field='telegram_since',
    destination=telegram_destination,
    send=send_telegram,
    disable=disable_telegram,
    secret=telegram_token,
    placeholder='<token>',
)
