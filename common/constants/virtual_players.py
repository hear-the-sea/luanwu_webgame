from __future__ import annotations

VIRTUAL_PLAYER_ARCHETYPES = frozenset({"balanced", "rich", "dojo", "guard", "abandoned"})

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
