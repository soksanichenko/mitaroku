"""Provider clients shared by the whole web process."""

from functools import cache

from catalog.sync.common import Clients, default_clients


@cache
def shared_clients() -> Clients:
    """Return the provider clients, built on first use and reused afterwards.

    Views use this instead of building a client per request, so the HTTP
    connection pools and rate limiters are shared. Tests patch this function.
    """
    return default_clients()
