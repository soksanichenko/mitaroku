import pytest
from django.test import RequestFactory
from web.tasks import ping
from web.views import healthz


def call_healthz():
    return healthz(RequestFactory().get('/healthz/'))


@pytest.mark.django_db
def test_healthz_reports_ok_when_dependencies_answer():
    response = call_healthz()
    assert response.status_code == 200
    assert response.content == b'{"status": "ok"}'


@pytest.mark.django_db
def test_healthz_reports_a_down_redis(settings):
    settings.REDIS_PORT = 1
    response = call_healthz()
    assert response.status_code == 503
    assert response.content == b'{"status": "error", "failing": ["redis"]}'


def test_healthz_reports_a_down_database(monkeypatch):
    def broken():
        raise RuntimeError('db down')

    monkeypatch.setattr('web.views.connection.ensure_connection', broken)
    response = call_healthz()
    assert response.status_code == 503
    assert response.content == b'{"status": "error", "failing": ["database"]}'


def test_ping_task_runs_through_huey():
    assert ping().get() == 'pong'
