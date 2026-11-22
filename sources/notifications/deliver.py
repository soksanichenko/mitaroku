"""The e-mail sender: one digest per user and pass, with retries.

A system job that spans all users: it reads ``Notification``, ``Delivery`` and
``Profile`` rows without ``for_user``, like the scanner and the retention job.
"""

import logging
import re
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, QuerySet
from notifications.channel import effective_address, email_configured
from notifications.digest import build_digest
from notifications.models import Delivery, Notification

logger = logging.getLogger(__name__)

EXPIRY = timedelta(hours=48)
RETRY_PAUSES = (
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(hours=6),
)
MAX_ATTEMPTS = 5
ERROR_LENGTH = 150
ERROR_FIELD_LENGTH = Delivery._meta.get_field('last_error').max_length

EMAIL = Delivery.Channel.EMAIL
PENDING = Delivery.Status.PENDING


def _materialise(now: datetime) -> None:
    """Create a pending delivery for each notification that should be mailed.

    Only users with the channel on, a moment it was switched on (nothing earlier
    is ever mailed) and an address qualify, and only notifications younger than
    ``EXPIRY``. A user without an address gets no row: there is nowhere to
    send it, and the news of the last ``EXPIRY`` is picked up once an address
    appears. The unique key makes two runs that race here harmless.
    """
    has_delivery = Delivery.objects.filter(notification=OuterRef('pk'), channel=EMAIL)
    notifications = (
        Notification.objects.filter(
            ~Exists(has_delivery),
            user__profile__email_enabled=True,
            created_at__gt=F('user__profile__email_since'),
        )
        .filter(created_at__gt=now - EXPIRY)
        .select_related('user__profile')
        .order_by('pk')
    )
    Delivery.objects.bulk_create(
        (
            Delivery(user_id=n.user_id, notification=n, channel=EMAIL)
            for n in notifications
            if effective_address(n.user)
        ),
        ignore_conflicts=True,
    )


def _settle(now: datetime) -> None:
    """Close the pending deliveries that must not be sent any more.

    Expiry goes first, so a delivery that is both old and read is ``expired``.
    """
    # ``Exists`` keeps each update on the delivery table alone. With a join in
    # the filter Django turns the update into ``id IN (subquery)``, so the
    # ``pending`` check would not be repeated on the row once it has waited for
    # another run's lock, and a delivery that run just marked ``sent`` would be
    # overwritten. This way the database re-checks ``status`` after the wait.
    pending = Delivery.objects.filter(channel=EMAIL, status=PENDING)
    pending.filter(
        Exists(
            Notification.objects.filter(
                pk=OuterRef('notification_id'), created_at__lt=now - EXPIRY
            )
        )
    ).update(status=Delivery.Status.EXPIRED)
    pending.filter(
        Exists(
            Notification.objects.filter(
                pk=OuterRef('notification_id'), read_at__isnull=False
            )
        )
    ).update(status=Delivery.Status.SKIPPED)


def _due(now: datetime) -> QuerySet[Delivery]:
    """Return the pending deliveries that may be sent now.

    The channel must still be on and the notification must be newer than the
    moment it was switched on, so an unsubscribe also stops what is queued and
    a later switch-on does not mail the backlog. A notification read after the
    settle step is left for the next one to skip.
    """
    return Delivery.objects.filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
        channel=EMAIL,
        status=PENDING,
        user__profile__email_enabled=True,
        notification__read_at__isnull=True,
        notification__created_at__gt=F('user__profile__email_since'),
    )


def _error_text(exc: Exception, address: str) -> str:
    """Describe ``exc`` for ``Delivery.last_error`` without the recipient.

    The address is replaced before the text is cut, so the cut cannot leave a
    part of it behind. The whole text is capped to the field, whatever the
    length of the exception class name.
    """
    message = re.sub(re.escape(address), '<address>', str(exc), flags=re.IGNORECASE)
    return f'{type(exc).__name__}: {message[:ERROR_LENGTH]}'[:ERROR_FIELD_LENGTH]


def _record_failure(deliveries: list[Delivery], error: str, now: datetime) -> None:
    """Count a failed attempt on each delivery and schedule the next one."""
    for delivery in deliveries:
        delivery.attempts += 1
        delivery.last_error = error
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = Delivery.Status.FAILED
        else:
            delivery.next_attempt_at = now + RETRY_PAUSES[delivery.attempts - 1]
    Delivery.objects.bulk_update(
        deliveries, ['attempts', 'last_error', 'status', 'next_attempt_at']
    )


def _send_to_user(user_id: int, now: datetime) -> bool:
    """Send the digest of one user's due deliveries; return whether it went out.

    One transaction per user: the rows are locked, mailed and marked in it, so
    a delivery is sent only by the run that holds its lock, and a failure of
    this user is saved with its own lock and never touches the other users'
    committed marks. ``skip_locked`` makes an overlapping run pass over rows
    that another run is sending. A crash between ``send()`` and the commit can
    repeat one mail; that at-least-once case is accepted.
    """
    with transaction.atomic():
        deliveries = list(
            _due(now)
            .filter(user_id=user_id)
            # Lock only the delivery rows, not the notification, user or profile.
            .select_for_update(skip_locked=True, of=('self',))
            .select_related('notification__title', 'user__profile')
            .order_by('pk')
        )
        if not deliveries:
            return False
        user = deliveries[0].user
        address = effective_address(user)
        if not address:
            # The address was removed after the rows were made; they expire.
            return False
        try:
            # A savepoint: a database error while building must not poison the
            # transaction that still has to save the failure.
            with transaction.atomic():
                build_digest(
                    user, [delivery.notification for delivery in deliveries], now
                ).send()
        except Exception as exc:
            error = _error_text(exc, address)
            # Not ``logger.exception``: the traceback repeats the exception
            # text, which can hold the recipient address.
            logger.error('Email delivery failed for user %s: %s', user_id, error)
            _record_failure(deliveries, error, now)
            return False
        for delivery in deliveries:
            delivery.status = Delivery.Status.SENT
            delivery.sent_at = now
            delivery.attempts += 1
        Delivery.objects.bulk_update(deliveries, ['status', 'sent_at', 'attempts'])
        return True


def send_deliveries(now: datetime) -> int:
    """Create, settle and send the e-mail deliveries; one message per user.

    Does nothing when outgoing email is not configured. The number of queries
    does not depend on the number of users or notifications, apart from the
    locking, sending and saving done once per user.

    Args:
        now: The current moment.

    Returns:
        The number of messages sent.
    """
    if not email_configured():
        return 0
    _materialise(now)
    _settle(now)
    user_ids = list(
        _due(now).order_by('user_id').values_list('user_id', flat=True).distinct()
    )
    return sum(_send_to_user(user_id, now) for user_id in user_ids)
