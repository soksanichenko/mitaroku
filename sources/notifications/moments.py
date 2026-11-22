"""The 09:00-in-the-user's-zone moments at which releases become due."""

from datetime import date, datetime, time
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

NOTIFY_HOUR = 9
_HIDDEN_PREFIXES = ('Etc/', 'posix/', 'right/', 'SystemV/')
# Not a place anyone lives in: a test placeholder shipped with the tz database.
_HIDDEN_NAMES = ('UTC', 'Factory')


def user_zone(name: str) -> ZoneInfo:
    """Return the zone called ``name``, or UTC when it is empty or unusable.

    Args:
        name: An IANA zone name as stored on the profile.

    Returns:
        The zone; UTC for an empty, unknown or malformed name. Never raises.
    """
    if name:
        try:
            return ZoneInfo(name)
        except (ValueError, OSError, LookupError):
            # ZoneInfoNotFoundError is a LookupError; a removed or hostile name
            # must behave as UTC instead of breaking a scan or a page.
            pass
    return ZoneInfo('UTC')


def due_moment(day: date, zone_name: str) -> datetime:
    """Return the moment a release dated ``day`` is due for a user.

    Args:
        day: The release date.
        zone_name: The user's IANA zone name.

    Returns:
        An aware datetime at ``NOTIFY_HOUR`` o'clock local time on ``day``.
    """
    return datetime.combine(day, time(NOTIFY_HOUR), tzinfo=user_zone(zone_name))


def local_day(moment: datetime, zone_name: str) -> date:
    """Return the calendar day of ``moment`` in the user's zone.

    Args:
        moment: An aware datetime.
        zone_name: The user's IANA zone name.

    Returns:
        The local date.
    """
    return moment.astimezone(user_zone(zone_name)).date()


@cache
def zone_names() -> list[str]:
    """Return the zone names offered in settings, ``UTC`` first, then sorted.

    The result is cached (the tz database does not change while the process
    runs), so callers must not modify the returned list.
    """
    names = sorted(
        name
        for name in available_timezones()
        if name not in _HIDDEN_NAMES
        and not name.startswith(_HIDDEN_PREFIXES)
        and user_zone(name).key == name
    )
    return ['UTC', *names]
