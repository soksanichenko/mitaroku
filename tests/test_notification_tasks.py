from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone
from huey.contrib.djhuey import HUEY
from notifications import tasks
from notifications.models import Notification
from tracking_helpers import make_movie

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def test_notify_due_scans_now_and_returns_the_count(monkeypatch):
    seen = []

    def fake_scan(now):
        seen.append(now)
        return 3

    monkeypatch.setattr(tasks, 'scan_due', fake_scan)

    assert tasks.notify_due.call_local() == 3
    assert len(seen) == 1
    assert seen[0].tzinfo is not None


def test_send_deliveries_task_calls_the_sender_with_now_and_returns_the_count(
    monkeypatch,
):
    seen = []

    def fake_send(now):
        seen.append(now)
        return 2

    monkeypatch.setattr(tasks, 'send_deliveries', fake_send)

    assert tasks.send_deliveries_task.call_local() == 2
    assert len(seen) == 1
    assert seen[0].tzinfo is not None


def _notification(user, title, key, created_at, read_at=None):
    return Notification.objects.create(
        user=user,
        kind=Notification.Kind.MOVIE_RELEASED,
        title=title,
        day=created_at.date(),
        dedupe_key=key,
        created_at=created_at,
        read_at=read_at,
    )


@pytest.mark.django_db
def test_gc_deletes_old_read_rows(alice):
    title = make_movie()
    old = _notification(alice, title, 'a', NOW - timedelta(days=100), NOW - 91 * DAY)
    kept = _notification(alice, title, 'b', NOW - timedelta(days=100), NOW - 89 * DAY)

    tasks.gc_notifications.call_local(NOW)

    assert not Notification.objects.filter(pk=old.pk).exists()
    assert Notification.objects.filter(pk=kept.pk).exists()


@pytest.mark.django_db
def test_gc_keeps_a_row_read_exactly_90_days_ago(alice):
    title = make_movie()
    edge = _notification(alice, title, 'a', NOW - timedelta(days=100), NOW - 90 * DAY)

    assert tasks.gc_notifications.call_local(NOW) == 0
    assert Notification.objects.filter(pk=edge.pk).exists()


@pytest.mark.django_db
def test_gc_deletes_any_row_older_than_180_days(alice):
    title = make_movie()
    unread = _notification(alice, title, 'a', NOW - 181 * DAY)
    read_recently = _notification(alice, title, 'b', NOW - 181 * DAY, NOW - DAY)
    young = _notification(alice, title, 'c', NOW - 179 * DAY)

    tasks.gc_notifications.call_local(NOW)

    assert not Notification.objects.filter(pk=unread.pk).exists()
    assert not Notification.objects.filter(pk=read_recently.pk).exists()
    assert Notification.objects.filter(pk=young.pk).exists()


@pytest.mark.django_db
def test_gc_keeps_recent_unread_rows(alice):
    title = make_movie()
    recent = _notification(alice, title, 'a', NOW - DAY)

    assert tasks.gc_notifications.call_local(NOW) == 0
    assert Notification.objects.filter(pk=recent.pk).exists()


@pytest.mark.django_db
def test_gc_returns_the_count(alice, bob):
    title = make_movie()
    _notification(alice, title, 'a', NOW - 200 * DAY)
    _notification(bob, title, 'a', NOW - 100 * DAY, NOW - 95 * DAY)
    _notification(bob, title, 'b', NOW - DAY)

    assert tasks.gc_notifications.call_local(NOW) == 2
    assert Notification.objects.count() == 1


@pytest.mark.django_db
def test_gc_uses_the_clock_when_no_moment_is_given(alice):
    title = make_movie()
    old = _notification(alice, title, 'a', timezone.now() - 200 * DAY)

    assert tasks.gc_notifications.call_local() == 1
    assert not Notification.objects.filter(pk=old.pk).exists()


def test_all_three_tasks_are_registered():
    registered = {task.name for task in HUEY._registry.periodic_tasks}

    assert tasks.notify_due.task_class.__name__ in registered
    assert tasks.gc_notifications.task_class.__name__ in registered
    assert tasks.send_deliveries_task.task_class.__name__ in registered
