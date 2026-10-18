"""Language view."""

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST


@login_not_required
@require_POST
def set_language(request: HttpRequest) -> HttpResponseRedirect:
    """Save the chosen interface language and return to the previous page.

    The language goes to the profile for signed-in users and to a cookie for
    everyone, so the login page honours it too.
    """
    code = request.POST.get('language', '')
    target = request.POST.get('next', '/')
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        target = '/'
    response = HttpResponseRedirect(target)
    if code in dict(settings.LANGUAGES):
        if request.user.is_authenticated:
            request.user.profile.interface_language = code
            request.user.profile.save(update_fields=['interface_language'])
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME, code, max_age=settings.LANGUAGE_COOKIE_AGE
        )
    return response
