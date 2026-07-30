from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from gameplay.models import EquipmentProduction, HorseProduction, LivestockProduction, SmeltingProduction

VIEW_CASES = (
    pytest.param(
        HorseProduction,
        HorseProduction.Status.PRODUCING,
        {"horse_key": "view_horse", "horse_name": "页面测试马", "grain_cost": 10},
        "stable",
        "cancel_horse_production",
        "取消生产",
        id="horse",
    ),
    pytest.param(
        LivestockProduction,
        LivestockProduction.Status.PRODUCING,
        {
            "livestock_key": "view_livestock",
            "livestock_name": "页面测试家畜",
            "grain_cost": 10,
        },
        "ranch",
        "cancel_livestock_production",
        "取消养殖",
        id="livestock",
    ),
    pytest.param(
        SmeltingProduction,
        SmeltingProduction.Status.PRODUCING,
        {
            "metal_key": "view_smelting",
            "metal_name": "页面测试物品",
            "cost_type": "silver",
            "cost_amount": 10,
        },
        "smithy",
        "cancel_smelting_production",
        "取消制作",
        id="smelting",
    ),
    pytest.param(
        EquipmentProduction,
        EquipmentProduction.Status.FORGING,
        {
            "equipment_key": "view_equipment",
            "equipment_name": "页面测试装备",
            "material_costs": {"tong": 10},
        },
        "forge",
        "cancel_equipment_forging",
        "取消锻造",
        id="equipment",
    ),
)


def _create_active_production(*, model, manor, status, fields):
    return model.objects.create(
        manor=manor,
        quantity=1,
        base_duration=120,
        actual_duration=120,
        complete_at=timezone.now() + timezone.timedelta(minutes=2),
        status=status,
        **fields,
    )


@pytest.mark.parametrize(
    ("model", "status", "fields", "page_name", "cancel_name", "button_text"),
    VIEW_CASES,
)
@pytest.mark.django_db
def test_production_page_shows_cancel_action_and_irreversible_warning(
    model,
    status,
    fields,
    page_name,
    cancel_name,
    button_text,
    manor_with_user,
):
    manor, client = manor_with_user
    production = _create_active_production(model=model, manor=manor, status=status, fields=fields)

    response = client.get(reverse(f"gameplay:{page_name}"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert reverse(f"gameplay:{cancel_name}", args=[production.pk]) in body
    assert button_text in body
    assert "已消耗的材料不会返还" in body
    assert "js/production-cancel.js" in body


@pytest.mark.parametrize(
    ("model", "status", "fields", "page_name", "cancel_name", "_button_text"),
    VIEW_CASES,
)
@pytest.mark.django_db
def test_cancel_production_view_marks_own_record_cancelled(
    model,
    status,
    fields,
    page_name,
    cancel_name,
    _button_text,
    manor_with_user,
):
    manor, client = manor_with_user
    manor.grain = 345
    manor.silver = 678
    manor.save(update_fields=["grain", "silver"])
    production = _create_active_production(model=model, manor=manor, status=status, fields=fields)
    payload = {"category": "helmet"} if page_name == "forge" else {}

    response = client.post(reverse(f"gameplay:{cancel_name}", args=[production.pk]), payload)

    production.refresh_from_db()
    manor.refresh_from_db()
    assert response.status_code == 302
    if page_name == "forge":
        assert response.url == f"{reverse('gameplay:forge')}?mode=synthesize&category=helmet"
    else:
        assert response.url == reverse(f"gameplay:{page_name}")
    assert production.status == model.Status.CANCELLED
    assert manor.grain == 345
    assert manor.silver == 678
    assert any("不予返还" in str(message) for message in get_messages(response.wsgi_request))


@pytest.mark.parametrize(
    ("model", "status", "fields", "_page_name", "cancel_name", "_button_text"),
    VIEW_CASES,
)
@pytest.mark.django_db
def test_cancel_production_view_requires_post(
    model,
    status,
    fields,
    _page_name,
    cancel_name,
    _button_text,
    manor_with_user,
):
    manor, client = manor_with_user
    production = _create_active_production(model=model, manor=manor, status=status, fields=fields)

    response = client.get(reverse(f"gameplay:{cancel_name}", args=[production.pk]))

    production.refresh_from_db()
    assert response.status_code == 405
    assert production.status == status
