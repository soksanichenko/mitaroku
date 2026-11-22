"""Settings views: the account, the languages, the time zone and email."""

import logging
from dataclasses import dataclass

from accounts.models import Profile
from django import forms
from django.conf import settings
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST
from notifications.channel import effective_address, email_configured
from notifications.confirmation import send_confirmation
from notifications.moments import zone_names

logger = logging.getLogger(__name__)

# One-shot session flag: the page that follows a save shows the confirmation once.
SAVED_FLAG = 'settings_saved'


class LanguageForm(forms.ModelForm):
    """The two language preferences of a profile."""

    class Meta:
        model = Profile
        fields = ('interface_language', 'metadata_language')
        labels = {
            'interface_language': _('Interface language'),
            'metadata_language': _('Metadata language'),
        }


class TimezoneForm(forms.Form):
    """The time zone that sets the hour notifications arrive at."""

    timezone = forms.ChoiceField(label=_('Time zone'))

    def __init__(self, *args, **kwargs) -> None:
        """Offer every zone of ``zone_names()``; anything else is invalid."""
        super().__init__(*args, **kwargs)
        self.fields['timezone'].choices = [(name, name) for name in zone_names()]


class EmailAddressForm(forms.Form):
    """The address the user wants mail to go to."""

    email = forms.EmailField(label=_('Email address'), max_length=254)


@dataclass(frozen=True)
class EmailFeedback:
    """What the last email action wants the Email block to say."""

    toggle_error: str = ''
    address_form: EmailAddressForm | None = None
    note: str = ''


def zone_groups() -> list[tuple[str, list[str]]]:
    """Group the offered zones by region for the select.

    Returns:
        ``(label, names)`` pairs: ``UTC`` alone first under an empty label (it
        is not shown in a group), then one group per part of the name before
        its first ``/``, and zones without a ``/`` under ``Other`` last.
    """
    regions: dict[str, list[str]] = {}
    other: list[str] = []
    for name in zone_names():
        if name == 'UTC':
            continue
        region, slash, _city = name.partition('/')
        (regions.setdefault(region, []) if slash else other).append(name)
    groups = [('', ['UTC']), *regions.items()]
    if other:
        groups.append((str(_('Other')), other))
    return groups


def _render_page(
    request: HttpRequest,
    language_form: LanguageForm,
    timezone_form: TimezoneForm,
    email_feedback: EmailFeedback | None = None,
) -> HttpResponse:
    """Render the settings page with the forms in their current state.

    This is the only renderer of the page. The Email block is added only while
    the operator has configured outgoing email.
    """
    profile = request.user.profile
    feedback = email_feedback or EmailFeedback()
    if profile.notify_email:
        email_source = 'entered'
    elif request.user.discord_id:
        email_source = 'portal'
    else:
        email_source = 'account'
    return render(
        request,
        'web/settings.html',
        {
            'form': language_form,
            'timezone_form': timezone_form,
            'zone_groups': zone_groups(),
            # The browser's zone is only offered while the profile holds the default.
            'detect_zone': profile.timezone == 'UTC',
            'email_shown': email_configured(),
            'email_address': effective_address(request.user),
            'email_source': email_source,
            'email_enabled': profile.email_enabled,
            'email_pending': profile.pending_email,
            'email_custom': bool(profile.notify_email or profile.pending_email),
            'email_toggle_error': feedback.toggle_error,
            'email_form': feedback.address_form or EmailAddressForm(),
            'email_note': feedback.note,
            'saved': request.session.pop(SAVED_FLAG, False),
        },
    )


@require_http_methods(['GET', 'POST'])
def settings_page(request: HttpRequest) -> HttpResponse:
    """Show the settings and save the language preferences.

    Only the profile of the signed-in user is ever edited. A valid save answers
    with a redirect (post/redirect/get), so the next request already runs in the
    new interface language.

    Args:
        request: The request; a POST carries both language fields.

    Returns:
        The page, or a redirect to it after a save.
    """
    profile = request.user.profile
    form = LanguageForm(request.POST or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        request.session[SAVED_FLAG] = True
        response = HttpResponseRedirect(reverse('settings'))
        # Same cookie as the header switch, so the login page speaks the language too.
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            profile.interface_language,
            max_age=settings.LANGUAGE_COOKIE_AGE,
        )
        return response
    return _render_page(
        request, form, TimezoneForm(initial={'timezone': profile.timezone})
    )


@require_POST
def settings_timezone(request: HttpRequest) -> HttpResponse:
    """Save the time zone of the signed-in user.

    Only ``Profile.timezone`` changes; ``notify_since`` is left alone. The
    notification dedupe key uses the release date, which no zone changes, so a
    zone change cannot repeat a notification, while an event that is already due
    in the new zone (a move to the east) is delivered at the next scan. Saving
    the zone the profile already holds changes nothing. The value is checked
    against ``zone_names()``; what the browser guessed is never trusted.

    Args:
        request: The POST request carrying ``timezone``.

    Returns:
        A redirect to the settings after a save, or the page with the error.
    """
    profile = request.user.profile
    form = TimezoneForm(request.POST)
    if not form.is_valid():
        return _render_page(request, LanguageForm(instance=profile), form)
    zone = form.cleaned_data['timezone']
    if zone != profile.timezone:
        profile.timezone = zone
        profile.save(update_fields=['timezone'])
    request.session[SAVED_FLAG] = True
    return HttpResponseRedirect(reverse('settings'))


def _saved(request: HttpRequest) -> HttpResponse:
    """Flag the save and redirect back to the settings (post/redirect/get)."""
    request.session[SAVED_FLAG] = True
    return HttpResponseRedirect(reverse('settings'))


def _page_with(request: HttpRequest, feedback: EmailFeedback) -> HttpResponse:
    """Render the settings page with the saved forms and ``feedback``."""
    profile = request.user.profile
    return _render_page(
        request,
        LanguageForm(instance=profile),
        TimezoneForm(initial={'timezone': profile.timezone}),
        feedback,
    )


def _toggle(request: HttpRequest, profile: Profile) -> HttpResponse:
    """Switch the email channel on or off."""
    if request.POST.get('enabled'):
        if not effective_address(request.user):
            return _page_with(
                request,
                EmailFeedback(toggle_error=gettext('Add an email address first')),
            )
        # Only the first switch-on sets the moment; nothing older is ever mailed.
        if not profile.email_enabled:
            profile.email_enabled = True
            profile.email_since = timezone.now()
            profile.save(update_fields=['email_enabled', 'email_since'])
    else:
        profile.email_enabled = False
        profile.email_since = None
        profile.save(update_fields=['email_enabled', 'email_since'])
    return _saved(request)


def _address(request: HttpRequest, profile: Profile) -> HttpResponse:
    """Hold a new address as pending and mail it a confirmation link."""
    form = EmailAddressForm(request.POST)
    if not form.is_valid():
        return _page_with(request, EmailFeedback(address_form=form))
    address = form.cleaned_data['email']
    if address.lower() == effective_address(request.user).lower():
        return _page_with(
            request, EmailFeedback(note=gettext('That is already your address.'))
        )
    try:
        send_confirmation(request.user, address)
    except Exception as exc:
        # The SMTP error text may name the address, so only the class is logged.
        logger.error('Could not send the confirmation mail (%s)', type(exc).__name__)
        profile.pending_email = ''
        profile.save(update_fields=['pending_email'])
        form.add_error(
            'email', gettext('We could not send the confirmation. Try again later.')
        )
        return _page_with(request, EmailFeedback(address_form=form))
    profile.pending_email = address
    profile.save(update_fields=['pending_email'])
    return _saved(request)


def _use_portal(request: HttpRequest, profile: Profile) -> HttpResponse:
    """Go back to the account address: drop the entered and the pending one."""
    profile.notify_email = ''
    profile.pending_email = ''
    profile.save(update_fields=['notify_email', 'pending_email'])
    return _saved(request)


EMAIL_ACTIONS = {'toggle': _toggle, 'address': _address, 'use_portal': _use_portal}


@require_POST
def settings_email(request: HttpRequest) -> HttpResponse:
    """Handle the three small forms of the Email block.

    The hidden ``action`` field picks the form: ``toggle``, ``address`` or
    ``use_portal``. Only the profile of the signed-in user changes.

    Args:
        request: The POST request.

    Returns:
        A redirect after a save, or the page with the error or note. A 404 when
        email is not configured, a 400 for an unknown action.
    """
    if not email_configured():
        raise Http404
    handler = EMAIL_ACTIONS.get(request.POST.get('action', ''))
    if handler is None:
        return HttpResponseBadRequest()
    return handler(request, request.user.profile)
