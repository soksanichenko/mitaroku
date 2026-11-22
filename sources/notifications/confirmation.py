"""The mail that asks a user to confirm the address they entered."""

from accounts.models import User
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext
from notifications.channel import absolute_url
from notifications.tokens import confirmation_token


def send_confirmation(user: User, address: str) -> None:
    """Send ``user`` a link that confirms ``address`` as their mail address.

    The message is built under the interface language of the profile. The
    subject is fixed text and the only variable parts are the address, which
    the templates escape, and a signed link.

    Args:
        user: The account the address is for.
        address: The address the mail goes to and that the link confirms.

    Raises:
        Exception: Whatever the mail backend raises; the caller decides what
            the user sees.
    """
    with translation.override(user.profile.interface_language):
        subject = gettext('Confirm your address for mitaroku')
        context = {
            'language': translation.get_language(),
            'subject': subject,
            'address': address,
            'confirm_url': absolute_url(
                reverse('email_confirm', args=[confirmation_token(user, address)])
            ),
        }
        text = render_to_string('notifications/email/confirm.txt', context)
        html = render_to_string('notifications/email/confirm.html', context)
    message = EmailMultiAlternatives(subject=subject, body=text, to=[address])
    message.attach_alternative(html, 'text/html')
    message.send()
