import re
from html import unescape

import pytest
from catalog.models import ExternalId, Title
from catalog.sync.common import Clients
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from fakes import FailingProvider, FakeAniList, FakeTmdb
from providers.tmdb import TmdbCollection
from providers.types import SearchHit
from redis.exceptions import ConnectionError as RedisConnectionError
from tracking.models import AddRequest, LibraryEntry

pytestmark = pytest.mark.django_db

URL = '/collections/10/'


def part(number: int, name: str, year: int | None = 2001, **kwargs) -> SearchHit:
    """Build a movie part of a collection."""
    values = {
        'original_title': name,
        'year': year,
        'poster_url': 'https://image.tmdb.org/t/p/w185/p.jpg',
        'overview': '',
        **kwargs,
    }
    return SearchHit('tmdb_movie', str(number), 'movie', name, **values)


def make_collection(parts, tmdb_id: int = 10) -> TmdbCollection:
    """Build a collection of ``parts``."""
    return TmdbCollection(
        tmdb_id=tmdb_id,
        name='The Matrix Collection',
        overview='A hacker keeps learning the truth.',
        poster_url='',
        parts=list(parts),
    )


def use_tmdb(monkeypatch, tmdb) -> None:
    """Replace the shared clients of the Collection view."""
    monkeypatch.setattr(
        'web.views.collections.shared_clients', lambda: Clients(tmdb, FakeAniList())
    )


@pytest.fixture
def four_parts(monkeypatch) -> FakeTmdb:
    """Serve collection 10 with four movies through the shared clients."""
    tmdb = FakeTmdb(
        collections={
            10: make_collection(
                [
                    part(1, 'First', 1999),
                    part(2, 'Second', 2003),
                    part(3, 'Third', 2003),
                    part(4, 'Undated', None),
                ]
            )
        }
    )
    use_tmdb(monkeypatch, tmdb)
    return tmdb


def get(client, headers, url=URL):
    """Return the response of ``url``."""
    return client.get(url, **headers)


def text(response) -> str:
    """Return the body of a response."""
    return response.content.decode()


def known(number: int, name: str) -> Title:
    """Create a catalog movie linked to the TMDB id ``number``."""
    title = Title.objects.create(kind='movie', original_title=name)
    ExternalId.objects.create(
        title=title, provider='tmdb_movie', external_id=str(number)
    )
    return title


def test_collection_shows_name_overview_and_count(
    client, portal_headers, alice, four_parts
):
    page = text(get(client, portal_headers))
    assert re.search(r'<h1[^>]*>\s*The Matrix Collection\s*</h1>', page)
    assert 'A hacker keeps learning the truth.' in page
    assert '4 films' in page
    assert four_parts.calls == [('collection', 10, 'en')]


def test_collection_count_is_singular_for_one_film(
    client, portal_headers, alice, monkeypatch
):
    use_tmdb(monkeypatch, FakeTmdb(collections={10: make_collection([part(1, 'A')])}))
    page = text(get(client, portal_headers))
    assert re.search(r'>\s*1 film\s*<', page)


def test_collection_lists_parts_in_release_order(
    client, portal_headers, alice, four_parts
):
    page = text(get(client, portal_headers))
    names = re.findall(r'class="mt-result-name"[^>]*>\s*<a[^>]*>(.*?)</a>', page, re.S)
    assert names == ['First', 'Second', 'Third', 'Undated']
    numbers = re.findall(r'class="mt-cl-num"[^>]*>\s*(\d+)\s*<', page)
    assert numbers == ['1', '2', '3', '4']
    assert '1999' in page and '2003' in page


def test_collection_poster_is_hotlinked_without_a_referrer(
    client, portal_headers, alice, four_parts
):
    page = text(get(client, portal_headers))
    assert 'src="https://image.tmdb.org/t/p/w185/p.jpg"' in page
    assert 'referrerpolicy="no-referrer"' in page and 'loading="lazy"' in page


def test_collection_progress_counts_only_completed_entries_of_the_user(
    client, portal_headers, other_headers, alice, bob, four_parts
):
    titles = [known(number, f'Part {number}') for number in (1, 2, 3, 4)]
    LibraryEntry.objects.create(user=alice, title=titles[0], status='completed')
    LibraryEntry.objects.create(user=alice, title=titles[1], status='completed')
    LibraryEntry.objects.create(user=alice, title=titles[2], status='planned')
    LibraryEntry.objects.create(user=bob, title=titles[3], status='planned')
    mine = text(get(client, portal_headers))
    theirs = text(get(client, other_headers))
    assert '2 of 4 completed' in mine
    assert '0 of 4 completed' in theirs
    assert 'aria-valuenow="2"' in mine and 'aria-valuemax="4"' in mine
    assert '--mt-pct: 50%' in mine and '--mt-pct: 0%' in theirs


def test_collection_has_no_mark_all_button(client, portal_headers, alice, four_parts):
    page = text(get(client, portal_headers))
    assert 'Mark all' not in page


def test_collection_rows_link_to_known_titles_and_to_open_for_unknown(
    client, portal_headers, alice, four_parts
):
    title = known(2, 'Second')
    page = text(get(client, portal_headers))
    assert f'href="/titles/{title.pk}/"' in page
    assert 'href="/open/tmdb_movie/1/"' in page
    assert 'href="/open/tmdb_movie/4/"' in page
    assert '/open/tmdb_movie/2/' not in page


def test_collection_rows_show_the_add_control_per_user(
    client, portal_headers, alice, four_parts
):
    LibraryEntry.objects.create(user=alice, title=known(1, 'First'), status='planned')
    AddRequest.objects.create(
        user=alice, provider='tmdb_movie', external_id='3', status='planned'
    )
    page = text(get(client, portal_headers))
    assert 'In your library: Plan to watch' in page
    assert 'mt-add--pending' in page
    assert 'name="external_id" value="2"' in page
    assert 'name="external_id" value="4"' in page
    assert 'name="external_id" value="1"' not in page
    assert 'name="external_id" value="3"' not in page


def test_unknown_collection_is_a_404(client, portal_headers, alice, four_parts):
    with override_settings(DEBUG=False):
        response = get(client, portal_headers, '/collections/99/')
    assert response.status_code == 404
    assert 'Page not found' in text(response)


@pytest.mark.parametrize('tmdb_id', ['0', '99999999999999999999999'])
def test_odd_ids_are_a_404_not_a_500(
    client, portal_headers, alice, four_parts, tmdb_id
):
    with override_settings(DEBUG=False):
        response = get(client, portal_headers, f'/collections/{tmdb_id}/')
    assert response.status_code == 404


def test_the_collection_id_must_be_a_number(client, portal_headers, alice, four_parts):
    assert get(client, portal_headers, '/collections/abc/').status_code == 404


def test_collection_error_state(client, portal_headers, alice, monkeypatch):
    use_tmdb(monkeypatch, FailingProvider())
    response = get(client, portal_headers)
    page = text(response)
    assert response.status_code == 200
    assert "Couldn't load this collection" in unescape(page)
    assert 'Try again' in page
    assert 'mt-state--error' in page and 'of 4 completed' not in page


def test_collection_error_state_without_a_token(
    client, portal_headers, alice, monkeypatch
):
    use_tmdb(monkeypatch, None)
    response = get(client, portal_headers)
    assert response.status_code == 200 and 'Try again' in text(response)


def test_collection_error_state_when_redis_is_down(
    client, portal_headers, alice, monkeypatch
):
    class Down:
        def collection(self, tmdb_id, language='en'):
            raise RedisConnectionError('down')

    use_tmdb(monkeypatch, Down())
    response = get(client, portal_headers)
    assert response.status_code == 200 and 'mt-state--error' in text(response)


def test_empty_collection(client, portal_headers, alice, monkeypatch):
    use_tmdb(monkeypatch, FakeTmdb(collections={10: make_collection([])}))
    page = text(get(client, portal_headers))
    assert 'This collection is empty' in page
    assert 'completed' not in page and 'mt-result' not in page
    assert 'The Matrix Collection' in page


def test_collection_in_russian(client, portal_headers, alice, four_parts):
    alice.profile.interface_language = 'ru'
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    known(1, 'First')
    page = text(get(client, portal_headers))
    assert '4 фильма' in page and '0 из 4 просмотрено' in page
    assert 'Коллекция' in page
    assert four_parts.calls == [('collection', 10, 'ru')]


def test_collection_error_and_empty_states_in_russian(
    client, portal_headers, alice, monkeypatch
):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    use_tmdb(monkeypatch, FakeTmdb(collections={10: make_collection([])}))
    assert 'Эта коллекция пуста' in text(get(client, portal_headers))
    use_tmdb(monkeypatch, FailingProvider())
    assert 'Не удалось загрузить коллекцию' in text(get(client, portal_headers))


def test_collection_query_count_is_constant(client, portal_headers, alice, monkeypatch):
    def queries(count: int) -> int:
        parts = [part(number, f'Part {number}') for number in range(1, count + 1)]
        use_tmdb(monkeypatch, FakeTmdb(collections={10: make_collection(parts)}))
        with CaptureQueriesContext(connection) as captured:
            assert get(client, portal_headers).status_code == 200
        return len(captured)

    for number in range(1, 9):
        title = known(number, f'Part {number}')
        LibraryEntry.objects.create(user=alice, title=title, status='completed')
    get(client, portal_headers)  # the first request creates the profile
    assert queries(8) == queries(2)
