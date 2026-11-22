from datetime import date, timedelta

import pytest
from accounts.models import Profile
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from notifications.models import Notification, dedupe_key
from tracking_helpers import make_movie


def _notification(user, title, key='movie_released:1:2026-11-21'):
    return Notification.objects.create(
        user=user,
        kind=Notification.Kind.MOVIE_RELEASED,
        title=title,
        day=date(2026, 11, 21),
        dedupe_key=key,
    )


def test_dedupe_key_format():
    key = dedupe_key(Notification.Kind.EPISODE_AIRED, 7, date(2026, 11, 21))
    assert key == 'episode_aired:7:2026-11-21'


@pytest.mark.django_db
def test_the_key_is_unique_per_user_not_across_users(alice, bob):
    title = make_movie()
    _notification(alice, title)
    _notification(bob, title)
    with pytest.raises(IntegrityError), transaction.atomic():
        _notification(alice, title)


@pytest.mark.django_db
def test_for_user_returns_only_own_rows(alice, bob):
    title = make_movie()
    mine = _notification(alice, title)
    _notification(bob, title)
    assert list(Notification.objects.for_user(alice)) == [mine]


@pytest.mark.django_db
def test_new_profile_notify_since_is_now_and_zone_is_utc(alice):
    profile = Profile.objects.get(user=alice)
    assert profile.timezone == 'UTC'
    assert timezone.now() - profile.notify_since < timedelta(minutes=1)


@pytest.mark.django_db(transaction=True)
def test_migration_gives_existing_profiles_the_migration_time():
    before = [('accounts', '0003_app_admins_permissions')]
    after = [('accounts', '0004_profile_timezone')]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    try:
        old_apps = executor.loader.project_state(before).apps
        user = old_apps.get_model('accounts', 'User').objects.create(username='old')
        old_apps.get_model('accounts', 'Profile').objects.create(user=user)

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        new_apps = executor.loader.project_state(after).apps
        profile = new_apps.get_model('accounts', 'Profile').objects.get(user_id=user.pk)
        assert profile.timezone == 'UTC'
        assert timezone.now() - profile.notify_since < timedelta(minutes=1)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
