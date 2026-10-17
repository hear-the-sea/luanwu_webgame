"""Reduce chest task-card frequency and cap multi-card rewards at one."""

from copy import deepcopy

from django.db import migrations

CARD_KEYS = frozenset({"mission_card", "recruitment_card"})
CHEST_CARD_CHANCES = {
    "work_chest_small": 0.125,
    "work_chest_medium": 1 / 6,
    "work_chest_large": 0.25,
    "mid_autumn_mooncake": 0.0125,
    "mission_chest_kasukabe_daisakusen": 0.02,
    "mission_chest_v3_kasukabe_daisakusen": 0.02,
}


def rebalance_payload(key, original):
    """Replace only the targeted reward, preserving all other chest content."""
    if key not in CHEST_CARD_CHANCES:
        return deepcopy(original)
    payload = deepcopy(original or {})
    entries = payload.get("item_rewards") or []
    groups = payload.get("random_item_groups") or []
    fixed_cards = [entry for entry in entries if entry.get("item_key") == "mission_card"]
    card_groups = [
        group
        for group in groups
        if any(choice.get("item_key") == "mission_card" for choice in group.get("choices", []))
    ]
    if len(fixed_cards) + len(card_groups) != 1:
        raise ValueError(f"unexpected task-card rewards in chest {key}")
    if fixed_cards:
        payload["item_rewards"] = [entry for entry in entries if entry.get("item_key") != "mission_card"]
        payload["random_item_groups"] = [
            {
                "chance": CHEST_CARD_CHANCES[key],
                "min_quantity": 1,
                "max_quantity": 1,
                "choices": [{"item_key": "mission_card", "weight": 1}],
            },
            *groups,
        ]
    else:
        group = card_groups[0]
        if len(group.get("choices", [])) != 1:
            raise ValueError(f"unexpected mixed task-card pool in chest {key}")
        group.update(chance=CHEST_CARD_CHANCES[key], min_quantity=1, max_quantity=1)
    return payload


def sync_chest_card_rewards(apps, schema_editor):
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    database = schema_editor.connection.alias
    for item in (
        ItemTemplate.objects.using(database)
        .select_for_update()
        .filter(
            key__in=CHEST_CARD_CHANCES,
            effect_type="loot_box",
        )
        .order_by("pk")
    ):
        payload = rebalance_payload(item.key, item.effect_payload)
        if payload != item.effect_payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0199_shenyi_poxu_skill_book")]
    operations = [migrations.RunPython(sync_chest_card_rewards, migrations.RunPython.noop)]
