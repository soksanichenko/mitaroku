"""Root redirect and health check."""

import redis
from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect


def index(request: HttpRequest) -> HttpResponse:
    """Send the root URL to the library."""
    return redirect('library')


@login_not_required
def healthz(request: HttpRequest) -> JsonResponse:
    """Report whether PostgreSQL and Redis answer.

    Returns:
        200 with ``{"status": "ok"}`` or 503 naming the failing dependency.
    """
    failing = []
    try:
        connection.ensure_connection()
    except Exception:
        failing.append('database')
    try:
        redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            socket_connect_timeout=2,
        ).ping()
    except Exception:
        failing.append('redis')
    if failing:
        return JsonResponse({'status': 'error', 'failing': failing}, status=503)
    return JsonResponse({'status': 'ok'})
