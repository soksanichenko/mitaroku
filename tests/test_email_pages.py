import pytest
from accounts.models import Profile
from django.test import Client, override_settings
from django.urls import reverse
from mitaroku.settings import backends_for, middleware_for
from notifications.tokens import confirmation_token, unsubscribe_token

pytestmark = pytest.mark.django_db

ADDRESS = 'new@example.com'


def enabled(user) -> Profile:
    """Turn the email channel on for ``user`` and return the fresh profile."""
    Profile.objects.filter(user=user).update(
        email_enabled=True, email_since='2026-01-01T00:00:00Z'
    )
    return Profile.objects.get(user=user)


def unsubscribe_url(user) -> str:
    return reverse('email_unsubscribe', args=[unsubscribe_token(user)])


def confirm_url(user, address: str = ADDRESS) -> str:
    return reverse('email_confirm', args=[confirmation_token(user, address)])


def test_get_unsubscribe_changes_nothing(alice):
    enabled(alice)
    response = Client().get(unsubscribe_url(alice))
    assert response.status_code == 200
    assert '<form' in response.content.decode()
    profile = Profile.objects.get(user=alice)
    assert profile.email_enabled is True
    assert profile.email_since is not None


def test_post_unsubscribe_needs_no_login_and_no_csrf_token(alice):
    enabled(alice)
    response = Client(enforce_csrf_checks=True).post(unsubscribe_url(alice))
    assert response.status_code == 200
    assert 'You will not get email from mitaroku any more' in response.content.decode()
    profile = Profile.objects.get(user=alice)
    assert profile.email_enabled is False
    assert profile.email_since is None


def test_post_unsubscribe_twice_is_harmless(alice):
    enabled(alice)
    client = Client()
    first = client.post(unsubscribe_url(alice))
    second = client.post(unsubscribe_url(alice))
    assert first.status_code == second.status_code == 200
    assert Profile.objects.get(user=alice).email_enabled is False


def test_the_unsubscribe_page_names_no_address(alice):
    Profile.objects.filter(user=alice).update(notify_email='secret@example.com')
    alice.email = 'account@example.com'
    alice.save()
    html = Client().get(unsubscribe_url(alice)).content.decode()
    assert 'secret@example.com' not in html
    assert 'account@example.com' not in html


def test_the_unsubscribe_page_speaks_the_profile_language(alice):
    Profile.objects.filter(user=alice).update(interface_language='ru')
    response = Client().get(unsubscribe_url(alice), HTTP_ACCEPT_LANGUAGE='en')
    html = response.content.decode()
    assert response.headers['Content-Language'] == 'ru'
    assert 'Отписаться' in html
    assert 'Unsubscribe' not in html


def test_the_confirm_page_speaks_the_profile_language(alice):
    Profile.objects.filter(user=alice).update(
        interface_language='ru', pending_email=ADDRESS
    )
    html = Client().get(confirm_url(alice), HTTP_ACCEPT_LANGUAGE='en').content.decode()
    assert 'Подтвердить' in html
    assert 'Confirm' not in html


def test_a_bad_token_is_a_400_page_and_changes_nothing(alice):
    enabled(alice)
    client = Client()
    for name in ('email_unsubscribe', 'email_confirm'):
        for method in (client.get, client.post):
            response = method(reverse(name, args=['garbage']))
            assert response.status_code == 400
            assert 'not valid' in response.content.decode()
            assert 'garbage' not in response.content.decode()
    profile = Profile.objects.get(user=alice)
    assert profile.email_enabled is True
    assert profile.notify_email == ''


def test_a_bad_token_page_uses_the_browser_language(alice):
    response = Client().get(
        reverse('email_unsubscribe', args=['garbage']), HTTP_ACCEPT_LANGUAGE='ru'
    )
    assert response.status_code == 400
    assert 'недействительна' in response.content.decode()


def test_get_confirm_changes_nothing(alice):
    Profile.objects.filter(user=alice).update(pending_email=ADDRESS)
    response = Client().get(confirm_url(alice))
    assert response.status_code == 200
    html = response.content.decode()
    assert ADDRESS in html
    assert '<form' in html
    profile = Profile.objects.get(user=alice)
    assert profile.notify_email == ''
    assert profile.pending_email == ADDRESS


def test_post_confirm_moves_the_pending_address(alice):
    Profile.objects.filter(user=alice).update(pending_email=ADDRESS)
    response = Client().post(confirm_url(alice))
    assert response.status_code == 200
    profile = Profile.objects.get(user=alice)
    assert profile.notify_email == ADDRESS
    assert profile.pending_email == ''


def test_post_confirm_with_a_replaced_pending_address_is_invalid(alice):
    Profile.objects.filter(user=alice).update(pending_email='other@example.com')
    client = Client()
    for method in (client.get, client.post):
        response = method(confirm_url(alice))
        assert response.status_code == 400
        assert ADDRESS not in response.content.decode()
        assert 'other@example.com' not in response.content.decode()
    profile = Profile.objects.get(user=alice)
    assert profile.notify_email == ''
    assert profile.pending_email == 'other@example.com'


def test_confirm_post_requires_csrf(alice):
    Profile.objects.filter(user=alice).update(pending_email=ADDRESS)
    response = Client(enforce_csrf_checks=True).post(confirm_url(alice))
    assert response.status_code == 403
    assert Profile.objects.get(user=alice).notify_email == ''


def test_the_pages_work_in_local_mode(alice):
    enabled(alice)
    Profile.objects.filter(user=alice).update(pending_email=ADDRESS)
    with override_settings(
        AUTH_MODE='local',
        MIDDLEWARE=middleware_for('local'),
        AUTHENTICATION_BACKENDS=backends_for('local'),
    ):
        assert Client().get(unsubscribe_url(alice)).status_code == 200
        assert Client().get(confirm_url(alice)).status_code == 200


def test_the_pages_work_in_portal_mode_without_headers(alice):
    enabled(alice)
    Profile.objects.filter(user=alice).update(pending_email=ADDRESS)
    with override_settings(
        AUTH_MODE='portal',
        MIDDLEWARE=middleware_for('portal'),
        AUTHENTICATION_BACKENDS=backends_for('portal'),
    ):
        assert Client().get(unsubscribe_url(alice)).status_code == 200
        assert Client().get(confirm_url(alice)).status_code == 200
        assert Client().post(unsubscribe_url(alice)).status_code == 200
