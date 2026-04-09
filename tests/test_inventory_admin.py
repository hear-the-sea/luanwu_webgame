from __future__ import annotations

import pytest
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.models.progression import WorkAssignment, WorkTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate


@pytest.mark.django_db
def test_inventory_item_admin_change_page_renders(client, django_user_model):
    admin_user = django_user_model.objects.create_user(
        username="inventory_admin",
        password="pass123",
        email="inventory_admin@test.local",
        is_staff=True,
        is_superuser=True,
    )
    manor_user = django_user_model.objects.create_user(
        username="inventory_owner",
        password="pass123",
        email="inventory_owner@test.local",
    )
    manor = ensure_manor(manor_user)
    template = ItemTemplate.objects.create(
        key="inventory_admin_tpl",
        name="后台测试道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="purple",
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=3)

    client.force_login(admin_user)
    response = client.get(reverse("admin:gameplay_inventoryitem_change", args=[item.pk]))

    assert response.status_code == 200


@pytest.mark.django_db
def test_guest_admin_change_page_renders(client, django_user_model):
    admin_user = django_user_model.objects.create_user(
        username="guest_admin",
        password="pass123",
        email="guest_admin@test.local",
        is_staff=True,
        is_superuser=True,
    )
    manor_user = django_user_model.objects.create_user(
        username="guest_owner",
        password="pass123",
        email="guest_owner@test.local",
    )
    manor = ensure_manor(manor_user)
    template = GuestTemplate.objects.create(
        key="guest_admin_tpl",
        name="后台测试门客",
        archetype="military",
        rarity="purple",
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        custom_name="后台门客",
        level=20,
        force=160,
        intellect=120,
        defense_stat=110,
        agility=105,
    )

    client.force_login(admin_user)
    response = client.get(reverse("admin:guests_guest_change", args=[guest.pk]))

    assert response.status_code == 200


@pytest.mark.django_db
def test_work_assignment_admin_search_renders(client, django_user_model):
    admin_user = django_user_model.objects.create_user(
        username="work_admin",
        password="pass123",
        email="work_admin@test.local",
        is_staff=True,
        is_superuser=True,
    )
    manor_user = django_user_model.objects.create_user(
        username="work_owner",
        password="pass123",
        email="work_owner@test.local",
    )
    manor = ensure_manor(manor_user)
    template = GuestTemplate.objects.create(
        key="work_guest_tpl",
        name="打工门客",
        archetype="military",
        rarity="green",
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        custom_name="打工仔",
        level=15,
        force=120,
        intellect=95,
        defense_stat=100,
        agility=90,
    )
    work_template = WorkTemplate.objects.create(
        key="admin_work_tpl",
        name="巡街",
        tier=WorkTemplate.Tier.JUNIOR,
        required_level=1,
        reward_silver=100,
        work_duration=3600,
    )
    WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=work_template,
        complete_at=guest.created_at,
    )

    client.force_login(admin_user)
    response = client.get(f"{reverse('admin:gameplay_workassignment_changelist')}?q=打工")

    assert response.status_code == 200
