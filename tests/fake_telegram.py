"""An in-memory Telegram bot, served to ``httpx`` through a mock transport."""

import json

import httpx


class FakeBot:
    """The Bot API as the code under test sees it.

    Attributes:
        token: The token the fake accepts in the request path.
        sent: The payload of every ``sendMessage`` call.
        calls: ``(method, payload)`` of every call, in order.
        updates: The queued updates ``getUpdates`` serves.
        garbage: When set, every call answers HTTP 200 with this raw body.
        down: When true, every call fails like an unreachable network.
    """

    def __init__(self, token: str = '123456:TEST-token') -> None:
        self.token = token
        self.sent: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.garbage: str | None = None
        self.down = False
        self._failures: dict[str, httpx.Response] = {}

    def say(
        self,
        chat_id: int,
        text: str,
        *,
        update_id: int | None = None,
        chat_type: str = 'private',
        language_code: str = 'en',
    ) -> dict:
        """Queue a message from a user and return its update.

        Args:
            chat_id: The chat (and the user) the message comes from.
            text: The message text.
            update_id: The update id; the next free one when omitted.
            chat_type: The chat's type.
            language_code: The user's language code.

        Returns:
            The queued update.
        """
        if update_id is None:
            update_id = max((u['update_id'] for u in self.updates), default=0) + 1
        update = {
            'update_id': update_id,
            'message': {
                'message_id': update_id,
                'chat': {'id': chat_id, 'type': chat_type},
                'from': {'id': chat_id, 'language_code': language_code},
                'text': text,
            },
        }
        self.updates.append(update)
        return update

    def fail(
        self,
        method: str,
        status: int | None,
        description: str = '',
        retry_after: int | None = None,
    ) -> None:
        """Make every later call of ``method`` answer with an error.

        Args:
            method: The Bot API method, e.g. ``sendMessage``.
            status: The HTTP status and ``error_code``; None clears the failure.
            description: The error's ``description``.
            retry_after: The ``parameters.retry_after`` of a 429.
        """
        if status is None:
            self._failures.pop(method, None)
            return
        body = {'ok': False, 'error_code': status, 'description': description}
        if retry_after is not None:
            body['parameters'] = {'retry_after': retry_after}
        self._failures[method] = httpx.Response(status, json=body)

    def client(self) -> httpx.Client:
        """Return an ``httpx.Client`` that talks to this fake."""
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request the way the Bot API would."""
        if self.down:
            # Like a real transport error, the text carries the URL and so the token.
            raise httpx.ConnectError(f'cannot connect to {request.url}')
        prefix, _, method = request.url.path.rpartition('/')
        payload = json.loads(request.content or b'{}')
        self.calls.append((method, payload))
        if self.garbage is not None:
            return httpx.Response(200, content=self.garbage.encode())
        if prefix != f'/bot{self.token}':
            body = {'ok': False, 'error_code': 401, 'description': 'Unauthorized'}
            return httpx.Response(401, json=body)
        if method in self._failures:
            return self._failures[method]
        if method == 'sendMessage':
            self.sent.append(payload)
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': 1}})
        if method == 'getUpdates':
            offset = payload.get('offset')
            result = [
                u for u in self.updates if offset is None or u['update_id'] >= offset
            ]
            return httpx.Response(200, json={'ok': True, 'result': result})
        body = {'ok': False, 'error_code': 404, 'description': 'Not Found'}
        return httpx.Response(404, json=body)
