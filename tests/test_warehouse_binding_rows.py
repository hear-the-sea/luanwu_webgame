import pytest
from django.test import Client
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.selectors.warehouse import get_warehouse_context
from gameplay.services.manor.core import ensure_manor

pytestmark = pytest.mark.django_db


@pytest.fixture
def split_world(django_user_model):
    user = django_user_model.objects.create_user(username="split_warehouse", password="pass")
    return user, ensure_manor(user)


@pytest.mark.parametrize(
    "quantity,bound,expected",
    [(13, 10, [("bound", 10), ("unbound", 3)]), (3, 0, [("unbound", 3)]), (3, 3, [("bound", 3)])],
)
def test_inventory_rows_split_without_changing_stock(split_world, quantity, bound, expected):
    _, manor = split_world
    template = ItemTemplate.objects.create(key="split_stock", name="分组道具", effect_type="tool")
    stock = InventoryItem.objects.create(manor=manor, template=template, quantity=quantity, bound_quantity=bound)
    context = get_warehouse_context(manor, "warehouse", "tool")
    rows = context["inventory_items"]
    assert [(row.binding_mode, row.display_quantity) for row in rows] == expected
    assert {row.pk for row in rows} == {stock.pk}
    for row in rows:
        assert row.transferable_quantity == (0 if row.display_is_bound else row.display_quantity)
    stock.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (quantity, bound)
    assert InventoryItem.objects.filter(manor=manor, template=template).count() == 1


def test_pagination_counts_display_rows_and_keeps_filter(split_world):
    _, manor = split_world
    for index in range(11):
        template = ItemTemplate.objects.create(key=f"split_page_{index}", name=f"分组{index:02}", effect_type="tool")
        InventoryItem.objects.create(manor=manor, template=template, quantity=2, bound_quantity=1)
    hidden = ItemTemplate.objects.create(key="split_excluded", name="其他类别", effect_type="skill_book")
    InventoryItem.objects.create(manor=manor, template=hidden, quantity=2, bound_quantity=1)
    first = get_warehouse_context(manor, "warehouse", "tool", 1)
    second = get_warehouse_context(manor, "warehouse", "tool", 2)
    assert first["pagination"]["total_count"] == 22
    assert first["pagination"]["total_pages"] == 2
    assert len(first["inventory_items"]) == 20
    assert len(second["inventory_items"]) == 2
    assert [row.binding_mode for row in second["inventory_items"]] == ["bound", "unbound"]
    assert all(row.template.key != hidden.key for row in first["inventory_items"] + second["inventory_items"])


def test_page_renders_separate_actions_for_each_binding_group(split_world):
    user, manor = split_world
    template = ItemTemplate.objects.create(
        key="split_usable", name="分组宝箱", effect_type="loot_box", is_usable=True, effect_payload={"items": {}}
    )
    stock = InventoryItem.objects.create(manor=manor, template=template, quantity=13, bound_quantity=10)
    client = Client()
    client.force_login(user)
    page = client.get(reverse("gameplay:warehouse"))
    assert page.status_code == 200
    body = page.content.decode()
    assert '<tr data-binding-mode="bound">' in body
    assert '<tr data-binding-mode="unbound">' in body
    assert 'name="binding_mode" value="bound"' in body
    assert 'name="binding_mode" value="unbound"' in body
    assert body.count(reverse("gameplay:use_item", args=[stock.pk])) == 2
    assert body.count(reverse("gameplay:move_to_treasury", args=[stock.pk])) == 1
    assert "绑定物品不可转移" in body
    assert "分组宝箱（绑定）</span>" in body
    assert "分组宝箱</span>" in body
    assert "分组宝箱（非绑定）" not in body
