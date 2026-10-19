"""Register the tracking tasks with the Huey worker, which imports ``tasks``."""

from tracking.adding import add_title_task, cleanup_add_requests

__all__ = ['add_title_task', 'cleanup_add_requests']
