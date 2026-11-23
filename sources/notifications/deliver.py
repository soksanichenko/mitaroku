"""The shared sender: one message per user, channel and pass, with retries.

A system job that spans all users: it reads ``Notification``, ``Delivery`` and
``Profile`` rows without ``for_user``, like the scanner and the retention job.
Each channel is described by a ``ChannelSpec`` and goes through the same steps.
"""

import enum
import logging
import re
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q, QuerySet
from notifications.channels import EMAIL, TELEGRAM, ChannelSpec
from notifications.errors import PermanentFailure, RetryAfter, Unreachable
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

CHANNELS: tuple[ChannelSpec, ...] = (EMAIL, TELEGRAM)

PENDING = Delivery.Status.PENDING


class Result(enum.Enum):
    """What happened to one user's due deliveries of one channel."""

    NOTHING = enum.auto()
    SENT = enum.auto()
    FAILED = enum.auto()
    UNREACHABLE = enum.auto()


def _materialise(now: datetime, spec: ChannelSpec) -> None:
    """Create a pending delivery for each notification the channel should send.

    Only users with the channel on, a moment it was switched on (nothing earlier
    is ever sent) and a destination qualify, and only notifications younger
    than ``EXPIRY``. A user without a destination gets no row: there is nowhere
    to send it, and the news of the last ``EXPIRY`` is picked up once a
    destination appears. The unique key makes two runs that race here harmless.
    """
    has_delivery = Delivery.objects.filter(
        notification=OuterRef('pk'), channel=spec.channel
    )
    notifications = (
        Notification.objects.filter(
            ~Exists(has_delivery),
            **{f'user__profile__{spec.enabled_field}': True},
            created_at__gt=F(f'user__profile__{spec.since_field}'),
        )
        .filter(created_at__gt=now - EXPIRY)
        .select_related('user__profile')
        .order_by('pk')
    )
    Delivery.objects.bulk_create(
        (
            Delivery(user_id=n.user_id, notification=n, channel=spec.channel)
            for n in notifications
            if spec.destination(n.user)
        ),
        ignore_conflicts=True,
    )


def _settle(now: datetime, spec: ChannelSpec) -> None:
    """Close the channel's pending deliveries that must not be sent any more.

    Expiry goes first, so a delivery that is both old and read is ``expired``.
    """
    # ``Exists`` keeps each update on the delivery table alone. With a join in
    # the filter Django turns the update into ``id IN (subquery)``, so the
    # ``pending`` check would not be repeated on the row once it has waited for
    # another run's lock, and a delivery that run just marked ``sent`` would be
    # overwritten. This way the database re-checks ``status`` after the wait.
    pending = Delivery.objects.filter(channel=spec.channel, status=PENDING)
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


def _due(now: datetime, spec: ChannelSpec) -> QuerySet[Delivery]:
    """Return the channel's pending deliveries that may be sent now.

    The channel must still be on and the notification must be newer than the
    moment it was switched on, so switching it off also stops what is queued
    and a later switch-on does not send the backlog. A notification read after
    the settle step is left for the next one to skip.
    """
    return Delivery.objects.filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now),
        channel=spec.channel,
        status=PENDING,
        notification__read_at__isnull=True,
        notification__created_at__gt=F(f'user__profile__{spec.since_field}'),
        **{f'user__profile__{spec.enabled_field}': True},
    )


def _error_text(exc: Exception, secret: str, placeholder: str = '<address>') -> str:
    """Describe ``exc`` for ``Delivery.last_error`` without the secret.

    The secret is replaced by ``placeholder`` before the text is cut, so the cut
    cannot leave a part of it behind. The whole text is capped to the field,
    whatever the length of the exception class name. An empty secret is not
    scrubbed: it would match between every two characters.
    """
    message = str(exc)
    if secret:
        message = re.sub(re.escape(secret), placeholder, message, flags=re.IGNORECASE)
    return f'{type(exc).__name__}: {message[:ERROR_LENGTH]}'[:ERROR_FIELD_LENGTH]


def _record_failure(
    deliveries: list[Delivery],
    error: str,
    now: datetime,
    minimum: timedelta = timedelta(0),
) -> None:
    """Count a failed attempt on each delivery and schedule the next one.

    The next attempt waits for the usual pause, or for ``minimum`` when the
    service asked for a longer one.
    """
    for delivery in deliveries:
        delivery.attempts += 1
        delivery.last_error = error
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = Delivery.Status.FAILED
        else:
            pause = RETRY_PAUSES[delivery.attempts - 1]
            delivery.next_attempt_at = now + max(pause, minimum)
    Delivery.objects.bulk_update(
        deliveries, ['attempts', 'last_error', 'status', 'next_attempt_at']
    )


def _send_to_user(user_id: int, now: datetime, spec: ChannelSpec) -> Result:
    """Send one message with one user's due deliveries of the channel.

    One transaction per user: the rows are locked, sent and marked in it, so
    a delivery is sent only by the run that holds its lock, and a failure of
    this user is saved with its own lock and never touches the other users'
    committed marks. ``skip_locked`` makes an overlapping run pass over rows
    that another run is sending. A crash between ``send`` and the commit can
    repeat one message; that at-least-once case is accepted.

    A permanent refusal skips all the user's pending deliveries of the channel
    and switches it off, without counting an attempt. A failure to reach the
    service is recorded like any other, and reported so the pass can stop.
    """
    with transaction.atomic():
        deliveries = list(
            _due(now, spec)
            .filter(user_id=user_id)
            # Lock only the delivery rows, not the notification, user or profile.
            .select_for_update(skip_locked=True, of=('self',))
            .select_related('notification__title', 'user__profile')
            .order_by('pk')
        )
        if not deliveries:
            return Result.NOTHING
        user = deliveries[0].user
        if not spec.destination(user):
            # The destination was removed after the rows were made; they expire.
            return Result.NOTHING
        secret = spec.secret(user)
        try:
            # A savepoint: a database error while building must not poison the
            # transaction that still has to save the failure.
            with transaction.atomic():
                spec.send(user, [delivery.notification for delivery in deliveries], now)
        except PermanentFailure as exc:
            error = _error_text(exc, secret, spec.placeholder)
            logger.error(
                '%s delivery refused for good for user %s: %s',
                spec.channel.value,
                user_id,
                error,
            )
            Delivery.objects.for_user(user).filter(
                channel=spec.channel, status=PENDING
            ).update(status=Delivery.Status.SKIPPED, last_error=error)
            spec.disable(user)
            return Result.FAILED
        except Exception as exc:
            error = _error_text(exc, secret, spec.placeholder)
            # Not ``logger.exception``: the traceback repeats the exception
            # text, which can hold the secret.
            logger.error(
                '%s delivery failed for user %s: %s', spec.channel.value, user_id, error
            )
            minimum = (
                timedelta(seconds=exc.seconds)
                if isinstance(exc, RetryAfter)
                else timedelta(0)
            )
            _record_failure(deliveries, error, now, minimum)
            return Result.UNREACHABLE if isinstance(exc, Unreachable) else Result.FAILED
        for delivery in deliveries:
            delivery.status = Delivery.Status.SENT
            delivery.sent_at = now
            delivery.attempts += 1
        Delivery.objects.bulk_update(deliveries, ['status', 'sent_at', 'attempts'])
        return Result.SENT


def send_deliveries(now: datetime) -> int:
    """Create, settle and send the deliveries of every configured channel.

    The channels run in the order of ``CHANNELS``; each sends one message per
    user. A channel that is not configured is passed over. When a channel's
    service cannot be reached, the rest of its users wait for the next pass
    untouched: they would fail the same way.

    The number of queries does not depend on the number of users or
    notifications, apart from the locking, sending and saving done once per
    user.

    Args:
        now: The current moment.

    Returns:
        The number of messages sent over all channels.
    """
    sent = 0
    for spec in CHANNELS:
        if not spec.configured():
            continue
        _materialise(now, spec)
        _settle(now, spec)
        user_ids = list(
            _due(now, spec)
            .order_by('user_id')
            .values_list('user_id', flat=True)
            .distinct()
        )
        for user_id in user_ids:
            result = _send_to_user(user_id, now, spec)
            if result is Result.SENT:
                sent += 1
            elif result is Result.UNREACHABLE:
                break
    return sent
