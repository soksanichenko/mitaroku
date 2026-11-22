"""Signed tokens for the email links that work without a session."""

from datetime import timedelta

from accounts.models import User
from django.core import signing

MAX_AGE_CONFIRM = timedelta(days=3)

# Separate salts keep a token of one kind from being read as the other.
_UNSUBSCRIBE_SALT = 'mitaroku.email.unsubscribe'
_CONFIRM_SALT = 'mitaroku.email.confirm'


def unsubscribe_token(user: User) -> str:
    """Return the token that stops email for ``user``.

    It never expires: an old digest must still let its reader unsubscribe.
    """
    return signing.dumps({'u': user.pk}, salt=_UNSUBSCRIBE_SALT)


def unsubscribe_user(token: str) -> User | None:
    """Return the user an unsubscribe ``token`` names, or ``None`` if it is invalid."""
    payload = _load(token, _UNSUBSCRIBE_SALT, max_age=None)
    return _user(payload) if payload else None


def confirmation_token(user: User, address: str) -> str:
    """Return the token that confirms ``address`` as the mail address of ``user``."""
    return signing.dumps({'u': user.pk, 'a': address}, salt=_CONFIRM_SALT)


def confirmation_target(token: str) -> tuple[User, str] | None:
    """Return the user and address a confirmation ``token`` names.

    Returns:
        ``None`` for a bad signature, an expired token or an unknown user.
    """
    payload = _load(token, _CONFIRM_SALT, max_age=MAX_AGE_CONFIRM)
    if not payload or not isinstance(payload.get('a'), str):
        return None
    user = _user(payload)
    return (user, payload['a']) if user else None


def _load(token: str, salt: str, max_age: timedelta | None) -> dict | None:
    """Return the payload of ``token``, or ``None`` if the signature is bad."""
    try:
        payload = signing.loads(token, salt=salt, max_age=max_age)
    except signing.BadSignature:
        return None
    return payload if isinstance(payload, dict) else None


def _user(payload: dict) -> User | None:
    """Return the user a payload names; a plain lookup, the token is the proof."""
    return User.objects.filter(pk=payload.get('u')).first()
