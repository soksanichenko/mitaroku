from datetime import datetime, timezone

import pytest
from django.contrib import admin
from django.db import IntegrityError, transaction
from tracking.models import LibraryEntry, WatchEvent
from tracking_helpers import make_movie, make_show

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def test_one_entry_per_user_and_title(alice, bob):
    title, _, _ = make_show()
    LibraryEntry.objects.create(user=alice, title=title)
    with pytest.raises(IntegrityError), transaction.atomic():
        LibraryEntry.objects.create(user=alice, title=title)
    LibraryEntry.objects.create(user=bob, title=title)


def test_score_range_is_enforced(alice):
    title, _, _ = make_show()
    with pytest.raises(IntegrityError), transaction.atomic():
        LibraryEntry.objects.create(user=alice, title=title, score=11)
    entry = LibraryEntry.objects.create(user=alice, title=title, score=10)
    assert entry.status == LibraryEntry.Status.WATCHING


def test_watch_event_identity_is_user_episode_time(alice, bob):
    title, episodes, _ = make_show()
    movie = make_movie()
    WatchEvent.objects.create(
        user=alice, title=title, episode=episodes[0], watched_at=NOW
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        WatchEvent.objects.create(
            user=alice, title=title, episode=episodes[0], watched_at=NOW
        )
    WatchEvent.objects.create(
        user=bob, title=title, episode=episodes[0], watched_at=NOW
    )

    WatchEvent.objects.create(user=alice, title=movie, watched_at=NOW)
    with pytest.raises(IntegrityError), transaction.atomic():
        WatchEvent.objects.create(user=alice, title=movie, watched_at=NOW)
    WatchEvent.objects.create(user=bob, title=movie, watched_at=NOW)


def test_for_user_returns_only_own_rows(alice, bob):
    title, episodes, _ = make_show()
    mine = LibraryEntry.objects.create(user=alice, title=title)
    LibraryEntry.objects.create(user=bob, title=title)
    event = WatchEvent.objects.create(
        user=alice, title=title, episode=episodes[0], watched_at=NOW
    )
    WatchEvent.objects.create(
        user=bob, title=title, episode=episodes[0], watched_at=NOW
    )

    assert list(LibraryEntry.objects.for_user(alice)) == [mine]
    assert list(WatchEvent.objects.for_user(alice)) == [event]


def test_tracking_data_is_not_in_the_admin():
    assert admin.site._registry
    assert LibraryEntry not in admin.site._registry
    assert WatchEvent not in admin.site._registry
