"""Title views."""

from catalog.models import Title
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render


def title(request: HttpRequest, pk: int) -> HttpResponse:
    """Render a title page so that links to it resolve.

    The title page screen replaces this placeholder.
    """
    return render(request, 'web/title.html', {'title': get_object_or_404(Title, pk=pk)})
