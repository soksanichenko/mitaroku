"""HTTP access to the metadata providers.

``ProviderHttp`` wraps one ``httpx.Client`` with the layers the spec asks for:
a Redis response cache with a longer-lived stale copy, a single-flight lock so
concurrent callers share one request, a per-provider rate limiter, retries on
429, 5xx and network errors, and stale-on-error.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from redis import Redis
from redis.exceptions import LockError

logger = logging.getLogger(__name__)

MAX_RETRY_DELAY = 120.0
NOT_FOUND_TTL = 300
LOCK_MARGIN = 30.0


class ProviderError(Exception):
    """A provider request failed and no stale copy was available."""


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a ``Retry-After`` header into seconds to wait.

    Args:
        value: The header value: delta-seconds or an HTTP date.
        now: Current time, for tests.

    Returns:
        Seconds to wait (never negative), or None when absent or unparsable.
    """
    if not value:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - (now or datetime.now(UTC))).total_seconds())


class ProviderHttp:
    """Cached, rate-limited, retrying JSON access to one provider."""

    def __init__(
        self,
        name: str,
        *,
        client: httpx.Client,
        redis: Redis,
        min_interval: float = 0.0,
        ttl: int = 6 * 3600,
        stale_ttl: int = 7 * 24 * 3600,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        prefix: str = 'mitaroku:providers:',
    ) -> None:
        """Create the wrapper.

        Args:
            name: Provider name, used in cache keys and log lines.
            client: The ``httpx.Client`` to send requests with.
            redis: Redis connection created with ``decode_responses=True``.
            min_interval: Minimum seconds between two requests to this provider.
            ttl: Seconds a response is served without asking the provider again.
            stale_ttl: Seconds the stale copy survives for stale-on-error.
            max_attempts: Tries per request before giving up.
            sleep: Replaceable sleep, so tests do not wait.
            prefix: Redis key prefix.
        """
        self.name = name
        self._client = client
        self._redis = redis
        self._min_interval = min_interval
        self._ttl = ttl
        self._stale_ttl = stale_ttl
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._prefix = f'{prefix}{name}:'

    def get_json(
        self, url: str, *, params: dict | None = None, ttl: int | None = None
    ) -> object | None:
        """GET a JSON document.

        Args:
            url: Absolute URL.
            params: Query parameters.
            ttl: Cache lifetime override in seconds.

        Returns:
            The parsed JSON, or None when the provider answers 404.

        Raises:
            ProviderError: The request failed and no stale copy exists.
        """
        key = self._key('GET', url, params)
        return self._cached(key, ttl, lambda: self._client.get(url, params=params))

    def post_json(
        self, url: str, *, json: dict, ttl: int | None = None
    ) -> object | None:
        """POST a JSON body (GraphQL) and return the parsed JSON response.

        Args:
            url: Absolute URL.
            json: Request body; it is part of the cache key.
            ttl: Cache lifetime override in seconds.

        Returns:
            The parsed JSON, or None when the provider answers 404.

        Raises:
            ProviderError: The request failed and no stale copy exists.
        """
        key = self._key('POST', url, json)
        return self._cached(key, ttl, lambda: self._client.post(url, json=json))

    def _key(self, method: str, url: str, extra: object) -> str:
        """Return a stable cache key for a request."""
        raw = json.dumps([method, url, extra], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _read(self, key: str) -> tuple[object | None] | None:
        """Return ``(value,)`` for a cached entry, or None when absent."""
        raw = self._redis.get(self._prefix + key)
        return None if raw is None else (json.loads(raw),)

    def _store(self, key: str, value: object | None, ttl: int) -> None:
        """Write the fresh copy and the longer-lived stale copy."""
        raw = json.dumps(value)
        self._redis.set(self._prefix + key, raw, ex=ttl)
        self._redis.set(self._prefix + 'stale:' + key, raw, ex=self._stale_ttl)

    def _cached(
        self, key: str, ttl: int | None, send: Callable[[], httpx.Response]
    ) -> object | None:
        """Serve from cache, or fetch once under a lock, or fall back to stale."""
        hit = self._read(key)
        if hit is not None:
            return hit[0]
        worst_case = self._worst_case_duration()
        lock = self._redis.lock(self._prefix + 'lock:' + key, timeout=worst_case)
        locked = lock.acquire(blocking=True, blocking_timeout=worst_case)
        if not locked:
            logger.warning('%s: proceeding without the request lock', self.name)
        try:
            hit = self._read(key)
            if hit is not None:
                return hit[0]
            try:
                value = self._send(send)
            except ProviderError:
                stale = self._read('stale:' + key)
                if stale is None:
                    raise
                logger.warning('%s unavailable, serving a stale copy', self.name)
                return stale[0]
            self._store(
                key, value, NOT_FOUND_TTL if value is None else ttl or self._ttl
            )
            return value
        finally:
            if locked:
                try:
                    lock.release()
                except LockError:
                    logger.warning('%s lock expired before release', self.name)

    def _worst_case_duration(self) -> float:
        """Return the longest a single fetch can take, retries included.

        The single-flight lock must outlive this, or a second caller could send
        a duplicate request while the first is still retrying.
        """
        timeout = self._client.timeout
        per_request = sum(
            part
            for part in (timeout.connect, timeout.read, timeout.write, timeout.pool)
            if part is not None
        )
        sleeps = (self._max_attempts - 1) * MAX_RETRY_DELAY
        return sleeps + self._max_attempts * per_request + LOCK_MARGIN

    def _throttle(self) -> None:
        """Wait until this provider's minimum request interval has passed."""
        if self._min_interval <= 0:
            return
        slot = self._prefix + 'slot'
        while not self._redis.set(slot, 1, nx=True, px=int(self._min_interval * 1000)):
            self._sleep(min(self._min_interval / 4, 0.05))

    def _send(self, send: Callable[[], httpx.Response]) -> object | None:
        """Send with retries on 429, 5xx and network errors."""
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            delay = min(2.0 ** (attempt - 1), MAX_RETRY_DELAY)
            try:
                response = send()
            except httpx.TransportError as error:
                logger.info('%s network error: %s', self.name, error)
                reason = type(error).__name__
            else:
                status = response.status_code
                if status == 404:
                    return None
                if status < 400:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise ProviderError(
                            f'{self.name} sent a body that is not JSON'
                        ) from error
                if status != 429 and status < 500:
                    raise ProviderError(f'{self.name} answered HTTP {status}')
                retry_after = parse_retry_after(response.headers.get('Retry-After'))
                if retry_after is not None:
                    delay = min(retry_after, MAX_RETRY_DELAY)
                reason = f'HTTP {status}'
            if attempt == self._max_attempts:
                raise ProviderError(
                    f'{self.name} failed after {attempt} tries: {reason}'
                )
            logger.info('%s retry %s in %.1f s (%s)', self.name, attempt, delay, reason)
            self._sleep(delay)
        raise ProviderError(f'{self.name}: no attempts made')
