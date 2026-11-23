"""One-time link tokens that connect a Telegram chat to an account.

Whoever sends ``/start <token>`` to the bot gets their chat connected to the
account that issued the token, so the token is the only proof of who the chat
belongs to. It is random, valid once and for a short time, and kept only as a
SHA-256 hash, so a database leak does not yield links that still work. The
bot's poll acts on behalf of whoever writes to the bot, not on behalf of one
user, so ``connect`` is system code that spans users.
"""

import enum
import hashlib
import re
import secrets
from datetime import datetime, timedelta

from accounts.models import Profile, User
from django.conf import settings
from django.db import IntegrityError, transaction

TOKEN_LIFETIME = timedelta(minutes=30)

# ``secrets.token_urlsafe(24)`` gives 32 characters; the cap keeps a hostile
# message from being hashed and looked up at all.
_MAX_TOKEN_LENGTH = 128
_TOKEN_SHAPE = re.compile(r'[A-Za-z0-9_-]+')


class Outcome(enum.Enum):
    """What came of an attempt to connect a chat."""

    CONNECTED = 'connected'
    INVALID = 'invalid'
    CHAT_TAKEN = 'chat_taken'


def _hash(token: str) -> str:
    """Return the SHA-256 hex digest of ``token``."""
    return hashlib.sha256(token.encode()).hexdigest()


def issue_link_token(user: User, now: datetime) -> str:
    """Issue a link token for ``user`` and forget any earlier one.

    Args:
        user: The account the token connects a chat to.
        now: The current moment; the token expires ``TOKEN_LIFETIME`` later.

    Returns:
        The token; only its hash is stored.
    """
    token = secrets.token_urlsafe(24)
    profile = user.profile
    profile.telegram_link_hash = _hash(token)
    profile.telegram_link_expires = now + TOKEN_LIFETIME
    profile.save(update_fields=['telegram_link_hash', 'telegram_link_expires'])
    return token


def link_url(token: str) -> str:
    """Return the link that opens the bot with ``token`` as the start payload."""
    return f'https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}'


def _lock_profile(token_hash: str, now: datetime) -> Profile | None:
    """Return the profile holding an unexpired ``token_hash``, row-locked.

    Must run inside a transaction. A concurrent call with the same hash waits
    here for the lock and, once the first one commits, finds no token left.
    """
    return (
        Profile.objects.select_for_update()
        .select_related('user')
        .filter(telegram_link_hash=token_hash, telegram_link_expires__gt=now)
        .first()
    )


def connect(token: str, chat_id: int, now: datetime) -> tuple[Outcome, User | None]:
    """Connect ``chat_id`` to the account that issued ``token``.

    A same-account relink replaces the old chat. A chat that already belongs to
    another account is refused and the token stays valid.

    Args:
        token: The token from the ``/start`` message, as presented.
        chat_id: The Telegram chat that presented it.
        now: The current moment; the channel counts notifications from here.

    Returns:
        ``(CONNECTED, user)``, ``(INVALID, None)`` for an unknown, used, expired
        or malformed token, or ``(CHAT_TAKEN, None)``.
    """
    if len(token) > _MAX_TOKEN_LENGTH or not _TOKEN_SHAPE.fullmatch(token):
        return Outcome.INVALID, None
    token_hash = _hash(token)
    with transaction.atomic():
        profile = _lock_profile(token_hash, now)
        if profile is None:
            return Outcome.INVALID, None
        if (
            Profile.objects.filter(telegram_chat_id=chat_id)
            .exclude(pk=profile.pk)
            .exists()
        ):
            return Outcome.CHAT_TAKEN, None
        profile.telegram_chat_id = chat_id
        profile.telegram_enabled = True
        profile.telegram_since = now
        profile.telegram_blocked = False
        profile.telegram_link_hash = ''
        profile.telegram_link_expires = None
        try:
            with transaction.atomic():
                profile.save()
        except IntegrityError:
            # Another account took the chat after the check above.
            return Outcome.CHAT_TAKEN, None
    return Outcome.CONNECTED, profile.user


def disconnect(profile: Profile) -> None:
    """Forget the chat of ``profile`` and any link token, and switch Telegram off."""
    profile.telegram_chat_id = None
    profile.telegram_enabled = False
    profile.telegram_since = None
    profile.telegram_link_hash = ''
    profile.telegram_link_expires = None
    profile.telegram_blocked = False
    profile.save(
        update_fields=[
            'telegram_chat_id',
            'telegram_enabled',
            'telegram_since',
            'telegram_link_hash',
            'telegram_link_expires',
            'telegram_blocked',
        ]
    )
