from __future__ import annotations

from typing import Any

from gameplay.models import ItemTemplate
from guests.models import Guest, GuestTemplate


def _build_guest_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"竞技场模板-{key}",
        archetype="military",
        rarity="green",
    )


def _build_guest(manor: Any, template: GuestTemplate, suffix: str) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=f"竞技{suffix}",
        level=20,
        force=160,
        intellect=110,
        defense_stat=120,
        agility=120,
    )


def _ensure_gladiator_item_templates() -> None:
    key_to_name = {
        "equip_jiaodoushitoukui": "角斗士头盔",
        "equip_jiaodoushixiongjia": "角斗士胸甲",
        "equip_jiaodoushizhixue": "角斗士之靴",
        "equip_jiaodoushizhichui": "角斗士之锤",
    }
    for key, name in key_to_name.items():
        ItemTemplate.objects.get_or_create(
            key=key,
            defaults={
                "name": name,
                "effect_type": ItemTemplate.EffectType.TOOL,
            },
        )
