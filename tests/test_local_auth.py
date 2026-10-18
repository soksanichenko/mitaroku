import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from mitaroku.settings import backends_for, middleware_for

User = get_user_model()
pytestmark = pytest.mark.django_db

LOCAL = {
    'AUTH_MODE': 'local',
    'MIDDLEWARE': middleware_for('local'),
    'AUTHENTICATION_BACKENDS': backends_for('local'),
}


@override_settings(**LOCAL)
def test_anonymous_visitor_is_redirected_to_login(client):
    response = client.get('/library/')
    assert response.status_code == 302
    assert response['Location'].startswith('/login/')


@override_settings(**LOCAL)
def test_login_page_renders_without_a_session(client):
    response = client.get('/login/')
    assert response.status_code == 200
    assert 'name="username"' in response.content.decode()


@override_settings(**LOCAL)
def test_valid_credentials_sign_in(client):
    User.objects.create_user('kira', password='correct horse')
    response = client.post(
        '/login/',
        {'username': 'kira', 'password': 'correct horse', 'next': '/library/'},
    )
    assert response.status_code == 302
    assert response['Location'] == '/library/'
    assert client.get('/library/').status_code == 200


@override_settings(**LOCAL)
def test_wrong_password_shows_an_error(client):
    User.objects.create_user('kira', password='correct horse')
    response = client.post('/login/', {'username': 'kira', 'password': 'nope'})
    assert response.status_code == 200
    assert 'role="alert"' in response.content.decode()


@override_settings(**LOCAL)
def test_logout_requires_post_and_signs_out(client):
    User.objects.create_user('kira', password='pw')
    client.login(username='kira', password='pw')
    assert client.get('/logout/').status_code == 405
    assert client.post('/logout/').status_code == 302
    assert client.get('/library/').status_code == 302


@override_settings(**LOCAL)
def test_local_mode_shows_a_logout_form(client):
    User.objects.create_user('kira', password='pw')
    client.login(username='kira', password='pw')
    html = client.get('/library/').content.decode()
    assert 'action="/logout/"' in html


def test_portal_mode_redirects_login_to_the_library(client, portal_headers):
    response = client.get('/login/', **portal_headers)
    assert response.status_code == 302
    assert response['Location'] == '/'
