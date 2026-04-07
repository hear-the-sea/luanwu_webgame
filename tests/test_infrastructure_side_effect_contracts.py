from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_trade_tasks_no_longer_uses_bare_delay():
    content = _read("trade/tasks.py")
    assert ".delay()" not in content
    assert "safe_apply_async(" in content


def test_arena_coop_uses_after_commit_helper_for_messages():
    content = _read("gameplay/services/arena/coop_core.py")
    assert "schedule_best_effort_after_commit(" in content


def test_raid_travel_uses_after_commit_helper_for_messages():
    content = _read("gameplay/services/raid/combat/travel.py")
    assert "schedule_best_effort_after_commit(" in content


def test_scout_followups_use_after_commit_helper():
    content = _read("gameplay/services/raid/scout_followups.py")
    assert "schedule_best_effort_after_commit(" in content


def test_global_mail_uses_atomic_failed_id_merge():
    content = _read("gameplay/tasks/global_mail.py")
    assert "merge_int_id_set(key, failed_ids, ttl=FAILED_GLOBAL_MAIL_MANOR_IDS_TTL)" in content
    assert "existing = cache.get(key) or []" not in content


def test_guild_production_uses_atomic_failed_id_merge():
    content = _read("guilds/tasks.py")
    assert "merge_int_id_set(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY, normalized_ids, ttl=None)" in content
    assert "existing_ids = _normalize_failed_guild_ids(cache.get(FAILED_GUILD_PRODUCTION_IDS_CACHE_KEY))" not in content


def test_degraded_counter_uses_atomic_increment_helper():
    content = _read("core/utils/task_monitoring.py")
    assert "increment_counter(key, ttl=_DEGRADED_COUNTER_TTL)" in content
    assert "cache.set(key, 1, timeout=_DEGRADED_COUNTER_TTL)" not in content
