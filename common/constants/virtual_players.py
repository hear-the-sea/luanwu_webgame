from __future__ import annotations

VIRTUAL_PLAYER_ARCHETYPES = frozenset({"balanced", "rich", "dojo", "guard", "abandoned"})

# Scouts belong to the human-controlled reconnaissance flow.  V2 virtual
# players use ordinary combat troops as their permanent guard force.
VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS = frozenset({"scout"})

VIRTUAL_PLAYER_BUILDING_TARGET_KEYS = (
    "arrow_tower",
    "bathhouse",
    "citang",
    "farm",
    "forge",
    "granary",
    "jiadingfang",
    "jail",
    "juxianzhuang",
    "latrine",
    "lianggongchang",
    "oath_grove",
    "ranch",
    "silver_vault",
    "smithy",
    "stable",
    "tavern",
    "tax_office",
    "treasury",
    "wall",
    "youxibaota",
)

VIRTUAL_PLAYER_TECHNOLOGY_TARGET_KEYS = (
    "animal_husbandry",
    "architecture",
    "dao_agility",
    "dao_attack",
    "dao_defense",
    "dao_double_strike",
    "dao_hp",
    "dao_recruit",
    "farming",
    "forging",
    "gong_agility",
    "gong_attack",
    "gong_defense",
    "gong_hp",
    "gong_melee",
    "gong_range",
    "gong_recruit",
    "horsemanship",
    "jian_agility",
    "jian_attack",
    "jian_defense",
    "jian_hp",
    "jian_preempt",
    "jian_recruit",
    "jian_reflect",
    "march_art",
    "qiang_agility",
    "qiang_attack",
    "qiang_counter",
    "qiang_defense",
    "qiang_hp",
    "qiang_recruit",
    "qiang_siege",
    "quan_agility",
    "quan_attack",
    "quan_defense",
    "quan_heal",
    "quan_hp",
    "quan_recruit",
    "quan_vs_ranged",
    "scout_art",
    "smelting",
)

VIRTUAL_PLAYER_INVENTORY_EFFECT_TYPES = frozenset(
    {
        "resource_pack",
        "resource",
        "skill_book",
        "experience_items",
        "medicine",
        "tool",
        "loot_box",
        "equip_helmet",
        "equip_armor",
        "equip_shoes",
        "equip_weapon",
        "equip_mount",
        "equip_ornament",
        "equip_device",
    }
)

VIRTUAL_PLAYER_MANAGED_STOCK_EFFECT_TYPES = frozenset(
    {"resource_pack", "resource", "experience_items", "medicine", "loot_box"}
)

DEFAULT_VIRTUAL_PLAYER_PRESTIGE_BANDS = frozenset({"newbie", "junior", "middle", "senior", "veteran"})
