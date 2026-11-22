from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from notifications.moments import due_moment, local_day, user_zone, zone_names


def test_due_moment_is_nine_local():
    moment = due_moment(date(2026, 11, 21), 'Europe/Kyiv')
    assert moment == datetime(2026, 11, 21, 7, 0, tzinfo=UTC)


def test_due_moment_half_hour_and_far_east_zones():
    assert due_moment(date(2026, 11, 21), 'Asia/Kolkata') == datetime(
        2026, 11, 21, 3, 30, tzinfo=UTC
    )
    assert due_moment(date(2026, 11, 21), 'Pacific/Auckland') == datetime(
        2026, 11, 20, 20, 0, tzinfo=UTC
    )


def test_due_moment_follows_daylight_saving():
    moment = due_moment(date(2026, 7, 1), 'Europe/Kyiv')
    assert moment == datetime(2026, 7, 1, 6, 0, tzinfo=UTC)


def test_local_day_crosses_midnight():
    moment = datetime(2026, 11, 21, 23, 30, tzinfo=UTC)
    assert local_day(moment, 'Asia/Tokyo') == date(2026, 11, 22)


@pytest.mark.parametrize('name', ['', 'Nope/Zone', '../etc', 'a' * 200, ' '])
def test_user_zone_never_raises(name):
    assert user_zone(name) == ZoneInfo('UTC')


def test_zone_names_start_with_utc_and_are_valid():
    names = zone_names()
    assert names[0] == 'UTC'
    assert names[1:] == sorted(names[1:])
    assert 'Europe/Kyiv' in names
    assert not [n for n in names if n.startswith(('Etc/', 'posix/', 'right/'))]
    assert all(user_zone(n).key == n for n in names)


def test_zone_names_drop_factory_and_are_cached():
    zone_names.cache_clear()
    names = zone_names()
    assert 'Factory' not in names
    assert zone_names() is names
    zone_names.cache_clear()
