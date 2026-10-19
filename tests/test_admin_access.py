import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('path', ['/admin/accounts/user/', '/admin/accounts/profile/'])
def test_portal_admin_can_open_user_and_profile_lists(client, portal_headers, path):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    assert client.get(path, **portal_headers).status_code == 200


def test_portal_admin_cannot_change_users(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    client.get('/library/', **portal_headers)
    pk = get_user_model().objects.get(discord_id='1001').pk
    url = f'/admin/accounts/user/{pk}/change/'
    # View-only: the page renders, but a POST (e.g. ticking is_superuser) is refused.
    assert client.get(url, **portal_headers).status_code == 200
    assert client.post(url, {'is_superuser': 'on'}, **portal_headers).status_code == 403


def test_portal_admin_cannot_add_users(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    assert client.get('/admin/accounts/user/add/', **portal_headers).status_code == 403


@pytest.mark.parametrize('path', ['/admin/accounts/user/', '/admin/accounts/profile/'])
def test_portal_member_cannot_open_admin_lists(client, portal_headers, path):
    response = client.get(path, **portal_headers)
    assert response.status_code in (302, 403)


@pytest.mark.parametrize(
    'path',
    [
        '/admin/catalog/title/',
        '/admin/catalog/season/',
        '/admin/catalog/episode/',
    ],
)
def test_portal_admin_can_browse_the_catalog(client, portal_headers, path):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    assert client.get(path, **portal_headers).status_code == 200


def test_portal_admin_cannot_edit_the_catalog(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    assert client.get('/admin/catalog/title/add/', **portal_headers).status_code == 403
