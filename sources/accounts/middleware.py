"""Middleware for portal authentication and per-user language."""

from accounts.portal import is_trusted_peer, is_valid_discord_id, sync_user
from django.conf import settings
from django.contrib import auth
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import translation

ID_HEADER = 'HTTP_X_DISCORD_USER_ID'


class PortalAuthMiddleware:
    """Log users in from the headers set by nginx after the portal check.

    Headers are honoured only when the TCP peer is inside a trusted proxy
    network. A request without the header ends any existing session, and an
    anonymous request to a view that requires a login gets 401 (views marked
    ``login_not_required`` stay public).
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Authenticate from the headers, then run the rest of the stack."""
        discord_id = request.META.get(ID_HEADER)
        if discord_id is None:
            if request.user.is_authenticated:
                auth.logout(request)
        elif not is_trusted_peer(
            request.META.get('REMOTE_ADDR', ''), settings.PORTAL_TRUSTED_NETWORKS
        ):
            return HttpResponseForbidden('Portal headers from an untrusted peer')
        elif not is_valid_discord_id(discord_id):
            return HttpResponseBadRequest('Invalid portal user id')
        else:
            self._sign_in(request, discord_id)
            if request.user.is_authenticated:
                sync_user(
                    request.user,
                    email=request.META.get('HTTP_X_DISCORD_EMAIL', ''),
                    avatar_url=request.META.get('HTTP_X_DISCORD_AVATAR_URL', ''),
                    access_level=request.META.get('HTTP_X_DISCORD_ACCESS_LEVEL', ''),
                )
        return self.get_response(request)

    def _sign_in(self, request, discord_id: str) -> None:
        """Make ``request.user`` the portal user, replacing a different session."""
        if request.user.is_authenticated:
            if request.user.get_username() == discord_id:
                return
            auth.logout(request)
        user = auth.authenticate(request, remote_user=discord_id)
        if user:
            request.user = user
            auth.login(request, user)

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Reject anonymous requests to views that require a login."""
        if request.user.is_authenticated or not getattr(
            view_func, 'login_required', True
        ):
            return None
        return HttpResponse('Authentication required', status=401)


class ProfileLanguageMiddleware:
    """Activate the signed-in user's interface language."""

    def __init__(self, get_response):
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Switch language for authenticated users before the view runs."""
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            language = user.profile.interface_language
            translation.activate(language)
            request.LANGUAGE_CODE = language
        return self.get_response(request)
