"""下一关凭证直接从任务掉落、不再放进宝箱的迁移回归。"""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

rules = import_module("gameplay.migrations.0273_direct_mission_progression_tokens")

pytestmark = pytest.mark.django_db


def test_direct_progression_token_migration_covers_all_chains():
    from gameplay.models import ItemTemplate, MissionTemplate

    for mission_key, token_key in rules.PROGRESSION_DROPS.items():
        chest_key = "mission_chest_v3_" + mission_key
        MissionTemplate.objects.update_or_create(
            key=mission_key,
            defaults={"name": mission_key, "drop_table": {"silver": 1, chest_key: 1}},
        )
        ItemTemplate.objects.update_or_create(
            key=chest_key,
            defaults={
                "name": mission_key,
                "description": "old description",
                "effect_type": "loot_box",
                "is_usable": True,
                "effect_payload": {
                    "reward_theme": "old theme",
                    "item_rewards": [
                        {"item_key": token_key, "min_quantity": 1, "max_quantity": 1},
                        {"item_key": "buxuedan", "min_quantity": 1, "max_quantity": 1},
                    ],
                },
            },
        )

    rules.apply_direct_progression_tokens(apps, SimpleNamespace(connection=connection))

    for mission_key, token_key in rules.PROGRESSION_DROPS.items():
        mission = MissionTemplate.objects.get(key=mission_key)
        assert mission.drop_table[token_key] == 1
        chest = ItemTemplate.objects.get(key="mission_chest_v3_" + mission_key)
        assert all(row["item_key"] != token_key for row in chest.effect_payload["item_rewards"])
        text_update = rules.CHEST_TEXT_UPDATES[chest.key]
        assert chest.effect_payload["reward_theme"] == text_update["reward_theme"]
        assert chest.description == text_update.get("description", "old description")
