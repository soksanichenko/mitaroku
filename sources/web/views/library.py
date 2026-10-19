"""Library views."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def library(request: HttpRequest) -> HttpResponse:
    """Render the library page (filled in by the tracking plan)."""
    return render(request, 'web/library.html')
