import re
from pathlib import Path

import pytest

SOURCES = Path(__file__).resolve().parents[1] / 'sources'

# Through the default manager only these may skip for_user: they write, never read.
WRITES = r'(?:create|get_or_create|bulk_create|for_user)'
MANAGER = (
    r'\b(?:LibraryEntry|WatchEvent|AddRequest)\s*\.\s*objects\b'
    rf'(?!\s*\.\s*{WRITES}\b)'
)
# Related managers use the default manager, so they must be scoped the same way.
RELATED = (
    r'\.\s*(?:library_entries|watch_events|add_requests)\b'
    r'(?!\s*\.\s*for_user\s*\()'
)
UNSCOPED = re.compile(f'{MANAGER}|{RELATED}')


def find_unscoped(source: str) -> list[str]:
    """Return the unscoped uses of the tracking models found in ``source``."""
    return [' '.join(match.group(0).split()) for match in UNSCOPED.finditer(source)]


def scan(paths) -> list[str]:
    """Return ``path: use`` for every unscoped use in the given files."""
    return [
        f'{path.relative_to(SOURCES)}: {use}'
        for path in paths
        for use in find_unscoped(path.read_text())
    ]


def scanned_files() -> list[Path]:
    """Return the application files that must use ``for_user``.

    models.py defines the manager and migrations are generated.
    """
    return [
        path
        for path in [*SOURCES.glob('tracking/**/*.py'), *SOURCES.glob('web/**/*.py')]
        if path.name != 'models.py' and 'migrations' not in path.parts
    ]


def test_reads_and_writes_go_through_for_user():
    """Only create/get_or_create/bulk_create may bypass for_user: reads must be scoped."""
    files = scanned_files()
    assert SOURCES / 'web' / 'views' / 'library.py' in files
    assert SOURCES / 'tracking' / 'models.py' not in files
    assert scan(files) == []


@pytest.mark.parametrize(
    'line',
    [
        'LibraryEntry.objects.filter(x)',
        'LibraryEntry.objects\n    .filter(x)',
        "WatchEvent.objects.order_by('x')",
        'WatchEvent.objects.update(a=1)',
        'WatchEvent.objects.all().delete()',
        'WatchEvent.objects.update_or_create(a=1)',
        'manager = WatchEvent.objects',
        'title.watch_events.all()',
        'episode.watch_events.filter(a=1)',
        'title.library_entries.count()',
        'user.library_entries',
        'user.add_requests.filter(a=1)',
    ],
)
def test_the_guard_reports_unscoped_use(line):
    assert find_unscoped(line)


@pytest.mark.parametrize(
    'line',
    [
        'WatchEvent.objects.create(user=u)',
        'LibraryEntry.objects.get_or_create(user=u)',
        'WatchEvent.objects.bulk_create(rows)',
        'WatchEvent.objects.for_user(u).filter(a=1)',
        'title.watch_events.for_user(u)',
        "prefetch_related('watch_events')",
    ],
)
def test_the_guard_accepts_scoped_use(line):
    assert find_unscoped(line) == []
