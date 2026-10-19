"""Page and health views, one module per area."""

from web.views.shell import connection, healthz, index

# ``connection`` stays importable here because the health tests patch
# ``web.views.connection``.
__all__ = ['connection', 'healthz', 'index']
