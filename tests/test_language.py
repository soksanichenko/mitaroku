import pytest
from django.contrib.auth import get_user_model

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_profile_language_is_active_for_the_request(client, portal_headers):
    client.get('/library/', **portal_headers)
    profile = User.objects.get(discord_id='1001').profile
    profile.interface_language = 'ru'
    profile.save()
    response = client.get('/library/', **portal_headers)
    assert response.wsgi_request.LANGUAGE_CODE == 'ru'
    assert response['Content-Language'] == 'ru'


def test_set_language_saves_profile_and_cookie(client, portal_headers):
    client.get('/library/', **portal_headers)
    response = client.post(
        '/language/', {'language': 'ru', 'next': '/upcoming/'}, **portal_headers
    )
    assert response.status_code == 302
    assert response['Location'] == '/upcoming/'
    assert response.cookies['django_language'].value == 'ru'
    assert User.objects.get(discord_id='1001').profile.interface_language == 'ru'


def test_set_language_ignores_unknown_language(client, portal_headers):
    client.get('/library/', **portal_headers)
    client.post('/language/', {'language': 'xx'}, **portal_headers)
    assert User.objects.get(discord_id='1001').profile.interface_language == 'en'


def test_set_language_rejects_open_redirect(client, portal_headers):
    client.get('/library/', **portal_headers)
    response = client.post(
        '/language/',
        {'language': 'ru', 'next': 'https://evil.example/'},
        **portal_headers,
    )
    assert response['Location'] == '/'


def test_set_language_requires_post(client, portal_headers):
    assert client.get('/language/', **portal_headers).status_code == 405
