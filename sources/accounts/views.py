"""Login, logout and language views."""

from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST


@method_decorator(login_not_required, name='dispatch')
class MitarokuLoginView(LoginView):
    """Local login form; in portal mode there is nothing to log in to."""

    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Send portal-mode visitors to the library."""
        if settings.AUTH_MODE == 'portal':
            return redirect('/')
        return super().dispatch(request, *args, **kwargs)


@login_not_required
@require_POST
def set_language(request: HttpRequest) -> HttpResponseRedirect:
    """Save the chosen interface language and return to the previous page.

    The language goes to the profile for signed-in users and to a cookie for
    everyone, so the login page honours it too.
    """
    requested = request.POST.get('language', '')
    # The cookie and the profile get the value from our own list, never the input.
    code = next((known for known, _ in settings.LANGUAGES if known == requested), None)
    target = request.POST.get('next', '/').replace('\\', '')
    parsed = urlsplit(target)
    if (
        parsed.scheme
        or parsed.netloc
        or not url_has_allowed_host_and_scheme(
            target,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        target = '/'
    response = HttpResponseRedirect(target)
    if code is not None:
        if request.user.is_authenticated:
            request.user.profile.interface_language = code
            request.user.profile.save(update_fields=['interface_language'])
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME, code, max_age=settings.LANGUAGE_COOKIE_AGE
        )
    return response
