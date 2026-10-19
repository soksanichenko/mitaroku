"""Background tasks that keep the catalog fresh."""

import logging
from datetime import timedelta

from accounts.models import Profile
from catalog.models import EpisodeTranslation, Title, TitleTranslation
from catalog.sync import sync_title
from catalog.sync.common import default_clients
from catalog.translations import (
    FALLBACK_LANGUAGE,
    ensure_episode_translations,
    ensure_title_translation,
)
from django.db import transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task
from huey.exceptions import TaskLockedException
from providers.clients import connect_redis
from tracking.models import LibraryEntry

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
CLAIM_PREFIX = 'mitaroku:claims:'
# A page view may ask for a sync at most this often per title.
SYNC_CLAIM_TTL = 300
# A language job is not queued again for the same title and language this soon.
LANGUAGE_CLAIM_TTL = 3600


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


def claim(key: str, ttl: int) -> bool:
    """Claim ``key`` for ``ttl`` seconds.

    Args:
        key: What is being claimed, for example ``sync-queued:42``.
        ttl: How long the claim holds, in seconds.

    Returns:
        True only for the first caller within the window.
    """
    return bool(connect_redis().set(f'{CLAIM_PREFIX}{key}', '1', nx=True, ex=ttl))


def enqueue_sync_once(title: Title) -> bool:
    """Queue a non-forced sync of a never-synced or due title, at most once per window.

    Args:
        title: The title whose page was opened.

    Returns:
        True when a sync was queued.
    """
    due = title.next_sync_at is None or title.next_sync_at <= timezone.now()
    if not due or not claim(f'sync-queued:{title.pk}', SYNC_CLAIM_TTL):
        return False
    sync_title_task(title.pk)
    return True


@db_task(retries=2, retry_delay=60)
def ensure_language_task(title_id: int, language: str) -> str:
    """Fetch the title and episode translations of a title in ``language``.

    A failing provider raises ``ProviderError``, so Huey retries the job.

    Args:
        title_id: Primary key of the title.
        language: Metadata language to fetch.

    Returns:
        ``done``, ``missing`` (no such title) or ``no tmdb`` (no token set).
    """
    title = Title.objects.filter(pk=title_id).first()
    if title is None:
        return 'missing'
    tmdb = default_clients().tmdb
    if tmdb is None:
        return 'no tmdb'
    ensure_title_translation(title, language, tmdb)
    for season in title.seasons.all():
        ensure_episode_translations(season, language, tmdb)
    return 'done'


def enqueue_language_once(title: Title, language: str) -> bool:
    """Queue a translation job for ``title``, at most once an hour per language.

    Args:
        title: The title whose page was opened.
        language: The viewer's metadata language; English is always stored.

    Returns:
        True when a job was queued.
    """
    if language == FALLBACK_LANGUAGE:
        return False
    if not claim(f'language-queued:{title.pk}:{language}', LANGUAGE_CLAIM_TTL):
        return False
    ensure_language_task(title.pk, language)
    return True


@db_periodic_task(crontab(day_of_week='0', hour='4', minute='30'))
def gc_translations() -> int:
    """Delete translations in languages that no profile uses (English is kept).

    Returns:
        How many title and episode translations were deleted.
    """
    in_use = {
        FALLBACK_LANGUAGE,
        *Profile.objects.values_list('metadata_language', flat=True),
    }
    deleted = 0
    for model in (TitleTranslation, EpisodeTranslation):
        deleted += model.objects.exclude(language__in=in_use).delete()[0]
    return deleted


@db_periodic_task(crontab(minute='*/15'))
def sync_due_titles() -> int:
    """Queue the never-synced titles and the due titles that are in a library.

    Every queued title gets a lease: its ``next_sync_at`` moves ``LEASE`` into
    the future (``synced_at`` is untouched, so a never-synced title stays
    recognisable). The sync is then queued with ``force``, because the lease
    itself would make the task answer "not due". Titles nobody has in a library
    are left to be synced when their page is opened. The order is never-synced
    first, then titles somebody is watching, then planned ones, then the rest.

    Returns:
        How many titles were queued.
    """
    now = timezone.now()
    # Built from Title (not LibraryEntry): "is it in anyone's library" is a
    # cross-user fact about the shared catalog, so no user scope applies.
    in_library = Exists(
        Title.objects.filter(pk=OuterRef('pk'), library_entries__isnull=False)
    )
    watching = Exists(
        Title.objects.filter(
            pk=OuterRef('pk'), library_entries__status=LibraryEntry.Status.WATCHING
        )
    )
    planned = Exists(
        Title.objects.filter(
            pk=OuterRef('pk'), library_entries__status=LibraryEntry.Status.PLANNED
        )
    )
    due = list(
        Title.objects.annotate(
            in_library=in_library, watching=watching, planned=planned
        )
        .filter(Q(next_sync_at__isnull=True) | Q(next_sync_at__lte=now))
        .filter(Q(synced_at__isnull=True) | Q(in_library=True))
        .order_by(
            Case(
                When(synced_at__isnull=True, then=Value(0)),
                When(watching=True, then=Value(1)),
                When(planned=True, then=Value(2)),
                default=Value(3),
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
