"""Adding titles to a library, through a background request when needed.

The browser sends only a provider, an id and a status. Everything else comes
from the provider, so a forged request cannot put data into the shared catalog.
"""

import logging
import re
from datetime import timedelta

from accounts.models import User
from catalog.models import ExternalId, Title
from catalog.sync import default_clients, get_or_create_title, sync_title
from django.db import IntegrityError, transaction
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task
from providers.http import ProviderError
from providers.types import SearchHit
from tracking.models import AddRequest, LibraryEntry

logger = logging.getLogger(__name__)

PROVIDER_KIND = {'tmdb_movie': 'movie', 'tmdb_tv': 'tv', 'anilist': 'anime'}
MAX_PENDING = 5
# A pending request this old belongs to a job that died or was lost.
PENDING_TIMEOUT = timedelta(hours=1)
# How long a finished request stays, so the page can still poll its result.
FINISHED_RETENTION = timedelta(days=7)

EXTERNAL_ID = re.compile(r'[0-9]{1,10}')


class TooManyPending(Exception):
    """The user already has ``MAX_PENDING`` requests in progress."""


def _add_to_library(user: User, title: Title, status: str) -> LibraryEntry:
    """Return the user's entry for ``title``, creating it with ``status``."""
    entry, _ = LibraryEntry.objects.get_or_create(
        user=user, title=title, defaults={'status': status}
    )
    return entry


def request_add(
    user: User, provider: str, external_id: str, status: str
) -> LibraryEntry | AddRequest:
    """Add a title to the user's library now, or start a background request.

    Args:
        user: Who adds the title.
        provider: ``tmdb_movie``, ``tmdb_tv`` or ``anilist``.
        external_id: The id at that provider: 1 to 10 digits.
        status: The wanted ``LibraryEntry`` status. An entry that already exists
            keeps its own status.

    Returns:
        The library entry when the title is already synced in the catalog,
        otherwise the pending request (an existing one for the same id is
        reused). The job is queued once the transaction commits.

    Raises:
        ValueError: The provider, the id or the status is not valid.
        TooManyPending: The user has ``MAX_PENDING`` other requests in progress.
    """
    if provider not in PROVIDER_KIND:
        raise ValueError(f'Unknown provider: {provider!r}')
    if not EXTERNAL_ID.fullmatch(external_id):
        raise ValueError(f'Invalid external id: {external_id!r}')
    if status not in LibraryEntry.Status.values:
        raise ValueError(f'Unknown status: {status!r}')
    # "007" and "7" are the same provider id.
    external_id = str(int(external_id))

    link = (
        ExternalId.objects.select_related('title')
        .filter(
            provider=provider, external_id=external_id, title__synced_at__isnull=False
        )
        .first()
    )
    if link is not None:
        return _add_to_library(user, link.title, status)

    pending = AddRequest.objects.for_user(user).filter(state=AddRequest.State.PENDING)
    existing = pending.filter(provider=provider, external_id=external_id).first()
    if existing is not None:
        return existing
    if pending.count() >= MAX_PENDING:
        raise TooManyPending(f'{MAX_PENDING} titles are already being added')
    try:
        with transaction.atomic():
            request = AddRequest.objects.create(
                user=user, provider=provider, external_id=external_id, status=status
            )
    except IntegrityError:
        # A concurrent request for the same id won; if its job has already
        # finished, the title is synced now and this call returns the entry.
        return request_add(user, provider, external_id, status)
    transaction.on_commit(lambda: add_title_task(request.pk))
    return request


def _discard_placeholder(title: Title) -> None:
    """Delete a title that this job created and no provider ever described.

    A title that was synced meanwhile or that someone already tracks is kept.
    The checks and the delete are one statement, so a concurrent add cannot slip
    in between.
    """
    Title.objects.filter(
        pk=title.pk, synced_at__isnull=True, library_entries__isnull=True
    ).delete()


@db_task()
def add_title_task(request_id: int) -> str:
    """Create, sync and add the title of a pending request.

    The provider layer already retries, so Huey does not.

    Args:
        request_id: Primary key of the ``AddRequest``.

    Returns:
        ``done``, ``failed`` (the provider failed; the request says so) or
        ``skipped`` (the request is missing or no longer pending).
    """
    # Reads of a request go through its owner, like everywhere else.
    user = User.objects.filter(add_requests__pk=request_id).first()
    if user is None:
        return 'skipped'
    request = (
        AddRequest.objects.for_user(user)
        .filter(pk=request_id, state=AddRequest.State.PENDING)
        .first()
    )
    if request is None:
        return 'skipped'
    hit = SearchHit(
        provider=request.provider,
        external_id=request.external_id,
        kind=PROVIDER_KIND[request.provider],
        title='',
        original_title='',
        year=None,
        poster_url='',
        overview='',
    )
    # One pair of clients per job: each opens a connection that is not closed.
    clients = default_clients()
    created = False
    title = None
    try:
        title, created = get_or_create_title(hit, clients)
        sync_title(title, clients)
    except ProviderError as error:
        logger.warning('Adding %s:%s failed: %s', hit.provider, hit.external_id, error)
        if created:
            _discard_placeholder(title)
        _finish(user, request, AddRequest.State.FAILED, None, AddRequest.Error.PROVIDER)
        return 'failed'
    _add_to_library(user, title, request.status)
    _finish(user, request, AddRequest.State.DONE, title, AddRequest.Error.NONE)
    return 'done'


def _finish(
    user: User,
    request: AddRequest,
    state: str,
    title: Title | None,
    error: str,
) -> None:
    """Close a request, unless something else (the timeout) closed it first."""
    AddRequest.objects.for_user(user).filter(
        pk=request.pk, state=AddRequest.State.PENDING
    ).update(state=state, title=title, error=error, finished_at=timezone.now())


@db_periodic_task(crontab(hour='4', minute='10'))
def cleanup_add_requests() -> int:
    """Time out lost requests and delete old finished ones.

    A pending request older than ``PENDING_TIMEOUT`` fails with ``timeout``; a
    finished one older than ``FINISHED_RETENTION`` is deleted. Requests are
    reached through their owners, like all reads of user data.

    Returns:
        How many requests were changed or deleted.
    """
    now = timezone.now()
    touched = 0
    stale = User.objects.filter(
        add_requests__state=AddRequest.State.PENDING,
        add_requests__created_at__lt=now - PENDING_TIMEOUT,
    ).distinct()
    for user in stale:
        touched += (
            AddRequest.objects.for_user(user)
            .filter(
                state=AddRequest.State.PENDING, created_at__lt=now - PENDING_TIMEOUT
            )
            .update(
                state=AddRequest.State.FAILED,
                error=AddRequest.Error.TIMEOUT,
                finished_at=now,
            )
        )
    old = User.objects.filter(
        add_requests__state__in=[AddRequest.State.DONE, AddRequest.State.FAILED],
        add_requests__finished_at__lt=now - FINISHED_RETENTION,
    ).distinct()
    for user in old:
        deleted, _ = (
            AddRequest.objects.for_user(user)
            .filter(
                state__in=[AddRequest.State.DONE, AddRequest.State.FAILED],
                finished_at__lt=now - FINISHED_RETENTION,
            )
            .delete()
        )
        touched += deleted
    return touched
