from notifications import tasks


def test_notify_due_scans_now_and_returns_the_count(monkeypatch):
    seen = []

    def fake_scan(now):
        seen.append(now)
        return 3

    monkeypatch.setattr(tasks, 'scan_due', fake_scan)

    assert tasks.notify_due.call_local() == 3
    assert len(seen) == 1
    assert seen[0].tzinfo is not None
