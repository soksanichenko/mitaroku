from datetime import timedelta

import pytest
from catalog import tasks
from catalog.models import ExternalId, Season, Title
from catalog.sync import Clients
from django.core.management import CommandError, call_command
from django.utils import timezone
from fakes import FakeAniList, media
from huey.contrib.djhuey import lock_task
from providers.http import ProviderError

pytestmark = pytest.mark.django_db


@pytest.fixture
def synced(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, 'sync_title', lambda title: calls.append(title.pk))
    return calls


def make_title(**kwargs):
    return Title.objects.create(kind='tv', original_title='x', **kwargs)


def test_a_never_synced_title_is_synced(synced):
    title = make_title()
    assert tasks.sync_title_task.call_local(title.pk) == 'synced'
    assert synced == [title.pk]


def test_a_title_that_is_not_due_is_skipped_unless_forced(synced):
    title = make_title(next_sync_at=timezone.now() + timedelta(hours=1))
    assert tasks.sync_title_task.call_local(title.pk) == 'not due'
    assert synced == []
    assert tasks.sync_title_task.call_local(title.pk, force=True) == 'synced'
    assert synced == [title.pk]


def test_an_unknown_title_id_is_not_an_error(synced):
    assert tasks.sync_title_task.call_local(999999) == 'missing'


def test_a_title_being_synced_elsewhere_is_left_alone(synced):
    title = make_title()
    with lock_task(f'sync-title-{title.pk}'):
        assert tasks.sync_title_task.call_local(title.pk) == 'busy'
    assert synced == []


def test_a_provider_error_propagates_so_huey_can_retry(monkeypatch):
    def boom(title):
        raise ProviderError('down')

    monkeypatch.setattr(tasks, 'sync_title', boom)
    title = make_title()
    with pytest.raises(ProviderError):
        tasks.sync_title_task.call_local(title.pk)


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tasks, 'sync_title_task', lambda pk, force=False: calls.append((pk, force))
    )
    return calls


def test_the_scheduler_queues_unsynced_and_due_titles_only(queued):
    now = timezone.now()
    fresh = make_title(synced_at=now, next_sync_at=now + timedelta(days=1))
    never = make_title()
    due = make_title(
        synced_at=now - timedelta(days=2), next_sync_at=now - timedelta(minutes=1)
    )
    assert tasks.sync_due_titles.call_local() == 2
    assert {pk for pk, _ in queued} == {never.pk, due.pk}
    assert fresh.pk not in {pk for pk, _ in queued}


def test_the_scheduler_serves_never_synced_titles_before_due_ones(queued):
    now = timezone.now()
    for _ in range(tasks.DUE_BATCH):
        make_title(
            synced_at=now - timedelta(days=2), next_sync_at=now - timedelta(minutes=1)
        )
    never = make_title()
    tasks.sync_due_titles.call_local()
    assert never.pk in {pk for pk, _ in queued}


def test_the_scheduler_queues_at_most_one_batch(queued):
    for _ in range(tasks.DUE_BATCH + 5):
        make_title()
    assert tasks.sync_due_titles.call_local() == tasks.DUE_BATCH
    assert len(queued) == tasks.DUE_BATCH


def test_enqueue_waits_for_the_commit(queued, django_capture_on_commit_callbacks):
    title = make_title()
    with django_capture_on_commit_callbacks(execute=True):
        tasks.enqueue_sync(title, force=True)
        assert queued == []
    assert queued == [(title.pk, True)]


@pytest.fixture
def command_clients(monkeypatch):
    """Give the command fake clients, so adding an anime does not use the network."""
    clients = Clients(tmdb=None, anilist=FakeAniList(media(1)))
    monkeypatch.setattr(
        'catalog.management.commands.sync_title.default_clients', lambda: clients
    )


def test_command_adds_and_syncs_a_title(monkeypatch, capsys, command_clients):
    def fake_sync(title, clients):
        season, _ = Season.objects.get_or_create(title=title, number=1, kind='tv')
        season.episodes.get_or_create(number=1)

    monkeypatch.setattr('catalog.management.commands.sync_title.sync_title', fake_sync)
    call_command('sync_title', 'tmdb_tv', '1000')
    out = capsys.readouterr().out
    assert 'Added' in out and '1 seasons, 1 episodes' in out
    assert ExternalId.objects.get().provider == 'tmdb_tv'
    call_command('sync_title', 'tmdb_tv', '1000')
    assert 'Synced' in capsys.readouterr().out
    assert Title.objects.count() == 1


def test_command_reports_a_provider_failure(monkeypatch, command_clients):
    def boom(title, clients):
        raise ProviderError('TMDB is not configured')

    monkeypatch.setattr('catalog.management.commands.sync_title.sync_title', boom)
    with pytest.raises(CommandError, match='not configured'):
        call_command('sync_title', 'anilist', '1')
