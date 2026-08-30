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


def test_device_effect_summary_renders_troop_single_and_all_stat_bonuses():
    mechanical_cat = InventoryItem(
        template=ItemTemplate(
            key="equip_jixiemao_summary",
            name="机械猫",
            effect_type="equip_device",
            effect_payload={"troop_stat_bonus": {"gong": {"hp_pct": 0.01}}},
        )
    )
    mohist_device = InventoryItem(
        template=ItemTemplate(
            key="equip_mojiajiguanren_summary",
            name="墨家机关人",
            effect_type="equip_device",
            effect_payload={
                "troop_stat_bonus": {
                    troop_class: {"attack_pct": 0.005, "defense_pct": 0.005, "hp_pct": 0.005}
                    for troop_class in ("dao", "qiang", "jian", "quan", "gong", "scout")
                }
            },
        )
    )

    assert mechanical_cat.effect_summary == "弓系生命+1%"
    assert mohist_device.effect_summary == "全兵种全部属性+0.5%"
    assert mechanical_cat.template.troop_stat_bonus_summary == "弓系生命+1%"
    assert mechanical_cat.template.equipment_effect_summary == "弓系生命+1%"
    assert mohist_device.template.troop_stat_bonus_summary == "全兵种全部属性+0.5%"
