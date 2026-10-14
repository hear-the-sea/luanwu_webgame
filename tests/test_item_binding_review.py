import pytest

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.selectors.warehouse import get_warehouse_context
from gameplay.services.equipment_template_sync import synchronize_equipment_templates
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate
from guests.services.health import heal_all_guests_with_medicine
from trade.models import FrozenGoldBar


@pytest.fixture
def binding_manor(django_user_model):
    return ensure_manor(django_user_model.objects.create_user(username="binding_review", password="pass"))


@pytest.mark.django_db
def test_warehouse_transfer_controls_exclude_bound_and_frozen_gold(binding_manor):
    template, _ = ItemTemplate.objects.get_or_create(key="gold_bar", defaults={"name": "金条"})
    InventoryItem.objects.create(manor=binding_manor, template=template, quantity=10, bound_quantity=6)
    FrozenGoldBar.objects.create(manor=binding_manor, amount=3, reason=FrozenGoldBar.Reason.AUCTION_BID)
    context = get_warehouse_context(binding_manor, current_tab="warehouse", selected_category="all")
    rows = [row for row in context["inventory_items"] if row.template.key == "gold_bar"]
    bound_row = next(row for row in rows if row.display_is_bound)
    assert bound_row.display_quantity == 6
    assert bound_row.transferable_quantity == 0
    row = next(row for row in rows if not row.display_is_bound)
    assert row.quantity == 10
    assert row.bound_quantity == 6
    assert row.transferable_quantity == 1
    assert row.display_quantity == 1


@pytest.mark.django_db
@pytest.mark.parametrize("existing_target", [False, True])
def test_equipment_alias_merge_preserves_all_binding(binding_manor, existing_target):
    old = ItemTemplate.objects.create(key="equip_xiaoweitoukie", name="旧头盔", effect_type="equip_helmet")
    target = ItemTemplate.objects.create(key="equip_xiaoweitoukui", name="头盔", effect_type="equip_helmet")
    InventoryItem.objects.create(manor=binding_manor, template=old, quantity=5, bound_quantity=3)
    if existing_target:
        InventoryItem.objects.create(manor=binding_manor, template=target, quantity=4, bound_quantity=2)
    synchronize_equipment_templates([target.key])
    row = InventoryItem.objects.get(manor=binding_manor, template=target)
    assert (row.quantity, row.bound_quantity) == ((9, 5) if existing_target else (5, 3))
    assert not ItemTemplate.objects.filter(pk=old.pk).exists()


@pytest.mark.django_db
def test_batch_healing_consumes_bound_first_across_multiple_doses(binding_manor):
    template = GuestTemplate.objects.create(key="binding_healing_guest", name="疗伤门客", base_hp=1000)
    guest = Guest.objects.create(manor=binding_manor, template=template, defense_stat=0, current_hp=1)
    medicine = ItemTemplate.objects.create(
        key="binding_healing_medicine", name="疗伤药", effect_type="medicine", effect_payload={"hp": 400}
    )
    stock = InventoryItem.objects.create(manor=binding_manor, template=medicine, quantity=5, bound_quantity=2)
    result = heal_all_guests_with_medicine(binding_manor)
    guest.refresh_from_db()
    stock.refresh_from_db()
    assert result["consumed_item_count"] == 3
    assert guest.current_hp == guest.max_hp
    assert (stock.quantity, stock.bound_quantity) == (2, 0)
