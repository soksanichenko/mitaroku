import importlib.util
import os

import pytest
from accounts.models import Profile
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import override_settings
from mitaroku.config import load_settings
from notifications.channel import telegram_configured
from notifications.models import Delivery, Notification
from tracking_helpers import make_movie


def _settings_module_values(monkeypatch, **environment):
    """Execute ``mitaroku.settings`` afresh under the given environment."""
    with monkeypatch.context() as patch:
        for name, value in environment.items():
            patch.setenv(name, value)
        load_settings.cache_clear()
        try:
            spec = importlib.util.find_spec(os.environ['DJANGO_SETTINGS_MODULE'])
            fresh = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fresh)
        finally:
            load_settings.cache_clear()
    return fresh


@pytest.mark.parametrize('enabled', [True, False])
def test_telegram_configured_follows_the_setting(enabled):
    with override_settings(TELEGRAM_ENABLED=enabled):
        assert telegram_configured() is enabled


def test_the_default_test_settings_leave_telegram_disabled():
    assert telegram_configured() is False
    assert settings.TELEGRAM_BOT_TOKEN == ''
    assert settings.TELEGRAM_BOT_USERNAME == ''


@pytest.mark.parametrize(
    ('token', 'username', 'public_url', 'enabled'),
    [
        ('123456:abc', 'mitaroku_bot', 'https://m.example', True),
        ('123456:abc', 'mitaroku_bot', '', False),
        ('', '', 'https://m.example', False),
    ],
)
def test_the_setting_needs_token_username_and_public_url(
    monkeypatch, token, username, public_url, enabled
):
    module = _settings_module_values(
        monkeypatch,
        MITAROKU_TELEGRAM__BOT_TOKEN=token,
        MITAROKU_TELEGRAM__BOT_USERNAME=username,
        MITAROKU_PUBLIC_URL=public_url,
    )
    assert module.TELEGRAM_ENABLED is enabled
    assert module.TELEGRAM_BOT_TOKEN == token
    assert module.TELEGRAM_BOT_USERNAME == username


@pytest.mark.django_db
def test_profile_telegram_defaults(alice):
    profile = alice.profile
    assert profile.telegram_enabled is False
    assert profile.telegram_since is None
    assert profile.telegram_chat_id is None
    assert profile.telegram_link_hash == ''
    assert profile.telegram_link_expires is None
    assert profile.telegram_blocked is False


@pytest.mark.django_db
def test_two_profiles_cannot_share_a_chat(alice, bob):
    Profile.objects.filter(user=alice).update(telegram_chat_id=42)
    with pytest.raises(IntegrityError), transaction.atomic():
        Profile.objects.filter(user=bob).update(telegram_chat_id=42)


@pytest.mark.django_db
def test_many_profiles_may_have_no_chat(alice, bob):
    assert alice.profile.telegram_chat_id is None
    assert bob.profile.telegram_chat_id is None
    alice.profile.save()
    bob.profile.save()


@pytest.mark.django_db
def test_one_notification_can_have_a_delivery_per_channel(alice):
    notification = Notification.objects.create(
        user=alice,
        kind=Notification.Kind.MOVIE_RELEASED,
        title=make_movie(),
        day='2026-11-21',
        dedupe_key='movie_released:1:2026-11-21',
    )
    for channel in (Delivery.Channel.EMAIL, Delivery.Channel.TELEGRAM):
        Delivery.objects.create(user=alice, notification=notification, channel=channel)
    assert Delivery.objects.filter(notification=notification).count() == 2
    with pytest.raises(IntegrityError), transaction.atomic():
        Delivery.objects.create(
            user=alice,
            notification=notification,
            channel=Delivery.Channel.TELEGRAM,
        )
