from datetime import UTC, date, datetime, timedelta

import conftest
import pytest
from catalog.models import Episode, Season, Title
from django.db import connection
from django.test.utils import CaptureQueriesContext
from notifications.models import Notification
from notifications.scan import WINDOW, scan_due
from tracking.models import LibraryEntry
from tracking_helpers import make_movie, watch

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)
DAY = date(2026, 11, 21)

pytestmark = pytest.mark.django_db

Status = LibraryEntry.Status


def _member(discord_id):
    """Create one more account, like the portal login does."""
    return conftest.make_portal_user(discord_id)


def _user(user, zone='Europe/Kyiv'):
    """Give ``user`` a zone and an old ``notify_since``."""
    profile = user.profile
    profile.timezone = zone
    profile.notify_since = LONG_AGO
    profile.save()
    return user


def _follow(user, title, status=Status.WATCHING):
    """Add ``title`` to the library with an old ``added_at``."""
    entry = LibraryEntry.objects.create(user=user, title=title, status=status)
    LibraryEntry.objects.filter(pk=entry.pk).update(added_at=LONG_AGO)
    return entry


def _show(air_date=DAY, number=1, season_number=1, kind=Season.Kind.TV, extra=False):
    """Create a TV title with one season holding one episode."""
    title = Title.objects.create(kind=Title.Kind.TV, original_title='Show')
    season = Season.objects.create(
        title=title, number=season_number, kind=kind, is_extra=extra
    )
    episode = Episode.objects.create(season=season, number=number, air_date=air_date)
    return title, season, episode


def _rows(user=None):
    rows = Notification.objects.all()
    if user is not None:
        rows = rows.filter(user=user)
    return rows.order_by('pk')


def test_an_aired_episode_notifies_a_watching_user(alice):
    _user(alice)
    title, _, episode = _show()
    _follow(alice, title)

    assert scan_due(NOW) == 1

    row = _rows(alice).get()
    assert row.kind == Notification.Kind.EPISODE_AIRED
    assert row.title == title
    assert row.day == DAY
    assert row.payload == {'episodes': [episode.pk]}
    assert row.read_at is None


def test_a_planned_user_gets_a_premiere_only_for_episode_one_of_a_tv_season(alice, bob):
    _user(alice)
    _user(bob)
    title, season, first = _show(season_number=2)
    Episode.objects.create(season=season, number=2, air_date=DAY)
    _follow(alice, title, Status.PLANNED)
    _follow(bob, title, Status.WATCHING)
    extra_title, _, _ = _show(season_number=2, extra=True)
    ova_title, _, _ = _show(kind=Season.Kind.OVA)
    _follow(alice, extra_title, Status.PLANNED)
    _follow(alice, ova_title, Status.PLANNED)

    scan_due(NOW)

    row = _rows(alice).get()
    assert row.kind == Notification.Kind.PREMIERE
    assert row.title == title
    assert row.payload == {'episodes': [first.pk]}
    watching = _rows(bob).get()
    assert watching.kind == Notification.Kind.EPISODE_AIRED
    assert len(watching.payload['episodes']) == 2


def test_a_movie_release_notifies_planned_and_watching(alice, bob):
    _user(alice)
    _user(bob)
    movie = make_movie(release_offset=0)
    movie.release_date = DAY
    movie.save()
    _follow(alice, movie, Status.PLANNED)
    _follow(bob, movie, Status.WATCHING)
    done = _user(_member('1003'))
    gone = _user(_member('1004'))
    _follow(done, movie, Status.COMPLETED)
    _follow(gone, movie, Status.DROPPED)

    assert scan_due(NOW) == 2

    assert {(r.user_id, r.kind) for r in _rows()} == {
        (alice.pk, Notification.Kind.MOVIE_RELEASED),
        (bob.pk, Notification.Kind.MOVIE_RELEASED),
    }
    assert _rows(alice).get().payload == {}


def test_nothing_before_the_moment(alice, bob):
    _user(alice, 'Pacific/Auckland')
    _user(bob, 'America/Los_Angeles')
    title, _, _ = _show()
    _follow(alice, title)
    _follow(bob, title)

    scan_due(NOW)

    # 09:00 on 11-21 is 20:00 UTC on 11-20 in Auckland, 17:00 UTC on 11-21 in LA.
    assert [r.user_id for r in _rows()] == [alice.pk]
    assert _rows(alice).get().day == DAY


def test_events_before_the_entry_are_ignored(alice):
    _user(alice)
    title, _, _ = _show()
    entry = _follow(alice, title)
    LibraryEntry.objects.filter(pk=entry.pk).update(
        added_at=datetime(2026, 11, 21, 8, 0, tzinfo=UTC)
    )

    assert scan_due(NOW) == 0
    assert not _rows().exists()


def test_events_before_notify_since_are_ignored(alice):
    _user(alice)
    alice.profile.notify_since = datetime(2026, 11, 21, 8, 0, tzinfo=UTC)
    alice.profile.save()
    title, _, _ = _show()
    _follow(alice, title)

    assert scan_due(NOW) == 0
    assert not _rows().exists()


def test_the_window_is_forty_eight_hours(alice):
    assert WINDOW == timedelta(hours=48)
    _user(alice, 'UTC')
    title, _, _ = _show()
    _follow(alice, title)
    due = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

    assert scan_due(due + timedelta(hours=49)) == 0
    assert scan_due(due + timedelta(hours=47)) == 1


def test_a_second_scan_changes_nothing(alice):
    _user(alice)
    title, _, _ = _show()
    _follow(alice, title)
    scan_due(NOW)
    read_at = datetime(2026, 11, 21, 12, 1, tzinfo=UTC)
    Notification.objects.update(read_at=read_at)

    assert scan_due(NOW + timedelta(minutes=15)) == 0

    row = _rows().get()
    assert row.read_at == read_at


def test_two_episodes_of_one_day_merge_and_unread_again(alice):
    _user(alice)
    title, season, first = _show()
    _follow(alice, title)
    scan_due(NOW)
    Notification.objects.update(read_at=NOW)
    second = Episode.objects.create(season=season, number=2, air_date=DAY)

    assert scan_due(NOW + timedelta(minutes=15)) == 1

    row = _rows().get()
    assert row.payload == {'episodes': [first.pk, second.pk]}
    assert row.read_at is None


def test_watched_episodes_are_skipped(alice, bob):
    _user(alice)
    _user(bob)
    title, season, first = _show()
    second = Episode.objects.create(season=season, number=2, air_date=DAY)
    _follow(alice, title)
    _follow(bob, title)
    watch(alice, first)
    watch(bob, second)

    scan_due(NOW)

    assert _rows(alice).get().payload == {'episodes': [second.pk]}
    assert _rows(bob).get().payload == {'episodes': [first.pk]}


def test_completed_and_dropped_entries_get_nothing(alice, bob):
    _user(alice)
    _user(bob)
    title, _, _ = _show()
    _follow(alice, title, Status.COMPLETED)
    _follow(bob, title, Status.DROPPED)

    assert scan_due(NOW) == 0


def test_an_unknown_zone_counts_as_utc(alice):
    _user(alice, 'Gone/Zone')
    title, _, _ = _show()
    _follow(alice, title)

    assert scan_due(datetime(2026, 11, 21, 9, 0, tzinfo=UTC)) == 1
    assert _rows().get().day == DAY


def test_rows_are_per_user(alice, bob):
    _user(alice)
    _user(bob)
    title, _, _ = _show()
    _follow(alice, title)
    _follow(bob, title)

    assert scan_due(NOW) == 2

    assert _rows(alice).count() == 1
    assert _rows(bob).count() == 1


def _count_queries(users: int, titles: int) -> int:
    people = [_user(_member(f'9{users}{n}')) for n in range(users)]
    for index in range(titles):
        title, _, _ = _show()
        for person in people:
            _follow(person, title)
    with CaptureQueriesContext(connection) as queries:
        assert scan_due(NOW) == users * titles
    return len(queries)


def test_the_query_count_does_not_grow(db):
    small = _count_queries(1, 1)
    Notification.objects.all().delete()
    LibraryEntry.objects.all().delete()
    Episode.objects.all().delete()
    large = _count_queries(4, 4)
    assert small == large


def test_switching_the_zone_east_delivers_an_already_due_event(alice):
    _user(alice, 'America/Los_Angeles')
    title, _, _ = _show()
    _follow(alice, title)
    alice.profile.timezone = 'Europe/Kyiv'  # what the zone view does, nothing more
    alice.profile.save(update_fields=['timezone'])

    # 09:00 in Kyiv is 07:00 UTC, already past; 09:00 in LA would be 17:00 UTC.
    assert scan_due(datetime(2026, 11, 21, 8, 0, tzinfo=UTC)) == 1

    row = _rows(alice).get()
    assert row.kind == Notification.Kind.EPISODE_AIRED
    assert row.day == DAY


def test_switching_the_zone_after_delivery_makes_no_duplicate(alice):
    _user(alice, 'Europe/Kyiv')
    title, _, _ = _show()
    _follow(alice, title)
    assert scan_due(datetime(2026, 11, 21, 8, 0, tzinfo=UTC)) == 1
    read_at = datetime(2026, 11, 21, 8, 30, tzinfo=UTC)
    Notification.objects.filter(user=alice).update(read_at=read_at)

    alice.profile.timezone = 'America/Los_Angeles'
    alice.profile.save(update_fields=['timezone'])

    assert scan_due(datetime(2026, 11, 21, 18, 0, tzinfo=UTC)) == 0
    row = _rows(alice).get()
    assert row.read_at == read_at
