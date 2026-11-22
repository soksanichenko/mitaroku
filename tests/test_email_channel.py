import importlib
import os

import pytest
from django.conf import settings
from django.core import mail
from django.test import override_settings
from mitaroku.config import EmailConfig, load_settings, mailers_setting
from notifications.channel import absolute_url, effective_address, email_configured
from pydantic import ValidationError


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


def test_the_default_mailer_carries_the_smtp_options():
    email = EmailConfig(
        host='smtp.example',
        port=2525,
        user='robot',
        password='smtp-secret',
        starttls=False,
        from_address='mitaroku@example.com',
    )
    mailers = mailers_setting(email)
    assert (
        mailers['default']['BACKEND'] == 'django.core.mail.backends.smtp.EmailBackend'
    )
    assert mailers['default']['OPTIONS'] == {
        'host': 'smtp.example',
        'port': 2525,
        'username': 'robot',
        'password': 'smtp-secret',
        'use_tls': False,
        'use_ssl': False,
        'timeout': 15,
    }
    assert 'smtp-secret' not in repr(email)


def test_the_default_mailer_uses_starttls_and_not_implicit_tls():
    options = mailers_setting(EmailConfig())['default']['OPTIONS']
    assert options['use_tls'] is True
    assert options['use_ssl'] is False


def test_ssl_maps_to_implicit_tls_and_ignores_a_defaulted_starttls():
    options = mailers_setting(EmailConfig(ssl=True, port=465))['default']['OPTIONS']
    assert options['use_ssl'] is True
    assert options['use_tls'] is False


def test_ssl_with_an_explicit_starttls_is_rejected():
    with pytest.raises(ValidationError, match='ssl'):
        EmailConfig(ssl=True, starttls=True)


def test_an_unconfigured_mailer_points_at_localhost():
    options = mailers_setting(EmailConfig())['default']['OPTIONS']
    assert options['host'] == 'localhost'
    assert options['password'] == ''


def test_the_settings_define_a_default_mailer_and_no_deprecated_options():
    assert 'default' in settings.MAILERS
    assert not hasattr(settings, 'EMAIL_HOST')


def test_the_settings_module_builds_mailers_from_the_loaded_email_block():
    # The test runner swaps settings.MAILERS for the locmem backend, so compare
    # the module's own value.
    module = importlib.import_module(os.environ['DJANGO_SETTINGS_MODULE'])
    assert module.MAILERS == mailers_setting(load_settings().email)


def test_a_message_sent_in_a_test_lands_in_the_outbox():
    mail.mailers.default.send_messages(
        [mail.EmailMessage('Hi', 'Body', 'a@example.com', ['b@example.com'])]
    )
    assert [message.subject for message in mail.outbox] == ['Hi']
