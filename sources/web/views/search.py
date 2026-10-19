"""Search views."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def search(request: HttpRequest) -> HttpResponse:
    """Render an empty search page so the header and nav links resolve.

    The search screen replaces this placeholder.
    """
    return render(request, 'web/search.html')
