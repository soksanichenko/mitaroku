import pytest
from accounts.models import Profile
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db


def test_saving_a_user_creates_a_profile_with_english_defaults():
    user = get_user_model().objects.create_user('kira')
    assert user.profile.interface_language == 'en'
    assert user.profile.metadata_language == 'en'


def test_saving_a_user_again_does_not_duplicate_the_profile():
    user = get_user_model().objects.create_user('kira')
    user.first_name = 'Kira'
    user.save()
    assert Profile.objects.filter(user=user).count() == 1


def test_discord_id_is_unique_but_optional():
    users = get_user_model().objects
    users.create_user('a')
    users.create_user('b')  # two users without a discord id are fine
    users.create_user('c', discord_id='7')
    with pytest.raises(IntegrityError), transaction.atomic():
        users.create_user('d', discord_id='7')
