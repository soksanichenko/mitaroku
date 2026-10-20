"""Settings view: the read-only account and the language preferences."""

from accounts.models import Profile
from django import forms
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

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
    return render(
        request,
        'web/settings.html',
        {'form': form, 'saved': request.session.pop(SAVED_FLAG, False)},
    )
