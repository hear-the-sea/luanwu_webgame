from __future__ import annotations

from types import SimpleNamespace

from gameplay.services.raid.combat import messaging as raid_messaging


def test_send_raid_battle_messages_sends_extra_capture_notice_to_defender_when_defender_guest_is_captured(monkeypatch):
    sent: list[dict[str, object]] = []

    def _create_message(*, manor, kind, title, body, battle_report=None):
        sent.append(
            {
                "manor": manor,
                "kind": kind,
                "title": title,
                "body": body,
                "battle_report": battle_report,
            }
        )

    monkeypatch.setattr(raid_messaging, "create_message", _create_message)

    report = object()
    attacker = SimpleNamespace(location_display="江南", display_name="进攻方")
    defender = SimpleNamespace(display_name="防守方")
    run = SimpleNamespace(
        is_attacker_victory=True,
        battle_rewards={"capture": {"guest_name": "赵云", "from": "defender"}},
        loot_resources={},
        loot_items={},
        attacker_prestige_change=10,
        defender_prestige_change=-20,
        attacker=attacker,
        defender=defender,
        battle_report=report,
    )

    raid_messaging._send_raid_battle_messages(run)

    assert len(sent) == 3
    assert sent[1]["manor"] is defender
    assert sent[2]["manor"] is defender
    assert sent[2]["title"] == "门客被俘通知"
    assert "赵云" in str(sent[2]["body"])
    assert "已被押入" in str(sent[2]["body"])
    assert sent[2]["battle_report"] is report


def test_send_raid_battle_messages_sends_extra_capture_notice_to_attacker_when_attacker_guest_is_captured(monkeypatch):
    sent: list[dict[str, object]] = []

    def _create_message(*, manor, kind, title, body, battle_report=None):
        sent.append(
            {
                "manor": manor,
                "kind": kind,
                "title": title,
                "body": body,
                "battle_report": battle_report,
            }
        )

    monkeypatch.setattr(raid_messaging, "create_message", _create_message)

    report = object()
    attacker = SimpleNamespace(location_display="江南", display_name="进攻方")
    defender = SimpleNamespace(display_name="防守方")
    run = SimpleNamespace(
        is_attacker_victory=False,
        battle_rewards={"capture": {"guest_name": "关羽", "from": "attacker"}},
        loot_resources={},
        loot_items={},
        attacker_prestige_change=-10,
        defender_prestige_change=20,
        attacker=attacker,
        defender=defender,
        battle_report=report,
    )

    raid_messaging._send_raid_battle_messages(run)

    assert len(sent) == 3
    assert sent[0]["manor"] is attacker
    assert sent[2]["manor"] is attacker
    assert sent[2]["title"] == "门客被俘通知"
    assert "关羽" in str(sent[2]["body"])
    assert "装备尽失" in str(sent[2]["body"])
    assert sent[2]["battle_report"] is report


def test_send_raid_battle_messages_does_not_send_extra_notice_without_capture(monkeypatch):
    sent: list[dict[str, object]] = []

    def _create_message(*, manor, kind, title, body, battle_report=None):
        sent.append(
            {
                "manor": manor,
                "kind": kind,
                "title": title,
                "body": body,
                "battle_report": battle_report,
            }
        )

    monkeypatch.setattr(raid_messaging, "create_message", _create_message)

    run = SimpleNamespace(
        is_attacker_victory=True,
        battle_rewards={},
        loot_resources={},
        loot_items={},
        attacker_prestige_change=10,
        defender_prestige_change=-20,
        attacker=SimpleNamespace(location_display="江南", display_name="进攻方"),
        defender=SimpleNamespace(display_name="防守方"),
        battle_report=object(),
    )

    raid_messaging._send_raid_battle_messages(run)

    assert len(sent) == 2
