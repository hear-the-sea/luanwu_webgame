from __future__ import annotations

from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.constants import PVPConstants
from gameplay.models import Building, BuildingType, InventoryItem, ItemTemplate, Message, PlayerTechnology, PlayerTroop
from gameplay.services.city_defense_rules import CITY_DEFENSE_MAX_HP
from gameplay.services.raid import scout as scout_service
from gameplay.services.raid.scout_intel import _gather_warehouse_summary
from gameplay.services.scout_intel_rules import SCOUT_INTEL_SCHEMA_VERSION, get_scout_intel_progress
from gameplay.services.scout_reports import build_scout_report_body, split_scout_report_details
from gameplay.services.technology import get_technology_display_data
from guests.models import Guest, GuestRarity, GuestStatus, GuestTemplate, Skill
from tests.raid_scout_refresh.support import build_attacker_defender


@pytest.fixture
def scout_world(django_user_model):
    attacker, defender = build_attacker_defender(
        django_user_model, attacker_username="intel_attacker", defender_username="intel_defender"
    )
    troop_template, _ = TroopTemplate.objects.get_or_create(key="intel_arrow", defaults={"name": "情报箭神"})
    troop = PlayerTroop.objects.create(manor=defender, troop_template=troop_template, count=1500)
    guest_template = GuestTemplate.objects.create(
        key="intel_guest", name="情报门客", rarity=GuestRarity.ORANGE, archetype="military"
    )
    guest = Guest.objects.create(manor=defender, template=guest_template, level=120, current_hp=17)
    return attacker, defender, troop, guest


@pytest.mark.django_db
@pytest.mark.parametrize("level", [0, 49, 50, 99, 100, 149, 150, 199, 200])
def test_scout_intel_unlocks_are_cumulative_and_do_not_collect_locked_fields(scout_world, level):
    _attacker, defender, _troop, guest = scout_world
    collected_at = timezone.now()
    before = (guest.current_hp, guest.status, guest.last_hp_recovery_at)
    with transaction.atomic():
        intel = scout_service._gather_scout_intel(defender, level, now=collected_at)
    assert intel["scouted_at"] == collected_at.isoformat()
    assert intel["scout_level"] == level
    assert ("troops" in intel) == (level >= 50)
    assert ("total_troops" in intel) == (level >= 50)
    assert ("defending_guests" in intel) == (level >= 100)
    assert ("warehouse_summary" in intel) == (level >= 150)
    assert ("silver" in intel) == (level >= 150)
    assert ("warehouse_grain" in intel) == (level >= 150)
    assert ("city_defenses" in intel) == (level >= 200)
    assert ("martial_technologies" in intel) == (level >= 200)
    if level >= 100:
        assert ("attributes" in intel["defending_guests"][0]) == (level >= 200)
        assert ("skills" in intel["defending_guests"][0]) == (level >= 200)
    guest.refresh_from_db()
    assert (guest.current_hp, guest.status, guest.last_hp_recovery_at) == before


@pytest.mark.django_db
def test_scout_lists_actual_defenders_and_keeps_total_guest_summary(scout_world, monkeypatch):
    _attacker, defender, _troop, guest = scout_world
    Guest.objects.create(manor=defender, template=guest.template, level=200, status=GuestStatus.DEPLOYED)
    Guest.objects.create(manor=defender, template=guest.template, level=180, status=GuestStatus.VACATION)
    Guest.objects.create(manor=defender, template=guest.template, level=100, status=GuestStatus.IDLE)
    monkeypatch.setattr(type(defender), "max_squad_size", property(lambda self: 1))
    with transaction.atomic():
        intel = scout_service._gather_scout_intel(defender, 100)
    assert intel["guest_count"] == 4
    assert [row["level"] for row in intel["defending_guests"]] == [120]


def _add_inventory(defender, *, key, quantity, effect_type, rarity="gray", tradeable=True, location="warehouse"):
    template = ItemTemplate.objects.create(
        key=f"intel_{key}", name=f"秘密物品_{key}", effect_type=effect_type, rarity=rarity, tradeable=tradeable
    )
    return InventoryItem.objects.create(manor=defender, template=template, quantity=quantity, storage_location=location)


@pytest.mark.django_db
def test_warehouse_summary_counts_quantities_and_material_types_without_item_details(scout_world):
    _attacker, defender, _troop, _guest = scout_world
    _add_inventory(defender, key="orange", quantity=2, effect_type="equip_weapon", rarity="orange")
    _add_inventory(defender, key="purple", quantity=5, effect_type="equip_armor", rarity="purple")
    _add_inventory(defender, key="blue", quantity=17, effect_type="equip_device", rarity="blue")
    _add_inventory(defender, key="ore", quantity=1000, effect_type="resource")
    _add_inventory(defender, key="wood", quantity=1, effect_type="resource")
    _add_inventory(defender, key="book", quantity=60, effect_type="skill_book")
    _add_inventory(defender, key="protected", quantity=80, effect_type="equip_weapon", tradeable=False)
    _add_inventory(defender, key="treasury", quantity=90, effect_type="equip_weapon", location="treasury")
    _add_inventory(defender, key="empty", quantity=0, effect_type="resource")
    grain = ItemTemplate.objects.get_or_create(key="grain", defaults={"name": "粮食", "effect_type": "resource"})[0]
    grain.tradeable = True
    grain.save(update_fields=["tradeable"])
    InventoryItem.objects.update_or_create(manor=defender, template=grain, defaults={"quantity": 32000})
    summary = _gather_warehouse_summary(defender)
    assert summary["equipment_count"] == 24
    assert summary["equipment_rarities"] == [{"name": "橙", "count": 2}, {"name": "紫", "count": 5}]
    assert summary["equipment_other_count"] == 17
    assert summary["material_type_count"] == 2
    assert summary["other_type_count"] == 1
    with transaction.atomic():
        intel = scout_service._gather_scout_intel(defender, 150)
    body = build_scout_report_body(defender.display_name, intel)
    assert "装备 24 件，材料 2 种，其他物资 1 种" in body
    assert "橙 2 件、紫 5 件、其余 17 件" in body
    assert "仓库粮食 32,000" in body
    assert "秘密物品" not in body
    assert "intel_ore" not in str(intel)


@pytest.mark.django_db
def test_warehouse_summary_respects_virtual_equipment_cap(scout_world, monkeypatch):
    _attacker, defender, _troop, _guest = scout_world
    _add_inventory(defender, key="orange", quantity=2, effect_type="equip_weapon", rarity="orange")
    _add_inventory(defender, key="blue", quantity=3, effect_type="equip_weapon", rarity="blue")
    monkeypatch.setattr("gameplay.services.raid.scout_intel._virtual_defender_loot_rarity_cap", lambda manor: "blue")
    summary = _gather_warehouse_summary(defender)
    assert summary["equipment_count"] == 3
    assert summary["equipment_rarities"] == [{"name": "蓝", "count": 3}]


@pytest.mark.django_db
def test_empty_warehouse_and_empty_defense_have_clear_report_text(scout_world):
    _attacker, defender, troop, guest = scout_world
    troop.count = 0
    troop.save(update_fields=["count"])
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])
    with transaction.atomic():
        intel = scout_service._gather_scout_intel(defender, 200)
    body = build_scout_report_body(defender.display_name, intel)
    assert "暂无护院" in body
    assert "【防守门客】暂无" in body
    assert "【仓库概况】暂无可掠夺物资" in body


@pytest.mark.django_db
def test_defense_details_include_relevant_martial_technologies_and_city_levels_without_repair(scout_world):
    _attacker, defender, troop, _guest = scout_world
    actual_template, _ = TroopTemplate.objects.get_or_create(key="jian_sheng", defaults={"name": "剑圣"})
    troop.troop_template = actual_template
    troop.save(update_fields=["troop_template"])
    PlayerTechnology.objects.create(manor=defender, tech_key="jian_attack", level=7)
    PlayerTechnology.objects.create(manor=defender, tech_key="dao_attack", level=8)
    wall_type, _ = BuildingType.objects.get_or_create(key=next(iter(CITY_DEFENSE_MAX_HP)), defaults={"name": "城墙"})
    wall, _ = Building.objects.update_or_create(
        manor=defender, building_type=wall_type, defaults={"level": 3, "current_hp": 12}
    )
    previous_update = wall.hp_updated_at
    with transaction.atomic():
        intel = scout_service._gather_scout_intel(defender, 200)
    technologies = {row["name"]: row["level"] for row in intel["martial_technologies"]}
    assert technologies["长虹贯日"] == 7
    assert "力劈华山" not in technologies
    assert {"name": wall_type.name, "level": 3} in intel["city_defenses"]
    wall.refresh_from_db()
    assert wall.current_hp == 12
    assert wall.hp_updated_at == previous_update


@pytest.mark.django_db
@pytest.mark.parametrize("dispatch_level,expected_rate", [(1, 0.01), (49, 0.01), (150, 0.95)])
def test_dispatch_level_is_frozen_but_target_data_is_collected_on_arrival(
    scout_world, monkeypatch, dispatch_level, expected_rate
):
    attacker, defender, troop, _guest = scout_world
    technology = PlayerTechnology.objects.create(manor=attacker, tech_key="scout_art", level=dispatch_level)
    PlayerTechnology.objects.create(manor=defender, tech_key="scout_art", level=100)
    scout_template, _ = TroopTemplate.objects.get_or_create(key=PVPConstants.SCOUT_TROOP_KEY, defaults={"name": "探子"})
    PlayerTroop.objects.update_or_create(manor=attacker, troop_template=scout_template, defaults={"count": 1})
    monkeypatch.setattr(scout_service, "can_attack_target", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(scout_service, "check_scout_cooldown", lambda *args, **kwargs: (False, None))
    monkeypatch.setattr(scout_service.scout_followups, "schedule_scout_completion", lambda *args, **kwargs: None)
    monkeypatch.setattr(scout_service.scout_followups, "schedule_scout_return_completion", lambda *args, **kwargs: None)
    monkeypatch.setattr(scout_service, "_roll_scout_success", lambda: 0)
    record = scout_service.start_scout(attacker, defender)
    assert record.success_rate == pytest.approx(expected_rate)
    assert record.intel_data == {"intel_schema_version": SCOUT_INTEL_SCHEMA_VERSION, "scout_level": dispatch_level}
    technology.level = 200
    technology.save(update_fields=["level"])
    troop.count = 2500
    troop.save(update_fields=["count"])
    arrived_at = timezone.now() + timedelta(minutes=1)
    scout_service.finalize_scout(record, now=arrived_at)
    record.refresh_from_db()
    assert record.success_rate == pytest.approx(expected_rate)
    assert record.intel_data["scout_level"] == dispatch_level
    assert record.intel_data["scouted_at"] == arrived_at.isoformat()
    assert "city_defenses" not in record.intel_data
    if dispatch_level >= 50:
        assert record.intel_data["troops"][0]["count"] == 2500
    else:
        assert "troops" not in record.intel_data
    snapshot = dict(record.intel_data)
    monkeypatch.setattr(
        scout_service.scout_followups,
        "schedule_scout_followup",
        lambda action, result, **kwargs: scout_service.scout_followups.run_scout_followup(action, result),
    )
    scout_service.finalize_scout_return(record, now=arrived_at + timedelta(minutes=1))
    record.refresh_from_db()
    assert record.intel_data == snapshot
    message = Message.objects.get(manor=attacker, title=f"侦察报告 - {defender.display_name}")
    assert "防守详情】" not in message.body
    assert PlayerTroop.objects.get(manor=attacker, troop_template=scout_template).count == 1


@pytest.mark.parametrize(
    "level,next_level,unlocked", [(0, 50, 0), (49, 50, 0), (50, 100, 1), (150, 200, 3), (200, None, 4)]
)
def test_technology_unlock_progress(level, next_level, unlocked):
    progress = get_scout_intel_progress(level)
    assert len(progress["unlocked"]) == unlocked
    assert (progress["next_unlock"]["level"] if progress["next_unlock"] else None) == next_level


@pytest.mark.django_db
@pytest.mark.parametrize("level,next_level", [(49, 50), (100, 150), (200, None)])
def test_technology_page_shows_current_and_next_intel_unlock(manor_with_user, level, next_level):
    manor, client = manor_with_user
    PlayerTechnology.objects.create(manor=manor, tech_key="scout_art", level=level)
    entry = next(row for row in get_technology_display_data(manor, "basic") if row["key"] == "scout_art")
    assert len(entry["scout_intel_progress"]["unlocked"]) == level // 50
    response = client.get(reverse("gameplay:technology"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    text = soup.select_one("#tech-scout_art").get_text()
    assert "侦察情报" in text
    if next_level:
        assert f"{next_level}级" in text
        assert "下次解锁" in text
    else:
        assert "庄园底细已全部解锁" in text
        assert "下次解锁" not in text


@pytest.mark.django_db
def test_max_level_report_details_are_collapsed_escaped_and_use_saved_snapshot(scout_world, manor_with_user):
    _attacker, defender, _troop, guest = scout_world
    manor, client = manor_with_user
    guest.custom_name = "<script>门客</script>"
    guest.save(update_fields=["custom_name"])
    skill = Skill.objects.create(key="intel_skill", name="暗箭", description="<img src=x onerror=alert(1)>")
    guest.skills.add(skill)
    with transaction.atomic():
        intel = scout_service._gather_scout_intel(defender, 200)
    body = build_scout_report_body(defender.display_name, intel)
    message = Message.objects.create(manor=manor, kind="system", title="侦察报告 - 青松庄", body=body)
    guest.level = 121
    guest.save(update_fields=["level"])
    response = client.get(reverse("gameplay:view_message", kwargs={"pk": message.pk}))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    details = soup.select("details.msg-scout-details")
    assert [section.summary.get_text() for section in details] == ["门客本事", "护院武艺", "城防情况"]
    assert all(not section.has_attr("open") for section in details)
    assert "120级" in details[0].get_text()
    assert "121级" not in details[0].get_text()
    assert "攻击" in details[0].get_text()
    assert "暗箭" in details[0].get_text()
    assert details[0].find("script") is None
    assert details[0].find("img") is None
    message.refresh_from_db()
    assert message.body == body
    assert message.is_read is False


def test_old_reports_and_non_scout_messages_are_not_reinterpreted():
    intel = {"troop_description": "少量", "guest_count": 1, "avg_guest_level": 10, "asset_level": "一般"}
    body = build_scout_report_body("旧庄园", intel)
    assert "【门客数量】1 人" in body
    assert "下次解锁" not in body
    assert split_scout_report_details(kind="system", title="侦察报告 - 旧庄园", body=body) == {
        "body": body,
        "defense_sections": [],
    }
    detailed_body = build_scout_report_body("新庄园", {"intel_schema_version": 2, "scout_level": 200})
    assert not split_scout_report_details(kind="guild", title="侦察报告 - 新庄园", body=detailed_body)[
        "defense_sections"
    ]
    assert not split_scout_report_details(kind="system", title="普通消息", body=detailed_body)["defense_sections"]
