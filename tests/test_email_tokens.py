import time
from datetime import timedelta

import pytest
from accounts.models import User
from django.core import signing
from notifications import tokens

pytestmark = pytest.mark.django_db


def test_an_unsubscribe_token_round_trips(alice):
    token = tokens.unsubscribe_token(alice)
    assert tokens.unsubscribe_user(token) == alice


def test_a_tampered_token_is_rejected(alice):
    token = tokens.unsubscribe_token(alice)
    assert (
        tokens.unsubscribe_user(token[:-1] + ('A' if token[-1] != 'A' else 'B')) is None
    )
    assert tokens.unsubscribe_user('not-a-token') is None


def test_an_unsubscribe_token_has_no_expiry(alice, monkeypatch):
    token = tokens.unsubscribe_token(alice)
    later = time.time() + 10 * 365 * 24 * 3600
    monkeypatch.setattr(time, 'time', lambda: later)
    assert tokens.unsubscribe_user(token) == alice


def test_a_confirmation_token_carries_the_address(alice):
    token = tokens.confirmation_token(alice, 'kira@example.com')
    assert tokens.confirmation_target(token) == (alice, 'kira@example.com')


def test_an_expired_confirmation_token_is_rejected(alice, monkeypatch):
    token = tokens.confirmation_token(alice, 'kira@example.com')
    assert tokens.MAX_AGE_CONFIRM == timedelta(days=3)
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now + 3 * 24 * 3600 - 60)
    assert tokens.confirmation_target(token) is not None
    monkeypatch.setattr(time, 'time', lambda: now + 3 * 24 * 3600 + 60)
    assert tokens.confirmation_target(token) is None


def test_tokens_of_one_kind_do_not_read_as_the_other(alice):
    assert (
        tokens.unsubscribe_user(tokens.confirmation_token(alice, 'kira@example.com'))
        is None
    )
    assert tokens.confirmation_target(tokens.unsubscribe_token(alice)) is None


def test_a_token_of_a_deleted_user_reads_as_none(alice):
    unsubscribe = tokens.unsubscribe_token(alice)
    confirmation = tokens.confirmation_token(alice, 'kira@example.com')
    User.objects.filter(pk=alice.pk).delete()
    assert tokens.unsubscribe_user(unsubscribe) is None
    assert tokens.confirmation_target(confirmation) is None


def test_the_unsubscribe_token_uses_the_mitaroku_salt(alice):
    token = tokens.unsubscribe_token(alice)
    payload = signing.loads(token, salt='mitaroku.email.unsubscribe')
    assert payload == {'u': alice.pk}


def test_the_confirmation_token_uses_the_mitaroku_salt(alice):
    token = tokens.confirmation_token(alice, 'kira@example.com')
    payload = signing.loads(
        token, salt='mitaroku.email.confirm', max_age=tokens.MAX_AGE_CONFIRM
    )
    assert payload == {'u': alice.pk, 'a': 'kira@example.com'}
