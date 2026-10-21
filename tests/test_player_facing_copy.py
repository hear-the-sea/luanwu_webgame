"""玩家文案迁移须与配置一致，且不改动战斗、奖励与玩家库存。"""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from gameplay.models import InventoryItem, ItemTemplate, MissionRun, MissionTemplate

rules = import_module("gameplay.migrations.0242_player_facing_mission_item_copy")
DATA = Path(__file__).resolve().parents[1] / "data"


def test_player_copy_snapshots_match_current_yaml():
    missions = {
        entry["key"]: entry for entry in yaml.safe_load((DATA / "mission_templates.yaml").read_text())["missions"]
    }
    items = {entry["key"]: entry for entry in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]}
    assert len(rules.MISSION_UPDATES) == 27
    assert len(rules.ITEM_UPDATES) == 20
    for updates, current in ((rules.MISSION_UPDATES, missions), (rules.ITEM_UPDATES, items)):
        for key, fields in updates.items():
            assert set(fields) == {"description"}
            assert fields["description"] == current[key]["description"]
            assert fields["description"].strip()

    # 核对玩家会据以决策的文案，与实际兵力、奖励数量及掉落概率相符。
    assert "两千四百" in missions["wagangzhai_nixi"]["description"]
    assert sum(missions["wagangzhai_nixi"]["enemy_troops"].values()) == 2400
    assert "分桃宝箱" in missions["taozi_fenban"]["description"]
    peach_rewards = items["mission_chest_v3_taozi_fenban"]["effect_payload"]["item_rewards"]
    peach = next(reward for reward in peach_rewards if reward["item_key"] == "experience_peach")
    assert peach["min_quantity"] == peach["max_quantity"] == 15
    assert "河西走廊" not in missions["shanhaiguan"]["description"]
    wuhu_chest = items["mission_chest_v3_zhanlong_wuhu_shizhen"]
    groups = {group["label"]: group for group in wuhu_chest["effect_payload"]["random_item_groups"]}
    assert "1%的机会" in wuhu_chest["description"]
    assert groups["zhaoyun_guest_scroll"]["chance"] == 0.01
    assert "10%的机会" in wuhu_chest["description"]
    assert groups["赵云名器"]["chance"] == 0.1
    assert groups["赵云名器"]["choices"] == [
        {"item_key": "equip_shuduanbaijinpao", "weight": 1},
        {"item_key": "equip_longdanqiang", "weight": 1},
    ]


@pytest.mark.django_db
def test_player_copy_migration_is_idempotent_and_preserves_rules_and_instances(manor_with_user):
    manor, _ = manor_with_user
    missions = []
    for index, key in enumerate(rules.MISSION_UPDATES):
        mission, _ = MissionTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": f"保留任务名称{index}",
                "description": "更新前的任务介绍",
                "difficulty": "intermediate",
                "enemy_guests": [{"key": "preserved_enemy", "label": "保留敌将", "skills": ["preserved_skill"]}],
                "enemy_troops": {"dao_sheng": index + 7},
                "enemy_troops_display": {"dao_sheng": index + 700},
                "enemy_technology": {"level": 6, "guest_level": 81, "levels": {"wolf_hp": 4}},
                "entry_cost": {"preserved_token": index + 1},
                "drop_table": {"preserved_reward": index + 9},
                "probability_drop_table": {"preserved_reward": {"chance": 0.3, "count": 2}},
                "daily_limit": 7,
                "mission_card_daily_limit": 2,
                "base_travel_time": 4321,
                "available_weekdays": [2, 4],
            },
        )
        missions.append(mission)
    item_templates = []
    for index, key in enumerate(rules.ITEM_UPDATES):
        template, _ = ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": f"保留道具名称{index}",
                "description": "更新前的道具介绍",
                "effect_type": "tool",
                "effect_payload": {"action": "preserved_action", "required_items": {"preserved_token": index + 1}},
                "price": index + 321,
                "image": f"items/preserved_{index}.webp",
                "tradeable": True,
                "default_bound": True,
                "is_usable": False,
                "rarity": "purple",
                "storage_space": 47,
            },
        )
        item_templates.append(template)
        for location in InventoryItem.StorageLocation.values:
            InventoryItem.objects.update_or_create(
                manor=manor,
                template=template,
                storage_location=location,
                defaults={"quantity": 19, "bound_quantity": 8},
            )
    MissionTemplate.objects.create(
        key="copy_unrelated_mission", name="其他任务", description="保留其他介绍", drop_table={"silver": 12345}
    )
    unrelated_item = ItemTemplate.objects.create(
        key="copy_unrelated_item", name="其他道具", description="保留其他道具介绍", effect_payload={"hp": 987}
    )
    InventoryItem.objects.create(manor=manor, template=unrelated_item, quantity=23, bound_quantity=11)
    run = MissionRun.objects.create(
        manor=manor,
        mission=missions[0],
        guest_snapshots=[{"id": 123, "current_hp": 456}],
        troop_loadout={"dao_sheng": 67},
        travel_time=456,
    )
    before_missions = list(MissionTemplate.objects.order_by("id").values())
    before_items = list(ItemTemplate.objects.order_by("id").values())
    before_inventory = list(InventoryItem.objects.filter(manor=manor).order_by("id").values())
    before_run = MissionRun.objects.filter(pk=run.pk).values().get()
    expected_missions = deepcopy(before_missions)
    expected_items = deepcopy(before_items)
    for rows, updates in ((expected_missions, rules.MISSION_UPDATES), (expected_items, rules.ITEM_UPDATES)):
        for row in rows:
            if row["key"] in updates:
                row["description"] = updates[row["key"]]["description"]

    for _ in range(2):
        rules.sync_player_facing_copy(apps, SimpleNamespace(connection=connection))
        assert list(MissionTemplate.objects.order_by("id").values()) == expected_missions
        assert list(ItemTemplate.objects.order_by("id").values()) == expected_items
        assert list(InventoryItem.objects.filter(manor=manor).order_by("id").values()) == before_inventory
        assert MissionRun.objects.filter(pk=run.pk).values().get() == before_run


@pytest.mark.django_db
def test_copy_migration_updates_affixed_equipment_without_rebuilding_it(manor_with_user):
    manor, _ = manor_with_user
    equipment = ItemTemplate.objects.create(
        key="copy_affixed_pojunji",
        name="锋锐破军戟",
        description="旧的破军戟介绍",
        effect_type="equip_weapon",
        rarity="orange",
        image="items/preserved_affixed_pojunji.webp",
        price=91234,
        effect_payload={
            "equipment_base_key": "equip_pojunji",
            "affixes": [{"key": "sharp", "value": 37}],
            "force": 654,
            "agility": 98,
        },
    )
    other_equipment = ItemTemplate.objects.create(
        key="copy_other_affixed_weapon",
        name="其他词缀武器",
        description="保留其他基础武器的说明",
        effect_type="equip_weapon",
        effect_payload={"equipment_base_key": "equip_unrelated_base", "force": 678},
    )
    other_tool = ItemTemplate.objects.create(
        key="copy_tool_with_equipment_key",
        name="非装备道具",
        description="保留非装备道具说明",
        effect_type="tool",
        effect_payload={"equipment_base_key": "equip_pojunji", "action": "preserved_action"},
    )
    for template in (equipment, other_equipment, other_tool):
        InventoryItem.objects.create(manor=manor, template=template, quantity=5, bound_quantity=3)
    expected_equipment = ItemTemplate.objects.filter(pk=equipment.pk).values().get()
    expected_equipment["description"] = rules.ITEM_UPDATES["equip_pojunji"]["description"]
    before_other = list(ItemTemplate.objects.filter(pk__in=[other_equipment.pk, other_tool.pk]).order_by("id").values())
    before_inventory = list(InventoryItem.objects.filter(manor=manor).order_by("id").values())

    for _ in range(2):
        rules.sync_player_facing_copy(apps, SimpleNamespace(connection=connection))
        assert ItemTemplate.objects.filter(pk=equipment.pk).values().get() == expected_equipment
        assert (
            list(ItemTemplate.objects.filter(pk__in=[other_equipment.pk, other_tool.pk]).order_by("id").values())
            == before_other
        )
        assert list(InventoryItem.objects.filter(manor=manor).order_by("id").values()) == before_inventory
