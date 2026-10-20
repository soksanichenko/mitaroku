"""Middleware for portal authentication and per-user language."""

from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

from accounts.portal import is_trusted_peer, is_valid_discord_id, sync_user
from django.conf import settings
from django.contrib import auth
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
)
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme

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

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
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

    def _sign_in(self, request: HttpRequest, discord_id: str) -> None:
        """Make ``request.user`` the portal user, replacing a different session."""
        if request.user.is_authenticated:
            if request.user.get_username() == discord_id:
                return
            auth.logout(request)
        user = auth.authenticate(request, remote_user=discord_id)
        if user:
            request.user = user
            auth.login(request, user)

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., HttpResponse],
        view_args: tuple,
        view_kwargs: dict,
    ) -> HttpResponse | None:
        """Reject anonymous requests to views that require a login."""
        if request.user.is_authenticated or not getattr(
            view_func, 'login_required', True
        ):
            return None
        return HttpResponse('Authentication required', status=401)


class ProfileLanguageMiddleware:
    """Activate the signed-in user's interface language."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Switch language for authenticated users before the view runs."""
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            language = user.profile.interface_language
            translation.activate(language)
            request.LANGUAGE_CODE = language
        return self.get_response(request)


class HtmxLoginRedirectMiddleware:
    """Answer an anonymous HTMX request with ``HX-Redirect`` to the login page.

    Placed before ``LoginRequiredMiddleware`` (local mode), it turns that 302
    into a 204 that htmx follows with a full page load. Otherwise the XHR would
    follow the redirect and swap the login page into a fragment. The login page
    sends the user back to the page they were on (``HX-Current-URL``).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Replace the login redirect for HTMX requests."""
        response = self.get_response(request)
        if (
            request.headers.get('HX-Request') == 'true'
            and not request.user.is_authenticated
            and response.status_code == 302
            and urlsplit(response['Location']).path == settings.LOGIN_URL
        ):
            login = HttpResponse(status=204)
            login['HX-Redirect'] = (
                f'{settings.LOGIN_URL}?{urlencode({"next": self._next(request)})}'
            )
            return login
        return response

    @staticmethod
    def _next(request: HttpRequest) -> str:
        """Return the path of the page the request came from, or ``/``.

        Only a same-host URL is used, and only as a local path: an absolute or
        protocol-relative target (``//host``, ``/\\host``) never gets through.
        """
        current = urlsplit(request.headers.get('HX-Current-URL', ''))
        if current.netloc and current.netloc != request.get_host():
            return '/'
        target = current.path + (f'?{current.query}' if current.query else '')
        if (
            not target.startswith('/')
            or target.startswith(('//', '/\\'))
            or target.startswith(settings.LOGIN_URL)
            or not url_has_allowed_host_and_scheme(target, allowed_hosts=None)
        ):
            return '/'
        return target
