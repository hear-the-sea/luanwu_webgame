from __future__ import annotations

import pytest
from django.db import transaction
from django.urls import reverse

from core.exceptions import GameError, InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.core import consume_inventory_item_locked
from gameplay.services.inventory.guest_items import use_xisuidan
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from tests.inventory_guest_items.support_reset import _prepare_xisuidan_case

pytestmark = pytest.mark.django_db


def _box(manor):
    reward = ItemTemplate.objects.create(key="warehouse_group_reward", name="分组奖励", effect_type="tool")
    template = ItemTemplate.objects.create(
        key="warehouse_group_box",
        name="分组宝箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={"item_rewards": [{"item_key": reward.key, "min_quantity": 2, "max_quantity": 2}]},
    )
    return InventoryItem.objects.create(manor=manor, template=template, quantity=2, bound_quantity=1), reward


@pytest.mark.parametrize(
    "binding_mode,remaining_bound,reward_bound", [("bound", 0, 2), ("unbound", 1, 0), (None, 0, 2)]
)
def test_mixed_box_uses_selected_group_and_preserves_reward_binding(
    django_user_model, binding_mode, remaining_bound, reward_bound
):
    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_group_box"))
    box, reward = _box(manor)
    use_inventory_item(box, manor, binding_mode=binding_mode)
    box.refresh_from_db()
    assert (box.quantity, box.bound_quantity) == (1, remaining_bound)
    output = InventoryItem.objects.get(manor=manor, template=reward)
    assert (output.quantity, output.bound_quantity) == (2, reward_bound)


@pytest.mark.parametrize("binding_mode,bound_count", [("bound", 0), ("unbound", 2)])
def test_selected_empty_box_group_does_not_consume_other_group_or_grant_rewards(
    django_user_model, binding_mode, bound_count
):
    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_empty_group"))
    box, reward = _box(manor)
    box.bound_quantity = bound_count
    box.save(update_fields=["bound_quantity"])
    with pytest.raises(InsufficientStockError):
        use_inventory_item(box, manor, binding_mode=binding_mode)
    box.refresh_from_db()
    assert (box.quantity, box.bound_quantity) == (2, bound_count)
    assert not InventoryItem.objects.filter(manor=manor, template=reward).exists()


@pytest.mark.parametrize("binding_mode", ["", "auto", "BOUND", 1, {}, False])
def test_invalid_binding_group_is_rejected_without_mutation(django_user_model, binding_mode):
    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_bad_group"))
    box, reward = _box(manor)
    with pytest.raises(GameError, match="绑定类型无效"):
        use_inventory_item(box, manor, binding_mode=binding_mode)
    box.refresh_from_db()
    assert (box.quantity, box.bound_quantity) == (2, 1)
    assert not InventoryItem.objects.filter(manor=manor, template=reward).exists()


@pytest.mark.parametrize("binding_mode", ["bound", "unbound"])
def test_group_consumption_checks_entire_requested_amount(django_user_model, binding_mode):
    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_group_quantity"))
    box, _ = _box(manor)
    with transaction.atomic(), pytest.raises(InsufficientStockError):
        consume_inventory_item_locked(box, 2, binding_mode=binding_mode)
    box.refresh_from_db()
    assert (box.quantity, box.bound_quantity) == (2, 1)


@pytest.mark.parametrize("binding_mode,bound_count", [("bound", 0), ("unbound", 1)])
def test_xisuidan_rejects_empty_group_before_guest_reroll(django_user_model, binding_mode, bound_count):
    manor, guest, item = _prepare_xisuidan_case(django_user_model, "binding_empty")
    item.bound_quantity = bound_count
    item.save(update_fields=["bound_quantity"])
    old_stats = (guest.force, guest.intellect, guest.defense_stat, guest.agility, guest.xisuidan_used)
    with pytest.raises(InsufficientStockError):
        use_xisuidan(manor, item, guest.pk, binding_mode=binding_mode)
    guest.refresh_from_db()
    item.refresh_from_db()
    assert (guest.force, guest.intellect, guest.defense_stat, guest.agility, guest.xisuidan_used) == old_stats
    assert (item.quantity, item.bound_quantity) == (1, bound_count)


@pytest.mark.parametrize("binding_mode,remaining_bound", [("bound", 0), ("unbound", 1)])
def test_xisuidan_consumes_only_selected_group(django_user_model, monkeypatch, binding_mode, remaining_bound):
    manor, guest, item = _prepare_xisuidan_case(django_user_model, "binding_mixed")
    item.quantity = 2
    item.bound_quantity = 1
    item.save(update_fields=["quantity", "bound_quantity"])
    monkeypatch.setattr(
        "guests.utils.attribute_growth.allocate_level_up_attributes",
        lambda *_args, **_kwargs: {"force": 120, "intellect": 120, "defense": 120, "agility": 120},
    )
    use_xisuidan(manor, item, guest.pk, binding_mode=binding_mode)
    item.refresh_from_db()
    guest.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (1, remaining_bound)
    assert guest.xisuidan_used == 1


@pytest.mark.parametrize("binding_mode", ["bound", "unbound"])
def test_generic_use_view_forwards_binding_group(manor_with_user, binding_mode):
    manor, client = manor_with_user
    box, reward = _box(manor)
    response = client.post(
        reverse("gameplay:use_item", kwargs={"pk": box.pk}),
        {"binding_mode": binding_mode},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert response.status_code == 200
    box.refresh_from_db()
    assert (box.quantity, box.bound_quantity) == (1, int(binding_mode == "unbound"))
    assert InventoryItem.objects.get(manor=manor, template=reward).bound_quantity == (
        2 if binding_mode == "bound" else 0
    )


@pytest.mark.parametrize(
    "url_name,action,service",
    [
        ("use_rebirth_card", "rebirth_guest", "use_guest_rebirth_card"),
        ("use_super_guest_disguise", "disguise_guest", "use_super_guest_disguise_card"),
        ("use_guest_disguise", "disguise_guest_avatar", "use_guest_disguise_card"),
        ("use_guest_rarity_upgrade", "upgrade_guest_rarity", "use_guest_rarity_upgrade_item"),
        ("use_soul_container", "soul_fusion", "use_soul_container"),
        ("use_xisuidan", "reroll_growth", "use_xisuidan"),
        ("use_xidianka", "reset_allocation", "use_xidianka"),
    ],
)
def test_guest_tool_endpoints_forward_binding_group(manor_with_user, monkeypatch, url_name, action, service):
    manor, client = manor_with_user
    template = ItemTemplate.objects.create(
        key="warehouse_forward_tool", name="分组工具", effect_type="tool", effect_payload={"action": action}
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=2, bound_quantity=1)
    captured = {}

    def fake_service(*args, **kwargs):
        captured.update(kwargs)
        return {"_message": "已使用"}

    monkeypatch.setattr(f"gameplay.views.inventory.{service}", fake_service)
    response = client.post(
        reverse(f"gameplay:{url_name}", kwargs={"pk": item.pk}),
        {"guest_id": 1, "binding_mode": "unbound", "avatar_key": "pig_wu_male1.png"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert response.status_code == 200
    assert captured == {"binding_mode": "unbound"}


@pytest.mark.parametrize("binding_mode", ["bound", "unbound", None])
def test_synthesis_same_key_material_reserves_selected_source(django_user_model, binding_mode):
    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_same_key"))
    result = ItemTemplate.objects.create(key="warehouse_same_key_result", name="合成结果", effect_type="tool")
    template = ItemTemplate.objects.create(
        key="warehouse_same_key_source",
        name="合成碎片",
        effect_type="tool",
        is_usable=True,
        effect_payload={
            "action": "synthesize_item",
            "required_items": {"warehouse_same_key_source": 2},
            "result_item_key": result.key,
        },
    )
    source = InventoryItem.objects.create(manor=manor, template=template, quantity=3, bound_quantity=1)
    use_inventory_item(source, manor, binding_mode=binding_mode)
    assert not InventoryItem.objects.filter(manor=manor, template=template).exists()
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (1, 1)


@pytest.mark.parametrize("binding_mode,expected_bound", [("bound", 1), ("unbound", 2)])
def test_grain_group_consumption_preserves_other_group_and_manor_ledger(
    django_user_model, binding_mode, expected_bound
):
    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_grain_group"))
    template = ItemTemplate.objects.create(key="grain", name="粮食")
    source = InventoryItem.objects.create(manor=manor, template=template, quantity=5, bound_quantity=2)
    with transaction.atomic():
        consume_inventory_item_locked(source, 1, binding_mode=binding_mode)
    source.refresh_from_db()
    manor.refresh_from_db()
    assert (source.quantity, source.bound_quantity, manor.grain) == (4, expected_bound, 4)


@pytest.mark.parametrize("binding_mode", ["bound", "unbound"])
def test_bound_gold_is_available_when_all_unbound_gold_is_frozen(django_user_model, binding_mode):
    from trade.models import FrozenGoldBar

    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_frozen_group"))
    template = ItemTemplate.objects.create(key="gold_bar", name="金条")
    source = InventoryItem.objects.create(manor=manor, template=template, quantity=4, bound_quantity=1)
    FrozenGoldBar.objects.create(manor=manor, amount=3, is_frozen=True)
    with transaction.atomic():
        if binding_mode == "unbound":
            with pytest.raises(InsufficientStockError):
                consume_inventory_item_locked(source, 1, binding_mode=binding_mode)
        else:
            assert consume_inventory_item_locked(source, 1, binding_mode=binding_mode) == 1
    source.refresh_from_db()
    assert (source.quantity, source.bound_quantity) == ((3, 0) if binding_mode == "bound" else (4, 1))


def test_resource_overflow_confirmation_is_scoped_to_selected_binding_group(django_user_model, monkeypatch):
    from core.exceptions import ItemResourceOverflowConfirmationRequired

    manor = ensure_manor(django_user_model.objects.create_user(username="warehouse_overflow_group"))
    template = ItemTemplate.objects.create(
        key="warehouse_group_resource_pack",
        name="粮食礼包",
        effect_type="resource_pack",
        is_usable=True,
        effect_payload={"grain": 10},
    )
    source = InventoryItem.objects.create(manor=manor, template=template, quantity=2, bound_quantity=1)
    monkeypatch.setattr(
        "gameplay.services.inventory.use.preview_resource_grant", lambda *_: ({"grain": 5}, {"grain": 5})
    )
    monkeypatch.setattr(
        "gameplay.services.inventory.use._grant_item_resources", lambda *_, **__: ({"grain": 5}, {"grain": 5})
    )
    with pytest.raises(ItemResourceOverflowConfirmationRequired) as bound_confirmation:
        use_inventory_item(source, manor, binding_mode="bound")
    with pytest.raises(ItemResourceOverflowConfirmationRequired) as unbound_confirmation:
        use_inventory_item(
            source,
            manor,
            binding_mode="unbound",
            resource_overflow_confirmation=bound_confirmation.value.confirmation_snapshot,
        )
    source.refresh_from_db()
    assert (source.quantity, source.bound_quantity) == (2, 1)
    use_inventory_item(
        source,
        manor,
        binding_mode="unbound",
        resource_overflow_confirmation=unbound_confirmation.value.confirmation_snapshot,
    )
    source.refresh_from_db()
    assert (source.quantity, source.bound_quantity) == (1, 1)
