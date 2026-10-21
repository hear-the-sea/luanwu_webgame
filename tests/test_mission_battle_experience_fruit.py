from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import OperationalError
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import InventoryItem, ItemTemplate, MissionRun, MissionTemplate
from gameplay.services import battle_salvage
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl import execution
from gameplay.services.missions_impl.drops import resolve_defense_drops_if_missing
from gameplay.services.missions_impl.finalization_helpers import (
    apply_mission_rewards_if_won,
    build_mission_drops_with_salvage,
)

LOGGER = logging.getLogger(__name__)
V3_BOX = "mission_chest_v3_experience_test"


def _report(**overrides):
    payload = {
        "seed": 42,
        "drops": {"silver": 100, V3_BOX: 1},
        "winner": "attacker",
        "attacker_team": [],
        "defender_team": [],
        "losses": {
            "attacker": {"casualties": [{"key": "dao_jie", "lost": 400}]},
            "defender": {"casualties": [{"key": "dao_jie", "lost": 400}]},
        },
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _run(*, is_defense=False, is_retreating=False):
    return SimpleNamespace(
        mission=SimpleNamespace(is_defense=is_defense, name="经验回收任务", drop_table={"silver": 100, V3_BOX: 1}),
        manor=SimpleNamespace(),
        is_retreating=is_retreating,
    )


def _build(run, report):
    return build_mission_drops_with_salvage(
        run,
        report,
        "defender" if run.mission.is_defense else "attacker",
        logger=LOGGER,
        resolve_defense_drops_if_missing=resolve_defense_drops_if_missing,
    )


@pytest.mark.parametrize("is_defense", [False, True])
@pytest.mark.parametrize("lost,expected_fruit", [(200, 0), (400, 1), (600, 1), (800, 2)])
def test_v3_direct_fruit_halves_both_side_casualties_without_equipment(is_defense, lost, expected_fruit, monkeypatch):
    # dao_jie 招募耗时 90 秒，双方回收经验果汇总后减半并向下取整。
    run = _run(is_defense=is_defense)
    report = _report(
        drops={} if is_defense else {"silver": 100, V3_BOX: 1},
        losses={
            "attacker": {"casualties": [{"key": "dao_jie", "lost": lost}]},
            "defender": {"casualties": [{"key": "dao_jie", "lost": lost}]},
        },
    )
    equipment = Mock(side_effect=AssertionError("v3 不应执行装备随机回收"))
    monkeypatch.setattr(battle_salvage, "_calculate_equipment_recovery", equipment)

    expected_drops = {"silver": 100, V3_BOX: 1}
    if expected_fruit:
        expected_drops["experience_fruit"] = expected_fruit
    assert _build(run, report) == expected_drops
    equipment.assert_not_called()


def test_mission_halves_troop_and_guest_experience_without_changing_shared_salvage(monkeypatch):
    report = _report(
        attacker_team=[
            {"level": 50, "rarity": "blue", "max_hp": 100, "initial_hp": 50, "remaining_hp": 0},
        ],
        defender_team=[
            {"level": 20, "rarity": "blue", "max_hp": 100, "initial_hp": 100, "remaining_hp": 0},
            {"level": 99, "rarity": "orange", "max_hp": 100, "initial_hp": 100, "remaining_hp": 1},
        ],
    )
    # 先汇总再取整：护院 2 + 我方门客 2.5 + 敌方门客 2 = 6，PVE 减半为 3。
    expected, _ = battle_salvage.calculate_battle_salvage(report)
    assert expected == 6
    equipment = Mock(side_effect=AssertionError("禁用装备回收后仍执行了装备循环"))
    monkeypatch.setattr(battle_salvage, "_calculate_equipment_recovery", equipment)
    assert battle_salvage.calculate_battle_salvage(report, include_equipment_recovery=False) == (expected, {})
    assert _build(_run(), report)["experience_fruit"] == expected // 2
    equipment.assert_not_called()


@pytest.mark.parametrize("lost", [0, 1, 400, 799])
def test_no_loss_or_fractional_experience_does_not_grant_fruit(lost):
    report = _report(losses={"attacker": {"casualties": [{"key": "dao_jie", "lost": lost}]}, "defender": {}})
    assert _build(_run(), report) == {"silver": 100, V3_BOX: 1}


@pytest.mark.parametrize("saved_quantity", [0, 7])
def test_saved_experience_is_reused_without_recalculation(saved_quantity, monkeypatch):
    report = _report(drops={V3_BOX: 1, "experience_fruit": saved_quantity})
    salvage = Mock(side_effect=AssertionError("已保存经验不应再计算"))
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)
    assert _build(_run(), report) == report.drops
    salvage.assert_not_called()


@pytest.mark.parametrize("is_retreating,winner", [(True, "attacker"), (False, "defender"), (False, "draw")])
def test_loss_draw_or_retreat_does_not_award_experience(is_retreating, winner, monkeypatch):
    salvage = Mock(side_effect=AssertionError("失败或撤退不得计算胜利奖励"))
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)
    report = _report(winner=winner, save=Mock())
    award = Mock()
    apply_mission_rewards_if_won(
        _run(is_retreating=is_retreating),
        report,
        "attacker",
        logger=LOGGER,
        resolve_defense_drops_if_missing=resolve_defense_drops_if_missing,
        award_mission_drops_locked=award,
    )
    salvage.assert_not_called()
    report.save.assert_not_called()
    award.assert_not_called()


def test_pending_v2_report_keeps_old_box_without_extra_experience(monkeypatch):
    old_drops = {"silver": 100, "mission_chest_jingyanggang": 1}
    report = _report(drops=old_drops)
    salvage = Mock(side_effect=AssertionError("旧 v2 箱已有箱内经验，不可再发"))
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)
    # 当前任务已更新为 v3，仍按战报中已保存的旧奖励辨认版本。
    assert _build(_run(), report) == old_drops
    salvage.assert_not_called()


def test_legacy_scattered_report_halves_new_fruit_and_keeps_saved_fruit_and_equipment(monkeypatch):
    report = _report(drops={"huashan_wulin_chest": 1, "experience_fruit": 3})
    equipment = Mock(return_value={"equip_dakandao": 2})
    monkeypatch.setattr(battle_salvage, "_calculate_equipment_recovery", equipment)
    assert _build(_run(), report) == {
        "huashan_wulin_chest": 1,
        "experience_fruit": 4,
        "equip_dakandao": 2,
    }
    equipment.assert_called_once()


def test_default_salvage_still_recovers_equipment(monkeypatch):
    equipment = Mock(return_value={"equip_dakandao": 2})
    monkeypatch.setattr(battle_salvage, "_calculate_equipment_recovery", equipment)
    assert battle_salvage.calculate_battle_salvage(_report()) == (2, {"equip_dakandao": 2})
    equipment.assert_called_once()


def _create_world(django_user_model, *, is_defense=False, drops=None):
    manor = ensure_manor(django_user_model.objects.create_user(username="mission_direct_experience"))
    fruit = ItemTemplate.objects.create(key="experience_fruit", name="经验果")
    reward = ItemTemplate.objects.create(key="mission_xp_test_reward", name="奖励物品")
    box = ItemTemplate.objects.create(
        key=V3_BOX,
        name="任务宝箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "mission_reward_version": 1,
            "reward_balance_version": 3,
            "item_rewards": [{"item_key": reward.key, "min_quantity": 1, "max_quantity": 1}],
        },
    )
    now = timezone.now()
    report_payload = vars(_report(drops=drops if drops is not None else {V3_BOX: 1})).copy()
    report_payload["winner"] = "defender" if is_defense else "attacker"
    report = BattleReport.objects.create(
        manor=manor, opponent_name="任务敌军", starts_at=now, completed_at=now, **report_payload
    )
    mission = MissionTemplate.objects.create(
        key="direct_experience_test", name="经验回收任务", is_defense=is_defense, drop_table={V3_BOX: 1}
    )
    run = MissionRun.objects.create(manor=manor, mission=mission, battle_report=report, return_at=now)
    return manor, run, fruit, box, reward


@pytest.mark.django_db
@pytest.mark.parametrize("is_defense", [False, True])
def test_real_finalize_grants_experience_once_and_opening_box_never_grants_it_again(
    django_user_model, is_defense, monkeypatch
):
    manor, run, fruit, box, reward = _create_world(
        django_user_model, is_defense=is_defense, drops={} if is_defense else None
    )
    salvage = Mock(wraps=battle_salvage.calculate_battle_salvage)
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)
    assert execution.finalize_mission_run(run) is True
    assert InventoryItem.objects.get(manor=manor, template=fruit).quantity == 1
    assert execution.finalize_mission_run(run) is False
    salvage.assert_called_once()
    assert salvage.call_args.kwargs["include_equipment_recovery"] is False
    run.battle_report.refresh_from_db()
    assert run.battle_report.drops == {box.key: 1, fruit.key: 1}
    assert InventoryItem.objects.get(manor=manor, template=box).quantity == 1

    use_inventory_item(InventoryItem.objects.get(manor=manor, template=box), manor)
    assert InventoryItem.objects.get(manor=manor, template=fruit).quantity == 1
    assert InventoryItem.objects.get(manor=manor, template=reward).quantity == 1


@pytest.mark.django_db
def test_real_finalize_reuses_saved_experience_snapshot(django_user_model, monkeypatch):
    manor, run, fruit, _, _ = _create_world(django_user_model, drops={V3_BOX: 1, "experience_fruit": 7})
    salvage = Mock(side_effect=AssertionError("已有经验快照，不可重算"))
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)
    assert execution.finalize_mission_run(run) is True
    assert execution.finalize_mission_run(run) is False
    assert InventoryItem.objects.get(manor=manor, template=fruit).quantity == 7
    salvage.assert_not_called()


@pytest.mark.django_db
def test_real_pending_v2_box_preserves_experience_until_opening(django_user_model, monkeypatch):
    manor, run, fruit, box, _ = _create_world(django_user_model)
    box.key = "mission_chest_experience_test"
    box.effect_payload["reward_balance_version"] = 2
    box.effect_payload["item_rewards"].append({"item_key": fruit.key, "min_quantity": 20, "max_quantity": 20})
    box.save(update_fields=["key", "effect_payload"])
    run.battle_report.drops = {box.key: 1}
    run.battle_report.save(update_fields=["drops"])
    salvage = Mock(side_effect=AssertionError("历史 v2 奖励不可散发第二份经验"))
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)

    assert execution.finalize_mission_run(run) is True
    assert not InventoryItem.objects.filter(manor=manor, template=fruit).exists()
    assert InventoryItem.objects.get(manor=manor, template=box).quantity == 1
    use_inventory_item(InventoryItem.objects.get(manor=manor, template=box), manor)
    assert InventoryItem.objects.get(manor=manor, template=fruit).quantity == 20
    assert execution.finalize_mission_run(run) is False
    assert InventoryItem.objects.get(manor=manor, template=fruit).quantity == 20
    salvage.assert_not_called()


@pytest.mark.django_db
def test_real_zero_experience_grants_only_box_and_retry_skips_calculation(django_user_model, monkeypatch):
    manor, run, fruit, box, _ = _create_world(django_user_model)
    run.battle_report.losses = {"attacker": {"casualties": []}, "defender": {"casualties": []}}
    run.battle_report.save(update_fields=["losses"])
    salvage = Mock(wraps=battle_salvage.calculate_battle_salvage)
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)
    assert execution.finalize_mission_run(run) is True
    assert execution.finalize_mission_run(run) is False
    assert not InventoryItem.objects.filter(manor=manor, template=fruit).exists()
    assert InventoryItem.objects.get(manor=manor, template=box).quantity == 1
    salvage.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize("is_retreating,winner", [(True, "attacker"), (False, "defender"), (False, "draw")])
def test_real_loss_draw_or_retreat_completes_without_any_reward(django_user_model, is_retreating, winner, monkeypatch):
    manor, run, fruit, box, _ = _create_world(django_user_model)
    run.is_retreating = is_retreating
    run.save(update_fields=["is_retreating"])
    run.battle_report.winner = winner
    run.battle_report.save(update_fields=["winner"])
    salvage = Mock(side_effect=AssertionError("非胜利结算不应计算奖励"))
    monkeypatch.setattr(battle_salvage, "calculate_battle_salvage", salvage)
    assert execution.finalize_mission_run(run) is True
    assert execution.finalize_mission_run(run) is False
    run.refresh_from_db()
    assert run.status == MissionRun.Status.COMPLETED
    assert not InventoryItem.objects.filter(manor=manor, template__in=[fruit, box]).exists()
    salvage.assert_not_called()


@pytest.mark.django_db
def test_failed_finalize_rolls_back_fruit_box_report_and_status_then_retries_once(django_user_model, monkeypatch):
    manor, run, fruit, box, _ = _create_world(django_user_model)
    original_create_message = execution.create_message
    monkeypatch.setattr(execution, "create_message", Mock(side_effect=OperationalError("settlement rollback")))
    with pytest.raises(OperationalError, match="settlement rollback"):
        execution.finalize_mission_run(run)
    run.refresh_from_db()
    run.battle_report.refresh_from_db()
    assert run.status == MissionRun.Status.ACTIVE
    assert run.battle_report.drops == {box.key: 1}
    assert not InventoryItem.objects.filter(manor=manor, template__in=[fruit, box]).exists()

    monkeypatch.setattr(execution, "create_message", original_create_message)
    assert execution.finalize_mission_run(run) is True
    assert execution.finalize_mission_run(run) is False
    assert InventoryItem.objects.get(manor=manor, template=fruit).quantity == 1
    assert InventoryItem.objects.get(manor=manor, template=box).quantity == 1
