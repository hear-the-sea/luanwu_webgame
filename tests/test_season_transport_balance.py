"""运输成本、窗口、实际交付与真实拦截双份贡献回归。"""

from datetime import timedelta

import pytest

from gameplay.models import Message
from gameplay.services.season.transport_rewards import delivery_merit
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401


@pytest.fixture
def resource_campaign(request):
    return request.getfixturevalue("_resource_campaign")


@pytest.mark.parametrize("value,expected", [(0, 0), (1, 0), (100, 20), (200, 40), (600, 60), (1200, 90), (3600, 90)])
def test_delivery_reward_is_useful_for_small_orders_and_capped_for_full_cargo(value, expected):
    assert delivery_merit(value, 1200, 90, 30, 200) == expected


def convoy(resource_campaign):
    season, player, clock = resource_campaign
    carrier, guest = player("history")
    guest.force = guest.defense_stat = guest.intellect = 1
    guest.current_hp = guest.max_hp
    guest.save(update_fields=["force", "defense_stat", "intellect", "current_hp"])
    site = season.sites.get(key="mountain:gate")
    site.owner = carrier.faction
    site.durability = 0
    site.save(update_fields=["owner", "durability"])
    run = issue(carrier, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    return carrier, guest, run


@pytest.mark.django_db
def test_transport_freezes_fifteen_minute_base_and_scaled_forty_five_minute_window(resource_campaign, settings):
    settings.GAME_TIME_MULTIPLIER = 10
    season, _, clock = resource_campaign
    _, _, run = convoy(resource_campaign)
    assert run.travel_seconds == 41  # 15分钟 × 敏捷减半 ÷ 10倍速，再享己方关隘减时10%。
    assert run.result["transport_seconds"] == 270
    policy = run.result["activity"]["delivery_reward"]
    assert policy == {
        "base_merit": 30,
        "base_value": 200,
        "maximum_merit": 90,
        "full_value": 1200,
        "participation_full_value": 80,
        "participation_merit": 30,
    }
    season.rules["campaign"]["expeditions"]["transport_seconds"] = 900
    season.save(update_fields=["rules"])
    advance_resource_run(run, clock)
    assert run.status == "transporting"
    assert run.due_at == clock[0] + timedelta(seconds=270)


@pytest.mark.django_db
def test_interception_credits_real_battle_and_loot_and_mails_total_once(resource_campaign):
    season, player, clock = resource_campaign
    carrier, guard, target = convoy(resource_campaign)
    advance_resource_run(target, clock)
    original = dict(target.cargo)
    raider, guest = player("fiction")
    run = issue(raider, guest, clock, mode="intercept", node="river:docks", target_run_id=target.pk)
    assert run.travel_seconds == 450
    advance_resource_run(run, clock)
    assert run.status == "transporting" and not run.report_id
    advance_resource_run(target, clock)
    run.refresh_from_db()
    assert run.report_id and run.report.winner == "attacker"
    target.refresh_from_db()
    expected_loot = {key: count * 30 // 100 for key, count in original.items()}
    assert target.result["delivered_resources"] == {key: count - expected_loot[key] for key, count in original.items()}
    assert target.cargo == {}
    combat = raider.actions.get(request_hash="settled-expedition", report=run.report)
    loot = raider.actions.get(kind="merit_credit", result__reason="运输遭遇战按实际掠夺分配贡献")
    assert combat.result["merit"] == 60
    assert target.result["convoy_loot"] == expected_loot
    assert loot.result["merit"] == 30
    raider.refresh_from_db()
    assert raider.merit == 90
    mail = Message.objects.get(manor=raider.manor, battle_report=run.report)
    assert "本场贡献：+90" in mail.body and "阵营夺取物资合计" in mail.body
    advance_resource_run(run, clock)
    raider.refresh_from_db()
    assert raider.merit == 90
    assert Message.objects.filter(manor=raider.manor, battle_report=run.report).count() == 1


@pytest.mark.django_db
def test_transport_migration_preserves_inflight_times_and_old_window(resource_campaign):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    season, _, clock = resource_campaign
    season.rules["campaign"]["expeditions"]["transport_seconds"] = 1200
    season.save(update_fields=["rules"])
    _, _, run = convoy(resource_campaign)
    # 模拟更新前已发兵、尚未冻结运输窗口的队伍。
    run.result.pop("transport_seconds")
    run.result["activity"].pop("delivery_reward")
    run.save(update_fields=["result"])
    before = (run.travel_seconds, run.due_at, run.result.copy())
    import_module("gameplay.migrations.0263_season_transport_balance").update_transport_balance(
        apps, SimpleNamespace(connection=connection)
    )
    season.refresh_from_db()
    run.refresh_from_db()
    assert (run.travel_seconds, run.due_at, run.result) == before
    exp = season.rules["campaign"]["expeditions"]
    assert exp["legacy_transport_seconds"] == 1200
    assert exp["transport_seconds"] == 2700
    assert exp["mode_travel_seconds"] == {"transport": 900, "intercept": 900, "escort": 900}
    advance_resource_run(run, clock)
    assert run.due_at == clock[0] + timedelta(seconds=1200)


@pytest.mark.django_db
def test_convoy_upgrade_preserves_existing_cargo_and_raises_only_future_capacity(resource_campaign):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    season, _, clock = resource_campaign
    season.rules["campaign"]["expeditions"]["convoy_encounters_enabled"] = False
    season.rules["campaign"]["resource_economy"]["transport"]["cargo_capacity"] = 1200
    season.save(update_fields=["rules"])
    _, _, run = convoy(resource_campaign)
    before = (run.travel_seconds, run.due_at, run.cargo.copy(), run.result.copy())
    import_module("gameplay.migrations.0264_season_convoy_encounters").enable_convoy_encounters(
        apps, SimpleNamespace(connection=connection)
    )
    season.refresh_from_db()
    run.refresh_from_db()
    assert (run.travel_seconds, run.due_at, run.cargo, run.result) == before
    assert season.rules["campaign"]["resource_economy"]["transport"]["cargo_capacity"] == 15000
    assert season.rules["campaign"]["expeditions"]["transport_reward_full_value"] == 1200
    assert season.rules["campaign"]["expeditions"]["convoy_encounters_enabled"]
