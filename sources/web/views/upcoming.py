"""Upcoming views."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def upcoming(request: HttpRequest) -> HttpResponse:
    """Render the upcoming page (filled in by the tracking plan)."""
    return render(request, 'web/upcoming.html')
