"""Shared fixtures."""

import uuid
from datetime import UTC, date, datetime

import httpx
import pytest
from accounts.models import User
from catalog.models import ExternalId, Title
from catalog.sync.common import Clients
from django.core.management import call_command
from fakes import (
    FailingProvider,
    FakeAniList,
    FakeTmdb,
    edge,
    media,
    tmdb_movie,
    tmdb_season,
    tmdb_show,
)
from providers.clients import connect_redis
from providers.http import ProviderHttp
from providers.search import SearchResults

PORTAL = {
    'HTTP_X_DISCORD_USER_ID': '1001',
    'HTTP_X_DISCORD_EMAIL': 'kira@example.com',
    'HTTP_X_DISCORD_AVATAR_URL': 'https://cdn.example.com/a.png',
    'HTTP_X_DISCORD_ACCESS_LEVEL': 'member',
}


@pytest.fixture(scope='session', autouse=True)
def compiled_messages() -> None:
    """Compile the .po files so translations can be asserted."""
    call_command('compilemessages', verbosity=0)


@pytest.fixture
def portal_headers() -> dict[str, str]:
    """Return a fresh copy of valid portal headers for a member."""
    return dict(PORTAL)


@pytest.fixture
def other_headers() -> dict[str, str]:
    """Return portal headers for a second member (the user ``bob``)."""
    return {**PORTAL, 'HTTP_X_DISCORD_USER_ID': '1002'}


def make_portal_user(discord_id: str) -> User:
    """Create the account the portal login would create for ``discord_id``."""
    user = User(username=discord_id, discord_id=discord_id)
    user.set_unusable_password()
    user.save()
    return user


@pytest.fixture
def alice(db) -> User:
    """Return the member that ``portal_headers`` signs in as."""
    return make_portal_user('1001')


@pytest.fixture
def bob(db) -> User:
    """Return the member that ``other_headers`` signs in as."""
    return make_portal_user('1002')


@pytest.fixture
def redis_client():
    """Return a Redis connection and delete the keys of this test afterwards."""
    client = connect_redis()
    yield client
    for key in client.scan_iter('test:*'):
        client.delete(key)


@pytest.fixture
def redis_prefix() -> str:
    """Return a Redis key prefix unique to the test."""
    return f'test:{uuid.uuid4().hex}:'


@pytest.fixture
def provider_http(redis_client, redis_prefix):
    """Return a factory for a ``ProviderHttp`` over a mock transport."""

    def build(handler, name: str = 'test', **kwargs) -> ProviderHttp:
        return ProviderHttp(
            name,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            redis=redis_client,
            prefix=redis_prefix,
            sleep=lambda seconds: None,
            **kwargs,
        )

    return build


@pytest.fixture
def fake_clients(monkeypatch) -> Clients:
    """Return fake provider clients and make the add flow use them.

    They hold the TMDB movie 603, the TMDB show 1396 with two seasons and the
    anime franchise 70 -> 71.
    """
    seasons = {
        1: tmdb_season(1, 3, date(2008, 1, 20)),
        2: tmdb_season(2, 3, date(2009, 3, 8)),
    }
    tmdb = FakeTmdb(
        movies={603: tmdb_movie(603)},
        shows={1396: (tmdb_show(1396, list(seasons.values())), seasons)},
    )
    anilist = FakeAniList(
        media(70, [edge('SEQUEL', 71)]),
        media(71, [edge('PREQUEL', 70)], year=2021),
    )
    clients = Clients(tmdb=tmdb, anilist=anilist)
    monkeypatch.setattr('tracking.adding.default_clients', lambda: clients)
    return clients


@pytest.fixture
def failing_clients(monkeypatch) -> Clients:
    """Return provider clients that always fail and make the add flow use them."""
    clients = Clients(tmdb=FailingProvider(), anilist=FailingProvider())
    monkeypatch.setattr('tracking.adding.default_clients', lambda: clients)
    return clients


@pytest.fixture
def queued(monkeypatch) -> list[int]:
    """Replace ``add_title_task`` with a recorder of the request ids it gets."""
    calls: list[int] = []

    def record(request_id: int) -> int:
        calls.append(request_id)
        return request_id

    monkeypatch.setattr('tracking.adding.add_title_task', record)
    return calls


@pytest.fixture
def capture_on_commit(django_capture_on_commit_callbacks):
    """Return a factory of the context manager that runs ``on_commit`` callbacks."""
    return lambda: django_capture_on_commit_callbacks(execute=True)


@pytest.fixture
def synced_movie(db) -> Title:
    """Return a movie linked to ``tmdb_movie:603`` that has been synced."""
    title = Title.objects.create(
        kind=Title.Kind.MOVIE,
        original_title='The Matrix',
        synced_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    )
    ExternalId.objects.create(title=title, provider='tmdb_movie', external_id='603')
    return title


class SearchRecorder:
    """Stand-in for ``providers.search.search`` that records its calls.

    Attributes:
        calls: ``(query, language)`` of every call.
        result: What the next calls return.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.result = SearchResults()

    def __call__(self, query: str, language: str, **clients) -> SearchResults:
        self.calls.append((query, language))
        return self.result


@pytest.fixture
def patched_search(monkeypatch) -> SearchRecorder:
    """Replace the provider search of the search view with a recorder.

    The shared clients are replaced too, so no test builds a real client.
    """
    recorder = SearchRecorder()
    monkeypatch.setattr('web.views.search.search', recorder)
    monkeypatch.setattr(
        'web.views.search.shared_clients', lambda: Clients(None, FakeAniList())
    )
    return recorder
