"""The Telegram block of the settings: connect, switch, disconnect, and its poll."""

from dataclasses import dataclass

from accounts.models import Profile
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
)
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.views.decorators.http import require_GET, require_POST
from notifications import telegram_link
from notifications.channel import telegram_configured


@dataclass(frozen=True)
class TelegramFeedback:
    """What the last Telegram action wants the Telegram block to say."""

    toggle_error: str = ''
    # The one-time link; it exists only in the response to the connect POST.
    link: str = ''


def _state(profile: Profile) -> str:
    """Return which of the five states of the block ``profile`` is in.

    A live link token with no chat means the user is waiting for Start, also
    after a block: pressing Connect again is the way out of the blocked state.
    """
    if profile.telegram_chat_id is not None:
        return 'connected' if profile.telegram_enabled else 'paused'
    expires = profile.telegram_link_expires
    if profile.telegram_link_hash and expires and expires > timezone.now():
        return 'waiting'
    return 'blocked' if profile.telegram_blocked else 'idle'


def telegram_context(
    profile: Profile, feedback: TelegramFeedback | None = None
) -> dict:
    """Return the template context of the Telegram block.

    Args:
        profile: The profile of the signed-in user.
        feedback: What the last action wants the block to say, if anything.

    Returns:
        The ``telegram_*`` context keys that ``_render_page`` merges.
    """
    feedback = feedback or TelegramFeedback()
    return {
        'telegram_shown': telegram_configured(),
        'telegram_state': _state(profile),
        'telegram_enabled': profile.telegram_enabled,
        'telegram_toggle_error': feedback.toggle_error,
        'telegram_link': feedback.link,
    }


def _connect(request: HttpRequest, profile: Profile) -> HttpResponse:
    """Issue a link token and render the page with the link, uncacheable.

    A profile that already has a chat gets nothing: no token is issued and the
    user is sent back to the settings. Moving to another chat is a disconnect
    first, then a connect.
    """
    from web.views.settings import _page_with, _saved

    if profile.telegram_chat_id is not None:
        return _saved(request)
    token = telegram_link.issue_link_token(request.user, timezone.now())
    response = _page_with(
        request, telegram_feedback=TelegramFeedback(link=telegram_link.link_url(token))
    )
    response['Cache-Control'] = 'no-store'
    return response


def _toggle(request: HttpRequest, profile: Profile) -> HttpResponse:
    """Switch the Telegram channel on or off."""
    from web.views.settings import _page_with, _saved

    if request.POST.get('enabled'):
        if profile.telegram_chat_id is None:
            return _page_with(
                request,
                telegram_feedback=TelegramFeedback(
                    toggle_error=gettext('Connect Telegram first')
                ),
            )
        # Each switch-on from off sets the moment; nothing older is ever sent.
        if not profile.telegram_enabled:
            profile.telegram_enabled = True
            profile.telegram_since = timezone.now()
            profile.save(update_fields=['telegram_enabled', 'telegram_since'])
    else:
        profile.telegram_enabled = False
        profile.telegram_since = None
        profile.save(update_fields=['telegram_enabled', 'telegram_since'])
    return _saved(request)


def _disconnect(request: HttpRequest, profile: Profile) -> HttpResponse:
    """Forget the chat and switch the channel off."""
    from web.views.settings import _saved

    telegram_link.disconnect(profile)
    return _saved(request)


TELEGRAM_ACTIONS = {
    'connect': _connect,
    'toggle': _toggle,
    'disconnect': _disconnect,
}


@require_POST
def settings_telegram(request: HttpRequest) -> HttpResponse:
    """Handle the small forms of the Telegram block.

    The hidden ``action`` field picks the form: ``connect``, ``toggle`` or
    ``disconnect``. Only the profile of the signed-in user changes. ``connect``
    answers with the page itself, because the one-time link must not outlive that
    response; the other actions redirect, and so does ``connect`` on a profile
    that already has a chat (it issues nothing).

    Args:
        request: The POST request.

    Returns:
        A redirect after a change, or the page. A 404 when Telegram is not
        configured, a 400 for an unknown action.
    """
    if not telegram_configured():
        raise Http404
    handler = TELEGRAM_ACTIONS.get(request.POST.get('action', ''))
    if handler is None:
        return HttpResponseBadRequest()
    return handler(request, request.user.profile)


@require_GET
def settings_telegram_status(request: HttpRequest) -> HttpResponse:
    """Tell the waiting block whether the chat has been connected.

    Args:
        request: The GET request the block polls with.

    Returns:
        204 while the profile of the user has no chat, 200 with ``HX-Redirect``
        to the settings once it has one. A redirect navigates with a GET; a
        refresh would re-send the POST that rendered the waiting page. A 404
        when Telegram is not configured.
    """
    if not telegram_configured():
        raise Http404
    connected = Profile.objects.filter(
        user=request.user, telegram_chat_id__isnull=False
    ).exists()
    response = HttpResponse(status=200 if connected else 204)
    if connected:
        response['HX-Redirect'] = reverse('settings')
    response['Cache-Control'] = 'no-store'
    return response
