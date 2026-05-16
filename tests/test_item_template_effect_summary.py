from gameplay.models import InventoryItem, ItemTemplate


def test_equipment_effect_summary_renders_multi_tier_set_bonus():
    template = ItemTemplate(
        key="equip_multi_tier_summary",
        name="多档套装测试装备",
        effect_type="equip_helmet",
        effect_payload={
            "hp": 260,
            "set_key": "multi_tier_summary_set",
            "set_description": "多档测试套装",
            "set_bonus": [
                {"pieces": 2, "bonus": {"attack": 40, "defense": 30}},
                {"pieces": 4, "bonus": {"hp": 600, "agility": 25, "luck": 20}},
            ],
        },
    )

    item = InventoryItem(template=template)

    assert item.effect_summary == (
        "生命+260；多档测试套装（2件）：攻击+40、防御+30；" "多档测试套装（4件）：生命+600、敏捷+25、运势+20"
    )
