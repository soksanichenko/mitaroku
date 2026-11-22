from datetime import date

import pytest
from accounts.models import Profile
from django.db import IntegrityError, transaction
from notifications.models import Delivery, Notification
from tracking_helpers import make_movie


def _notification(user, title):
    return Notification.objects.create(
        user=user,
        kind=Notification.Kind.MOVIE_RELEASED,
        title=title,
        day=date(2026, 11, 21),
        dedupe_key='movie_released:1:2026-11-21',
    )


def _delivery(notification, channel=Delivery.Channel.EMAIL):
    return Delivery.objects.create(
        user=notification.user, notification=notification, channel=channel
    )


@pytest.mark.django_db
def test_a_new_delivery_is_pending_with_no_attempts(alice):
    delivery = _delivery(_notification(alice, make_movie()))
    assert delivery.status == Delivery.Status.PENDING
    assert delivery.attempts == 0
    assert delivery.next_attempt_at is None
    assert delivery.sent_at is None
    assert delivery.last_error == ''


@pytest.mark.django_db
def test_one_delivery_per_notification_and_channel(alice):
    notification = _notification(alice, make_movie())
    _delivery(notification)
    with pytest.raises(IntegrityError), transaction.atomic():
        _delivery(notification)


@pytest.mark.django_db
def test_for_user_returns_only_own_deliveries(alice, bob):
    title = make_movie()
    mine = _delivery(_notification(alice, title))
    _delivery(_notification(bob, title))
    assert list(Delivery.objects.for_user(alice)) == [mine]


@pytest.mark.django_db
def test_deleting_a_notification_deletes_its_deliveries(alice):
    notification = _notification(alice, make_movie())
    _delivery(notification)
    notification.delete()
    assert not Delivery.objects.for_user(alice).exists()


@pytest.mark.django_db
def test_new_profile_email_fields_are_off_and_empty(alice):
    profile = Profile.objects.get(user=alice)
    assert profile.email_enabled is False
    assert profile.email_since is None
    assert profile.notify_email == ''
    assert profile.pending_email == ''
