"""Settings views: the read-only account, the languages and the time zone."""

from accounts.models import Profile
from django import forms
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST
from notifications.moments import zone_names

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
) -> HttpResponse:
    """Render the settings page with the two forms in their current state."""
    return render(
        request,
        'web/settings.html',
        {
            'form': language_form,
            'timezone_form': timezone_form,
            'zone_groups': zone_groups(),
            # The browser's zone is only offered while the profile holds the default.
            'detect_zone': request.user.profile.timezone == 'UTC',
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
