"""Background tasks that keep the catalog fresh."""

import logging

from catalog.models import Title
from catalog.sync import sync_title
from django.db import transaction
from django.db.models import F, Q
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

    Returns:
        How many titles were queued.
    """
    due = list(
        Title.objects.filter(
            Q(synced_at__isnull=True) | Q(next_sync_at__lte=timezone.now())
        )
        .order_by(F('next_sync_at').asc(nulls_first=True), 'pk')
        .values_list('pk', flat=True)[:DUE_BATCH]
    )
    for pk in due:
        sync_title_task(pk)
    return len(due)
