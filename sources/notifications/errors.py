"""The failures a channel's send reports to the shared sender."""


class PermanentFailure(Exception):
    """The destination refuses for good: skip the queue, switch the channel off."""


class RetryAfter(Exception):
    """The service asks to wait: the next attempt is not earlier than ``seconds``."""

    def __init__(self, seconds: float) -> None:
        """Keep the wait the service asked for.

        Args:
            seconds: How long to wait before the next attempt.
        """
        super().__init__(f'retry after {seconds} s')
        self.seconds = seconds


class Unreachable(Exception):
    """The service cannot be reached: the rest of the channel's pass is pointless."""
