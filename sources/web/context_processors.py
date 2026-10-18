"""Template context shared by every page."""

from django.conf import settings
from django.http import HttpRequest


def shell(request: HttpRequest) -> dict:
    """Expose the data the application shell needs.

    Args:
        request: The current request.

    Returns:
        Navigation state, the authentication mode, the portal logout URL and
        the available languages.
    """
    match = request.resolver_match
    return {
        'current_nav': match.url_name if match else '',
        'auth_mode': settings.AUTH_MODE,
        'portal_logout_url': settings.PORTAL_LOGOUT_URL,
        'language_choices': settings.LANGUAGES,
    }
