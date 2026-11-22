import itertools
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from catalog.models import Title
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from notifications import deliver
from notifications.deliver import MAX_ATTEMPTS, RETRY_PAUSES, send_deliveries
from notifications.models import Delivery, Notification

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
SINCE = NOW - timedelta(days=10)

Status = Delivery.Status


@pytest.fixture(autouse=True)
def email_settings():
    """Configure outgoing email for every test."""
    with override_settings(EMAIL_ENABLED=True, PUBLIC_URL='https://m.example'):
        yield


def enable(user, address: str | None = None, since: datetime = SINCE):
    """Turn the email channel on for ``user``, mailing to ``address``."""
    user.email = address or f'{user.username}@example.com'
    user.save()
    profile = user.profile
    profile.email_enabled = True
    profile.email_since = since
    profile.save()
    return user


def notify(user, name: str = 'Show', age: timedelta = HOUR, read: bool = False):
    """Create a notification about a new title, ``age`` old at ``NOW``."""
    title = Title.objects.create(kind=Title.Kind.TV, original_title=name)
    created = NOW - age
    return Notification.objects.create(
        user=user,
        kind=Notification.Kind.EPISODE_AIRED,
        title=title,
        day=created.date(),
        dedupe_key=f'episode_aired:{title.pk}:{created.date().isoformat()}',
        created_at=created,
        read_at=NOW if read else None,
    )


def deliveries(user) -> list[Delivery]:
    """Return the user's deliveries, oldest first."""
    return list(Delivery.objects.for_user(user).order_by('pk'))


@pytest.fixture
def failing_send(monkeypatch):
    """Make ``send`` raise for the given recipients and record every call.

    Returns:
        The list of the recipient lists ``send`` was called for; ``failing`` is
        the set of addresses it raises for, with the text built by ``error``.
    """
    original = EmailMultiAlternatives.send
    calls: list[list[str]] = []

    class Control:
        failing: set[str] = set()
        calls_made = calls

        @staticmethod
        def error(address: str) -> Exception:
            return RuntimeError(f'cannot deliver to {address}: mailbox is full')

    def send(self, fail_silently=False):
        calls.append(list(self.to))
        if self.to[0] in Control.failing:
            raise Control.error(self.to[0])
        return original(self, fail_silently)

    monkeypatch.setattr(EmailMultiAlternatives, 'send', send)
    return Control


@pytest.mark.django_db
def test_one_digest_per_user_with_all_items(alice, bob):
    enable(alice)
    enable(bob)
    for number in range(3):
        notify(alice, f'A{number}')
    notify(bob, 'B')

    assert send_deliveries(NOW) == 2

    by_recipient = {message.to[0]: message for message in mail.outbox}
    assert set(by_recipient) == {'1001@example.com', '1002@example.com'}
    assert by_recipient['1001@example.com'].subject == '3 new notifications'
    assert by_recipient['1002@example.com'].subject == '1 new notification'
    rows = deliveries(alice) + deliveries(bob)
    assert len(rows) == 4
    assert {row.status for row in rows} == {Status.SENT}
    assert {row.sent_at for row in rows} == {NOW}
    assert {row.attempts for row in rows} == {1}


@pytest.mark.django_db
def test_nothing_is_sent_when_email_is_not_configured(alice):
    enable(alice)
    notify(alice)

    with override_settings(EMAIL_ENABLED=False):
        assert send_deliveries(NOW) == 0

    assert mail.outbox == []
    assert Delivery.objects.count() == 0


@pytest.mark.django_db
def test_a_user_who_did_not_enable_email_gets_nothing(alice):
    alice.email = 'alice@example.com'
    alice.save()
    alice.profile.email_since = SINCE
    alice.profile.save()
    notify(alice)

    assert send_deliveries(NOW) == 0

    assert mail.outbox == []
    assert Delivery.objects.count() == 0


@pytest.mark.django_db
def test_nothing_before_email_since(alice):
    enable(alice, since=NOW - HOUR)
    notify(alice, age=2 * HOUR)

    assert send_deliveries(NOW) == 0

    assert mail.outbox == []
    assert Delivery.objects.count() == 0


@pytest.mark.django_db
def test_toggling_off_and_on_skips_the_backlog(alice):
    enable(alice)
    notify(alice, 'Old', age=3 * HOUR)
    profile = alice.profile
    # Switching the channel off clears the moment; switching it on sets a new one.
    profile.email_enabled = False
    profile.email_since = None
    profile.save()
    assert send_deliveries(NOW - 2 * HOUR) == 0
    profile.email_enabled = True
    profile.email_since = NOW - 2 * HOUR
    profile.save()
    new = notify(alice, 'New', age=HOUR)

    assert send_deliveries(NOW) == 1

    (message,) = mail.outbox
    assert message.subject == '1 new notification'
    assert [row.notification_id for row in deliveries(alice)] == [new.pk]


@pytest.mark.django_db
def test_a_second_run_sends_nothing_new(alice):
    enable(alice)
    notify(alice)

    assert send_deliveries(NOW) == 1
    assert send_deliveries(NOW + HOUR) == 0

    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_a_notification_merged_after_it_was_mailed_is_not_mailed_again(alice):
    enable(alice)
    notification = notify(alice)
    assert send_deliveries(NOW) == 1

    # The scanner merges into the same row: it becomes unread with a new payload.
    Notification.objects.filter(pk=notification.pk).update(
        read_at=None, payload={'episodes': [1, 2]}
    )

    assert send_deliveries(NOW + HOUR) == 0
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_a_read_notification_is_skipped(alice):
    enable(alice)
    notify(alice, read=True)

    assert send_deliveries(NOW) == 0

    assert mail.outbox == []
    (row,) = deliveries(alice)
    assert row.status == Status.SKIPPED


@pytest.mark.django_db
def test_an_old_notification_expires(alice):
    enable(alice)
    notify(alice, age=49 * HOUR)
    # Created before the expiry limit, so it never gets a row; one that did get
    # a row earlier is expired by the settle step.
    assert send_deliveries(NOW) == 0
    assert Delivery.objects.count() == 0

    notification = notify(alice, 'Waiting', age=HOUR)
    Delivery.objects.create(
        user=alice, notification=notification, channel=Delivery.Channel.EMAIL
    )

    assert send_deliveries(NOW + 48 * HOUR) == 0

    assert mail.outbox == []
    (row,) = deliveries(alice)
    assert row.status == Status.EXPIRED


@pytest.mark.django_db
def test_an_old_unsent_notification_is_expired_not_skipped_when_also_read(alice):
    enable(alice)
    notification = notify(alice, age=HOUR, read=True)
    Delivery.objects.create(
        user=alice, notification=notification, channel=Delivery.Channel.EMAIL
    )

    assert send_deliveries(NOW + 48 * HOUR) == 0

    (row,) = deliveries(alice)
    assert row.status == Status.EXPIRED


@pytest.mark.django_db
def test_a_failed_send_retries_with_backoff(alice, failing_send):
    enable(alice)
    failing_send.failing = {'1001@example.com'}
    notify(alice)
    assert RETRY_PAUSES == (15 * timedelta(minutes=1), HOUR, 6 * HOUR, 6 * HOUR)
    assert MAX_ATTEMPTS == 5

    now = NOW
    assert send_deliveries(now) == 0
    (row,) = deliveries(alice)
    assert (row.status, row.attempts) == (Status.PENDING, 1)
    assert row.next_attempt_at == now + timedelta(minutes=15)

    # A run before the moment does not try again.
    calls = len(failing_send.calls_made)
    assert send_deliveries(now + timedelta(minutes=14)) == 0
    assert len(failing_send.calls_made) == calls

    for failure, pause in enumerate(RETRY_PAUSES[1:], start=2):
        now = row.next_attempt_at
        assert send_deliveries(now) == 0
        row.refresh_from_db()
        assert (row.status, row.attempts) == (Status.PENDING, failure)
        assert row.next_attempt_at == now + pause

    last_due = row.next_attempt_at
    now = last_due
    assert send_deliveries(now) == 0
    row.refresh_from_db()
    assert (row.status, row.attempts) == (Status.FAILED, MAX_ATTEMPTS)
    assert row.next_attempt_at == last_due

    calls = len(failing_send.calls_made)
    assert send_deliveries(now + 100 * HOUR) == 0
    assert len(failing_send.calls_made) == calls
    assert mail.outbox == []


@pytest.mark.django_db
def test_a_retry_that_works_marks_the_delivery_sent(alice, failing_send):
    enable(alice)
    failing_send.failing = {'1001@example.com'}
    notify(alice)
    assert send_deliveries(NOW) == 0
    failing_send.failing = set()

    assert send_deliveries(NOW + timedelta(minutes=15)) == 1

    (row,) = deliveries(alice)
    assert (row.status, row.attempts) == (Status.SENT, 2)
    assert row.sent_at == NOW + timedelta(minutes=15)


@pytest.mark.django_db
def test_last_error_has_no_address(alice, failing_send, caplog):
    enable(alice, 'private.person@example.com')
    failing_send.failing = {'private.person@example.com'}
    notify(alice)

    send_deliveries(NOW)

    (row,) = deliveries(alice)
    assert (
        row.last_error == 'RuntimeError: cannot deliver to <address>: mailbox is full'
    )
    assert 'private.person' not in caplog.text


@pytest.mark.django_db
def test_last_error_is_cut_and_still_has_no_address(alice, failing_send):
    enable(alice, 'private.person@example.com')
    failing_send.failing = {'private.person@example.com'}
    failing_send.error = staticmethod(
        lambda address: RuntimeError('x' * 140 + address + 'y' * 100)
    )
    notify(alice)

    send_deliveries(NOW)

    (row,) = deliveries(alice)
    assert row.last_error.startswith('RuntimeError: xxx')
    assert len(row.last_error) <= len('RuntimeError: ') + 150
    assert 'private' not in row.last_error
    assert '<address>' in row.last_error


@pytest.mark.django_db
@pytest.mark.parametrize('failing', ['1001@example.com', '1002@example.com'])
def test_one_users_failure_does_not_stop_the_others(alice, bob, failing_send, failing):
    # Whichever user fails, first or last, the other one's mark is kept.
    enable(alice)
    enable(bob)
    failing_send.failing = {failing}
    notify(alice)
    notify(bob)

    assert send_deliveries(NOW) == 1

    ok = {'1001@example.com': bob, '1002@example.com': alice}[failing]
    bad = alice if ok is bob else bob
    assert [message.to for message in mail.outbox] == [[ok.email]]
    assert [row.status for row in deliveries(ok)] == [Status.SENT]
    assert [row.status for row in deliveries(bad)] == [Status.PENDING]
    assert [row.attempts for row in deliveries(bad)] == [1]


@pytest.mark.django_db
def test_a_user_without_an_address_is_skipped_without_a_delivery(alice, bob):
    enable(alice)
    notify(alice)
    enable(bob)
    bob.email = ''
    bob.save()
    notify(bob)

    assert send_deliveries(NOW) == 1

    assert [message.to for message in mail.outbox] == [['1001@example.com']]
    assert deliveries(bob) == []


@pytest.mark.django_db
def test_the_pending_address_is_never_used(alice):
    enable(alice, 'account@example.com')
    alice.profile.pending_email = 'unconfirmed@example.com'
    alice.profile.notify_email = ''
    alice.profile.save()
    notify(alice)

    assert send_deliveries(NOW) == 1

    assert [message.to for message in mail.outbox] == [['account@example.com']]


@pytest.mark.django_db
def test_a_disabled_channel_keeps_pending_rows_unsent(alice):
    enable(alice)
    notification = notify(alice)
    Delivery.objects.create(
        user=alice, notification=notification, channel=Delivery.Channel.EMAIL
    )
    alice.profile.email_enabled = False
    alice.profile.email_since = None
    alice.profile.save()

    assert send_deliveries(NOW) == 0

    assert mail.outbox == []
    (row,) = deliveries(alice)
    assert (row.status, row.attempts) == (Status.PENDING, 0)


@pytest.mark.django_db
def test_a_pending_row_from_before_email_since_is_not_sent(alice):
    enable(alice, since=NOW - 30 * timedelta(minutes=1))
    notification = notify(alice, age=HOUR)
    Delivery.objects.create(
        user=alice, notification=notification, channel=Delivery.Channel.EMAIL
    )

    assert send_deliveries(NOW) == 0

    assert mail.outbox == []


@pytest.mark.django_db(transaction=True)
def test_overlapping_runs_never_send_twice(alice, monkeypatch):
    enable(alice)
    notify(alice)
    holding = threading.Event()
    release = threading.Event()
    real_build = deliver.build_digest

    def slow_build(user, notifications, now):
        # The first run holds the row lock here until the second run is done.
        holding.set()
        assert release.wait(timeout=10)
        return real_build(user, notifications, now)

    monkeypatch.setattr(deliver, 'build_digest', slow_build)
    results: dict[str, int] = {}
    errors: list[BaseException] = []

    def run(name: str) -> None:
        try:
            results[name] = send_deliveries(NOW)
        except BaseException as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            # Otherwise the test database cannot be dropped.
            connection.close()

    first = threading.Thread(target=run, args=('first',))
    second = threading.Thread(target=run, args=('second',))
    first.start()
    try:
        assert holding.wait(timeout=10)
        second.start()
        second.join(timeout=10)
        assert not second.is_alive()
    finally:
        release.set()
        first.join(timeout=10)

    assert errors == []
    assert results == {'first': 1, 'second': 0}
    assert len(mail.outbox) == 1
    assert [row.status for row in deliveries(alice)] == [Status.SENT]


def _overlap_with_a_settle_that_matches_the_row(
    monkeypatch, alice, second_now: datetime, while_held
) -> dict[str, int]:
    """Run a send that holds the row lock and, meanwhile, a second full run.

    The first run (at ``NOW``) stops inside ``build_digest`` holding the lock.
    ``while_held`` then changes the data, the second run (at ``second_now``)
    starts and is waited for until it is blocked on that lock, and only then
    is the first run released and allowed to commit ``sent``.

    Returns:
        What each run returned, by ``first`` and ``second``.
    """
    holding = threading.Event()
    release = threading.Event()
    real_build = deliver.build_digest

    def slow_build(user, notifications, now):
        holding.set()
        assert release.wait(timeout=10)
        return real_build(user, notifications, now)

    monkeypatch.setattr(deliver, 'build_digest', slow_build)
    results: dict[str, int] = {}
    errors: list[BaseException] = []

    def run(name: str, now: datetime) -> None:
        try:
            results[name] = send_deliveries(now)
        except BaseException as exc:  # reported by the main thread
            errors.append(exc)
        finally:
            # Otherwise the test database cannot be dropped.
            connection.close()

    def blocked_on_a_lock() -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT count(*) FROM pg_stat_activity '
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            return cursor.fetchone()[0] > 0

    first = threading.Thread(target=run, args=('first', NOW))
    second = threading.Thread(target=run, args=('second', second_now))
    first.start()
    try:
        assert holding.wait(timeout=10)
        while_held()
        second.start()
        # The second run's settle waits for the row the first run has locked
        # (or, if it does not touch the row, simply finishes).
        deadline = time.monotonic() + 10
        while second.is_alive() and not blocked_on_a_lock():
            assert time.monotonic() < deadline
            second.join(timeout=0.01)
    finally:
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)

    assert errors == []
    assert not first.is_alive()
    assert not second.is_alive()
    return results


@pytest.mark.django_db(transaction=True)
def test_a_read_on_the_site_during_a_send_does_not_overwrite_sent(alice, monkeypatch):
    enable(alice)
    notification = notify(alice)

    def read_it() -> None:
        Notification.objects.filter(pk=notification.pk).update(read_at=NOW)

    results = _overlap_with_a_settle_that_matches_the_row(
        monkeypatch, alice, NOW, read_it
    )

    assert results == {'first': 1, 'second': 0}
    assert len(mail.outbox) == 1
    assert [row.status for row in deliveries(alice)] == [Status.SENT]


@pytest.mark.django_db(transaction=True)
def test_a_later_run_that_expires_the_row_does_not_overwrite_sent(alice, monkeypatch):
    enable(alice)
    notify(alice)

    results = _overlap_with_a_settle_that_matches_the_row(
        monkeypatch, alice, NOW + 48 * HOUR, lambda: None
    )

    assert results == {'first': 1, 'second': 0}
    assert len(mail.outbox) == 1
    assert [row.status for row in deliveries(alice)] == [Status.SENT]


def test_the_error_text_fits_the_field_even_with_a_long_class_name():
    error = type('E' * 300, (Exception,), {})('z' * 300)

    text = deliver._error_text(error, 'a@example.com')

    assert len(text) == Delivery._meta.get_field('last_error').max_length
    assert text.startswith('EEE')


@pytest.mark.django_db
def test_last_error_never_exceeds_the_field_even_with_a_long_class_name(
    alice, failing_send
):
    enable(alice)
    failing_send.failing = {'1001@example.com'}
    failing_send.error = staticmethod(
        lambda address: type('E' * 300, (Exception,), {})('z' * 300)
    )
    notify(alice)

    send_deliveries(NOW)

    (row,) = deliveries(alice)
    limit = Delivery._meta.get_field('last_error').max_length
    assert len(row.last_error) == limit
    assert row.last_error.startswith('EEE')


@pytest.mark.django_db
def test_materialise_and_settle_cost_the_same_for_one_user_and_four(
    django_user_model,
):
    def make(number: int):
        user = enable(django_user_model.objects.create(username=f'u{number}'))
        notify(user, f'Show {number}')

    def count(step) -> int:
        with CaptureQueriesContext(connection) as queries:
            step(NOW)
        return len(queries)

    make(1)
    materialise_one = count(deliver._materialise)
    assert Delivery.objects.count() == 1
    settle_one = count(deliver._settle)
    for number in (2, 3, 4):
        make(number)
    materialise_four = count(deliver._materialise)
    assert Delivery.objects.count() == 4
    settle_four = count(deliver._settle)

    assert materialise_four == materialise_one
    assert settle_four == settle_one


@pytest.mark.django_db
def test_a_user_costs_a_fixed_number_of_queries(django_user_model):
    made = itertools.count(1)

    def run(users: int, items: int) -> int:
        """Return the queries of a run in which ``users`` users each have news."""
        for _ in range(users):
            number = next(made)
            user = enable(django_user_model.objects.create(username=f'u{number}'))
            for item in range(items):
                notify(user, f'Show {number}-{item}')
        with CaptureQueriesContext(connection) as queries:
            assert send_deliveries(NOW) == users
        return len(queries)

    one = run(1, 1)
    two = run(2, 1)
    four = run(4, 1)
    many_items = run(1, 6)

    # Each user adds the same fixed cost, and more items add nothing.
    assert four - two == 2 * (two - one)
    assert many_items == one
