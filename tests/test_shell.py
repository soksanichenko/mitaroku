import pytest
from django.contrib.auth import get_user_model
from web.templatetags.web_extras import initials

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_root_redirects_to_library(client, portal_headers):
    response = client.get('/', **portal_headers)
    assert response.status_code == 302
    assert response['Location'] == '/library/'


def test_library_shows_navigation_theme_toggle_and_user_menu(client, portal_headers):
    html = client.get('/library/', **portal_headers).content.decode()
    assert '<html lang="en"' in html
    assert 'aria-current="page"' in html
    assert 'data-theme-toggle' in html
    assert 'aria-label="Account"' in html
    assert 'class="mt-bottom-nav"' in html
    assert 'X-CSRFToken' in html


def test_current_nav_marks_only_the_active_page(client, portal_headers):
    html = client.get('/upcoming/', **portal_headers).content.decode()
    upcoming = html.split('href="/upcoming/"')[1][:40]
    library = html.split('href="/library/"')[1][:40]
    assert 'aria-current="page"' in upcoming
    assert 'aria-current="page"' not in library


def test_russian_profile_language_translates_the_shell(client, portal_headers):
    client.get('/library/', **portal_headers)
    profile = User.objects.get(discord_id='1001').profile
    profile.interface_language = 'ru'
    profile.save()
    html = client.get('/library/', **portal_headers).content.decode()
    assert '<html lang="ru"' in html
    assert 'Библиотека' in html
    assert 'Выйти' not in html  # no logout item without a portal logout URL


def test_logout_link_appears_only_with_portal_logout_url(
    client, portal_headers, settings
):
    settings.PORTAL_LOGOUT_URL = 'https://portal.example/logout'
    html = client.get('/library/', **portal_headers).content.decode()
    assert 'href="https://portal.example/logout"' in html


@pytest.mark.parametrize(
    ('full_name', 'username', 'expected'),
    [('Kira Mori', 'k', 'KM'), ('', '1001', '10'), ('Cher', 'c', 'CH')],
)
def test_initials(full_name, username, expected):
    user = User(username=username)
    user.first_name, _, user.last_name = full_name.partition(' ')
    assert initials(user) == expected
