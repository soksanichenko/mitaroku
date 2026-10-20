import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings
from django.utils import translation
from django.views.defaults import server_error

pytestmark = pytest.mark.django_db


@override_settings(DEBUG=False)
def test_missing_title_page_gets_the_custom_404(client, portal_headers, alice):
    response = client.get('/titles/999999/', **portal_headers)
    assert response.status_code == 404
    html = response.content.decode()
    assert 'Page not found' in html
    assert 'href="/library/"' in html
    assert 'mt-header' in html


@override_settings(DEBUG=False)
def test_404_is_translated(client, portal_headers, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    html = client.get('/titles/999999/', **portal_headers).content.decode()
    assert 'Страница не найдена' in html
    assert 'Page not found' not in html


def test_500_renders_standalone():
    # Django renders it without a request or context: no user, no context processors.
    with translation.override('en'):
        html = render_to_string('500.html')
    assert 'Something went wrong' in html
    assert 'href="/library/"' in html
    assert 'web/app.css' in html
    assert 'extends' not in html


def test_server_error_handler_serves_it():
    response = server_error(RequestFactory().get('/boom/'))
    assert response.status_code == 500
    assert b'mt-state--error' in response.content


def test_500_is_translated():
    with translation.override('ru'):
        html = render_to_string('500.html')
    assert 'Что-то пошло не так' in html
    assert 'Открыть библиотеку' in html
