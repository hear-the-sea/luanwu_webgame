from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gameplay.services import global_announcement


class _MemoryCache:
    def __init__(self):
        self.value = None
        self.timeout = None

    def get(self, _key):
        return self.value

    def set(self, _key, value, timeout):
        self.value = value
        self.timeout = timeout

    def delete(self, _key):
        self.value = None


def test_publish_global_announcement_round_trips_until_expiry(monkeypatch):
    memory_cache = _MemoryCache()
    monkeypatch.setattr(global_announcement, "cache", memory_cache)
    now = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)

    published = global_announcement.publish_global_announcement(
        title="全服公告",
        body="恭喜首通！",
        duration_seconds=3600,
        announcement_id="first-clear",
        now=now,
    )

    assert memory_cache.timeout == 3600
    assert global_announcement.get_active_global_announcement(now=now + timedelta(minutes=30)) == published
    assert global_announcement.get_active_global_announcement(now=now + timedelta(hours=1)) is None


def test_broadcast_global_announcement_targets_real_player_manors(monkeypatch):
    calls = []

    class _QuerySet:
        def filter(self, **_kwargs):
            return self

        def order_by(self, *_fields):
            return self

        def values_list(self, *_fields, **_kwargs):
            return self

        def iterator(self, chunk_size):
            assert chunk_size == 500
            return iter([11, 22])

    class _Manor:
        objects = _QuerySet()

    monkeypatch.setattr(global_announcement, "Manor", _Manor)
    monkeypatch.setattr(
        global_announcement,
        "notify_user",
        lambda user_id, payload, **_kwargs: calls.append((user_id, payload)) or user_id == 22,
    )

    attempted, delivered = global_announcement.broadcast_global_announcement(
        {
            "id": "first-clear",
            "title": "全服公告",
            "body": "恭喜首通！",
            "expires_at": "2026-09-26T10:00:00+00:00",
        }
    )

    assert attempted == 2
    assert delivered == 1
    assert calls[0][1]["kind"] == "announcement"
    assert calls[0][1]["announcement_id"] == "first-clear"


@pytest.mark.parametrize("duration", [0, -1, 7 * 24 * 60 * 60 + 1])
def test_publish_global_announcement_rejects_invalid_duration(duration):
    with pytest.raises(ValueError, match="持续时间"):
        global_announcement.publish_global_announcement(title="公告", body="正文", duration_seconds=duration)
