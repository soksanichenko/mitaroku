import hashlib
import re
import threading
from datetime import UTC, datetime, timedelta

import pytest
from accounts.models import Profile
from django.db import connection
from notifications import telegram_link
from notifications.telegram_link import (
    TOKEN_LIFETIME,
    Outcome,
    connect,
    disconnect,
    issue_link_token,
    link_url,
)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def profile_of(user) -> Profile:
    """Return the freshly loaded profile of ``user``."""
    return Profile.objects.get(user=user)


def test_a_token_is_urlsafe_short_and_stored_only_as_a_hash(alice):
    token = issue_link_token(alice, NOW)

    assert re.fullmatch(r'[A-Za-z0-9_-]{1,64}', token)
    profile = profile_of(alice)
    assert profile.telegram_link_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in profile.telegram_link_hash
    assert profile.telegram_link_expires == NOW + TOKEN_LIFETIME


def test_tokens_are_not_repeated(alice, bob):
    assert issue_link_token(alice, NOW) != issue_link_token(bob, NOW)


def test_a_new_token_replaces_the_old_one(alice):
    old = issue_link_token(alice, NOW)
    new = issue_link_token(alice, NOW)

    assert connect(old, 42, NOW) == (Outcome.INVALID, None)
    assert profile_of(alice).telegram_chat_id is None
    assert connect(new, 42, NOW) == (Outcome.CONNECTED, alice)


def test_connecting_switches_the_channel_on_from_that_moment(alice):
    profile = alice.profile
    profile.telegram_blocked = True
    profile.save()
    token = issue_link_token(alice, NOW)
    later = NOW + timedelta(minutes=5)

    outcome, user = connect(token, 42, later)

    assert (outcome, user) == (Outcome.CONNECTED, alice)
    profile = profile_of(alice)
    assert profile.telegram_chat_id == 42
    assert profile.telegram_enabled is True
    assert profile.telegram_since == later
    assert profile.telegram_blocked is False
    assert profile.telegram_link_hash == ''
    assert profile.telegram_link_expires is None


def test_a_used_token_cannot_connect_a_second_chat(alice):
    token = issue_link_token(alice, NOW)
    assert connect(token, 42, NOW)[0] is Outcome.CONNECTED

    assert connect(token, 99, NOW) == (Outcome.INVALID, None)
    assert profile_of(alice).telegram_chat_id == 42


def test_an_expired_token_is_invalid_and_changes_nothing(alice):
    token = issue_link_token(alice, NOW)
    late = NOW + TOKEN_LIFETIME + timedelta(seconds=1)

    assert connect(token, 42, late) == (Outcome.INVALID, None)

    profile = profile_of(alice)
    assert profile.telegram_chat_id is None
    assert profile.telegram_enabled is False
    assert profile.telegram_link_hash != ''


def test_a_token_is_valid_until_the_moment_it_expires(alice):
    token = issue_link_token(alice, NOW)

    assert connect(token, 42, NOW + TOKEN_LIFETIME - timedelta(seconds=1))[0] is (
        Outcome.CONNECTED
    )


@pytest.mark.parametrize(
    'token',
    ['', 'unknown', 'a' * 129, 'has space', 'semi;colon', 'ключ', '-', '%s'],
)
def test_an_unknown_or_malformed_token_is_invalid(alice, token):
    issue_link_token(alice, NOW)

    assert connect(token, 42, NOW) == (Outcome.INVALID, None)
    assert profile_of(alice).telegram_chat_id is None


def test_a_malformed_token_is_refused_without_a_query(alice, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert connect('a' * 129, 42, NOW) == (Outcome.INVALID, None)
        assert connect('no good', 42, NOW) == (Outcome.INVALID, None)


def test_a_chat_of_another_account_is_refused_and_the_token_survives(alice, bob):
    bob_token = issue_link_token(bob, NOW)
    connect(bob_token, 42, NOW)
    token = issue_link_token(alice, NOW)

    assert connect(token, 42, NOW) == (Outcome.CHAT_TAKEN, None)

    assert profile_of(alice).telegram_chat_id is None
    assert profile_of(alice).telegram_enabled is False
    assert profile_of(bob).telegram_chat_id == 42
    disconnect(profile_of(bob))
    assert connect(token, 42, NOW) == (Outcome.CONNECTED, alice)
    assert profile_of(bob).telegram_chat_id is None


def test_the_same_account_may_move_to_another_chat(alice):
    connect(issue_link_token(alice, NOW), 42, NOW)
    later = NOW + timedelta(hours=1)

    assert connect(issue_link_token(alice, later), 77, later)[0] is Outcome.CONNECTED

    profile = profile_of(alice)
    assert profile.telegram_chat_id == 77
    assert profile.telegram_since == later


def test_the_same_account_may_connect_the_same_chat_again(alice):
    connect(issue_link_token(alice, NOW), 42, NOW)

    assert connect(issue_link_token(alice, NOW), 42, NOW)[0] is Outcome.CONNECTED
    assert profile_of(alice).telegram_chat_id == 42


def test_a_clash_on_the_unique_chat_id_gives_chat_taken(alice, bob, monkeypatch):
    # Another account takes the chat between the check and the write.
    token = issue_link_token(alice, NOW)
    real_save = Profile.save

    def racing_save(self, *args, **kwargs):
        if self.user_id == alice.pk:
            Profile.objects.filter(user=bob).update(telegram_chat_id=42)
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Profile, 'save', racing_save)

    assert connect(token, 42, NOW) == (Outcome.CHAT_TAKEN, None)

    assert profile_of(alice).telegram_chat_id is None
    assert profile_of(alice).telegram_link_hash != ''


def test_disconnect_forgets_everything(alice):
    connect(issue_link_token(alice, NOW), 42, NOW)
    issue_link_token(alice, NOW)
    profile = profile_of(alice)
    profile.telegram_blocked = True
    profile.save()

    disconnect(profile_of(alice))

    profile = profile_of(alice)
    assert profile.telegram_chat_id is None
    assert profile.telegram_enabled is False
    assert profile.telegram_since is None
    assert profile.telegram_link_hash == ''
    assert profile.telegram_link_expires is None
    assert profile.telegram_blocked is False


def test_link_url_names_the_bot(bot):
    assert link_url('abc-DEF_1') == 'https://t.me/mitaroku_bot?start=abc-DEF_1'


@pytest.mark.django_db(transaction=True)
def test_two_chats_cannot_both_consume_one_token(alice, monkeypatch):
    token = issue_link_token(alice, NOW)
    holding = threading.Event()
    release = threading.Event()
    real_lookup = telegram_link._lock_profile

    def slow_lookup(token_hash, now):
        # The first call keeps the row lock until the second one is waiting.
        found = real_lookup(token_hash, now)
        if not holding.is_set():
            holding.set()
            assert release.wait(timeout=10)
        return found

    monkeypatch.setattr(telegram_link, '_lock_profile', slow_lookup)
    results: dict[int, Outcome] = {}
    errors: list[BaseException] = []

    def run(chat_id: int) -> None:
        try:
            results[chat_id] = connect(token, chat_id, NOW)[0]
        except BaseException as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            # Otherwise the test database cannot be dropped.
            connection.close()

    first = threading.Thread(target=run, args=(42,))
    second = threading.Thread(target=run, args=(77,))
    first.start()
    try:
        assert holding.wait(timeout=10)
        second.start()
        # Give the second call time to reach the lock and wait on it.
        second.join(timeout=1)
        assert second.is_alive()
    finally:
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)

    assert errors == []
    assert results == {42: Outcome.CONNECTED, 77: Outcome.INVALID}
    assert profile_of(alice).telegram_chat_id == 42
