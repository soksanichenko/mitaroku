"""A thin client of the Telegram Bot API.

The bot token is part of every request URL, so nothing here puts an ``httpx``
exception, a response URL or ``raise_for_status()`` text into an error or a
return value. ``httpx`` itself logs every request URL at INFO, so the URL is
kept out of the logs only by ``_HideTelegramUrls``, installed on the ``httpx``
logger when this module is imported. ``httpcore`` logs the host and port, never
the path, and needs no filter.
"""

import logging
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from notifications.errors import PermanentFailure, RetryAfter, Unreachable

API_URL = 'https://api.telegram.org'
TIMEOUT = 10.0


class _HideTelegramUrls(logging.Filter):
    """Drop the log records that mention the Bot API, whose URLs hold the token."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Tell whether the record may be logged.

        Args:
            record: The record about to be handled.

        Returns:
            False when the formatted message mentions the Bot API's host.
        """
        return urlsplit(API_URL).hostname not in record.getMessage()


def _hide_telegram_urls() -> None:
    """Install the filter on the ``httpx`` logger once, even after a reload."""
    logger = logging.getLogger('httpx')
    for existing in logger.filters:
        if type(existing).__name__ == _HideTelegramUrls.__name__:
            logger.removeFilter(existing)
    logger.addFilter(_HideTelegramUrls())


_hide_telegram_urls()


class TelegramError(Exception):
    """The Bot API answered, but not with a usable success.

    Attributes:
        code: Telegram's ``error_code`` or the HTTP status; None for a 200 whose
            body is not a success.
    """

    def __init__(self, message: str, code: int | None = None) -> None:
        """Keep the description and the code.

        Args:
            message: What went wrong; never contains the token.
            code: Telegram's ``error_code`` or the HTTP status.
        """
        super().__init__(message)
        self.code = code


class BotApi:
    """The two Bot API methods the channel uses."""

    def __init__(self, token: str, client: httpx.Client) -> None:
        """Create the wrapper.

        Args:
            token: The bot token.
            client: The ``httpx.Client`` to send requests with.
        """
        self._token = token
        self._client = client

    def send_message(self, chat_id: int, text: str) -> None:
        """Send an HTML message without a link preview.

        Args:
            chat_id: The chat to send to.
            text: The message, in Telegram's HTML subset.

        Raises:
            PermanentFailure: The chat refuses the bot for good.
            RetryAfter: Telegram asks to wait.
            Unreachable: Telegram cannot be reached.
            TelegramError: Any other failed answer.
        """
        self._call(
            'sendMessage',
            {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'link_preview_options': {'is_disabled': True},
            },
        )

    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        """Fetch the updates the bot has not confirmed yet.

        Args:
            offset: The first ``update_id`` to return; None for all pending.
            timeout: Seconds Telegram may hold the request open for news.

        Returns:
            The updates, oldest first.

        Raises:
            Unreachable: Telegram cannot be reached.
            TelegramError: A failed answer, or one without a ``result`` list.
        """
        payload: dict = {'timeout': timeout}
        if offset is not None:
            payload = {'offset': offset, **payload}
        result = self._call('getUpdates', payload)
        if not isinstance(result, list):
            raise TelegramError('getUpdates answered without a result list')
        return result

    def _call(self, method: str, payload: dict) -> object:
        """POST one method and return its ``result``.

        Raises:
            PermanentFailure: 403, or 400 for an unknown chat.
            RetryAfter: 429 with the seconds to wait.
            Unreachable: A transport error; only its class name is kept.
            TelegramError: Any other answer that is not ``ok``.
        """
        url = f'{API_URL}/bot{self._token}/{method}'
        reason = None
        try:
            response = self._client.post(url, json=payload)
        except httpx.TransportError as error:
            reason = type(error).__name__
        if reason is not None:
            # Raised outside the handler so the URL-bearing error is not chained.
            raise Unreachable(reason)
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get('ok') is True and status < 400:
            return body.get('result')
        self._fail(method, status, body if isinstance(body, dict) else {})

    @staticmethod
    def _fail(method: str, status: int, body: dict) -> None:
        """Raise the error that matches a failed answer.

        Args:
            method: The Bot API method, for the message.
            status: The HTTP status.
            body: The parsed JSON body, or an empty dict.
        """
        code = body.get('error_code')
        if not isinstance(code, int):
            code = status if status >= 400 else None
        description = str(body.get('description') or '')
        message = f'{method} failed: {code} {description}'.strip()
        if code == 403 or (code == 400 and 'chat not found' in description.lower()):
            raise PermanentFailure(message)
        parameters = body.get('parameters')
        seconds = (
            parameters.get('retry_after') if isinstance(parameters, dict) else None
        )
        if code == 429 and isinstance(seconds, (int, float)):
            raise RetryAfter(seconds)
        raise TelegramError(message, code)


def make_client() -> httpx.Client:
    """Return the ``httpx.Client`` the bot talks through."""
    return httpx.Client(timeout=TIMEOUT)


def bot_api() -> BotApi:
    """Return a ``BotApi`` for the configured bot."""
    return BotApi(settings.TELEGRAM_BOT_TOKEN, make_client())
