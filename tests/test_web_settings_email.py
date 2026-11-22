import re
from unittest.mock import patch

import pytest
from accounts.models import Profile, User
from django.core import mail
from django.test import Client, override_settings
from mitaroku.settings import backends_for, middleware_for
from notifications.confirmation import send_confirmation

pytestmark = pytest.mark.django_db

URL = '/settings/email/'
CONFIGURED = {'EMAIL_ENABLED': True, 'PUBLIC_URL': 'https://mitaroku.example'}
LOCAL = {
    'AUTH_MODE': 'local',
    'MIDDLEWARE': middleware_for('local'),
    'AUTHENTICATION_BACKENDS': backends_for('local'),
}
NEW = 'new@example.com'


@pytest.fixture(autouse=True)
def configured():
    """Run every test with outgoing email configured, unless it overrides it."""
    with override_settings(**CONFIGURED):
        yield


def page(client, headers) -> str:
    """Return the HTML of ``/settings/``."""
    response = client.get('/settings/', **headers)
    assert response.status_code == 200
    return response.content.decode()


def profile_of(user: User) -> Profile:
    """Return the fresh profile of ``user``."""
    return Profile.objects.get(user=user)


def with_account_address(user: User, address: str = 'kira@example.com') -> None:
    """Give ``user`` an account address, as the portal would."""
    user.email = address
    user.save()


def test_the_email_block_is_absent_when_email_is_not_configured(
    client, portal_headers, alice
):
    with override_settings(EMAIL_ENABLED=False):
        assert 'aria-labelledby="set-email"' not in page(client, portal_headers)
        for action in ('toggle', 'address', 'use_portal'):
            response = client.post(
                URL, {'action': action, 'email': NEW}, **portal_headers
            )
            assert response.status_code == 404
    assert 'aria-labelledby="set-email"' in page(client, portal_headers)


def test_the_block_shows_the_effective_address_and_the_switch(
    client, portal_headers, alice
):
    with_account_address(alice)
    html = page(client, portal_headers)
    block = re.search(r'<section[^>]*set-email.*?</section>', html, re.DOTALL).group()
    assert 'kira@example.com' in block
    assert 'name="action" value="toggle"' in block
    assert re.search(r'<input[^>]*name="enabled"', block)
    assert 'checked' not in re.search(r'<input[^>]*name="enabled"[^>]*>', block).group()
    assert 'name="action" value="address"' in block
    assert 'type="email"' in block
    assert 'Use the portal address' not in block
    Profile.objects.filter(user=alice).update(email_enabled=True)
    block = re.search(
        r'<section[^>]*set-email.*?</section>', page(client, portal_headers), re.DOTALL
    ).group()
    assert 'checked' in re.search(r'<input[^>]*name="enabled"[^>]*>', block).group()


def test_the_block_names_where_the_address_comes_from(client, portal_headers, alice):
    with_account_address(alice)
    assert 'Portal address' in page(client, portal_headers)
    Profile.objects.filter(user=alice).update(notify_email='mine@example.com')
    html = page(client, portal_headers)
    assert 'mine@example.com' in html
    assert 'The address you entered' in html
    assert 'name="action" value="use_portal"' in html


def test_turning_on_sets_email_since_and_turning_off_clears_it(
    client, portal_headers, alice
):
    with_account_address(alice)
    response = client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    assert response.status_code == 302
    assert response['Location'] == '/settings/'
    profile = profile_of(alice)
    assert profile.email_enabled is True
    assert profile.email_since is not None
    assert 'Settings saved' in page(client, portal_headers)
    assert client.post(URL, {'action': 'toggle'}, **portal_headers).status_code == 302
    profile = profile_of(alice)
    assert profile.email_enabled is False
    assert profile.email_since is None


def test_turning_on_without_any_address_is_refused_with_a_message(
    client, portal_headers, alice
):
    # The portal headers carry an email and would put it back on the request.
    headers = {k: v for k, v in portal_headers.items() if 'EMAIL' not in k}
    response = client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **headers)
    assert response.status_code == 200
    html = response.content.decode()
    assert re.search(r'role="alert"[^>]*>\s*Add an email address first', html)
    profile = profile_of(alice)
    assert profile.email_enabled is False
    assert profile.email_since is None


def test_turning_on_again_keeps_email_since(client, portal_headers, alice):
    with_account_address(alice)
    Profile.objects.filter(user=alice).update(
        email_enabled=True, email_since='2026-01-01T00:00:00Z'
    )
    before = profile_of(alice).email_since
    client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    profile = profile_of(alice)
    assert profile.email_enabled is True
    assert profile.email_since == before


def test_a_new_address_goes_pending_and_a_confirmation_mail_is_sent_to_it(
    client, portal_headers, alice
):
    with_account_address(alice)
    Profile.objects.filter(user=alice).update(
        interface_language='ru', notify_email='old@example.com'
    )
    response = client.post(URL, {'action': 'address', 'email': NEW}, **portal_headers)
    assert response.status_code == 302
    profile = profile_of(alice)
    assert profile.pending_email == NEW
    assert profile.notify_email == 'old@example.com'
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [NEW]
    assert 'https://mitaroku.example/email/confirm/' in message.body
    assert 'Подтвердите' in message.body
    assert 'kira@example.com' not in message.body


def test_the_pending_address_is_shown_as_waiting(client, portal_headers, alice):
    with_account_address(alice)
    Profile.objects.filter(user=alice).update(pending_email='<b>x</b>@example.com')
    html = page(client, portal_headers)
    assert 'Waiting for confirmation of' in html
    assert '&lt;b&gt;x&lt;/b&gt;@example.com' in html
    assert '<b>x</b>' not in html
    assert 'name="action" value="use_portal"' in html


def test_an_invalid_address_is_rejected_without_a_mail(client, portal_headers, alice):
    for bad in ('', 'not-an-address', 'a' * 250 + '@example.com'):
        response = client.post(
            URL, {'action': 'address', 'email': bad}, **portal_headers
        )
        assert response.status_code == 200
        assert 'role="alert"' in response.content.decode()
    assert mail.outbox == []
    assert profile_of(alice).pending_email == ''


def test_the_current_address_is_a_no_op_with_a_note(client, portal_headers, alice):
    with_account_address(alice)
    response = client.post(
        URL, {'action': 'address', 'email': 'KIRA@example.com'}, **portal_headers
    )
    assert response.status_code == 200
    assert 'That is already your address' in response.content.decode()
    assert mail.outbox == []
    assert profile_of(alice).pending_email == ''


def test_a_failed_confirmation_send_leaves_nothing_pending(
    client, portal_headers, alice, caplog
):
    Profile.objects.filter(user=alice).update(pending_email='older@example.com')
    with patch(
        'web.views.settings.send_confirmation',
        side_effect=OSError(f'refused {NEW}'),
    ):
        response = client.post(
            URL, {'action': 'address', 'email': NEW}, **portal_headers
        )
    assert response.status_code == 200
    html = response.content.decode()
    assert 'We could not send the confirmation. Try again later.' in html
    assert profile_of(alice).pending_email == ''
    assert NEW not in caplog.text
    assert 'OSError' in caplog.text


def test_use_portal_address_clears_both_custom_addresses(client, portal_headers, alice):
    Profile.objects.filter(user=alice).update(
        notify_email='mine@example.com', pending_email=NEW
    )
    response = client.post(URL, {'action': 'use_portal'}, **portal_headers)
    assert response.status_code == 302
    profile = profile_of(alice)
    assert profile.notify_email == ''
    assert profile.pending_email == ''


def test_an_unknown_or_missing_action_is_a_400(client, portal_headers, alice):
    assert client.post(URL, {'action': 'nope'}, **portal_headers).status_code == 400
    assert client.post(URL, {}, **portal_headers).status_code == 400


def test_the_email_view_only_takes_post(client, portal_headers, alice):
    assert client.get(URL, **portal_headers).status_code == 405


def test_another_users_profile_is_never_touched(client, portal_headers, alice, bob):
    Profile.objects.filter(user=bob).update(
        notify_email='bob@example.com', pending_email='p@example.com'
    )
    with_account_address(alice)
    client.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    client.post(URL, {'action': 'address', 'email': NEW}, **portal_headers)
    client.post(URL, {'action': 'use_portal'}, **portal_headers)
    other = profile_of(bob)
    assert other.email_enabled is False
    assert other.notify_email == 'bob@example.com'
    assert other.pending_email == 'p@example.com'


def test_the_email_forms_need_csrf(portal_headers, alice):
    Profile.objects.filter(user=alice).update(notify_email='mine@example.com')
    strict = Client(enforce_csrf_checks=True)
    response = strict.post(URL, {'action': 'toggle', 'enabled': 'on'}, **portal_headers)
    assert response.status_code == 403
    html = strict.get('/settings/', **portal_headers).content.decode()
    block = re.search(r'<section[^>]*set-email.*?</section>', html, re.DOTALL).group()
    assert block.count('csrfmiddlewaretoken') == 3


@override_settings(**LOCAL)
def test_a_local_account_can_use_the_block(client):
    user = User.objects.create_user('kira', password='pw', email='kira@home.example')
    client.login(username='kira', password='pw')
    html = client.get('/settings/').content.decode()
    assert 'kira@home.example' in html
    assert 'Account address' in html
    response = client.post(URL, {'action': 'toggle', 'enabled': 'on'})
    assert response.status_code == 302
    assert profile_of(user).email_enabled is True


def test_the_settings_page_keeps_working_when_the_timezone_form_is_used(
    client, portal_headers, alice
):
    response = client.post(
        '/settings/timezone/', {'timezone': 'Asia/Tokyo'}, **portal_headers
    )
    assert response.status_code == 302
    assert profile_of(alice).timezone == 'Asia/Tokyo'
    assert 'name="timezone"' in page(client, portal_headers)


def test_the_timezone_error_page_still_shows_the_email_block(
    client, portal_headers, alice
):
    response = client.post(
        '/settings/timezone/', {'timezone': 'Mars/Base'}, **portal_headers
    )
    assert response.status_code == 200
    assert 'aria-labelledby="set-email"' in response.content.decode()


def test_the_coming_later_note_mentions_only_telegram(client, portal_headers, alice):
    html = page(client, portal_headers)
    assert 'Telegram notifications are coming later.' in html
    assert 'Email and Telegram' not in html
    Profile.objects.filter(user=alice).update(interface_language='ru')
    html = page(client, portal_headers)
    assert 'Уведомления в Telegram появятся позже.' in html
    assert 'по почте' not in html


def test_the_russian_block_is_translated(client, portal_headers, alice):
    Profile.objects.filter(user=alice).update(
        interface_language='ru', pending_email=NEW
    )
    html = page(client, portal_headers)
    assert 'Ожидает подтверждения' in html
    assert 'Waiting for' not in html


def test_the_confirmation_mail_is_localised_and_has_a_fixed_subject(alice):
    alice.first_name = '<script>Kira</script>'
    alice.save()
    Profile.objects.filter(user=alice).update(interface_language='ru')
    alice = User.objects.get(pk=alice.pk)
    result = send_confirmation(alice, 'a&b@example.com')
    assert result is None
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ['a&b@example.com']
    assert message.subject == 'Подтвердите адрес для mitaroku'
    assert 'Kira' not in message.subject + message.body
    assert 'https://mitaroku.example/email/confirm/' in message.body
    html, kind = message.alternatives[0]
    assert kind == 'text/html'
    assert 'a&amp;b@example.com' in html
    assert 'https://mitaroku.example/email/confirm/' in html
    assert 'lang="ru"' in html
    Profile.objects.filter(user=alice).update(interface_language='en')
    send_confirmation(User.objects.get(pk=alice.pk), NEW)
    assert mail.outbox[1].subject == 'Confirm your address for mitaroku'


def test_the_confirmation_mail_raises_when_the_send_fails(alice):
    with patch(
        'django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('down')
    ):
        with pytest.raises(OSError):
            send_confirmation(alice, NEW)
