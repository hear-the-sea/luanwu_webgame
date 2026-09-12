from datetime import UTC, datetime
from types import SimpleNamespace

from gameplay.services.raid import scout_followups
from gameplay.services.raid.combat import run_side_effects


def test_scout_detected_message_uses_configured_local_timezone(monkeypatch):
    detected_at = datetime(2026, 9, 12, 4, 5, 6, tzinfo=UTC)
    monkeypatch.setattr(scout_followups.timezone, "now", lambda: detected_at)
    captured = {}
    monkeypatch.setattr(scout_followups, "create_message", lambda **kwargs: captured.update(kwargs))

    record = SimpleNamespace(
        attacker=SimpleNamespace(display_name="进攻方", location_display="东境"),
        defender=SimpleNamespace(display_name="防守方"),
    )
    scout_followups.send_scout_detected_message(record)

    assert "发现时间：2026-09-12 12:05:06" in captured["body"]


def test_raid_incoming_message_uses_configured_local_timezone():
    captured = {}
    run_side_effects.send_raid_incoming_message(
        SimpleNamespace(
            battle_at=datetime(2026, 9, 12, 4, 5, 6, tzinfo=UTC),
            attacker=SimpleNamespace(display_name="进攻方", location_display="东境"),
            defender=SimpleNamespace(),
        ),
        create_message=lambda **kwargs: captured.update(kwargs),
    )

    assert "预计抵达时间：2026-09-12 12:05:06" in captured["body"]
