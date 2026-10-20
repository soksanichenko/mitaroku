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


HX = {'HTTP_HX_REQUEST': 'true'}


@override_settings(**LOCAL)
def test_an_expired_session_answers_htmx_with_a_redirect_header(client):
    response = client.post(
        '/titles/1/status/',
        {'status': 'watching'},
        HTTP_HX_REQUEST='true',
        HTTP_HX_CURRENT_URL='http://testserver/titles/1/?tab=episodes',
    )
    assert response.status_code == 204
    assert response['HX-Redirect'] == '/login/?next=%2Ftitles%2F1%2F%3Ftab%3Depisodes'
    assert response.content == b''


@override_settings(**LOCAL)
def test_htmx_without_a_current_url_returns_to_the_home_page(client):
    response = client.get('/library/', **HX)
    assert response.status_code == 204
    assert response['HX-Redirect'] == '/login/?next=%2F'


@override_settings(**LOCAL)
@pytest.mark.parametrize(
    'current',
    [
        'https://evil.example/x',
        '//evil.example',
        '//evil.example/z',
        'http://testserver//evil.example/y',
        'http://testserver/\\evil.example',
        '/\\evil.example',
        'javascript:alert(1)',
        'http://evil.example@testserver/x',
        '',
    ],
)
def test_the_redirect_target_is_never_another_site(client, current):
    response = client.post('/library/', HTTP_HX_CURRENT_URL=current, **HX)
    assert response.status_code == 204
    assert response['HX-Redirect'] == '/login/?next=%2F'


@override_settings(**LOCAL)
def test_a_plain_request_is_still_redirected_with_a_302(client):
    response = client.get('/library/')
    assert response.status_code == 302
    assert response['Location'].startswith('/login/')
    assert 'HX-Redirect' not in response


@override_settings(**LOCAL)
def test_a_signed_in_htmx_request_is_untouched(client):
    User.objects.create_user('kira', password='pw')
    client.login(username='kira', password='pw')
    response = client.get('/library/', **HX)
    assert response.status_code == 200
    assert 'HX-Redirect' not in response
