"""Template filters for the shell."""

from django import template
from django.contrib.auth.models import AbstractUser

register = template.Library()


@register.filter
def initials(user: AbstractUser) -> str:
    """Return up to two uppercase initials for a user.

    Args:
        user: A user with ``get_full_name`` and ``get_username``.

    Returns:
        Initials from the full name, or the first two letters of the username.
    """
    name = (user.get_full_name() or user.get_username()).strip()
    words = name.split()
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return name[:2].upper()
