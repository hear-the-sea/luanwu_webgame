"""Add Shenyi Poxu to Fengbang Tianmen and the guild master skill-book box."""

from copy import deepcopy

from django.db import migrations

BOOK_KEY = "book_shenyi_poxu"
MISSION_CHESTS = ("mission_chest_fengbang_tianmen", "mission_chest_v3_fengbang_tianmen")
GUILD_CHEST = "guild_skill_book_box_master"
MISSION_REWARD = {
    "label": BOOK_KEY,
    "chance": 0.048,
    "min_quantity": 1,
    "max_quantity": 1,
    "choices": [{"item_key": BOOK_KEY, "weight": 1}],
}
GUILD_REWARD = {"item_key": BOOK_KEY, "weight": 22}


def add_reward_source(key, original):
    payload = deepcopy(original)
    if key in MISSION_CHESTS:
        groups = payload.setdefault("random_item_groups", [])
        if not any(choice.get("item_key") == BOOK_KEY for group in groups for choice in group.get("choices", [])):
            groups.append(deepcopy(MISSION_REWARD))
    elif key == GUILD_CHEST:
        choices = payload.setdefault("skill_book_choices", [])
        if not any(choice.get("item_key") == BOOK_KEY for choice in choices):
            choices.append(deepcopy(GUILD_REWARD))
    return payload


def add_shenyi_reward_sources(apps, schema_editor):
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    database = schema_editor.connection.alias
    for item in (
        ItemTemplate.objects.using(database)
        .select_for_update()
        .filter(key__in=(*MISSION_CHESTS, GUILD_CHEST), effect_type="loot_box")
        .order_by("pk")
    ):
        payload = add_reward_source(item.key, item.effect_payload)
        if payload != item.effect_payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0200_reduce_chest_card_rewards")]
    operations = [migrations.RunPython(add_shenyi_reward_sources, migrations.RunPython.noop)]
