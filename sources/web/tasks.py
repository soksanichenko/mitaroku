"""Background tasks."""

from huey.contrib.djhuey import db_task


@db_task()
def ping() -> str:
    """Return ``pong``; proves the worker pipeline end to end."""
    return 'pong'
