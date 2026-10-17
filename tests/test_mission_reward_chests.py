from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from core.exceptions import ItemNotConfiguredError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate, MissionTemplate
from gameplay.services.inventory import use as inventory_use
from gameplay.services.manor.core import ensure_manor
from gameplay.services.missions_impl.finalization_helpers import (
    apply_mission_rewards_if_won,
    build_mission_drops_with_salvage,
)
from gameplay.views.mission_helpers import build_chest_reward_preview

DATA = Path(__file__).resolve().parents[1] / "data"
ITEMS = {entry["key"]: entry for entry in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]}
MISSIONS = yaml.safe_load((DATA / "mission_templates.yaml").read_text())["missions"]
BOXES = [ITEMS[key] for mission in MISSIONS for key in mission["drop_table"] if key != "silver"]
HISTORICAL_BOXES = [ITEMS["mission_chest_" + mission["key"]] for mission in MISSIONS]
ALL_BOXES = BOXES + HISTORICAL_BOXES


def _reward_keys(box):
    payload = box["effect_payload"]
    return (
        {x["item_key"] for x in payload["item_rewards"]}
        | {choice["item_key"] for group in payload["random_item_groups"] for choice in group["choices"]}
        | {
            reward["item_key"]
            for entries in payload.get("blueprint_material_support", {}).values()
            for reward in entries
        }
    )


def test_all_personal_missions_award_one_named_chest_without_scattered_items():
    assert len(MISSIONS) == len(BOXES) == 32
    assert len(HISTORICAL_BOXES) == 32
    assert len({box["key"] for box in ALL_BOXES}) == 64
    assert len({box["effect_payload"]["mission_key"] for box in BOXES}) == 32
    for mission in MISSIONS:
        drops = mission["drop_table"]
        box_keys = set(drops) - {"silver"}
        assert len(box_keys) == 1
        box_key = box_keys.pop()
        box = ITEMS[box_key]
        assert drops[box_key] == 1
        assert box["effect_payload"]["mission_key"] == mission["key"]
        assert box["effect_payload"]["mission_reward_version"] == 1
        assert box["effect_payload"]["reward_balance_version"] == 3
        assert box["effect_payload"]["experience_fruit_source"] == "battle_salvage"
        assert box_key == "mission_chest_v3_" + mission["key"]
        assert "experience_fruit" not in _reward_keys(box)
        assert box["name"].endswith("宝箱")
        assert box["effect_type"] == "loot_box" and box["is_usable"]
        assert box["tradeable"] is False and box["storage_space"] == 5
        assert not mission.get("probability_drop_table")
        assert box["image"] == "mission_chest_v1.png"
        assert (DATA / "images/items" / box["image"]).is_file()


def test_chest_rewards_are_complete_and_devices_have_craftable_blueprints_only():
    recipes = {x["blueprint_key"]: x for x in yaml.safe_load((DATA / "forge_blueprints.yaml").read_text())["recipes"]}
    for box in ALL_BOXES:
        payload = box["effect_payload"]
        assert not payload.get("resources") and "silver_min" not in payload
        assert not payload.get("gear_choices")
        assert any(x["min_quantity"] >= 1 for x in payload["item_rewards"])
        for key in _reward_keys(box):
            assert key in ITEMS
            assert ITEMS[key]["effect_type"] != "equip_device"
            if key.startswith("blueprint_"):
                assert key in recipes
                assert recipes[key]["result_item_key"] in ITEMS
            if ITEMS[key]["effect_type"] == "loot_box":
                # 月饼仍可作为兑换材料；普通装备/技能箱必须展开，避免二次开箱。
                assert key == "mid_autumn_mooncake"
        for group in payload["random_item_groups"]:
            assert 0 < group["chance"] <= 1
            assert 1 <= group["min_quantity"] <= group["max_quantity"]
            assert all(choice["weight"] > 0 for choice in group["choices"])


def test_historical_boxes_keep_experience_fruit_and_the_same_remaining_reward_pools():
    for box, historical in zip(BOXES, HISTORICAL_BOXES):
        payload = box["effect_payload"]
        old_payload = historical["effect_payload"]
        assert payload["mission_key"] == old_payload["mission_key"]
        assert "experience_fruit_source" not in old_payload
        fruit_rewards = [row for row in old_payload["item_rewards"] if row["item_key"] == "experience_fruit"]
        assert len(fruit_rewards) == 1
        assert 1 <= fruit_rewards[0]["min_quantity"] == fruit_rewards[0]["max_quantity"]
        assert payload["item_rewards"] == [
            row for row in old_payload["item_rewards"] if row["item_key"] != "experience_fruit"
        ]
        for field in ("random_item_groups", "blueprint_material_support", "blueprint_forging_requirements"):
            assert payload[field] == old_payload[field]
        assert _reward_keys(historical) - _reward_keys(box) == {"experience_fruit"}


@pytest.mark.parametrize("box", ALL_BOXES, ids=lambda box: box["key"])
@pytest.mark.django_db
def test_each_real_chest_opens_atomically_and_cannot_be_used_twice(box, django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username=box["key"][:100]))
    for key in _reward_keys(box) | {box["key"]}:
        entry = ITEMS[key]
        ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                field: entry[field]
                for field in (
                    "name",
                    "effect_type",
                    "effect_payload",
                    "rarity",
                    "tradeable",
                    "price",
                    "storage_space",
                    "is_usable",
                )
                if field in entry
            },
        )
    item = InventoryItem.objects.create(manor=manor, template=ItemTemplate.objects.get(key=box["key"]), quantity=1)
    initial_fruit = sum(
        InventoryItem.objects.filter(manor=manor, template__key="experience_fruit").values_list("quantity", flat=True)
    )
    # 所有概率组都命中，覆盖普通物品、技能、图纸和特殊道具的真实发奖分支。
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.0)
    monkeypatch.setattr(inventory_use.inventory_random, "randint", lambda minimum, maximum: minimum)
    result = inventory_use.use_inventory_item(item, manor)
    assert result["rewards"]
    assert not result.get("skipped_bonus_items")
    granted_rows = list(InventoryItem.objects.filter(manor=manor).select_related("template"))
    for reward in box["effect_payload"]["item_rewards"]:
        assert (
            sum(
                row.quantity
                for row in granted_rows
                if row.template.key == reward["item_key"]
                or row.template.effect_payload.get("equipment_base_key") == reward["item_key"]
            )
            >= reward["min_quantity"]
        )
    expected_fruit = sum(
        reward["min_quantity"]
        for reward in box["effect_payload"]["item_rewards"]
        if reward["item_key"] == "experience_fruit"
    )
    assert (
        sum(
            InventoryItem.objects.filter(manor=manor, template__key="experience_fruit").values_list(
                "quantity", flat=True
            )
        )
        - initial_fruit
        == expected_fruit
    )
    assert not InventoryItem.objects.filter(manor=manor, quantity__gt=0, template__effect_type="equip_device").exists()
    quantities = dict(InventoryItem.objects.filter(manor=manor).values_list("template__key", "quantity"))
    with pytest.raises(ItemNotFoundError):
        inventory_use.use_inventory_item(item, manor)
    assert dict(InventoryItem.objects.filter(manor=manor).values_list("template__key", "quantity")) == quantities


@pytest.mark.parametrize(
    "box_key,target",
    [
        ("mission_chest_v3_jiguanshou_chuxian", "blueprint_jixiemao"),
        ("mission_chest_v3_jiguan_chuniao", "blueprint_jiguanchuniao"),
        ("mission_chest_v3_huashan_lunjian_advanced", "blueprint_taotieding"),
        ("mission_chest_jiguanshou_chuxian", "blueprint_jixiemao"),
        ("mission_chest_jiguan_chuniao", "blueprint_jiguanchuniao"),
        ("mission_chest_huashan_lunjian_advanced", "blueprint_taotieding"),
    ],
)
@pytest.mark.django_db
def test_device_blueprint_is_actually_granted_by_generic_chest_branch(box_key, target, django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="device_" + target))
    box = ITEMS[box_key]
    for key in _reward_keys(box) | {box_key}:
        entry = ITEMS[key]
        ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": entry["name"],
                "effect_type": entry["effect_type"],
                "effect_payload": entry.get("effect_payload", {}),
                "is_usable": entry.get("is_usable", False),
            },
        )
    item = InventoryItem.objects.create(manor=manor, template=ItemTemplate.objects.get(key=box_key), quantity=1)
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.0)
    monkeypatch.setattr(
        inventory_use, "weighted_random_choice", lambda keys, weights, rng: target if target in keys else keys[0]
    )
    inventory_use.use_inventory_item(item, manor)
    assert InventoryItem.objects.get(manor=manor, template__key=target).quantity >= 1


@pytest.mark.django_db
def test_missing_rare_candidate_keeps_chest_and_rolls_back_every_reward(django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="incomplete_mission_chest"))
    fruit = ItemTemplate.objects.create(key="chest_existing_reward", name="已同步奖励", effect_type="tool")
    box = ItemTemplate.objects.create(
        key="mission_chest_incomplete",
        name="未同步宝箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "mission_reward_version": 1,
            "item_rewards": [{"item_key": fruit.key, "min_quantity": 3}],
            "random_item_groups": [{"chance": 0.01, "choices": [{"item_key": "missing_rare_candidate", "weight": 1}]}],
        },
    )
    item = InventoryItem.objects.create(manor=manor, template=box, quantity=1)
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.99)
    with pytest.raises(ItemNotConfiguredError, match="宝箱已保留"):
        inventory_use.use_inventory_item(item, manor)
    item.refresh_from_db()
    assert item.quantity == 1
    assert not InventoryItem.objects.filter(manor=manor, template=fruit).exists()


@pytest.mark.parametrize("is_defense", [False, True])
def test_new_chest_settlement_does_not_scatter_salvage(is_defense, monkeypatch):
    drops = {"silver": 7000, "mission_chest_jingyanggang": 1}
    report = SimpleNamespace(drops={} if is_defense else drops)
    run = SimpleNamespace(mission=SimpleNamespace(is_defense=is_defense, drop_table=drops))
    salvage = Mock(side_effect=AssertionError("历史v2任务不应额外散发回收道具"))
    monkeypatch.setattr("gameplay.services.battle_salvage.calculate_battle_salvage", salvage)
    assert (
        build_mission_drops_with_salvage(
            run,
            report,
            "defender" if is_defense else "attacker",
            logger=logging.getLogger(__name__),
            resolve_defense_drops_if_missing=lambda *_args: drops,
        )
        == drops
    )
    salvage.assert_not_called()


@pytest.mark.parametrize("old_item", ["equip_hupixue", "huashan_wulin_chest"])
def test_pending_legacy_report_keeps_original_rewards_and_salvage(monkeypatch, old_item):
    run = SimpleNamespace(mission=SimpleNamespace(is_defense=False, drop_table={"mission_chest_v3_jingyanggang": 1}))
    report = SimpleNamespace(drops={"silver": 7000, old_item: 1})
    monkeypatch.setattr(
        "gameplay.services.battle_salvage.calculate_battle_salvage", lambda *a, **kw: (7, {"equip_tiejian": 2})
    )
    assert build_mission_drops_with_salvage(
        run, report, "attacker", logger=logging.getLogger(__name__), resolve_defense_drops_if_missing=lambda *_args: {}
    ) == {"silver": 7000, old_item: 1, "experience_fruit": 7, "equip_tiejian": 2}


@pytest.mark.parametrize("is_retreating,winner", [(True, "attacker"), (False, "defender")])
def test_lost_or_retreating_mission_does_not_award_a_chest(is_retreating, winner):
    grant = Mock()
    apply_mission_rewards_if_won(
        SimpleNamespace(is_retreating=is_retreating),
        SimpleNamespace(winner=winner),
        "attacker",
        logger=logging.getLogger(__name__),
        resolve_defense_drops_if_missing=Mock(),
        award_mission_drops_locked=grant,
    )
    grant.assert_not_called()


def test_all_current_and_legacy_task_chests_display_task_chest_category():
    legacy_keys = {
        "mid_autumn_mooncake",
        "passive_skill_book_box",
        "civil_skill_book_box",
        "saiwai_equipment_box",
        "fengshen_equipment_box",
        "huashan_wulin_chest",
    }
    boxes = [entry for key, entry in ITEMS.items() if key.startswith("mission_chest_") or key in legacy_keys]
    assert len(boxes) == 70
    for entry in boxes:
        template = ItemTemplate(key=entry["key"], effect_type=entry["effect_type"])
        assert InventoryItem(template=template).category_display == "任务宝箱"
    for key in ["work_chest_small", "work_chest_medium", "work_chest_large"]:
        template = ItemTemplate(key=key, effect_type=ITEMS[key]["effect_type"])
        assert InventoryItem(template=template).category_display == "道具"


def test_chest_preview_sorts_across_groups_by_actual_probability_without_showing_it():
    box = SimpleNamespace(
        name="奖励箱",
        effect_payload={
            "item_rewards": [{"item_key": "token", "min_quantity": 1, "max_quantity": 1}],
            "random_item_groups": [
                {
                    "chance": 0.35,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "mount", "weight": 2}, {"item_key": "token", "weight": 5}],
                },
                {
                    "chance": 0.60,
                    "min_quantity": 2,
                    "max_quantity": 4,
                    "choices": [{"item_key": "book", "weight": 1}, {"item_key": "peach", "weight": 9}],
                },
                {
                    "chance": 0.20,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "iron", "weight": 1}, {"item_key": "copper", "weight": 1}],
                },
            ],
        },
    )
    preview = build_chest_reward_preview(
        box,
        {
            "token": SimpleNamespace(name="战报"),
            "mount": SimpleNamespace(name="的卢"),
            "book": SimpleNamespace(name="技能书"),
            "peach": SimpleNamespace(name="经验桃"),
            "iron": SimpleNamespace(name="铁"),
            "copper": SimpleNamespace(name="铜"),
        },
    )
    assert preview["guaranteed"] == ["战报 ×1"]
    assert preview["possible"] == ["经验桃 ×2—4", "战报 ×1", "的卢 ×1", "铁 ×1", "铜 ×1", "技能书 ×2—4"]
    assert "groups" not in preview
    assert "%" not in str(preview)


@pytest.mark.django_db
def test_task_page_shows_named_box_and_opening_instructions(client, django_user_model):
    from django.urls import reverse

    user = django_user_model.objects.create_user(username="mission_chest_preview_page")
    ensure_manor(user)
    client.force_login(user)
    box = ITEMS["mission_chest_v3_jingyanggang"]
    for key in _reward_keys(box) | {box["key"]}:
        entry = ITEMS[key]
        ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": entry["name"],
                "description": entry.get("description", ""),
                "effect_type": entry["effect_type"],
                "effect_payload": entry.get("effect_payload", {}),
            },
        )
    mission = MissionTemplate.objects.create(
        key="chest_page_jingyanggang", name="景阳冈", drop_table={"silver": 7000, box["key"]: 1}
    )
    response = client.get(reverse("gameplay:tasks"), {"mission": mission.key})
    assert response.status_code == 200
    body = response.content.decode()
    assert "景阳冈宝箱：奖励一览" in body
    assert "虎皮靴 ×1" in body and "虎纹盔 ×1" in body
    chest_preview_html = body.split("景阳冈宝箱：奖励一览", 1)[1].split("</details>", 1)[0]
    assert "%" not in chest_preview_html
    assert "可能获得：" in body
    assert "仓库点击使用后开启" not in body
    assert "经验果按本场战斗回收量" not in body
    assert "以下各组独立抽取" not in body
    assert "图纸配套材料" not in body
    assert "奖励组（命中" not in body
    assert not response.context["selected_probability_drop_items"]


@pytest.mark.django_db
def test_opening_chest_keeps_existing_warehouse_overflow_policy(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="mission_chest_full_warehouse"))
    reward = ItemTemplate.objects.create(key="huge_chest_reward", name="大件奖励", storage_space=10**9)
    box = ItemTemplate.objects.create(
        key="mission_chest_full_warehouse",
        name="仓满测试箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={"mission_reward_version": 1, "item_rewards": [{"item_key": reward.key, "min_quantity": 2}]},
    )
    item = InventoryItem.objects.create(manor=manor, template=box, quantity=1)
    inventory_use.use_inventory_item(item, manor)
    assert InventoryItem.objects.get(manor=manor, template=reward).quantity == 2


@pytest.mark.django_db
@pytest.mark.parametrize("source_is_bound", [False, True])
def test_grant_failure_rolls_back_partial_rewards_and_keeps_whole_chest(
    django_user_model, monkeypatch, source_is_bound
):
    manor = ensure_manor(django_user_model.objects.create_user(username="mission_chest_atomic_failure"))
    first = ItemTemplate.objects.create(key="atomic_chest_first", name="第一项奖励")
    second = ItemTemplate.objects.create(key="atomic_chest_second", name="第二项奖励")
    box = ItemTemplate.objects.create(
        key="mission_chest_atomic_failure",
        name="回滚测试箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "mission_reward_version": 1,
            "item_rewards": [{"item_key": first.key, "min_quantity": 2}, {"item_key": second.key, "min_quantity": 1}],
        },
    )
    item = InventoryItem.objects.create(manor=manor, template=box, quantity=1, bound_quantity=int(source_is_bound))
    original = inventory_use.add_items_to_inventory_locked
    grant_calls = []

    def fail_second(owner, items, *, templates=None, is_bound=None):
        key, quantity = next(iter(items.items()))
        grant_calls.append((key, quantity, is_bound))
        if key == second.key:
            raise RuntimeError("模拟发奖中途失败")
        original(owner, items, templates=templates, is_bound=is_bound)
        granted = InventoryItem.objects.get(manor=owner, template__key=key)
        assert granted.quantity == 2
        assert granted.bound_quantity == (2 if source_is_bound else 0)
        return granted

    monkeypatch.setattr(inventory_use, "add_items_to_inventory_locked", fail_second)
    with pytest.raises(RuntimeError, match="模拟发奖中途失败"):
        inventory_use.use_inventory_item(item, manor)
    item.refresh_from_db()
    assert item.quantity == 1
    assert item.bound_quantity == int(source_is_bound)
    assert grant_calls == [(first.key, 2, source_is_bound), (second.key, 1, source_is_bound)]
    assert not InventoryItem.objects.filter(manor=manor, template__in=[first, second]).exists()
