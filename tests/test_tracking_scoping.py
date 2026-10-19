import re
from pathlib import Path

READS = (
    r'\b(LibraryEntry|WatchEvent|AddRequest)\.objects\.'
    r'(all|filter|get|exclude|count|first|last|values|values_list|select_related'
    r'|annotate|select_for_update|exists|in_bulk)\b'
)


def test_reads_and_writes_go_through_for_user():
    """Only create/get_or_create/bulk_create may bypass for_user: reads must be scoped."""
    root = Path('sources')
    offenders = [
        f'{path}: {match.group(0)}'
        for path in list(root.glob('tracking/**/*.py')) + list(root.glob('web/**/*.py'))
        if path.name != 'models.py' and 'migrations' not in path.parts
        for match in re.finditer(READS, path.read_text())
    ]
    assert offenders == []
