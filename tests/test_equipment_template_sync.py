from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.management import call_command

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.equipment_template_sync import synchronize_equipment_templates
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestTemplate
from guests.services.equipment_stats import apply_set_bonuses, apply_template_stats_to_guest
from guests.utils.equipment_utils import compute_set_bonus


def _create_guest(django_user_model, suffix: str) -> tuple[object, Guest]:
    user = django_user_model.objects.create_user(username=f"equipment_sync_{suffix}", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key=f"equipment_sync_guest_{suffix}",
        name="装备同步测试门客",
        archetype="military",
        rarity="blue",
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=75,
        base_luck=50,
        base_hp=1000,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        force=100,
        intellect=80,
        defense_stat=90,
        agility=75,
        luck=50,
    )
    return manor, guest


@pytest.mark.django_db
def test_sync_merges_legacy_head_and_reconciles_equipped_stats(django_user_model):
    manor, guest = _create_guest(django_user_model, "legacy")
    old_bonus = {"pieces": 2, "bonus": {"force": 40, "defense": 16, "troop_capacity": 36}}
    new_bonus = {"pieces": 2, "bonus": {"force": 40, "defense": 15, "troop_capacity": 100}}

    old_item = ItemTemplate.objects.create(
        key="equip_xiaoweitoukie",
        name="校尉头盔",
        effect_type="equip_helmet",
        rarity="blue",
        effect_payload={
            "hp": 330,
            "defense": 19,
            "force": 4,
            "set_key": "xiaowei_set",
            "set_description": "校尉套装",
            "set_bonus": old_bonus,
        },
    )
    new_item = ItemTemplate.objects.create(
        key="equip_xiaoweitoukui",
        name="校尉头盔",
        effect_type="equip_helmet",
        rarity="blue",
        effect_payload={
            "hp": 350,
            "defense": 20,
            "force": 15,
            "troop_capacity": 20,
            "set_key": "xiaowei_set",
            "set_description": "校尉套装",
            "set_bonus": new_bonus,
        },
    )
    armor_item = ItemTemplate.objects.create(
        key="equipment_sync_xiaowei_armor",
        name="校尉测试铠甲",
        effect_type="equip_armor",
        rarity="blue",
        effect_payload={
            "hp": 440,
            "defense": 25,
            "force": 10,
            "troop_capacity": 30,
            "set_key": "xiaowei_set",
            "set_description": "校尉套装",
            "set_bonus": new_bonus,
        },
    )

    old_head_template = GearTemplate.objects.create(
        key=old_item.key,
        name=old_item.name,
        slot=GearSlot.HELMET,
        rarity="blue",
        set_key="xiaowei_set",
        set_description="校尉套装",
        set_bonus=old_bonus,
        extra_stats={"hp": 330, "defense": 19, "force": 4},
    )
    new_head_template = GearTemplate.objects.create(
        key=new_item.key,
        name=new_item.name,
        slot=GearSlot.HELMET,
        rarity="blue",
        set_key="xiaowei_set",
        set_description="校尉套装",
        set_bonus=old_bonus,
        extra_stats={"hp": 330, "defense": 19, "force": 4},
    )
    armor_template = GearTemplate.objects.create(
        key=armor_item.key,
        name=armor_item.name,
        slot=GearSlot.ARMOR,
        rarity="blue",
        set_key="xiaowei_set",
        set_description="校尉套装",
        set_bonus=old_bonus,
        extra_stats={"hp": 400, "defense": 20, "force": 8},
    )
    head_gear = GearItem.objects.create(manor=manor, template=old_head_template, guest=guest)
    GearItem.objects.create(manor=manor, template=armor_template, guest=guest)

    updates: set[str] = set()
    apply_template_stats_to_guest(guest, old_head_template, +1, updates)
    apply_template_stats_to_guest(guest, armor_template, +1, updates)
    guest.save(update_fields=sorted(updates))
    apply_set_bonuses(guest)

    InventoryItem.objects.create(manor=manor, template=old_item, quantity=2)
    InventoryItem.objects.create(manor=manor, template=new_item, quantity=3)

    report = synchronize_equipment_templates([new_item.key, armor_item.key])

    guest.refresh_from_db()
    head_gear.refresh_from_db()
    new_head_template.refresh_from_db()
    armor_template.refresh_from_db()

    assert not ItemTemplate.objects.filter(key=old_item.key).exists()
    assert not GearTemplate.objects.filter(key=old_head_template.key).exists()
    assert head_gear.template_id == new_head_template.id
    assert new_head_template.extra_stats == {
        "hp": 350,
        "defense": 20,
        "force": 15,
        "troop_capacity": 20,
    }
    assert new_head_template.set_bonus == new_bonus
    assert armor_template.extra_stats == {
        "hp": 440,
        "defense": 25,
        "force": 10,
        "troop_capacity": 30,
    }
    assert armor_template.set_bonus == new_bonus
    assert guest.force == 165
    assert guest.defense_stat == 135
    assert guest.defense_bonus == 15
    assert guest.hp_bonus == 790
    assert guest.troop_capacity_bonus == 150
    assert guest.gear_set_bonus == new_bonus["bonus"]
    assert InventoryItem.objects.get(manor=manor, template=new_item).quantity == 5
    assert report.gear_templates_updated == 2
    assert report.gear_items_reassigned == 1
    assert report.guests_reconciled == 1
    assert report.item_aliases_merged == 1
    assert report.inventory_rows_rekeyed == 1


@pytest.mark.django_db
def test_sync_dry_run_rolls_back_alias_and_template_changes(django_user_model):
    manor, _guest = _create_guest(django_user_model, "dry_run")
    old_item = ItemTemplate.objects.create(
        key="equip_xiaoweitoukie",
        name="旧校尉头盔",
        effect_type="equip_helmet",
        rarity="blue",
        effect_payload={},
    )
    new_item = ItemTemplate.objects.create(
        key="equip_xiaoweitoukui",
        name="校尉头盔",
        effect_type="equip_helmet",
        rarity="blue",
        effect_payload={"hp": 350},
    )
    old_template = GearTemplate.objects.create(
        key=old_item.key,
        name=old_item.name,
        slot=GearSlot.HELMET,
        rarity="blue",
        extra_stats={"hp": 100},
    )
    GearItem.objects.create(manor=manor, template=old_template)

    report = synchronize_equipment_templates([new_item.key], dry_run=True)

    assert report.item_aliases_merged == 1
    assert report.gear_templates_created == 1
    assert report.gear_items_reassigned == 1
    assert ItemTemplate.objects.filter(key=old_item.key).exists()
    assert GearTemplate.objects.filter(key=old_template.key).exists()
    assert not GearTemplate.objects.filter(key=new_item.key).exists()


@pytest.mark.django_db
def test_sync_materializes_missing_canonical_gear_template():
    item = ItemTemplate.objects.create(
        key="equipment_sync_missing_sword",
        name="缺失长剑",
        effect_type="equip_weapon",
        rarity="blue",
        effect_payload={
            "force": 18,
            "troop_capacity": 25,
            "set_key": "equipment_sync_set",
            "set_description": "同步测试套装",
            "set_bonus": {
                "pieces": 2,
                "bonus": {"force": 20, "troop_capacity": 40},
            },
        },
    )

    report = synchronize_equipment_templates([item.key])

    template = GearTemplate.objects.get(key=item.key)
    assert report.gear_templates_created == 1
    assert template.name == item.name
    assert template.slot == GearSlot.WEAPON
    assert template.extra_stats == {"force": 18, "troop_capacity": 25}
    assert template.set_key == "equipment_sync_set"
    assert template.set_description == "同步测试套装"
    assert template.set_bonus == {
        "pieces": 2,
        "bonus": {"force": 20, "troop_capacity": 40},
    }


@pytest.mark.django_db
def test_repair_command_can_limit_sync_to_set_members(monkeypatch):
    set_item = ItemTemplate.objects.create(
        key="equipment_sync_scoped_set_item",
        name="范围测试套装装备",
        effect_type="equip_helmet",
        rarity="blue",
        effect_payload={"set_key": "equipment_sync_scoped_set"},
    )
    plain_item = ItemTemplate.objects.create(
        key="equipment_sync_scoped_plain_item",
        name="范围测试普通装备",
        effect_type="equip_weapon",
        rarity="blue",
        effect_payload={"force": 5},
    )
    captured: list[list[str]] = []
    empty_report = SimpleNamespace(
        gear_templates_created=0,
        gear_templates_updated=0,
        gear_items_reassigned=0,
        guests_reconciled=0,
        item_aliases_merged=0,
        inventory_rows_rekeyed=0,
        related_rows_rekeyed=0,
    )
    monkeypatch.setattr(
        "gameplay.management.commands.repair_equipment_templates.synchronize_equipment_templates",
        lambda keys, dry_run=False: captured.append(list(keys)) or empty_report,
    )

    call_command("repair_equipment_templates", "--sets-only")

    assert set_item.key in captured[0]
    assert plain_item.key not in captured[0]


def test_compute_set_bonus_rejects_inconsistent_member_definitions():
    first = SimpleNamespace(
        template=SimpleNamespace(
            set_key="broken_set",
            set_bonus={"pieces": 2, "bonus": {"force": 10}},
        )
    )
    second = SimpleNamespace(
        template=SimpleNamespace(
            set_key="broken_set",
            set_bonus={"pieces": 2, "bonus": {"force": 20}},
        )
    )

    with pytest.raises(AssertionError, match="inconsistent equipment set bonus definition"):
        compute_set_bonus([first, second])
