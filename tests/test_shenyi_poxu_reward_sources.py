from copy import deepcopy
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

rules = import_module("gameplay.migrations.0201_shenyi_poxu_reward_sources")
ITEMS = {
    row["key"]: row
    for row in yaml.safe_load((Path(__file__).resolve().parents[1] / "data/item_templates.yaml").read_text())["items"]
}
CHESTS = (*rules.MISSION_CHESTS, rules.GUILD_CHEST)


@pytest.mark.parametrize("key", CHESTS)
def test_reward_config_matches_migration_and_preserves_existing_rewards(key):
    payload = ITEMS[key]["effect_payload"]
    original = deepcopy(payload)
    if key in rules.MISSION_CHESTS:
        groups = original["random_item_groups"]
        added = [group for group in groups if group.get("label") == rules.BOOK_KEY]
        assert added == [rules.MISSION_REWARD]
        original["random_item_groups"] = [group for group in groups if group not in added]
        updated = rules.add_reward_source(key, original)
        assert updated["random_item_groups"][-1] == rules.MISSION_REWARD
        updated["random_item_groups"].pop()
    else:
        choices = original["skill_book_choices"]
        added = [choice for choice in choices if choice["item_key"] == rules.BOOK_KEY]
        assert added == [rules.GUILD_REWARD]
        assert payload["skill_book_chance"] == 1
        assert 22 / sum(choice["weight"] for choice in choices) == pytest.approx(22 / 168)
        original["skill_book_choices"] = [choice for choice in choices if choice not in added]
        updated = rules.add_reward_source(key, original)
        assert updated["skill_book_choices"].pop() == rules.GUILD_REWARD
    assert updated == original
    assert rules.add_reward_source(key, payload) == payload


@pytest.mark.django_db
def test_reward_migration_updates_only_target_chests_and_is_idempotent():
    from django.apps import apps
    from django.db import connection

    from gameplay.models import ItemTemplate

    for key in CHESTS:
        payload = {"custom": 123, "random_item_groups": [], "skill_book_choices": []}
        ItemTemplate.objects.update_or_create(key=key, defaults={"effect_type": "loot_box", "effect_payload": payload})
    unrelated = ItemTemplate.objects.create(key="unrelated_shenyi_source", effect_payload={"keep": 1})
    for _ in range(2):
        rules.add_shenyi_reward_sources(apps, SimpleNamespace(connection=connection))
    for item in ItemTemplate.objects.filter(key__in=CHESTS):
        assert item.effect_payload["custom"] == 123
        field = "skill_book_choices" if item.key == rules.GUILD_CHEST else "random_item_groups"
        assert item.effect_payload[field] == [
            rules.GUILD_REWARD if item.key == rules.GUILD_CHEST else rules.MISSION_REWARD
        ]
    unrelated.refresh_from_db()
    assert unrelated.effect_payload == {"keep": 1}


@pytest.mark.django_db
@pytest.mark.parametrize("key", CHESTS)
def test_chests_can_grant_the_new_book_bound_to_source(django_user_model, monkeypatch, key):
    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.services.inventory import use as inventory_use
    from gameplay.services.manor.core import ensure_manor

    manor = ensure_manor(django_user_model.objects.create_user(username="shenyi_reward_source"))
    book, _ = ItemTemplate.objects.get_or_create(
        key=rules.BOOK_KEY, defaults={"name": "《神意破虚》秘笈", "effect_type": "skill_book"}
    )
    payload = deepcopy(ITEMS[key]["effect_payload"])
    if key in rules.MISSION_CHESTS:
        payload["item_rewards"] = []
        payload["random_item_groups"] = [rules.MISSION_REWARD]
        payload["blueprint_material_support"] = {}
    chest = ItemTemplate.objects.create(
        key=key, name="测试宝箱", effect_type="loot_box", is_usable=True, effect_payload=payload
    )
    source = InventoryItem.objects.create(manor=manor, template=chest, quantity=1, bound_quantity=1)
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0)

    def choose(keys, weights, rng):
        assert rules.BOOK_KEY in keys
        return rules.BOOK_KEY

    monkeypatch.setattr(inventory_use, "weighted_random_choice", choose)
    inventory_use.use_inventory_item(source, manor)
    granted = InventoryItem.objects.get(manor=manor, template=book)
    assert granted.quantity == granted.bound_quantity == 1
