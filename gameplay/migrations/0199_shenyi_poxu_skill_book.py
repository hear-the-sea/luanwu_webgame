"""Add the balanced force/intellect skill and its book without distribution changes."""

from copy import deepcopy

from django.db import migrations

SKILL_KEY = "shenyi_poxu"
BOOK_KEY = "book_shenyi_poxu"
SKILL_DEFAULTS = {
    "name": "神意破虚",
    "rarity": "purple",
    "description": "神意洞察虚实，内劲直贯破绽。武力与智力相济时，方能尽展此招威力。",
    "base_power": 100,
    "base_probability": 0.75,
    "kind": "active",
    "status_effect": "",
    "status_probability": 0.0,
    "status_duration": 1,
    "damage_formula": {
        "base": 5000,
        "ally_min": {"stats": ["force", "intellect"], "coefficient": 15},
        "enemy": {"defense": 0.3},
    },
    "passive_config": {},
    "required_level": 80,
    "required_force": 400,
    "required_intellect": 400,
    "required_defense": 0,
    "required_agility": 0,
    "targets": 1,
}
BOOK_DEFAULTS = {
    "name": "《神意破虚》秘笈",
    "description": "记载神意与内劲相合的破敌之法，可供门客研习「神意破虚」。",
    "effect_type": "skill_book",
    "effect_payload": {"skill_key": SKILL_KEY, "skill_name": "神意破虚"},
    "rarity": "purple",
    "tradeable": True,
    "price": 750000,
    "storage_space": 125,
    "is_usable": False,
}


def add_shenyi_poxu(apps, schema_editor):
    database = schema_editor.connection.alias
    Skill = apps.get_model("guests", "Skill")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Skill.objects.using(database).update_or_create(key=SKILL_KEY, defaults=deepcopy(SKILL_DEFAULTS))
    book_defaults = deepcopy(BOOK_DEFAULTS)
    for key in ("book_brahma_true_fire", "book_city_felling_strike"):
        reference = ItemTemplate.objects.using(database).filter(key=key).only("image").first()
        if reference is not None and reference.image:
            book_defaults["image"] = reference.image.name
            break
    ItemTemplate.objects.using(database).update_or_create(key=BOOK_KEY, defaults=book_defaults)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0198_equipment_balance_and_requirements"),
        ("guests", "0089_equipment_requirements_and_identity"),
    ]
    operations = [migrations.RunPython(add_shenyi_poxu, migrations.RunPython.noop)]
