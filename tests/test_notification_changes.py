from datetime import UTC, date, datetime

import conftest
import pytest
from catalog import sync
from catalog.models import ExternalId, Title
from catalog.sync import sync_title
from fakes import tmdb_movie
from notifications.changes import record_release_change
from notifications.models import Notification
from tracking.models import LibraryEntry

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
PAST = date(2026, 10, 1)
SOON = date(2026, 12, 25)
LATER = date(2027, 2, 1)

pytestmark = pytest.mark.django_db

Status = LibraryEntry.Status
Kind = Notification.Kind


def _title(release=None):
    return Title.objects.create(
        kind=Title.Kind.MOVIE, original_title='Film', release_date=release
    )


def _follow(user, title, status=Status.PLANNED):
    return LibraryEntry.objects.create(user=user, title=title, status=status)


def _rows():
    return Notification.objects.all().order_by('pk')


def test_first_sync_is_a_baseline(alice):
    title = _title(SOON)
    _follow(alice, title)

    assert record_release_change(title, None, False, NOW) == 0
    assert not _rows().exists()


def test_an_announced_date_notifies_planned_users_only(alice, bob):
    title = _title(SOON)
    _follow(alice, title)
    _follow(bob, title, Status.WATCHING)
    third = conftest.make_portal_user('1003')
    _follow(third, title, Status.COMPLETED)

    assert record_release_change(title, None, True, NOW) == 1

    row = _rows().get()
    assert (row.user, row.kind, row.title) == (alice, Kind.RELEASE_ANNOUNCED, title)
    assert row.payload == {'to': '2026-12-25'}
    assert row.day == date(2026, 11, 21)
    assert row.dedupe_key == f'release_announced:{title.pk}:2026-11-21'
    assert row.read_at is None


def test_a_date_announced_in_the_past_is_not_news(alice):
    title = _title(PAST)
    _follow(alice, title)

    assert record_release_change(title, None, True, NOW) == 0
    assert not _rows().exists()


def test_a_changed_date_records_from_and_to(alice):
    title = _title(LATER)
    _follow(alice, title)

    assert record_release_change(title, SOON, True, NOW) == 1

    row = _rows().get()
    assert row.kind == Kind.RELEASE_CHANGED
    assert row.payload == {'from': '2026-12-25', 'to': '2027-02-01'}


def test_a_date_moved_from_the_past_into_the_future_is_news(alice):
    title = _title(SOON)
    _follow(alice, title)

    assert record_release_change(title, PAST, True, NOW) == 1
    assert _rows().get().payload == {'from': '2026-10-01', 'to': '2026-12-25'}


def test_a_correction_wholly_in_the_past_is_ignored(alice):
    title = _title(date(2026, 10, 2))
    _follow(alice, title)

    assert record_release_change(title, PAST, True, NOW) == 0
    assert not _rows().exists()


def test_a_removed_date_notifies_nobody(alice):
    title = _title(None)
    _follow(alice, title)

    assert record_release_change(title, SOON, True, NOW) == 0
    assert not _rows().exists()


def test_an_unchanged_date_notifies_nobody(alice):
    title = _title(SOON)
    _follow(alice, title)

    assert record_release_change(title, SOON, True, NOW) == 0
    assert not _rows().exists()


def test_a_second_change_on_the_same_day_keeps_from_and_replaces_to(alice):
    title = _title(LATER)
    _follow(alice, title)
    record_release_change(title, SOON, True, NOW)
    Notification.objects.update(read_at=NOW)

    title.release_date = date(2027, 3, 1)
    assert record_release_change(title, LATER, True, NOW) == 1

    row = _rows().get()
    assert row.payload == {'from': '2026-12-25', 'to': '2027-03-01'}
    assert row.read_at is None


def test_a_repeat_that_changes_nothing_leaves_a_read_row_read(alice):
    title = _title(LATER)
    _follow(alice, title)
    record_release_change(title, SOON, True, NOW)
    Notification.objects.update(read_at=NOW)

    assert record_release_change(title, SOON, True, NOW) == 0

    assert _rows().get().read_at == NOW


def test_the_day_follows_the_users_zone(alice):
    alice.profile.timezone = 'Asia/Tokyo'
    alice.profile.save()
    title = _title(SOON)
    _follow(alice, title)

    record_release_change(title, None, True, datetime(2026, 11, 21, 23, 30, tzinfo=UTC))

    row = _rows().get()
    assert row.day == date(2026, 11, 22)
    assert row.dedupe_key.endswith(':2026-11-22')


def test_sync_title_records_the_change_end_to_end(alice, fake_clients):
    title = Title.objects.create(kind=Title.Kind.MOVIE, original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    _follow(alice, title)
    sync_title(title, fake_clients, NOW)
    assert not _rows().exists()

    fake_clients.tmdb.movies[603] = tmdb_movie(603, release=SOON)
    sync_title(title, fake_clients, NOW)

    row = _rows().get()
    assert (row.user, row.kind) == (alice, Kind.RELEASE_CHANGED)
    assert row.payload == {'from': '1999-03-31', 'to': '2026-12-25'}


def test_a_failing_hook_does_not_fail_the_sync(alice, fake_clients, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('notification write failed')

    monkeypatch.setattr(sync, 'record_release_change', fail)
    title = Title.objects.create(kind=Title.Kind.MOVIE, original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')

    sync_title(title, fake_clients, NOW)

    title.refresh_from_db()
    assert title.synced_at == NOW
    assert title.release_date == date(1999, 3, 31)
