"""蚩尤奖励同步、指定兑换与失败回滚的集成回归。"""

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from core.exceptions import GuestAlreadyOwnedError, GuestCapacityFullError, InsufficientStockError, ItemNotUsableError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from guests.models import Guest, GuestTemplate, WorldUniqueGuest

rules = import_module("gameplay.migrations.0221_chiyou_rewards_and_unique_guests")
equipment_rules = import_module("gameplay.migrations.0222_zhaoyun_equipment_rewards")
growth_rules = import_module("gameplay.migrations.0223_chiyou_guest_growth")
alignment_rules = import_module("gameplay.migrations.0229_personal_guild_guest_profile_alignment")
armor_luck_rules = import_module("gameplay.migrations.0230_chiyou_armor_luck")
copy_rules = import_module("gameplay.migrations.0242_player_facing_mission_item_copy")
DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def exchange_world(db, settings, tmp_path, manor_factory):
    settings.MEDIA_ROOT = tmp_path
    rules.sync_chiyou_rewards(apps, SimpleNamespace(connection=connection))
    growth_rules.sync_chiyou_guest_growth(apps, SimpleNamespace(connection=connection))
    manor, _ = manor_factory(username="chiyou_exchange_integration")
    card = ItemTemplate.objects.get(key="chiyou_card")
    stock = InventoryItem.objects.create(manor=manor, template=card, quantity=100)
    return manor, stock


def test_reward_migration_freezes_current_equipment_and_recipe():
    entries = {row["key"]: row for row in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]}
    for key, defaults in {**rules.ITEM_DEFAULTS, **equipment_rules.ITEM_DEFAULTS}.items():
        expected = {field: value for field, value in entries[key].items() if field != "key"}
        sources = equipment_rules.IMAGE_SOURCES if key in equipment_rules.ITEM_DEFAULTS else rules.IMAGE_SOURCES
        assert sources[defaults["image"]] == "items/" + expected["image"]
        expected["image"] = defaults["image"]
        final_defaults = {
            **defaults,
            **alignment_rules.ITEM_STORAGE_OVERRIDES.get(key, {}),
            **copy_rules.ITEM_UPDATES.get(key, {}),
        }
        if key in armor_luck_rules.EQUIPMENT_PAYLOAD_OVERRIDES:
            final_defaults["effect_payload"] = {
                **final_defaults["effect_payload"],
                **armor_luck_rules.EQUIPMENT_PAYLOAD_OVERRIDES[key],
            }
        assert final_defaults == expected
    recipes = yaml.safe_load((DATA / "forge_blueprints.yaml").read_text())["recipes"]
    assert rules.ARMOR_RECIPE == next(row for row in recipes if row["blueprint_key"] == "blueprint_chiyoujia")
    heroes = yaml.safe_load((DATA / "guests/chiyou_exchange.yaml").read_text())["heroes"]["purple"]
    for hero in heroes:
        defaults = rules.GUEST_DEFAULTS[hero["key"]]
        assert defaults["name"] == hero["name"]
        assert defaults["flavor"] == hero["flavor"]
        assert growth_rules.GUEST_GROWTH_RANGES[hero["key"]] == hero["growth_range"]
        assert growth_rules.GUEST_ATTRIBUTE_WEIGHTS[hero["key"]] == hero["attribute_weights"]
        assert rules.IMAGE_SOURCES[defaults["avatar"]] == "guests/" + hero["avatar"]
        assert defaults["is_world_unique"] is hero["is_world_unique"] is True
    for source in rules.IMAGE_SOURCES.values():
        assert (DATA / "images" / source).is_file()


@pytest.mark.django_db
def test_repeat_sync_preserves_claimed_owner_and_historical_rewards(exchange_world):
    from django.core.files.storage import default_storage
    from PIL import Image

    manor, stock = exchange_world
    unrelated, _ = ItemTemplate.objects.update_or_create(
        key="mission_chest_zhuolu_zhongyuan", defaults={"name": "旧箱", "effect_payload": {"keep": 123}}
    )
    use_inventory_item(stock, manor, guest_template_key="hero_yang_zheng_purple")
    state = WorldUniqueGuest.objects.get(template__key="hero_yang_zheng_purple")
    ownership = (state.owner_guest_id, state.owner_manor_id, state.version)
    for _ in range(2):
        rules.sync_chiyou_rewards(apps, SimpleNamespace(connection=connection))
    state.refresh_from_db()
    unrelated.refresh_from_db()
    assert (state.owner_guest_id, state.owner_manor_id, state.version) == ownership
    assert state.status == "serving"
    assert WorldUniqueGuest.objects.filter(template__key__in=rules.GUEST_DEFAULTS).count() == 2
    assert unrelated.effect_payload == {"keep": 123}
    for item in ItemTemplate.objects.filter(key__in=rules.ITEM_DEFAULTS):
        assert str(item.image).endswith(".webp")
        with default_storage.open(str(item.image), "rb") as stored:
            with Image.open(stored) as image:
                assert max(image.size) <= 200
    for template in GuestTemplate.objects.filter(key__in=rules.GUEST_DEFAULTS):
        with default_storage.open(str(template.avatar), "rb") as stored:
            with Image.open(stored) as image:
                assert max(image.size) <= 400


@pytest.mark.django_db
def test_growth_sync_only_updates_templates_and_preserves_existing_guest(exchange_world):
    manor, stock = exchange_world
    use_inventory_item(stock, manor, guest_template_key="hero_yang_zheng_purple")
    guest = Guest.objects.get(manor=manor, template__key="hero_yang_zheng_purple")
    before = Guest.objects.filter(pk=guest.pk).values().get()
    for _ in range(2):
        growth_rules.sync_chiyou_guest_growth(apps, SimpleNamespace(connection=connection))
    assert Guest.objects.filter(pk=guest.pk).values().get() == before
    assert dict(
        GuestTemplate.objects.filter(key__in=growth_rules.GUEST_GROWTH_RANGES).values_list("key", "growth_range")
    ) == (growth_rules.GUEST_GROWTH_RANGES)
    assert (
        dict(
            GuestTemplate.objects.filter(key__in=growth_rules.GUEST_ATTRIBUTE_WEIGHTS).values_list(
                "key", "attribute_weights"
            )
        )
        == growth_rules.GUEST_ATTRIBUTE_WEIGHTS
    )


@pytest.mark.django_db
@pytest.mark.parametrize("selection", [None, "", "hero_yang_zheng", "hist_sljnbc_0013", "forged"])
def test_missing_or_unlisted_selection_never_spends_cards(exchange_world, selection):
    manor, stock = exchange_world
    with pytest.raises(ItemNotUsableError, match="请选择有效的兑换门客"):
        use_inventory_item(stock, manor, guest_template_key=selection)
    stock.refresh_from_db()
    assert stock.quantity == 100
    assert not Guest.objects.filter(manor=manor, template__key__in=rules.GUEST_DEFAULTS).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("quantity,bound,mode", [(100, 40, "bound"), (101, 40, "unbound"), (100, 100, "bound")])
def test_success_spends_exactly_one_hundred_with_mixed_binding(exchange_world, quantity, bound, mode):
    manor, stock = exchange_world
    stock.quantity, stock.bound_quantity = quantity, bound
    stock.save(update_fields=["quantity", "bound_quantity"])
    result = use_inventory_item(stock, manor, guest_template_key="hero_di_qinglin", binding_mode=mode)
    assert result["全服唯一"] is True
    remaining = InventoryItem.objects.filter(pk=stock.pk).first()
    assert (remaining.quantity if remaining else 0) == quantity - 100
    if remaining:
        assert 0 <= remaining.bound_quantity <= remaining.quantity
    assert Guest.objects.filter(manor=manor, template__key="hero_di_qinglin").count() == 1


@pytest.mark.django_db
def test_insufficient_cards_roll_back_without_claiming(exchange_world):
    manor, stock = exchange_world
    stock.quantity = 99
    stock.save(update_fields=["quantity"])
    with pytest.raises(InsufficientStockError):
        use_inventory_item(stock, manor, guest_template_key="hero_yang_zheng_purple")
    stock.refresh_from_db()
    assert stock.quantity == 99
    assert WorldUniqueGuest.objects.get(template__key="hero_yang_zheng_purple").status == "wild"


@pytest.mark.django_db
def test_second_manor_cannot_claim_taken_guest_and_keeps_all_cards(exchange_world, manor_factory):
    manor, stock = exchange_world
    target = "hero_yang_zheng_purple"
    use_inventory_item(stock, manor, guest_template_key=target)
    other, _ = manor_factory(username="chiyou_exchange_competitor")
    other_stock = InventoryItem.objects.create(
        manor=other, template=ItemTemplate.objects.get(key="chiyou_card"), quantity=100, bound_quantity=35
    )
    with pytest.raises(GuestAlreadyOwnedError):
        use_inventory_item(other_stock, other, guest_template_key=target)
    other_stock.refresh_from_db()
    assert (other_stock.quantity, other_stock.bound_quantity) == (100, 35)
    assert Guest.objects.filter(template__key=target).count() == 1
    assert WorldUniqueGuest.objects.get(template__key=target).owner_manor_id == manor.pk


@pytest.mark.django_db
def test_capacity_failure_keeps_cards_and_unique_state(exchange_world, monkeypatch):
    from gameplay.models import Manor

    manor, stock = exchange_world
    monkeypatch.setattr(Manor, "guest_capacity", property(lambda self: 0))
    with pytest.raises(GuestCapacityFullError):
        use_inventory_item(stock, manor, guest_template_key="hero_di_qinglin")
    stock.refresh_from_db()
    assert stock.quantity == 100
    assert WorldUniqueGuest.objects.get(template__key="hero_di_qinglin").status == "wild"


@pytest.mark.django_db
def test_selected_guest_cannot_redirect_a_normal_random_scroll(exchange_world):
    manor, _ = exchange_world
    template = GuestTemplate.objects.get(key="hero_yang_zheng_purple")
    card = ItemTemplate.objects.create(
        key="chiyou_normal_scroll",
        name="普通卷轴",
        effect_type="tool",
        is_usable=True,
        effect_payload={"action": "summon_guest", "choices": [{"template_key": template.key, "weight": 1}]},
    )
    stock = InventoryItem.objects.create(manor=manor, template=card, quantity=1)
    with pytest.raises(ItemNotUsableError, match="不支持选择"):
        use_inventory_item(stock, manor, guest_template_key=template.key)
    stock.refresh_from_db()
    assert stock.quantity == 1


@pytest.mark.django_db
def test_actual_chest_draws_seven_armors_blueprint_boots_and_coins_then_forges(exchange_world, monkeypatch):
    from gameplay.services.buildings import forge_blueprints
    from gameplay.services.inventory import use as inventory_use

    manor, stock = exchange_world
    stock.delete()
    coin_data = next(
        row
        for row in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]
        if row["key"] == "chunqiu_coin"
    )
    ItemTemplate.objects.update_or_create(
        key=coin_data["key"],
        defaults={field: coin_data[field] for field in ("name", "rarity", "effect_type")},
    )
    chest_template = ItemTemplate.objects.get(key="mission_chest_v3_zhuolu_zhongyuan")
    chest = InventoryItem.objects.create(manor=manor, template=chest_template, quantity=10, bound_quantity=1)
    picks = iter(range(10))
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.0)
    monkeypatch.setattr(inventory_use, "weighted_random_choice", lambda choices, weights, rng: choices[next(picks)])
    for _ in range(10):
        inventory_use.use_inventory_item(chest, manor)
    assert not InventoryItem.objects.filter(pk=chest.pk).exists()
    cards = InventoryItem.objects.get(manor=manor, template__key="chiyou_card")
    assert cards.quantity == 10
    assert cards.bound_quantity == 1
    assert InventoryItem.objects.get(manor=manor, template__key="chunqiu_coin").quantity == 2
    inventory = list(InventoryItem.objects.filter(manor=manor).select_related("template"))
    bases = [row.template.effect_payload.get("equipment_base_key", row.template.key) for row in inventory]
    assert set(rules.ARMOR_RECIPE["costs"]) <= set(bases)
    assert "equip_chiyouzhanxue" in bases
    assert "equip_chiyoujia" not in bases
    monkeypatch.setattr(forge_blueprints.technology_service, "get_player_technology_level", lambda *_: 10)
    crafted = forge_blueprints.synthesize_equipment_with_blueprint(
        manor, "blueprint_chiyoujia", recipe_index={"blueprint_chiyoujia": rules.ARMOR_RECIPE}
    )
    assert crafted["quantity"] == 1 and crafted["bound_quantity"] == 1
    assert not InventoryItem.objects.filter(manor=manor, template__key="blueprint_chiyoujia").exists()
    armor = next(
        row
        for row in InventoryItem.objects.filter(manor=manor).select_related("template")
        if row.template.effect_payload.get("equipment_base_key", row.template.key) == "equip_chiyoujia"
    )
    assert armor.quantity == armor.bound_quantity == 1
    assert (
        sum(
            row.quantity
            for row in InventoryItem.objects.filter(manor=manor).select_related("template")
            if row.template.effect_payload.get("equipment_base_key", row.template.key) in rules.ARMOR_RECIPE["costs"]
        )
        == 0
    )
