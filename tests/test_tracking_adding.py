from datetime import timedelta

import pytest
from catalog.models import ExternalId, Title
from django.db import IntegrityError
from django.utils import timezone
from tracking import adding
from tracking.models import AddRequest, LibraryEntry

pytestmark = pytest.mark.django_db


def test_a_synced_title_is_added_at_once(alice, synced_movie):
    entry = adding.request_add(alice, 'tmdb_movie', '603', 'planned')
    assert isinstance(entry, LibraryEntry) and entry.status == 'planned'
    assert entry.title == synced_movie
    assert not AddRequest.objects.exists()


def test_an_existing_entry_keeps_its_status(alice, synced_movie):
    LibraryEntry.objects.create(user=alice, title=synced_movie, status='completed')
    entry = adding.request_add(alice, 'tmdb_movie', '603', 'planned')
    assert entry.status == 'completed'
    assert LibraryEntry.objects.for_user(alice).count() == 1


def test_a_title_that_was_never_synced_still_needs_a_request(alice, capture_on_commit):
    title = Title.objects.create(kind='movie', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    assert isinstance(
        adding.request_add(alice, 'tmdb_movie', '603', 'planned'), AddRequest
    )


def test_a_new_title_becomes_a_pending_request_and_is_queued(
    alice, capture_on_commit, queued
):
    with capture_on_commit():
        request = adding.request_add(alice, 'tmdb_tv', '1396', 'watching')
        assert queued == []
    assert request.state == 'pending'
    assert (request.provider, request.external_id, request.status) == (
        'tmdb_tv',
        '1396',
        'watching',
    )
    assert queued == [request.pk]


def test_the_same_pending_request_is_reused(alice, bob, capture_on_commit, queued):
    with capture_on_commit():
        first = adding.request_add(alice, 'tmdb_tv', '1396', 'watching')
        again = adding.request_add(alice, 'tmdb_tv', '1396', 'planned')
        other = adding.request_add(bob, 'tmdb_tv', '1396', 'planned')
    assert again.pk == first.pk and again.status == 'watching'
    assert other.pk != first.pk
    assert queued == [first.pk, other.pk]


def hide_the_winner_until_the_insert(monkeypatch, winner):
    """Make the first look miss ``winner``, as if it committed a moment later."""
    state = {'hidden': True, 'creates': 0}
    real_for_user = AddRequest.objects.for_user
    real_create = AddRequest.objects.create

    def for_user(user):
        rows = real_for_user(user)
        return rows.exclude(pk=winner.pk) if state['hidden'] else rows

    def create(**kwargs):
        state['hidden'] = False
        state['creates'] += 1
        return real_create(**kwargs)

    monkeypatch.setattr(AddRequest.objects, 'for_user', for_user)
    monkeypatch.setattr(AddRequest.objects, 'create', create)
    return state


def test_a_lost_race_returns_the_winners_request(alice, monkeypatch):
    winner = AddRequest.objects.create(
        user=alice, provider='tmdb_tv', external_id='1396', status='planned'
    )
    state = hide_the_winner_until_the_insert(monkeypatch, winner)
    assert adding.request_add(alice, 'tmdb_tv', '1396', 'watching') == winner
    assert state['creates'] == 1
    assert AddRequest.objects.count() == 1


def test_a_lost_race_is_retried_only_once(alice, monkeypatch):
    calls = []

    def always_conflict(**kwargs):
        calls.append(kwargs)
        raise IntegrityError('unique_pending_add_request')

    monkeypatch.setattr(AddRequest.objects, 'create', always_conflict)
    with pytest.raises(IntegrityError):
        adding.request_add(alice, 'tmdb_tv', '1396', 'watching')
    assert len(calls) == 2


def test_pending_limit(alice, bob):
    for number in range(adding.MAX_PENDING):
        adding.request_add(alice, 'tmdb_tv', str(100 + number), 'planned')
    with pytest.raises(adding.TooManyPending):
        adding.request_add(alice, 'tmdb_tv', '999', 'planned')
    # The same id is still answered, and other users are not affected.
    assert adding.request_add(alice, 'tmdb_tv', '100', 'planned').external_id == '100'
    assert adding.request_add(bob, 'tmdb_tv', '999', 'planned')


def test_finished_requests_do_not_count_towards_the_limit(alice):
    for number in range(adding.MAX_PENDING):
        adding.request_add(alice, 'tmdb_tv', str(100 + number), 'planned')
    AddRequest.objects.for_user(alice).update(state='done')
    assert adding.request_add(alice, 'tmdb_tv', '999', 'planned')


def test_forged_values_are_rejected(alice):
    for provider, external_id, status in [
        ('imdb', '1', 'planned'),
        ('tmdb_tv', 'abc', 'planned'),
        ('tmdb_tv', '', 'planned'),
        ('tmdb_tv', '-1', 'planned'),
        ('tmdb_tv', '١٢٣', 'planned'),
        ('tmdb_tv', '+1', 'planned'),
        ('tmdb_tv', ' 1', 'planned'),
        ('tmdb_tv', '1\n', 'planned'),
        ('tmdb_tv', None, 'planned'),
        ('tmdb_tv', '1' * 11, 'planned'),
        ('tmdb_tv', '1', 'hacked'),
    ]:
        with pytest.raises(ValueError):
            adding.request_add(alice, provider, external_id, status)
    assert not AddRequest.objects.exists() and not Title.objects.exists()


def test_leading_zeros_do_not_make_a_second_request(alice):
    first = adding.request_add(alice, 'tmdb_tv', '1396', 'planned')
    assert adding.request_add(alice, 'tmdb_tv', '0001396', 'planned') == first


def test_job_syncs_and_adds_the_title(alice, fake_clients):
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_tv', external_id='1396', status='watching'
    )
    assert adding.add_title_task.call_local(request.pk) == 'done'
    request.refresh_from_db()
    assert (request.state, request.error) == ('done', '')
    assert request.finished_at is not None
    assert request.title.synced_at is not None
    assert request.title.seasons.count() == 2
    entry = LibraryEntry.objects.for_user(alice).get()
    assert (entry.title, entry.status) == (request.title, 'watching')


def test_job_fills_everything_from_the_provider(alice, fake_clients):
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='603', status='planned'
    )
    adding.add_title_task.call_local(request.pk)
    request.refresh_from_db()
    assert request.title.kind == 'movie'
    assert request.title.original_title == 'The Matrix (original)'


def test_job_keeps_the_status_of_an_existing_entry(alice, fake_clients):
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='603', status='planned'
    )
    adding.add_title_task.call_local(request.pk)
    request.refresh_from_db()
    LibraryEntry.objects.for_user(alice).update(status='completed')
    again = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='603', status='planned'
    )
    assert adding.add_title_task.call_local(again.pk) == 'done'
    assert LibraryEntry.objects.for_user(alice).get().status == 'completed'


def test_job_skips_a_missing_or_finished_request(alice, fake_clients):
    assert adding.add_title_task.call_local(999999) == 'skipped'
    request = AddRequest.objects.create(
        user=alice,
        provider='tmdb_movie',
        external_id='603',
        status='planned',
        state='failed',
        error='timeout',
    )
    assert adding.add_title_task.call_local(request.pk) == 'skipped'
    assert not Title.objects.exists() and not LibraryEntry.objects.exists()


def test_failed_first_sync_removes_the_placeholder(alice, failing_clients):
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='424242', status='planned'
    )
    assert adding.add_title_task.call_local(request.pk) == 'failed'
    request.refresh_from_db()
    assert (request.state, request.error) == ('failed', 'provider')
    assert request.finished_at is not None
    assert not Title.objects.exists() and not LibraryEntry.objects.exists()


def test_an_anime_lookup_failure_fails_the_request(alice, failing_clients):
    request = AddRequest.objects.create(
        user=alice, provider='anilist', external_id='70', status='planned'
    )
    assert adding.add_title_task.call_local(request.pk) == 'failed'
    request.refresh_from_db()
    assert (request.state, request.error) == ('failed', 'provider')
    assert not Title.objects.exists()


def test_a_failed_job_keeps_a_title_someone_else_tracks(alice, bob, failing_clients):
    title = Title.objects.create(kind='movie', original_title='placeholder')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='7')
    LibraryEntry.objects.create(user=bob, title=title)
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='7', status='planned'
    )
    assert adding.add_title_task.call_local(request.pk) == 'failed'
    assert Title.objects.filter(pk=title.pk).exists()
    assert LibraryEntry.objects.for_user(bob).count() == 1


def test_a_failed_job_keeps_a_title_that_was_synced_before(
    alice, synced_movie, failing_clients
):
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='603', status='planned'
    )
    assert adding.add_title_task.call_local(request.pk) == 'failed'
    assert Title.objects.filter(pk=synced_movie.pk).exists()


def test_a_failed_job_keeps_a_created_title_that_gained_an_entry(
    alice, bob, failing_clients, monkeypatch
):
    def lose_the_race(hit, clients):
        title = Title.objects.create(kind='movie', original_title='placeholder')
        LibraryEntry.objects.create(user=bob, title=title)
        return title, True

    monkeypatch.setattr(adding, 'get_or_create_title', lose_the_race)
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='7', status='planned'
    )
    assert adding.add_title_task.call_local(request.pk) == 'failed'
    assert Title.objects.count() == 1


def test_new_entry_of_a_known_franchise_adds_the_existing_title(alice, fake_clients):
    first = AddRequest.objects.create(
        user=alice, provider='anilist', external_id='70', status='planned'
    )
    adding.add_title_task.call_local(first.pk)
    second = AddRequest.objects.create(
        user=alice, provider='anilist', external_id='71', status='watching'
    )
    assert adding.add_title_task.call_local(second.pk) == 'done'
    first.refresh_from_db()
    second.refresh_from_db()
    assert second.title == first.title
    assert Title.objects.count() == 1
    assert LibraryEntry.objects.for_user(alice).get().status == 'planned'


def test_cleanup_times_out_stale_requests_and_deletes_old_ones(alice, bob):
    now = timezone.now()

    def make(user, external_id, state, created_ago, finished_ago=None):
        request = AddRequest.objects.create(
            user=user,
            provider='tmdb_tv',
            external_id=external_id,
            status='planned',
            state=state,
        )
        AddRequest.objects.for_user(user).filter(pk=request.pk).update(
            created_at=now - created_ago,
            finished_at=None if finished_ago is None else now - finished_ago,
        )
        return request

    stale = make(alice, '1', 'pending', timedelta(hours=2))
    fresh = make(alice, '2', 'pending', timedelta(minutes=10))
    old_done = make(bob, '3', 'done', timedelta(days=9), timedelta(days=8))
    old_failed = make(alice, '4', 'failed', timedelta(days=9), timedelta(days=8))
    recent = make(bob, '5', 'done', timedelta(days=2), timedelta(days=1))
    assert adding.cleanup_add_requests.call_local() == 3
    stale.refresh_from_db()
    assert (stale.state, stale.error) == ('failed', 'timeout')
    assert stale.finished_at is not None
    fresh.refresh_from_db()
    assert fresh.state == 'pending'
    recent.refresh_from_db()
    remaining = set(AddRequest.objects.values_list('pk', flat=True))
    assert remaining == {stale.pk, fresh.pk, recent.pk}
    assert old_done.pk not in remaining and old_failed.pk not in remaining
    assert adding.cleanup_add_requests.call_local() == 0


def test_a_numeric_id_is_accepted_as_a_string(alice):
    assert adding.request_add(alice, 'tmdb_tv', 1396, 'planned').external_id == '1396'


def make_pending(user, external_id, age):
    request = AddRequest.objects.create(
        user=user, provider='tmdb_tv', external_id=external_id, status='planned'
    )
    AddRequest.objects.for_user(user).filter(pk=request.pk).update(
        created_at=timezone.now() - age
    )
    return request


def test_a_stale_request_is_not_reused_and_does_not_count(alice):
    stale = [
        make_pending(alice, str(100 + n), timedelta(hours=2))
        for n in range(adding.MAX_PENDING)
    ]
    fresh = adding.request_add(alice, 'tmdb_tv', '100', 'planned')
    assert fresh.pk not in {request.pk for request in stale}
    assert fresh.state == 'pending'
    assert adding.request_add(alice, 'tmdb_tv', '999', 'planned')
    for request in AddRequest.objects.for_user(alice).filter(
        pk__in=[r.pk for r in stale]
    ):
        assert (request.state, request.error) == ('failed', 'timeout')
        assert request.finished_at is not None


def test_a_request_just_inside_the_timeout_is_reused(alice):
    request = make_pending(alice, '1396', timedelta(minutes=59))
    assert adding.request_add(alice, 'tmdb_tv', '1396', 'planned') == request


def test_stale_requests_of_other_users_are_left_alone(alice, bob):
    request = make_pending(bob, '1396', timedelta(hours=2))
    adding.request_add(alice, 'tmdb_tv', '1396', 'planned')
    request.refresh_from_db()
    assert request.state == 'pending'


def test_an_unexpected_failure_fails_the_request_and_propagates(
    alice, fake_clients, monkeypatch, caplog
):
    def boom(title, clients):
        raise RuntimeError('boom')

    monkeypatch.setattr(adding, 'sync_title', boom)
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='603', status='planned'
    )
    with pytest.raises(RuntimeError, match='boom'):
        adding.add_title_task.call_local(request.pk)
    request.refresh_from_db()
    assert (request.state, request.error) == ('failed', 'unexpected')
    assert request.finished_at is not None
    assert not Title.objects.exists() and not LibraryEntry.objects.exists()
    assert 'boom' in caplog.text
    # The user can try again at once.
    assert adding.request_add(alice, 'tmdb_movie', '603', 'planned').pk != request.pk


def test_the_placeholder_is_named_after_the_validated_ids(
    alice, fake_clients, monkeypatch
):
    seen = []
    monkeypatch.setattr(
        adding, 'sync_title', lambda t, c: seen.append(t.original_title)
    )
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='603', status='planned'
    )
    adding.add_title_task.call_local(request.pk)
    assert seen == ['tmdb_movie:603']


@pytest.mark.parametrize(
    ('other_state', 'survives'),
    [('pending', True), ('failed', False), ('done', False)],
)
def test_the_placeholder_stays_while_another_job_waits_for_it(
    alice, bob, failing_clients, other_state, survives
):
    other = AddRequest.objects.create(
        user=bob,
        provider='tmdb_movie',
        external_id='7',
        status='planned',
        state=other_state,
    )
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='7', status='planned'
    )
    assert adding.add_title_task.call_local(request.pk) == 'failed'
    assert Title.objects.exists() is survives
    assert ExternalId.objects.exists() is survives
    other.refresh_from_db()
    assert other.state == other_state


def test_a_request_for_another_id_does_not_keep_the_placeholder(
    alice, bob, failing_clients
):
    AddRequest.objects.create(
        user=bob, provider='tmdb_movie', external_id='8', status='planned'
    )
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='7', status='planned'
    )
    adding.add_title_task.call_local(request.pk)
    assert not Title.objects.exists()


def test_a_late_success_overrides_the_timeout(alice, fake_clients, monkeypatch):
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='603', status='planned'
    )
    real_sync = adding.sync_title

    def sync_after_the_timeout(title, clients):
        AddRequest.objects.for_user(alice).update(
            state='failed', error='timeout', finished_at=timezone.now()
        )
        real_sync(title, clients)

    monkeypatch.setattr(adding, 'sync_title', sync_after_the_timeout)
    assert adding.add_title_task.call_local(request.pk) == 'done'
    request.refresh_from_db()
    assert (request.state, request.error) == ('done', '')
    assert request.title is not None
    assert LibraryEntry.objects.for_user(alice).count() == 1


def test_a_late_failure_never_overrides_done(alice, failing_clients, monkeypatch):
    request = AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='7', status='planned'
    )
    real_get = adding.get_or_create_title

    def finish_elsewhere(hit, clients):
        AddRequest.objects.for_user(alice).update(state='done')
        return real_get(hit, clients)

    monkeypatch.setattr(adding, 'get_or_create_title', finish_elsewhere)
    adding.add_title_task.call_local(request.pk)
    request.refresh_from_db()
    assert (request.state, request.error) == ('done', '')
