import re
from datetime import timedelta
from hashlib import sha256

import pytest
from accounts.models import Profile, User
from django.test import Client, override_settings
from django.utils import timezone
from mitaroku.settings import backends_for, middleware_for

pytestmark = pytest.mark.django_db

URL = '/settings/telegram/'
STATUS = '/settings/telegram/status/'
LINK = re.compile(r'https://t\.me/mitaroku_bot\?start=([A-Za-z0-9_-]+)')
LOCAL = {
    'AUTH_MODE': 'local',
    'MIDDLEWARE': middleware_for('local'),
    'AUTHENTICATION_BACKENDS': backends_for('local'),
}
SECTION = re.compile(r'<section[^>]*set-telegram.*?</section>', re.DOTALL)


def page(client, headers) -> str:
    """Return the HTML of ``/settings/``."""
    response = client.get('/settings/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def block(html: str) -> str:
    """Return the Telegram section of ``html``."""
    return SECTION.search(html).group()


def profile_of(user: User) -> Profile:
    """Return the fresh profile of ``user``."""
    return Profile.objects.get(user=user)


def connect_chat(user: User, chat_id: int = 555, **extra) -> None:
    """Give ``user`` a connected chat, as the bot's poll would."""
    Profile.objects.filter(user=user).update(
        telegram_chat_id=chat_id,
        telegram_enabled=True,
        telegram_since=timezone.now(),
        **extra,
    )


def test_the_block_is_absent_when_telegram_is_not_configured(
    client, portal_headers, alice
):
    with override_settings(TELEGRAM_ENABLED=False):
        assert 'set-telegram' not in page(client, portal_headers)
        for action in ('connect', 'toggle', 'disconnect'):
            response = client.post(URL, {'action': action}, **portal_headers)
            assert response.status_code == 404
        assert client.get(STATUS, **portal_headers).status_code == 404


def test_the_old_coming_later_note_is_gone(client, portal_headers, alice, bot):
    assert 'coming later' not in page(client, portal_headers)
    Profile.objects.filter(user=alice).update(interface_language='ru')
    assert 'появятся позже' not in page(client, portal_headers)


def test_idle_shows_the_connect_button(client, portal_headers, alice, bot):
    html = block(page(client, portal_headers))
    assert 'name="action" value="connect"' in html
    assert 'Connect Telegram' in html
    assert 'hx-get' not in html
    assert 't.me' not in html
    assert 'name="action" value="toggle"' not in html


def test_connect_shows_a_one_time_link_in_the_response(
    client, portal_headers, alice, bot
):
    response = client.post(URL, {'action': 'connect'}, **portal_headers)
    assert response.status_code == 200
    html = response.content.decode()
    link = LINK.search(html)
    assert link
    assert (
        profile_of(alice).telegram_link_hash
        == sha256(link.group(1).encode()).hexdigest()
    )
    assert 'Open in Telegram' in html
    assert 'rel="noopener"' in html
    assert 'target="_blank"' in html
    assert 'no-store' in response['Cache-Control']
    assert 'hx-trigger="every 3s"' in html
    # Nothing keeps the link: not the session, not a flash, not the next page.
    assert link.group(1) not in str(dict(client.session))
    assert 'settings saved' not in html.lower()
    assert link.group(1) not in page(client, portal_headers)


def test_a_later_page_load_waits_without_the_link_and_polls(
    client, portal_headers, alice, bot
):
    client.post(URL, {'action': 'connect'}, **portal_headers)
    html = block(page(client, portal_headers))
    assert 'Waiting for you to press Start in Telegram.' in html
    assert 'hx-get="/settings/telegram/status/"' in html
    assert 'hx-trigger="every 3s"' in html
    assert 'hx-swap="none"' in html
    assert 't.me' not in html
    assert 'Get a new link' in html


def test_an_expired_token_is_idle_again(client, portal_headers, alice, bot):
    client.post(URL, {'action': 'connect'}, **portal_headers)
    Profile.objects.filter(user=alice).update(
        telegram_link_expires=timezone.now() - timedelta(seconds=1)
    )
    html = block(page(client, portal_headers))
    assert 'Waiting for you' not in html
    assert 'hx-get' not in html
    assert 'Connect Telegram' in html


def test_a_second_connect_replaces_the_first_token(client, portal_headers, alice, bot):
    first = LINK.search(
        client.post(URL, {'action': 'connect'}, **portal_headers).content.decode()
    ).group(1)
    second = LINK.search(
        client.post(URL, {'action': 'connect'}, **portal_headers).content.decode()
    ).group(1)
    assert first != second
    assert profile_of(alice).telegram_link_hash == sha256(second.encode()).hexdigest()


def test_connected_shows_the_switch_and_disconnect(client, portal_headers, alice, bot):
    connect_chat(alice)
    html = block(page(client, portal_headers))
    assert 'name="action" value="toggle"' in html
    assert 'Send me new notifications on Telegram' in html
    assert 'checked' in re.search(r'<input[^>]*name="enabled"[^>]*>', html).group()
    assert 'name="action" value="disconnect"' in html
    assert 'Disconnect' in html
    assert 'name="action" value="connect"' not in html
    assert 'hx-get' not in html


def test_paused_shows_the_switch_off(client, portal_headers, alice, bot):
    connect_chat(alice)
    Profile.objects.filter(user=alice).update(telegram_enabled=False)
    html = block(page(client, portal_headers))
    assert 'checked' not in re.search(r'<input[^>]*name="enabled"[^>]*>', html).group()
    assert 'name="action" value="disconnect"' in html
    assert 'hx-get' not in html


def test_toggle_on_sets_the_moment_and_off_clears_it(
    client, portal_headers, alice, bot
):
    connect_chat(alice)
    Profile.objects.filter(user=alice).update(
        telegram_enabled=False, telegram_since=None
    )
    response = client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    assert response.status_code == 302
    assert response['Location'] == '/settings/'
    profile = profile_of(alice)
    assert profile.telegram_enabled is True
    assert profile.telegram_since is not None
    assert 'Settings saved' in page(client, portal_headers)
    since = profile.telegram_since
    client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    assert profile_of(alice).telegram_since == since
    assert client.post(URL, {'action': 'toggle'}, **portal_headers).status_code == 302
    profile = profile_of(alice)
    assert profile.telegram_enabled is False
    assert profile.telegram_since is None
    assert profile.telegram_chat_id == 555


def test_toggle_on_without_a_chat_is_refused(client, portal_headers, alice, bot):
    response = client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    assert response.status_code == 200
    assert re.search(
        r'role="alert"[^>]*>\s*Connect Telegram first', response.content.decode()
    )
    profile = profile_of(alice)
    assert profile.telegram_enabled is False
    assert profile.telegram_since is None


def test_disconnect_forgets_the_chat(client, portal_headers, alice, bot):
    connect_chat(alice)
    response = client.post(URL, {'action': 'disconnect'}, **portal_headers)
    assert response.status_code == 302
    profile = profile_of(alice)
    assert profile.telegram_chat_id is None
    assert profile.telegram_enabled is False
    assert profile.telegram_since is None
    assert 'name="action" value="connect"' in block(page(client, portal_headers))


def test_a_blocked_account_shows_the_blocked_sentence_and_reconnecting_clears_it(
    client, portal_headers, alice, bot
):
    Profile.objects.filter(user=alice).update(telegram_blocked=True)
    html = block(page(client, portal_headers))
    assert (
        'Telegram was switched off because the bot was blocked. '
        'Connect again to resume.'
    ) in html
    assert 'name="action" value="connect"' in html
    assert 'hx-get' not in html
    # Pressing the button issues a link and the block waits for it, with no stale
    # blocked sentence next to it.
    response = client.post(URL, {'action': 'connect'}, **portal_headers)
    html = response.content.decode()
    assert LINK.search(html)
    assert 'hx-trigger="every 3s"' in html
    assert 'bot was blocked' not in html
    # The poll's successful connect clears the flag.
    from notifications.telegram_link import connect

    token = LINK.search(html).group(1)
    connect(token, 777, timezone.now())
    profile = profile_of(alice)
    assert profile.telegram_blocked is False
    assert 'bot was blocked' not in block(page(client, portal_headers))


def test_the_status_is_204_while_waiting_and_refreshes_once_connected(
    client, portal_headers, alice, bot
):
    client.post(URL, {'action': 'connect'}, **portal_headers)
    response = client.get(STATUS, **portal_headers)
    assert response.status_code == 204
    assert 'HX-Refresh' not in response.headers
    assert 'no-store' in response['Cache-Control']
    connect_chat(alice)
    response = client.get(STATUS, **portal_headers)
    assert response.status_code == 200
    assert response.headers['HX-Refresh'] == 'true'
    assert 'no-store' in response['Cache-Control']


def test_the_status_needs_a_login(client, bot):
    response = client.get(STATUS)
    assert response.status_code in (302, 401, 403)


def test_the_status_reads_only_the_requesters_profile(
    client, portal_headers, alice, bob, bot
):
    connect_chat(bob)
    assert client.get(STATUS, **portal_headers).status_code == 204


def test_state_changes_are_post_only(client, portal_headers, alice, bot):
    assert client.get(URL, **portal_headers).status_code == 405
    assert client.post(STATUS, **portal_headers).status_code == 405


def test_an_unknown_or_missing_action_is_a_400(client, portal_headers, alice, bot):
    assert client.post(URL, {'action': 'nope'}, **portal_headers).status_code == 400
    assert client.post(URL, {}, **portal_headers).status_code == 400


def test_a_second_user_is_never_touched(client, portal_headers, alice, bob, bot):
    connect_chat(bob, chat_id=888)
    connect_chat(alice)
    client.post(URL, {'action': 'toggle'}, **portal_headers)
    client.post(URL, {'action': 'connect'}, **portal_headers)
    client.post(URL, {'action': 'disconnect'}, **portal_headers)
    other = profile_of(bob)
    assert other.telegram_chat_id == 888
    assert other.telegram_enabled is True
    assert other.telegram_link_hash == ''


def test_the_forms_need_csrf(portal_headers, alice, bot):
    connect_chat(alice)
    strict = Client(enforce_csrf_checks=True)
    assert strict.post(URL, {'action': 'toggle'}, **portal_headers).status_code == 403
    html = strict.get('/settings/', **portal_headers).content.decode()
    assert block(html).count('csrfmiddlewaretoken') == 2


def test_the_timezone_error_page_still_shows_the_block(
    client, portal_headers, alice, bot
):
    response = client.post(
        '/settings/timezone/', {'timezone': 'Mars/Base'}, **portal_headers
    )
    assert response.status_code == 200
    assert 'set-telegram' in response.content.decode()


def test_the_block_is_in_russian_for_a_russian_profile(
    client, portal_headers, alice, bot
):
    Profile.objects.filter(user=alice).update(interface_language='ru')
    html = block(page(client, portal_headers))
    assert 'Подключить Telegram' in html
    assert 'Connect Telegram' not in html
    html = block(
        client.post(URL, {'action': 'connect'}, **portal_headers).content.decode()
    )
    assert 'Открыть в Telegram' in html
    assert 'Получить новую ссылку' in html
    assert 'Open in Telegram' not in html
    connect_chat(alice)
    html = block(page(client, portal_headers))
    assert 'Отключить' in html
    assert 'Disconnect' not in html
    assert 'Send me' not in html
    Profile.objects.filter(user=alice).update(
        telegram_chat_id=None, telegram_blocked=True
    )
    assert 'Telegram was switched off' not in block(page(client, portal_headers))
    client.post(URL, {'action': 'disconnect'}, **portal_headers)
    html = client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    assert 'Сначала подключите Telegram' in html.content.decode()


@override_settings(**LOCAL)
def test_local_accounts_mode_shows_the_block_too(client, bot):
    user = User.objects.create_user('kira', password='pw')
    client.login(username='kira', password='pw')
    assert 'set-telegram' in client.get('/settings/').content.decode()
    response = client.post(URL, {'action': 'connect'})
    assert response.status_code == 200
    assert LINK.search(response.content.decode())
    assert client.get(STATUS).status_code == 204
    connect_chat(user)
    assert client.get(STATUS).headers['HX-Refresh'] == 'true'
