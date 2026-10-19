"""Background tasks that keep the catalog fresh."""

import logging
from datetime import timedelta

from catalog.models import Title
from catalog.sync import sync_title
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task
from huey.exceptions import TaskLockedException

logger = logging.getLogger(__name__)

# At most this many titles are queued per scheduler run, so a large backlog
# cannot flood the rate-limited providers.
DUE_BATCH = 20
# A crashed worker must not block a title for ever: the lock expires.
LOCK_TTL = 900
# A queued title is pushed this far into the future, so one that keeps failing
# moves to the back of the queue instead of blocking the others. A successful
# sync overwrites it with the real schedule.
LEASE = timedelta(hours=1)


@db_task(retries=3, retry_delay=300)
def sync_title_task(title_id: int, force: bool = False) -> str:
    """Sync one title; a failing provider is retried by Huey.

    Args:
        title_id: Primary key of the title.
        force: Sync even when the title is not due yet.

    Returns:
        ``synced``, or why nothing was done: ``missing``, ``not due``, ``busy``.
    """
    title = Title.objects.filter(pk=title_id).first()
    if title is None:
        return 'missing'
    now = timezone.now()
    if not force and title.next_sync_at and title.next_sync_at > now:
        return 'not due'
    try:
        with lock_task(f'sync-title-{title_id}', ttl=LOCK_TTL):
            sync_title(title)
    except TaskLockedException:
        return 'busy'
    return 'synced'


def enqueue_sync(title: Title, force: bool = False) -> None:
    """Queue a sync of ``title`` once the surrounding transaction commits."""
    transaction.on_commit(lambda: sync_title_task(title.pk, force))


@db_periodic_task(crontab(minute='*/15'))
def sync_due_titles() -> int:
    """Queue the titles that were never synced or whose next sync time has come.

    Every queued title gets a lease: its ``next_sync_at`` moves ``LEASE`` into
    the future (``synced_at`` is untouched, so a never-synced title stays
    recognisable). The sync is then queued with ``force``, because the lease
    itself would make the task answer "not due".

    Returns:
        How many titles were queued.
    """
    now = timezone.now()
    due = list(
        Title.objects.filter(Q(next_sync_at__isnull=True) | Q(next_sync_at__lte=now))
        .order_by(
            Case(
                When(synced_at__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            'next_sync_at',
            'pk',
        )
        .values_list('pk', flat=True)[:DUE_BATCH]
    )
    Title.objects.filter(pk__in=due).update(next_sync_at=now + LEASE)
    for pk in due:
        sync_title_task(pk, True)
    return len(due)
