from pathlib import Path

import pytest
import yaml

from core.exceptions import GuestAlreadyOwnedError, GuestCapacityFullError, InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX
from guests.models import Guest, GuestTemplate

TOKEN_KEY = "mingjiao_holy_fire_token"
SEED_KEY = "mingjiao_fire_seed"
SCROLL_KEY = "xiaozhao_recruit_scroll"
GUEST_KEY = "hero_jinyong_0215"


@pytest.fixture
def summon_case(django_user_model):
    """使用正式道具配置，验证仓库已有召唤流程的完整配方。"""
    data_path = Path(__file__).resolve().parent.parent / "data" / "item_templates.yaml"
    rows = {row["key"]: row for row in yaml.safe_load(data_path.read_text(encoding="utf-8"))["items"]}
    manor = ensure_manor(django_user_model.objects.create_user(username="xiaozhao_summon"))
    guest_template = GuestTemplate.objects.create(
        key=GUEST_KEY,
        name="小昭",
        rarity="purple",
        archetype="civil",
        growth_range=[6, 12],
        recruitable=False,
    )
    stock = {}
    for key, quantity in ((TOKEN_KEY, 15), (SEED_KEY, 1200), (SCROLL_KEY, 2)):
        row = rows[key]
        template = ItemTemplate.objects.create(
            key=key,
            name=row["name"],
            effect_type=row["effect_type"],
            rarity=row["rarity"],
            tradeable=row["tradeable"],
            is_usable=row["is_usable"],
            effect_payload=row.get("effect_payload", {}),
        )
        stock[key] = InventoryItem.objects.create(manor=manor, template=template, quantity=quantity)
    return manor, guest_template, stock


def _quantities(stock):
    return {key: InventoryItem.objects.get(pk=item.pk).quantity for key, item in stock.items()}


@pytest.mark.django_db
def test_scroll_consumes_exact_recipe_and_grants_one_purple_civil_guest(summon_case):
    manor, template, stock = summon_case

    result = use_inventory_item(stock[SCROLL_KEY], manor)

    assert "小昭" in result["_message"]
    assert result["稀有度"] == template.get_rarity_display()
    assert _quantities(stock) == {TOKEN_KEY: 3, SEED_KEY: 200, SCROLL_KEY: 1}
    guest = manor.guests.get(template=template)
    assert guest.template.archetype == "civil"
    assert guest.template.rarity == "purple"
    assert guest.template.growth_range == [6, 12]


@pytest.mark.django_db
@pytest.mark.parametrize("key,shortage", [(TOKEN_KEY, 11), (SEED_KEY, 999)])
def test_material_shortage_keeps_every_item_and_creates_no_guest(summon_case, key, shortage):
    manor, template, stock = summon_case
    InventoryItem.objects.filter(pk=stock[key].pk).update(quantity=shortage)
    before = _quantities(stock)

    with pytest.raises(InsufficientStockError):
        use_inventory_item(stock[SCROLL_KEY], manor)

    assert _quantities(stock) == before
    assert not manor.guests.filter(template=template).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("promoted", [False, True])
def test_owned_xiaozhao_including_promoted_copy_preserves_recipe(summon_case, promoted):
    manor, template, stock = summon_case
    if promoted:
        template = GuestTemplate.objects.create(
            key=f"{RARITY_CONVERSION_TEMPLATE_KEY_PREFIX}orange_{template.pk}",
            name="小昭",
            rarity="orange",
            archetype="civil",
        )
    Guest.objects.create(manor=manor, template=template)
    before = _quantities(stock)

    with pytest.raises(GuestAlreadyOwnedError, match="小昭"):
        use_inventory_item(stock[SCROLL_KEY], manor)

    assert _quantities(stock) == before
    assert manor.guests.filter(template=template).count() == 1


@pytest.mark.django_db
def test_full_manor_keeps_scroll_and_materials(summon_case):
    manor, _template, stock = summon_case
    other = GuestTemplate.objects.create(key="xiaozhao_capacity_filler", name="普通门客", rarity="green")
    for _ in range(manor.guest_capacity - manor.active_guest_count):
        Guest.objects.create(manor=manor, template=other)
    before = _quantities(stock)

    with pytest.raises(GuestCapacityFullError):
        use_inventory_item(stock[SCROLL_KEY], manor)

    assert _quantities(stock) == before


@pytest.mark.django_db
def test_guest_creation_failure_rolls_back_both_materials_and_scroll(summon_case, monkeypatch):
    manor, template, stock = summon_case
    before = _quantities(stock)

    def fail_creation(**_kwargs):
        raise RuntimeError("召唤写入失败")

    monkeypatch.setattr("guests.services.recruitment_guests.create_guest_from_template", fail_creation)
    with pytest.raises(RuntimeError, match="召唤写入失败"):
        use_inventory_item(stock[SCROLL_KEY], manor)

    assert _quantities(stock) == before
    assert not manor.guests.filter(template=template).exists()


@pytest.mark.django_db
def test_repeated_use_of_remaining_scroll_does_not_charge_again(summon_case):
    manor, template, stock = summon_case
    use_inventory_item(stock[SCROLL_KEY], manor)
    before = _quantities(stock)

    with pytest.raises(GuestAlreadyOwnedError):
        use_inventory_item(stock[SCROLL_KEY], manor)

    assert _quantities(stock) == before
    assert manor.guests.filter(template=template).count() == 1
