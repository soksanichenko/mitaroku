"""Signals that keep related rows in step with users."""

from accounts.models import Profile
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs) -> None:
    """Create a profile the first time a user is saved."""
    if created:
        Profile.objects.create(user=instance)
