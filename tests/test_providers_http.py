import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from providers.http import ProviderError, ProviderHttp, parse_retry_after

URL = 'https://provider.example/items/1'


def make_http(handler, redis_client, redis_prefix, sleeps=None, **kwargs):
    """Build a ProviderHttp over a mock transport; ``sleeps`` records waits."""
    sleeps = sleeps if sleeps is not None else []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ProviderHttp(
        'test',
        client=client,
        redis=redis_client,
        prefix=redis_prefix,
        sleep=sleeps.append,
        **kwargs,
    )


def test_second_call_is_served_from_the_cache(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 1})

    http = make_http(handler, redis_client, redis_prefix)
    assert http.get_json(URL) == {'id': 1}
    assert http.get_json(URL) == {'id': 1}
    assert len(calls) == 1


def test_query_parameters_are_part_of_the_cache_key(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'q': request.url.params['q']})

    http = make_http(handler, redis_client, redis_prefix)
    assert http.get_json(URL, params={'q': 'a'}) == {'q': 'a'}
    assert http.get_json(URL, params={'q': 'b'}) == {'q': 'b'}
    assert len(calls) == 2


def test_not_found_returns_none_and_is_cached(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404)

    http = make_http(handler, redis_client, redis_prefix)
    assert http.get_json(URL) is None
    assert http.get_json(URL) is None
    assert len(calls) == 1


def test_post_body_is_part_of_the_cache_key(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(request.content)
        return httpx.Response(200, json={'ok': True})

    http = make_http(handler, redis_client, redis_prefix)
    http.post_json(URL, json={'query': 'a'})
    http.post_json(URL, json={'query': 'a'})
    http.post_json(URL, json={'query': 'b'})
    assert len(calls) == 2


def test_rate_limited_request_waits_for_retry_after_seconds(redis_client, redis_prefix):
    responses = iter(
        [
            httpx.Response(429, headers={'Retry-After': '7'}),
            httpx.Response(200, json=[1]),
        ]
    )
    sleeps = []
    http = make_http(
        lambda request: next(responses), redis_client, redis_prefix, sleeps
    )
    assert http.get_json(URL) == [1]
    assert sleeps == [7.0]


def test_server_errors_are_retried_with_backoff(redis_client, redis_prefix):
    responses = iter(
        [httpx.Response(503), httpx.Response(502), httpx.Response(200, json='ok')]
    )
    sleeps = []
    http = make_http(
        lambda request: next(responses), redis_client, redis_prefix, sleeps
    )
    assert http.get_json(URL) == 'ok'
    assert sleeps == [1.0, 2.0]


def test_timeouts_are_retried(redis_client, redis_prefix):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectTimeout('slow', request=request)
        return httpx.Response(200, json='ok')

    sleeps = []
    http = make_http(handler, redis_client, redis_prefix, sleeps)
    assert http.get_json(URL) == 'ok'
    assert len(attempts) == 3


def test_client_errors_are_not_retried(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    http = make_http(handler, redis_client, redis_prefix)
    with pytest.raises(ProviderError, match='HTTP 401'):
        http.get_json(URL)
    assert len(calls) == 1


def test_a_body_that_is_not_json_is_a_provider_error(redis_client, redis_prefix):
    http = make_http(
        lambda request: httpx.Response(200, text='<html>maintenance</html>'),
        redis_client,
        redis_prefix,
    )
    with pytest.raises(ProviderError, match='not JSON'):
        http.get_json(URL)


def test_gives_up_after_max_attempts(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    http = make_http(handler, redis_client, redis_prefix, max_attempts=3)
    with pytest.raises(ProviderError, match='after 3 tries'):
        http.get_json(URL)
    assert len(calls) == 3


def test_stale_copy_is_served_when_the_provider_is_down(redis_client, redis_prefix):
    state = {'up': True}

    def handler(request):
        if state['up']:
            return httpx.Response(200, json={'v': 1})
        return httpx.Response(503)

    http = make_http(handler, redis_client, redis_prefix, ttl=1)
    assert http.get_json(URL) == {'v': 1}
    for key in redis_client.scan_iter(redis_prefix + 'test:*'):
        if ':stale:' not in key and ':lock:' not in key:
            redis_client.delete(key)  # the fresh copy expires, the stale one stays
    state['up'] = False
    assert http.get_json(URL) == {'v': 1}


def test_concurrent_callers_share_one_request(redis_client, redis_prefix):
    calls = []

    def handler(request):
        calls.append(1)
        time.sleep(0.3)
        return httpx.Response(200, json='shared')

    http = make_http(handler, redis_client, redis_prefix)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(http.get_json(URL)))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == ['shared'] * 4
    assert len(calls) == 1


def test_requests_are_spaced_by_the_minimum_interval(redis_client, redis_prefix):
    def handler(request):
        return httpx.Response(200, json=str(request.url))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = ProviderHttp(
        'test', client=client, redis=redis_client, prefix=redis_prefix, min_interval=0.3
    )
    started = time.monotonic()
    http.get_json(URL + 'a')
    http.get_json(URL + 'b')
    assert time.monotonic() - started >= 0.28


def test_retry_after_accepts_seconds_and_http_dates():
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after('30') == 30.0
    assert parse_retry_after('Mon, 19 Oct 2026 12:01:30 GMT', now) == 90.0
    assert parse_retry_after('Mon, 19 Oct 2026 11:00:00 GMT', now) == 0.0
    assert parse_retry_after('soon') is None
    assert parse_retry_after(None) is None


def test_retry_after_ignores_non_ascii_digits():
    assert parse_retry_after('²') is None


def test_lock_outlives_the_worst_case_of_all_retries(redis_client, redis_prefix):
    remaining = []

    def handler(request):
        for key in redis_client.scan_iter(redis_prefix + 'test:lock:*'):
            remaining.append(redis_client.pttl(key) / 1000)
        return httpx.Response(200, json='ok')

    http = make_http(handler, redis_client, redis_prefix, max_attempts=4)
    assert http.get_json(URL) == 'ok'
    assert remaining
    assert remaining[0] > 3 * 120


def test_waiter_warns_when_it_proceeds_without_the_lock(
    redis_client, redis_prefix, monkeypatch, caplog
):
    monkeypatch.setattr('providers.http.LOCK_MARGIN', 0.2)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json='ok')),
        timeout=httpx.Timeout(None),
    )
    http = ProviderHttp(
        'test', client=client, redis=redis_client, prefix=redis_prefix, max_attempts=1
    )
    key = http._key('GET', URL, None)
    held = redis_client.lock(http._prefix + 'lock:' + key, timeout=30)
    assert held.acquire(blocking=False)
    try:
        with caplog.at_level('WARNING', logger='providers.http'):
            assert http.get_json(URL) == 'ok'
    finally:
        held.release()
    assert 'test: proceeding without the request lock' in caplog.messages


def test_an_invalid_body_is_not_cached_and_the_stale_copy_survives(
    provider_http, redis_client, redis_prefix
):
    calls = []

    def handler(request):
        calls.append(1)
        body = (
            {'data': {'ok': 1}}
            if len(calls) == 1
            else {'errors': [{'message': 'boom'}]}
        )
        return httpx.Response(200, json=body)

    def validate(body):
        if not body.get('data'):
            raise ProviderError('boom')

    http = provider_http(handler)
    url = 'https://x/graphql'
    assert http.post_json(url, json={'q': 1}, validate=validate) == {'data': {'ok': 1}}
    for key in redis_client.scan_iter(redis_prefix + 'test:*'):
        if ':stale:' not in key and ':lock:' not in key:
            redis_client.delete(key)  # the fresh copy expires, the stale one stays
    # the provider now answers with an error body: the stale copy is served
    assert http.post_json(url, json={'q': 1}, validate=validate) == {'data': {'ok': 1}}
    # with no stale copy the same body is an error, and it is not cached
    with pytest.raises(ProviderError, match='boom'):
        http.post_json(url, json={'q': 2}, validate=validate)
    with pytest.raises(ProviderError, match='boom'):
        http.post_json(url, json={'q': 2}, validate=validate)
    assert len(calls) == 4
