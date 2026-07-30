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
