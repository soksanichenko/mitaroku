import pytest
from django.test import override_settings
from notifications.channel import absolute_url, effective_address, email_configured


@pytest.mark.parametrize('enabled', [True, False])
def test_email_configured_follows_the_setting(enabled):
    with override_settings(EMAIL_ENABLED=enabled):
        assert email_configured() is enabled


def test_the_default_test_settings_leave_email_disabled():
    assert email_configured() is False


@pytest.mark.django_db
def test_effective_address_prefers_the_confirmed_address(alice):
    alice.email = 'portal@example.com'
    alice.profile.notify_email = 'chosen@example.com'
    assert effective_address(alice) == 'chosen@example.com'


@pytest.mark.django_db
def test_effective_address_falls_back_to_the_account_email(alice):
    alice.email = 'portal@example.com'
    alice.profile.notify_email = ''
    assert effective_address(alice) == 'portal@example.com'


@pytest.mark.django_db
@pytest.mark.parametrize('value', ['', '   '])
def test_effective_address_is_empty_without_any(alice, value):
    alice.email = value
    alice.profile.notify_email = value
    assert effective_address(alice) == ''


@pytest.mark.django_db
def test_effective_address_is_stripped(alice):
    alice.email = ' portal@example.com '
    alice.profile.notify_email = '  '
    assert effective_address(alice) == 'portal@example.com'


def test_absolute_url_joins_the_public_url():
    with override_settings(PUBLIC_URL='https://m.example'):
        assert absolute_url('/title/7/') == 'https://m.example/title/7/'
