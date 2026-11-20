from html import unescape

import pytest
from catalog import tasks
from catalog.models import ExternalId, Title
from django.utils import timezone
from tracking.models import LibraryEntry

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
POLL = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'opening'}


@pytest.fixture
def jobs(monkeypatch, redis_client) -> list[tuple[str, str]]:
    """Replace the open job with a recorder and free the open claims around a test."""

    def clear() -> None:
        for key in redis_client.scan_iter('mitaroku:claims:open:*'):
            redis_client.delete(key)

    clear()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        'catalog.tasks.open_title_task',
        lambda provider, external_id: calls.append((provider, external_id)),
    )
    yield calls
    clear()


@pytest.fixture
def claims(redis_client):
    """Free the open claims around a test that runs the job itself."""

    def clear() -> None:
        for key in redis_client.scan_iter('mitaroku:claims:open:*'):
            redis_client.delete(key)

    clear()
    yield
    clear()


def get(client, portal_headers, path, **extra):
    return client.get(path, **portal_headers, **extra)


def linked_title(provider='tmdb_movie', external_id='603', synced=True) -> Title:
    """Create a title linked to a provider id, synced or not."""
    title = Title.objects.create(
        kind='movie',
        original_title='x',
        synced_at=timezone.now() if synced else None,
    )
    ExternalId.objects.create(title=title, provider=provider, external_id=external_id)
    return title


def hold_claim(provider='tmdb_movie', external_id='603') -> None:
    """Hold the open claim, as a running open job does."""
    assert tasks.claim(tasks.open_key(provider, external_id), tasks.OPEN_CLAIM_TTL)


def test_open_redirects_to_an_unsynced_title_nobody_is_opening(
    client, portal_headers, alice, jobs
):
    # A title of a running add flow, or an older one that is not synced yet.
    title = Title.objects.create(kind='movie', original_title='Never synced')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    response = get(client, portal_headers, '/open/tmdb_movie/603/')
    assert response.status_code == 302
    assert response['Location'] == f'/titles/{title.pk}/'
    assert 'no-store' in response['Cache-Control']
    assert jobs == []


def test_open_redirects_an_htmx_request_with_a_header(
    client, portal_headers, alice, jobs
):
    title = Title.objects.create(kind='movie', original_title='x')
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    response = get(client, portal_headers, '/open/tmdb_movie/0603/', **HTMX)
    assert response.status_code == 200
    assert response['HX-Redirect'] == f'/titles/{title.pk}/'
    assert response.content == b''


def test_open_enqueues_once(client, portal_headers, alice, jobs):
    first = get(client, portal_headers, '/open/tmdb_movie/603/')
    second = get(client, portal_headers, '/open/tmdb_movie/603/')
    assert jobs == [('tmdb_movie', '603')]
    for response in (first, second):
        assert response.status_code == 200
        assert 'no-store' in response['Cache-Control']
        html = response.content.decode()
        assert 'id="opening"' in html and 'every 2s' in html
        assert 'hx-get="/open/tmdb_movie/603/"' in html


def test_open_normalises_the_id(client, portal_headers, alice, jobs):
    get(client, portal_headers, '/open/anilist/0070/')
    assert jobs == [('anilist', '70')]


def test_open_rejects_unknown_providers_and_bad_ids(
    client, portal_headers, alice, jobs
):
    for path in (
        '/open/imdb/603/',
        '/open/tmdb_movie/abc/',
        '/open/tmdb_movie/12345678901/',
        '/open/tmdb_movie/-1/',
        '/open/tmdb_movie/603x/',
    ):
        assert get(client, portal_headers, path).status_code == 404, path
    assert jobs == []


def test_open_is_get_only(client, portal_headers, alice, jobs):
    response = client.post('/open/tmdb_movie/603/', **portal_headers)
    assert response.status_code == 405
    assert jobs == []


def test_open_task_creates_and_syncs_without_a_library_entry(
    alice, fake_clients, claims
):
    key = tasks.open_key('tmdb_movie', '603')
    assert tasks.claim(key, tasks.OPEN_CLAIM_TTL)
    assert tasks.open_title_task.call_local('tmdb_movie', '603') == 'done'
    link = ExternalId.objects.select_related('title').get(
        provider='tmdb_movie', external_id='603'
    )
    assert link.title.synced_at is not None
    assert LibraryEntry.objects.count() == 0
    assert not tasks.claimed(key)


def test_failed_open_leaves_no_placeholder(alice, failing_clients, claims):
    key = tasks.open_key('tmdb_tv', '1396')
    assert tasks.claim(key, tasks.OPEN_CLAIM_TTL)
    assert tasks.open_title_task.call_local('tmdb_tv', '1396') == 'failed'
    assert Title.objects.count() == 0
    assert ExternalId.objects.count() == 0
    assert not tasks.claimed(key)


def test_failed_open_of_an_anime_entry_leaves_nothing(alice, failing_clients, claims):
    assert tasks.open_title_task.call_local('anilist', '70') == 'failed'
    assert Title.objects.count() == 0
    assert ExternalId.objects.count() == 0


def test_a_crashing_open_discards_the_placeholder_and_frees_the_claim(
    alice, fake_clients, claims, monkeypatch
):
    def boom(title, clients=None):
        raise RuntimeError('boom')

    monkeypatch.setattr('catalog.tasks.sync_title', boom)
    key = tasks.open_key('tmdb_movie', '603')
    assert tasks.claim(key, tasks.OPEN_CLAIM_TTL)
    with pytest.raises(RuntimeError):
        tasks.open_title_task.call_local('tmdb_movie', '603')
    assert Title.objects.count() == 0
    assert not tasks.claimed(key)


def test_open_task_keeps_a_title_somebody_tracks(
    alice, fake_clients, claims, monkeypatch
):
    def boom(title, clients=None):
        LibraryEntry.objects.create(user=alice, title=title)
        raise RuntimeError('boom')

    monkeypatch.setattr('catalog.tasks.sync_title', boom)
    with pytest.raises(RuntimeError):
        tasks.open_title_task.call_local('tmdb_movie', '603')
    assert Title.objects.count() == 1


def test_open_task_refuses_values_the_view_would_refuse(alice, fake_clients, claims):
    assert tasks.open_title_task.call_local('imdb', '603') == 'missing'
    assert tasks.open_title_task.call_local('tmdb_movie', 'x') == 'missing'
    assert tasks.open_title_task.call_local('tmdb_movie', '12345678901') == 'missing'
    assert Title.objects.count() == 0


def test_open_task_for_an_anime_entry_of_a_known_franchise_reuses_the_title(
    alice, fake_clients, claims
):
    assert tasks.open_title_task.call_local('anilist', '70') == 'done'
    ExternalId.objects.filter(provider='anilist', external_id='71').delete()
    assert tasks.open_title_task.call_local('anilist', '71') == 'done'
    assert Title.objects.count() == 1
    assert ExternalId.objects.filter(provider='anilist', external_id='71').exists()


def test_poll_replaces_the_page_when_the_title_is_synced(
    client, portal_headers, alice, jobs
):
    first = get(client, portal_headers, '/open/tmdb_movie/603/', **HTMX)
    assert 'every 2s' in first.content.decode()
    title = linked_title()
    poll = get(client, portal_headers, '/open/tmdb_movie/603/', **POLL)
    html = poll.content.decode()
    assert poll.status_code == 200 and 'no-store' in poll['Cache-Control']
    # HX-Redirect would push a history entry: Back would need two presses.
    assert 'HX-Redirect' not in poll
    assert f"window.location.replace('/titles/{title.pk}/')" in html
    assert 'id="opening"' in html and 'every 2s' not in html


def test_poll_keeps_loading_for_an_unsynced_title_while_the_claim_holds(
    client, portal_headers, alice, jobs
):
    get(client, portal_headers, '/open/tmdb_movie/603/')
    linked_title(synced=False)
    poll = get(client, portal_headers, '/open/tmdb_movie/603/', **POLL)
    html = poll.content.decode()
    assert 'HX-Redirect' not in poll and 'window.location' not in html
    assert 'every 2s' in html
    # The first visit does not redirect to the placeholder either, nor queue again.
    first = get(client, portal_headers, '/open/tmdb_movie/603/')
    assert first.status_code == 200 and 'every 2s' in first.content.decode()
    assert jobs == [('tmdb_movie', '603')]


def test_a_synced_title_redirects_even_while_the_claim_holds(
    client, portal_headers, alice, jobs
):
    title = linked_title()
    get(client, portal_headers, '/open/tmdb_movie/603/')  # no job: the title exists
    hold_claim()
    response = get(client, portal_headers, '/open/tmdb_movie/603/')
    assert (
        response.status_code == 302 and response['Location'] == f'/titles/{title.pk}/'
    )


def test_poll_redirects_once_the_claim_is_released(
    client, portal_headers, alice, jobs, redis_client
):
    hold_claim()
    title = linked_title(synced=False)
    assert (
        'every 2s'
        in get(client, portal_headers, '/open/tmdb_movie/603/', **POLL).content.decode()
    )
    # The job ended and left the title behind unsynced: nothing is worth waiting for.
    redis_client.delete(f'{tasks.CLAIM_PREFIX}{tasks.open_key("tmdb_movie", "603")}')
    poll = get(client, portal_headers, '/open/tmdb_movie/603/', **POLL)
    assert f"window.location.replace('/titles/{title.pk}/')" in poll.content.decode()


def test_the_loading_page_refreshes_without_javascript(
    client, portal_headers, alice, jobs, redis_client
):
    loading = get(client, portal_headers, '/open/tmdb_movie/603/').content.decode()
    assert '<noscript><meta http-equiv="refresh" content="2"></noscript>' in loading
    redis_client.delete(f'{tasks.CLAIM_PREFIX}{tasks.open_key("tmdb_movie", "603")}')
    failed = get(client, portal_headers, '/open/tmdb_movie/603/', **POLL)
    assert 'http-equiv="refresh"' not in failed.content.decode()


def test_open_does_not_accept_id_zero(client, portal_headers, alice, jobs):
    for path in ('/open/tmdb_movie/0/', '/open/anilist/000/', '/open/tmdb_tv/00/'):
        assert get(client, portal_headers, path).status_code == 404, path
        assert get(client, portal_headers, path, **POLL).status_code == 404, path
    assert jobs == []


def test_poll_keeps_loading_while_the_claim_holds(client, portal_headers, alice, jobs):
    get(client, portal_headers, '/open/tmdb_movie/603/')
    poll = get(client, portal_headers, '/open/tmdb_movie/603/', **POLL)
    assert 'every 2s' in poll.content.decode()
    assert jobs == [('tmdb_movie', '603')]  # a poll never queues a job


def test_poll_reports_failure_when_the_claim_is_gone(
    client, portal_headers, alice, jobs, redis_client
):
    get(client, portal_headers, '/open/tmdb_movie/603/')
    redis_client.delete(f'{tasks.CLAIM_PREFIX}{tasks.open_key("tmdb_movie", "603")}')
    poll = get(client, portal_headers, '/open/tmdb_movie/603/', **POLL)
    assert poll.status_code == 200
    assert 'no-store' in poll['Cache-Control']
    html = unescape(poll.content.decode())
    assert "We couldn't open this title" in html
    assert "The metadata provider didn't answer. Nothing was lost." in html
    assert 'every 2s' not in html and 'Try again' in html
    assert jobs == [('tmdb_movie', '603')]  # and neither does the failed poll


def test_open_survives_a_redis_outage(client, portal_headers, alice, jobs, monkeypatch):
    def down(*args, **kwargs):
        raise OSError('redis is down')

    monkeypatch.setattr('catalog.tasks.connect_redis', down)
    response = get(client, portal_headers, '/open/tmdb_movie/603/')
    assert response.status_code == 200
    html = unescape(response.content.decode())
    assert "The metadata provider didn't answer" in html and 'every 2s' not in html
    assert jobs == []


def test_open_page_is_translated_to_russian(
    client, portal_headers, alice, jobs, redis_client
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    html = get(client, portal_headers, '/open/tmdb_movie/603/').content.decode()
    assert 'Открываем' in html and 'Opening' not in html
    redis_client.delete(f'{tasks.CLAIM_PREFIX}{tasks.open_key("tmdb_movie", "603")}')
    html = get(client, portal_headers, '/open/tmdb_movie/603/', **POLL).content.decode()
    assert 'Не удалось открыть' in html and 'Повторить' in html


def test_open_requires_a_signed_in_user(client, jobs):
    assert client.get('/open/tmdb_movie/603/').status_code == 401
    assert client.get('/open/tmdb_movie/603/', **HTMX).status_code == 401
    assert jobs == []
