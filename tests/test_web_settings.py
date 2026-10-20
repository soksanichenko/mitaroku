import re

import pytest
from accounts.models import User
from catalog.models import Title, TitleTranslation
from django.conf import settings
from django.test import Client, override_settings
from mitaroku.settings import backends_for, middleware_for
from tracking.models import LibraryEntry

pytestmark = pytest.mark.django_db

LOCAL = {
    'AUTH_MODE': 'local',
    'MIDDLEWARE': middleware_for('local'),
    'AUTHENTICATION_BACKENDS': backends_for('local'),
}
LANGUAGE = {'interface_language': 'ru', 'metadata_language': 'ru'}


def page(client, headers) -> str:
    """Return the HTML of ``/settings/``."""
    response = client.get('/settings/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def tag(html: str, element_id: str) -> str:
    """Return the opening tag of the element with ``element_id`` (empty if absent)."""
    found = re.search(rf'<[a-z0-9]+[^>]*\bid="{re.escape(element_id)}"[^>]*>', html)
    return found.group() if found else ''


def selected(html: str, select_id: str) -> str:
    """Return the value of the selected option of the select ``select_id``."""
    block = re.search(
        rf'<select[^>]*\bid="{select_id}".*?</select>', html, re.DOTALL
    ).group()
    return re.search(r'<option value="([^"]*)" selected', block).group(1)


def test_settings_shows_the_account_and_language_sections(
    client, portal_headers, alice
):
    alice.first_name = 'Kira'
    alice.last_name = 'Mori'
    alice.save()
    html = page(client, portal_headers)
    assert '<h1 class="mt-page-title">Settings</h1>' in html
    assert 'aria-labelledby="set-account"' in html
    assert 'aria-labelledby="set-language"' in html
    assert 'Kira Mori' in html
    assert 'kira@example.com' in html
    assert 'Signed in through the portal' in html
    assert 'Local account' not in html
    # Plans 4 and 5 add these; nothing is reserved for them now.
    assert 'Notifications' not in html
    assert 'Your data' not in html


def test_the_language_selects_are_built_from_the_project_languages(
    client, portal_headers, alice
):
    html = page(client, portal_headers)
    for field, label in (
        ('interface_language', 'Interface language'),
        ('metadata_language', 'Metadata language'),
    ):
        select_id = f'id_{field}'
        assert f'<label for="{select_id}">{label}</label>' in html
        assert f'name="{field}"' in tag(html, select_id)
        # The help text is tied to the control.
        assert f'aria-describedby="{select_id}_help"' in tag(html, select_id)
        assert tag(html, f'{select_id}_help')
        assert selected(html, select_id) == 'en'
    block = re.search(
        r'<select[^>]*\bid="id_interface_language".*?</select>', html, re.DOTALL
    ).group()
    assert re.findall(r'<option value="([^"]*)"', block) == [
        code for code, _ in settings.LANGUAGES
    ]
    assert 'Русский' in block
    assert 'menus and buttons' in html
    assert 'titles and descriptions of shows' in html


def test_the_selects_show_the_saved_languages(client, portal_headers, alice):
    alice.profile.interface_language = 'en'
    alice.profile.metadata_language = 'ru'
    alice.profile.save()
    html = page(client, portal_headers)
    assert selected(html, 'id_interface_language') == 'en'
    assert selected(html, 'id_metadata_language') == 'ru'


def test_the_page_has_a_csrf_protected_post_form_with_no_htmx(
    client, portal_headers, alice
):
    html = page(client, portal_headers)
    form = re.search(r'<form[^>]*action="/settings/"[^>]*>.*?</form>', html, re.DOTALL)
    assert form is not None
    assert 'method="post"' in form.group()
    assert 'csrfmiddlewaretoken' in form.group()
    assert 'type="submit"' in form.group()
    assert 'hx-' not in form.group()


def test_the_post_needs_a_csrf_token(portal_headers, alice):
    strict = Client(enforce_csrf_checks=True)
    response = strict.post('/settings/', LANGUAGE, **portal_headers)
    assert response.status_code == 403
    alice.profile.refresh_from_db()
    assert alice.profile.interface_language == 'en'


def test_changing_languages_saves_the_profile_and_switches_the_ui(
    client, portal_headers, alice
):
    client.get('/settings/', **portal_headers)
    response = client.post('/settings/', LANGUAGE, **portal_headers)
    assert response.status_code == 302
    assert response['Location'] == '/settings/'
    profile = User.objects.get(discord_id='1001').profile
    assert (profile.interface_language, profile.metadata_language) == ('ru', 'ru')
    html = client.get('/settings/', **portal_headers).content.decode()
    assert 'Настройки' in html
    assert '<html lang="ru"' in html


def test_the_redirected_page_confirms_once_in_the_new_language(
    client, portal_headers, alice
):
    response = client.post('/settings/', LANGUAGE, **portal_headers, follow=True)
    html = response.content.decode()
    assert 'Настройки сохранены' in html
    assert 'role="status"' in html
    assert 'Настройки сохранены' not in page(client, portal_headers)


def test_the_confirmation_is_in_english_for_an_english_user(
    client, portal_headers, alice
):
    data = {'interface_language': 'en', 'metadata_language': 'ru'}
    html = client.post('/settings/', data, **portal_headers, follow=True).content
    assert 'Settings saved' in html.decode()


def test_a_plain_visit_shows_no_confirmation(client, portal_headers, alice):
    assert 'Settings saved' not in page(client, portal_headers)


def test_saving_remembers_the_interface_language_in_the_cookie(
    client, portal_headers, alice
):
    response = client.post('/settings/', LANGUAGE, **portal_headers)
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == 'ru'


def test_invalid_language_is_rejected_and_nothing_is_saved(
    client, portal_headers, alice
):
    data = {'interface_language': 'xx', 'metadata_language': 'ru'}
    response = client.post('/settings/', data, **portal_headers)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'role="alert"' in html
    assert 'Select a valid choice' in html
    assert 'Settings saved' not in html
    assert settings.LANGUAGE_COOKIE_NAME not in response.cookies
    profile = User.objects.get(discord_id='1001').profile
    assert (profile.interface_language, profile.metadata_language) == ('en', 'en')


def test_the_error_is_tied_to_its_field(client, portal_headers, alice):
    data = {'interface_language': 'en', 'metadata_language': 'xx'}
    html = client.post('/settings/', data, **portal_headers).content.decode()
    control = tag(html, 'id_metadata_language')
    assert 'aria-invalid="true"' in control
    assert 'id_metadata_language_error' in control
    assert 'id_metadata_language_help' in control
    assert tag(html, 'id_metadata_language_error')
    assert 'aria-invalid' not in tag(html, 'id_interface_language')


def test_a_missing_language_is_rejected(client, portal_headers, alice):
    response = client.post('/settings/', {'interface_language': 'ru'}, **portal_headers)
    assert response.status_code == 200
    alice.profile.refresh_from_db()
    assert alice.profile.interface_language == 'en'


def test_only_the_signed_in_profile_is_saved(client, portal_headers, alice, bob):
    data = {**LANGUAGE, 'user': bob.pk, 'id': bob.profile.pk, 'profile': bob.pk}
    assert client.post('/settings/', data, **portal_headers).status_code == 302
    bob.profile.refresh_from_db()
    alice.profile.refresh_from_db()
    assert bob.profile.interface_language == 'en'
    assert alice.profile.interface_language == 'ru'
    assert alice.profile.pk != bob.profile.pk


def test_users_see_their_own_account_only(
    client, portal_headers, other_headers, alice, bob
):
    alice.first_name = 'Alice'
    alice.save()
    bob.first_name = 'Bobby'
    bob.save()
    html = page(client, other_headers)
    assert 'Bobby' in html
    assert 'Alice' not in html


def test_metadata_language_changes_the_title_text(client, portal_headers, alice):
    title = Title.objects.create(kind='tv', original_title='Sousou no Frieren')
    TitleTranslation.objects.create(title=title, language='en', name='Frieren')
    TitleTranslation.objects.create(title=title, language='ru', name='Фрирен')
    LibraryEntry.objects.create(user=alice, title=title, status='watching')
    assert 'Frieren' in client.get('/library/', **portal_headers).content.decode()
    data = {'interface_language': 'en', 'metadata_language': 'ru'}
    assert client.post('/settings/', data, **portal_headers).status_code == 302
    library = client.get('/library/', **portal_headers).content.decode()
    assert 'Фрирен' in library
    assert 'Frieren' not in library
    # The interface stayed English while the titles changed language.
    assert 'Library' in library
    assert (
        'Фрирен'
        in client.get(f'/titles/{title.pk}/', **portal_headers).content.decode()
    )


def test_the_account_values_are_escaped(client, portal_headers, alice):
    alice.first_name = '<b>Kira</b>'
    alice.email = 'x<script>alert(1)</script>@example.com'
    alice.save()
    # The portal headers carry the email and would overwrite it on the request.
    headers = {k: v for k, v in portal_headers.items() if 'EMAIL' not in k}
    html = page(client, headers)
    assert '<b>Kira</b>' not in html
    assert '&lt;b&gt;Kira&lt;/b&gt;' in html
    assert '<script>alert(1)' not in html


@override_settings(**LOCAL)
def test_a_local_account_is_labelled_as_such(client):
    User.objects.create_user('kira', password='pw', email='kira@home.example')
    client.login(username='kira', password='pw')
    html = client.get('/settings/').content.decode()
    assert 'Local account' in html
    assert 'Signed in through the portal' not in html
    assert 'kira@home.example' in html


def test_the_russian_page_is_translated(client, portal_headers, alice):
    alice.profile.interface_language = 'ru'
    alice.profile.save()
    html = page(client, portal_headers)
    for text in (
        'Настройки',
        'Аккаунт',
        'Вход через портал',
        'Язык интерфейса',
        'Язык метаданных',
        'Сохранить',
    ):
        assert text in html
    assert 'Interface language' not in html


def test_the_account_menu_links_to_the_settings(client, portal_headers, alice):
    html = client.get('/library/', **portal_headers).content.decode()
    link = re.search(r'<a[^>]*href="/settings/"[^>]*>', html).group()
    assert 'role="menuitem"' in link
    assert 'aria-current' not in link
    assert (
        'aria-current="page"'
        in re.search(
            r'<a[^>]*href="/settings/"[^>]*>', page(client, portal_headers)
        ).group()
    )


def test_the_bottom_nav_has_no_settings_item(client, portal_headers, alice):
    html = page(client, portal_headers)
    nav = re.search(r'<nav class="mt-bottom-nav".*?</nav>', html, re.DOTALL).group()
    assert '/settings/' not in nav


def test_other_methods_are_refused(client, portal_headers, alice):
    assert client.put('/settings/', **portal_headers).status_code == 405
    assert client.delete('/settings/', **portal_headers).status_code == 405


def test_settings_requires_login(client):
    assert client.get('/settings/').status_code == 401
    assert client.post('/settings/', LANGUAGE).status_code == 401
