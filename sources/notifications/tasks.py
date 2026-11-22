"""Background tasks of the notifications app."""

import logging
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task
from notifications.deliver import send_deliveries
from notifications.models import Notification
from notifications.scan import scan_due

logger = logging.getLogger(__name__)

READ_RETENTION = timedelta(days=90)
MAX_AGE = timedelta(days=180)


@db_periodic_task(crontab(minute='*/15'))
def notify_due() -> int:
    """Write the notifications for the releases that became due.

    Returns:
        The number of notification rows created or changed.
    """
    count = scan_due(timezone.now())
    logger.info('Notification scan wrote %d rows', count)
    return count


@db_periodic_task(crontab(minute='2,17,32,47'))
def send_deliveries_task() -> int:
    """Send the e-mail digests that are due, after the scanner has run.

    Returns:
        The number of messages sent.
    """
    count = send_deliveries(timezone.now())
    logger.info('Email delivery sent %d messages', count)
    return count


@db_periodic_task(crontab(day_of_week='0', hour='5', minute='0'))
def gc_notifications(now: datetime | None = None) -> int:
    """Delete read notifications after 90 days and every notification after 180.

    A system job: it spans all users, so it does not go through ``for_user``.

    Args:
        now: The moment to measure the ages from; the clock when omitted.

    Returns:
        The number of rows deleted.
    """
    now = now or timezone.now()
    old = Q(read_at__lt=now - READ_RETENTION) | Q(created_at__lt=now - MAX_AGE)
    # delete() totals every cascaded model; only the notifications are reported.
    count = (
        Notification.objects.filter(old)
        .delete()[1]
        .get('notifications.Notification', 0)
    )
    logger.info('Deleted %d old notifications', count)
    return count
