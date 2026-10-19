from datetime import timedelta

import pytest
from accounts.models import User
from catalog import tasks as catalog_tasks
from catalog.models import (
    Episode,
    EpisodeTranslation,
    ExternalId,
    Season,
    Title,
    TitleTranslation,
)
from catalog.sync import Clients, sync_title
from fakes import FakeAniList
from providers.clients import connect_redis
from providers.http import ProviderError
from redis.exceptions import ConnectionError as RedisConnectionError
from tracking.models import LibraryEntry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_claims():
    """Delete the claim keys before and after a test, so none leak between runs."""
    client = connect_redis()
    for key in client.scan_iter(f'{catalog_tasks.CLAIM_PREFIX}*'):
        client.delete(key)
    yield
    for key in client.scan_iter(f'{catalog_tasks.CLAIM_PREFIX}*'):
        client.delete(key)


@pytest.fixture
def queued(monkeypatch):
    """Replace the sync task with a recorder of ``(title id, force)``."""
    calls = []
    monkeypatch.setattr(
        catalog_tasks,
        'sync_title_task',
        lambda pk, force=False: calls.append((pk, force)),
    )
    return calls


@pytest.fixture
def tmdb_clients(monkeypatch, fake_clients):
    """Make the language job use the fake clients."""
    monkeypatch.setattr(catalog_tasks, 'default_clients', lambda: fake_clients)
    return fake_clients


def make_title(synced=True, due=False, library_status=None, user=None, kind='tv'):
    """Create a title; ``due`` means its next sync time has passed."""
    now = catalog_tasks.timezone.now()
    title = Title.objects.create(
        kind=kind,
        original_title='x',
        synced_at=now - timedelta(days=2) if synced else None,
        next_sync_at=now - timedelta(minutes=1) if due else None,
    )
    if synced and not due:
        title.next_sync_at = now + timedelta(days=1)
        title.save()
    if library_status:
        LibraryEntry.objects.create(user=user, title=title, status=library_status)
    return title


def leased(pk):
    """Return whether the scheduler has leased the title."""
    next_sync_at = Title.objects.get(pk=pk).next_sync_at
    return next_sync_at is not None and next_sync_at > catalog_tasks.timezone.now()


def test_untracked_titles_are_not_scheduled(alice, queued):
    never = make_title(synced=False)
    tracked = make_title(due=True, library_status='planned', user=alice)
    untracked = make_title(due=True)
    assert catalog_tasks.sync_due_titles.call_local() == 2
    scheduled = {pk for pk in (never.pk, tracked.pk, untracked.pk) if leased(pk)}
    assert scheduled == {never.pk, tracked.pk}
    assert {pk for pk, _ in queued} == scheduled


def test_watching_titles_are_queued_before_others(alice, bob, queued, monkeypatch):
    monkeypatch.setattr(catalog_tasks, 'DUE_BATCH', 1)
    completed = make_title(due=True, library_status='completed', user=alice)
    planned = make_title(due=True, library_status='planned', user=alice)
    watching = make_title(due=True, library_status='watching', user=alice)
    LibraryEntry.objects.create(user=bob, title=completed, status='dropped')
    catalog_tasks.sync_due_titles.call_local()
    assert [pk for pk, _ in queued] == [watching.pk]
    assert not leased(planned.pk) and not leased(completed.pk)


def test_the_full_order_is_never_synced_watching_planned_the_rest(alice, queued):
    rest = make_title(due=True, library_status='dropped', user=alice)
    planned = make_title(due=True, library_status='planned', user=alice)
    watching = make_title(due=True, library_status='watching', user=alice)
    never = make_title(synced=False)
    catalog_tasks.sync_due_titles.call_local()
    assert [pk for pk, _ in queued] == [never.pk, watching.pk, planned.pk, rest.pk]


def test_a_title_in_two_libraries_is_queued_once(alice, bob, queued):
    title = make_title(due=True, library_status='planned', user=alice)
    LibraryEntry.objects.create(user=bob, title=title, status='watching')
    assert catalog_tasks.sync_due_titles.call_local() == 1
    assert queued == [(title.pk, True)]


def test_claim_is_true_once_per_window(redis_client):
    assert catalog_tasks.claim('policy-test', 60) is True
    assert catalog_tasks.claim('policy-test', 60) is False
    key = f'{catalog_tasks.CLAIM_PREFIX}policy-test'
    assert 0 < redis_client.ttl(key) <= 60


def test_enqueue_sync_once_skips_a_fresh_title_and_a_second_call(queued):
    fresh = make_title()
    assert catalog_tasks.enqueue_sync_once(fresh) is False
    stale = make_title(due=True)
    assert catalog_tasks.enqueue_sync_once(stale) is True
    assert catalog_tasks.enqueue_sync_once(stale) is False
    never = make_title(synced=False)
    assert catalog_tasks.enqueue_sync_once(never) is True
    assert queued == [(stale.pk, False), (never.pk, False)]


@pytest.fixture
def show(tmdb_clients):
    """Return the synced TMDB show 1396 with two seasons of three episodes."""
    title = Title.objects.create(kind='tv', original_title='x')
    ExternalId.objects.create(title=title, provider='tmdb_tv', external_id='1396')
    sync_title(title, tmdb_clients)
    return title


def test_language_job_fetches_title_and_episode_translations(show):
    assert Episode.objects.filter(season__title=show).count() == 6
    assert catalog_tasks.ensure_language_task.call_local(show.pk, 'ru') == 'done'
    assert show.translations.filter(language='ru').exists()
    assert EpisodeTranslation.objects.filter(language='ru').count() == 6


def test_language_job_for_an_unknown_title_is_a_noop(tmdb_clients):
    assert catalog_tasks.ensure_language_task.call_local(999999, 'ru') == 'missing'


def test_language_job_without_tmdb_is_a_noop(monkeypatch):
    monkeypatch.setattr(
        catalog_tasks,
        'default_clients',
        lambda: Clients(tmdb=None, anilist=FakeAniList()),
    )
    title = Title.objects.create(kind='tv', original_title='x')
    assert catalog_tasks.ensure_language_task.call_local(title.pk, 'ru') == 'no tmdb'
    assert not TitleTranslation.objects.exists()


def test_language_job_propagates_a_provider_error(monkeypatch, failing_clients):
    monkeypatch.setattr(catalog_tasks, 'default_clients', lambda: failing_clients)
    title = Title.objects.create(kind='movie', original_title='x')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    with pytest.raises(ProviderError):
        catalog_tasks.ensure_language_task.call_local(title.pk, 'ru')


def test_enqueue_language_once_queues_a_job_behind_a_claim(monkeypatch):
    calls = []
    monkeypatch.setattr(
        catalog_tasks, 'ensure_language_task', lambda *args: calls.append(args)
    )
    title = Title.objects.create(kind='tv', original_title='x')
    assert catalog_tasks.enqueue_language_once(title, 'ru') is True
    assert catalog_tasks.enqueue_language_once(title, 'ru') is False
    assert catalog_tasks.enqueue_language_once(title, 'de') is True
    assert calls == [(title.pk, 'ru'), (title.pk, 'de')]


def test_english_never_needs_a_language_job(monkeypatch):
    calls = []
    monkeypatch.setattr(
        catalog_tasks, 'ensure_language_task', lambda *args: calls.append(args)
    )
    title = Title.objects.create(kind='tv', original_title='x')
    assert catalog_tasks.enqueue_language_once(title, 'en') is False
    assert calls == []


def test_gc_keeps_english_and_languages_in_use(alice):
    User.objects.create(username='carol')
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    title = Title.objects.create(kind='tv', original_title='x')
    season = Season.objects.create(title=title, number=1, kind='tv')
    episode = Episode.objects.create(season=season, number=1)
    for language in ('en', 'ru', 'de'):
        TitleTranslation.objects.create(title=title, language=language, name='n')
        EpisodeTranslation.objects.create(episode=episode, language=language, name='n')
    assert catalog_tasks.gc_translations.call_local() == 2
    assert set(title.translations.values_list('language', flat=True)) == {'en', 'ru'}
    assert set(episode.translations.values_list('language', flat=True)) == {'en', 'ru'}


def test_a_title_watched_by_one_user_and_planned_by_another_ranks_as_watching(
    alice, bob, queued, monkeypatch
):
    monkeypatch.setattr(catalog_tasks, 'DUE_BATCH', 1)
    planned_only = make_title(due=True, library_status='planned', user=alice)
    mixed = make_title(due=True, library_status='planned', user=alice)
    LibraryEntry.objects.create(user=bob, title=mixed, status='watching')
    catalog_tasks.sync_due_titles.call_local()
    assert [pk for pk, _ in queued] == [mixed.pk]
    assert not leased(planned_only.pk)


def refuse(*args, **kwargs):
    raise RedisConnectionError('down')


@pytest.fixture
def enqueue_calls(monkeypatch):
    """Record the sync and language tasks the callers queue."""
    calls = []
    monkeypatch.setattr(catalog_tasks, 'sync_title_task', calls.append)
    monkeypatch.setattr(
        catalog_tasks, 'ensure_language_task', lambda *args: calls.append(args)
    )
    return calls


def test_a_redis_outage_does_not_break_the_enqueue_helpers(monkeypatch, enqueue_calls):
    monkeypatch.setattr(catalog_tasks, 'connect_redis', refuse)
    title = make_title(synced=False)
    assert catalog_tasks.enqueue_sync_once(title) is False
    assert catalog_tasks.enqueue_language_once(title, 'ru') is False
    assert enqueue_calls == []


def test_a_failing_enqueue_returns_false_and_frees_the_claim(monkeypatch):
    title = make_title(synced=False)
    monkeypatch.setattr(catalog_tasks, 'sync_title_task', refuse)
    monkeypatch.setattr(catalog_tasks, 'ensure_language_task', refuse)
    assert catalog_tasks.enqueue_sync_once(title) is False
    assert catalog_tasks.claim(f'sync-queued:{title.pk}', 300) is True
    assert catalog_tasks.enqueue_language_once(title, 'ru') is False
    assert catalog_tasks.claim(f'language-queued:{title.pk}:ru', 3600) is True
