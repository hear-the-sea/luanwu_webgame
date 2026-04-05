from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from types import SimpleNamespace

from guilds.services.guild_pvp_display import (
    project_active_guild_pvp_run,
    project_guild_pvp_target_card,
    project_incoming_guild_pvp_run,
)


class _RunStatus:
    MARCHING = "marching"
    BATTLING = "battling"
    RETURNING = "returning"
    RETREATED = "retreated"


_STATUS_LABELS = {
    _RunStatus.MARCHING: "行军中",
    _RunStatus.BATTLING: "战斗中",
    _RunStatus.RETURNING: "返程中",
    _RunStatus.RETREATED: "已撤退",
}


class _FakeRun(SimpleNamespace):
    Status = _RunStatus

    def get_status_display(self) -> str:
        return _STATUS_LABELS[self.status]


def test_project_active_attacker_run_uses_battle_eta_and_retreat_contract():
    now = datetime(2026, 4, 5, 12, 0, tzinfo=dt_timezone.utc)
    battle_at = now + timedelta(seconds=120)
    run = _FakeRun(
        status=_RunStatus.MARCHING,
        battle_at=battle_at,
        return_at=now + timedelta(seconds=240),
        defender_guild=SimpleNamespace(name="守方帮会"),
    )

    display = project_active_guild_pvp_run(run, now=now, can_manage=True)

    assert display.run is run
    assert display.display_status_key == "marching"
    assert display.display_status_label == "行军中"
    assert display.display_hint == "正在向守方帮会进军"
    assert display.display_eta_at == battle_at
    assert display.display_eta_label == "到达"
    assert display.can_retreat is True
    assert display.action_label == "撤回"
    assert display.action_kind == "retreat"


def test_project_active_attacker_run_does_not_expose_retreat_for_overdue_marching():
    now = datetime(2026, 4, 5, 12, 0, tzinfo=dt_timezone.utc)
    return_at = now + timedelta(seconds=180)
    run = _FakeRun(
        status=_RunStatus.MARCHING,
        battle_at=now - timedelta(seconds=5),
        return_at=return_at,
        defender_guild=SimpleNamespace(name="守方帮会"),
    )

    display = project_active_guild_pvp_run(run, now=now, can_manage=True)

    assert display.run is run
    assert display.display_status_key == "battling"
    assert display.display_status_label == "战斗中"
    assert display.display_hint == "已抵达守方帮会，正在交战"
    assert display.display_eta_at == return_at
    assert display.display_eta_label == "结束"
    assert display.can_retreat is False
    assert display.action_label == ""
    assert display.action_kind == "none"


def test_project_incoming_defender_run_keeps_overdue_marching_visible_as_arrived_battling():
    now = datetime(2026, 4, 5, 12, 0, tzinfo=dt_timezone.utc)
    return_at = now + timedelta(seconds=180)
    run = _FakeRun(
        status=_RunStatus.MARCHING,
        battle_at=now - timedelta(seconds=5),
        return_at=return_at,
        attacker_guild=SimpleNamespace(name="来袭帮会"),
    )

    display = project_incoming_guild_pvp_run(run, now=now)

    assert display.run is run
    assert display.display_status_key == "arrived"
    assert display.display_status_label == "已抵达"
    assert display.display_hint == "敌方帮会已抵达，正在交战"
    assert display.display_eta_at == return_at
    assert display.display_eta_label == "结束"
    assert display.can_retreat is False
    assert display.action_label == ""
    assert display.action_kind == "none"


def test_project_target_card_emits_travel_projection_label_and_status_wording():
    guild = SimpleNamespace(
        name="青丘会",
        level=12,
        founder=SimpleNamespace(manor=SimpleNamespace(region="north")),
    )

    display = project_guild_pvp_target_card(guild, can_attack=True, blocked_reason="", travel_time_seconds=120)

    assert display.guild is guild
    assert display.status_key == "attackable"
    assert display.status_label == "可进攻"
    assert display.detail_message == "符合当前条件，可作为本次进攻目标。"
    assert display.region_key == "north"
    assert display.region_display == "北俱芦洲"
    assert display.search_text == "青丘会 北俱芦洲 lv 12 attackable"
    assert display.travel_time_seconds == 120
    assert display.travel_projection_label == "基础阵容预计 120 秒"
