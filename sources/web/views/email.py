"""Public email pages: unsubscribe and address confirmation.

Both work without a session, because they are opened from a mail client. A GET
only shows a page, since mail scanners prefetch links; a POST acts.
"""

from accounts.models import Profile, User
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import translation
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from notifications.tokens import confirmation_target, unsubscribe_user


def _invalid(request: HttpRequest) -> HttpResponse:
    """Render the invalid-link page in the language of the request."""
    return render(request, 'web/email_invalid.html', status=400)


def _render(
    request: HttpRequest, template: str, user: User, context: dict
) -> HttpResponse:
    """Render ``template`` in the interface language of the token's ``user``."""
    language = user.profile.interface_language
    with translation.override(language):
        response = render(request, template, context)
    # LocaleMiddleware reads the language after the override ended.
    response.headers['Content-Language'] = language
    return response


# The mail client posts the unsubscribe itself (RFC 8058 one-click), without
# a cookie or a CSRF token; the signed token in the URL is the proof.
@login_not_required
@csrf_exempt
@require_http_methods(['GET', 'POST'])
def email_unsubscribe(request: HttpRequest, token: str) -> HttpResponse:
    """Turn email off for the user the token names (POST) or offer to (GET)."""
    user = unsubscribe_user(token)
    if user is None:
        return _invalid(request)
    done = request.method == 'POST'
    if done:
        Profile.objects.filter(user=user).update(email_enabled=False, email_since=None)
    return _render(request, 'web/email_unsubscribe.html', user, {'done': done})


@login_not_required
@require_http_methods(['GET', 'POST'])
def email_confirm(request: HttpRequest, token: str) -> HttpResponse:
    """Confirm the address in the token (POST) or offer to (GET)."""
    target = confirmation_target(token)
    if target is None:
        return _invalid(request)
    user, address = target
    # A link for an address the user has since replaced is no longer valid.
    if user.profile.pending_email != address:
        return _invalid(request)
    done = request.method == 'POST'
    if done:
        Profile.objects.filter(user=user, pending_email=address).update(
            notify_email=address, pending_email=''
        )
    return _render(
        request,
        'web/email_confirm.html',
        user,
        {'address': address, 'done': done},
    )
