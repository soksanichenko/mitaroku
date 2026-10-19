"""Background tasks of the providers app."""

import httpx
from huey import crontab
from huey.contrib.djhuey import db_periodic_task
from mitaroku.config import load_settings
from providers import mapping


def refresh_mapping_now() -> int:
    """Download the Fribb dataset and load it; return the number of rows."""
    with httpx.Client(timeout=120) as client:
        entries = mapping.download_dataset(load_settings().providers.fribb_url, client)
    return mapping.load_entries(entries)


@db_periodic_task(
    crontab(day_of_week='1', hour='3', minute='30'), retries=2, retry_delay=600
)
def refresh_mapping_weekly() -> int:
    """Refresh the Fribb mapping every Monday at 03:30 UTC."""
    return refresh_mapping_now()
