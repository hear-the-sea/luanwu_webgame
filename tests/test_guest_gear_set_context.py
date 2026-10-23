from types import SimpleNamespace

import pytest

from gameplay.models import ItemTemplate
from guests.models import GearSlot, GearTemplate
from guests.views.roster import _build_gear_set_context


@pytest.mark.django_db
def test_gear_set_context_uses_item_catalog_and_marks_members_by_template_key():
    set_bonus = {
        "pieces": 4,
        "bonus": {"force": 40, "troop_capacity": 100},
    }
    for key, name, effect_type in (
        ("equip_xiaoweitoukui", "校尉头盔", "equip_helmet"),
        ("equip_xiaoweichangjian", "校尉长剑", "equip_weapon"),
    ):
        ItemTemplate.objects.create(
            key=key,
            name=name,
            effect_type=effect_type,
            rarity="blue",
            effect_payload={
                "set_key": "xiaowei_set",
                "set_description": "校尉套装",
                "set_bonus": set_bonus,
            },
        )
    ItemTemplate.objects.create(
        key="xiaowei_blueprint",
        name="校尉图纸",
        effect_type="tool",
        rarity="blue",
        effect_payload={"set_key": "xiaowei_set"},
    )

    GearTemplate.objects.create(
        key="equip_xiaoweitoukie",
        name="校尉头盔",
        slot=GearSlot.HELMET,
        rarity="blue",
        set_key="xiaowei_set",
        set_description="校尉套装",
        set_bonus={"pieces": 4, "bonus": {"troop_capacity": 36}},
    )
    equipped_template = SimpleNamespace(
        key="equip_xiaoweitoukui",
        set_key="xiaowei_set",
    )

    gear_sets, gear_set_map = _build_gear_set_context([SimpleNamespace(template=equipped_template)])

    assert len(gear_sets) == 1
    members = gear_sets[0]["members"]
    assert [(member["key"], member["name"]) for member in members] == [
        ("equip_xiaoweitoukui", "校尉头盔"),
        ("equip_xiaoweichangjian", "校尉长剑"),
    ]
    assert [member["slot"] for member in members] == ["头盔", "武器"]
    assert {member["key"]: member["equipped"] for member in members} == {
        "equip_xiaoweitoukui": True,
        "equip_xiaoweichangjian": False,
    }
    assert gear_set_map["xiaowei_set"]["description"] == "校尉套装"
    assert gear_set_map["xiaowei_set"]["bonus"] == set_bonus


@pytest.mark.django_db
@pytest.mark.parametrize("base_exists", [True, False])
@pytest.mark.parametrize("base_metadata", [True, False])
def test_gear_set_context_collapses_affixes_and_highlights_equipped_variant(base_exists, base_metadata):
    payload = {
        "set_key": "test_set",
        "set_description": "测试套装",
        "set_bonus": {"pieces": 2, "bonus": {"defense": 30}},
    }
    if base_exists:
        ItemTemplate.objects.create(
            key="equip_test_helmet",
            name="套装头盔",
            effect_type="equip_helmet",
            rarity="purple",
            effect_payload=payload,
        )
    for affix in ("agility", "force", "intellect", "defense"):
        ItemTemplate.objects.create(
            key=f"affix_{affix}__equip_test_helmet",
            name=f"{affix}之套装头盔",
            effect_type="equip_helmet",
            rarity="purple",
            effect_payload={
                **payload,
                "equipment_base_key": "equip_test_helmet",
                "equipment_base_name": "套装头盔",
                "equipment_affix": affix,
            },
        )
    ItemTemplate.objects.create(
        key="equip_test_armor",
        name="套装铠甲",
        effect_type="equip_armor",
        rarity="purple",
        effect_payload=payload,
    )
    equipped = SimpleNamespace(
        key="affix_force__equip_test_helmet",
        base_key="equip_test_helmet" if base_metadata else "",
        set_key="test_set",
    )
    gear_sets, set_map = _build_gear_set_context([SimpleNamespace(template=equipped)])
    members = set_map["test_set"]["members"]
    assert len(members) == 2
    assert {member["key"]: (member["name"], member["equipped"]) for member in members} == {
        "equip_test_helmet": ("套装头盔", True),
        "equip_test_armor": ("套装铠甲", False),
    }
    assert gear_sets[0]["bonus"] == payload["set_bonus"]
    from guests.templatetags.guest_extras import gear_tooltip

    tooltip = str(gear_tooltip(equipped, set_map))
    assert tooltip.count('class="set-member ') == 2
    assert 'class="set-member equipped">头盔·套装头盔' in tooltip
    assert "force之" not in tooltip and "agility之" not in tooltip
    if base_exists:
        assert next(member for member in members if member["key"] == "equip_test_helmet")["id"] == (
            ItemTemplate.objects.get(key="equip_test_helmet").pk
        )


@pytest.mark.django_db
def test_gear_set_context_merges_legacy_alias_and_keeps_distinct_same_slot_members():
    payload = {"set_key": "xiaowei_set", "set_description": "校尉套装"}
    for key, name in (
        ("equip_xiaoweitoukie", "校尉头盔"),
        ("equip_xiaoweitoukui", "校尉头盔"),
        ("equip_other_helmet", "另一件头盔"),
    ):
        ItemTemplate.objects.create(key=key, name=name, effect_type="equip_helmet", effect_payload=payload)
    equipped = SimpleNamespace(key="equip_xiaoweitoukie", set_key="xiaowei_set")
    _, set_map = _build_gear_set_context([SimpleNamespace(template=equipped)])
    members = set_map["xiaowei_set"]["members"]
    assert len(members) == 2
    assert {member["key"]: member["equipped"] for member in members} == {
        "equip_xiaoweitoukui": True,
        "equip_other_helmet": False,
    }
