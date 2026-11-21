"""Background tasks of the notifications app."""

import logging

from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task
from notifications.scan import scan_due

logger = logging.getLogger(__name__)


@db_periodic_task(crontab(minute='*/15'))
def notify_due() -> int:
    """Write the notifications for the releases that became due.

    Returns:
        The number of notification rows created or changed.
    """
    count = scan_due(timezone.now())
    logger.info('Notification scan wrote %d rows', count)
    return count
