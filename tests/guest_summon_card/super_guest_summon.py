from pathlib import Path

import pytest
import yaml

from core.exceptions import NoTemplateAvailableError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.constants import SUPER_GUEST_SUMMON_ACTION
from guests.models import Guest, GuestTemplate


def _make_orange_template(
    key: str,
    *,
    recruitable: bool = True,
    is_world_unique: bool = False,
) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=key,
        archetype="military",
        rarity="orange",
        recruitable=recruitable,
        is_world_unique=is_world_unique,
    )


def _make_super_guest_summon_item(manor, *, quantity: int = 1) -> InventoryItem:
    template = ItemTemplate.objects.create(
        key="super_guest_summon_card_test",
        name="超级门客召唤卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="orange",
        is_usable=True,
        effect_payload={"action": SUPER_GUEST_SUMMON_ACTION},
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


@pytest.mark.django_db
def test_super_guest_summon_only_uses_unowned_recruitable_orange_templates(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="super_guest_summon_filter", password="pass123")
    manor = ensure_manor(user)

    owned = _make_orange_template("super_summon_owned_orange")
    available = _make_orange_template("super_summon_available_orange")
    unrecruitable = _make_orange_template("super_summon_unrecruitable_orange", recruitable=False)
    world_unique = _make_orange_template(
        "super_summon_world_unique_orange",
        is_world_unique=True,
    )
    Guest.objects.create(manor=manor, template=owned)
    item = _make_super_guest_summon_item(manor)

    selected_candidates: list[list[GuestTemplate]] = []

    def choose(candidates):
        selected_candidates.append(candidates)
        return available

    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.choice", choose)

    result = use_inventory_item(item, manor)

    assert len(selected_candidates) == 1
    assert available in selected_candidates[0]
    assert owned not in selected_candidates[0]
    assert unrecruitable not in selected_candidates[0]
    assert world_unique not in selected_candidates[0]
    assert all(
        template.recruitable and not template.is_world_unique and not template.is_hermit
        for template in selected_candidates[0]
    )
    assert result["获得门客"] == available.name
    assert result["稀有度"] == "橙"
    assert manor.guests.filter(template=available).count() == 1
    assert not manor.guests.filter(template=unrecruitable).exists()
    assert not manor.guests.filter(template=world_unique).exists()
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_super_guest_summon_does_not_consume_card_when_all_recruitable_oranges_are_owned(
    monkeypatch,
    django_user_model,
):
    user = django_user_model.objects.create_user(username="super_guest_summon_empty", password="pass123")
    manor = ensure_manor(user)
    orange = _make_orange_template("super_summon_already_owned_orange")
    Guest.objects.create(manor=manor, template=orange)
    item = _make_super_guest_summon_item(manor)
    monkeypatch.setattr(
        "guests.services.recruitment_templates._get_recruitable_templates_by_rarity",
        lambda: {"orange": [orange]},
    )

    with pytest.raises(NoTemplateAvailableError):
        use_inventory_item(item, manor)

    item.refresh_from_db()
    assert item.quantity == 1
    assert manor.guests.filter(template=orange).count() == 1


def test_super_guest_summon_item_config_and_icon_are_present():
    project_root = Path(__file__).resolve().parents[2]
    item_config = yaml.safe_load((project_root / "data" / "item_templates.yaml").read_text(encoding="utf-8"))
    item = next(row for row in item_config["items"] if row["key"] == "super_guest_summon_card")

    assert item["name"] == "超级门客召唤卡"
    assert item["effect_type"] == "tool"
    assert item["rarity"] == "orange"
    assert item["is_usable"] is True
    assert item["effect_payload"] == {"action": SUPER_GUEST_SUMMON_ACTION}
    assert (project_root / "data" / "images" / "items" / item["image"]).is_file()
