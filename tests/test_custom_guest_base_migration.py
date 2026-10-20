from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, transaction

from gameplay.services.manor.core import ensure_manor
from guests.models import CustomGuestProgress, Guest, GuestTemplate

rules = import_module("gameplay.migrations.0203_custom_guest_orange_civil_base")


@pytest.mark.django_db
def test_base_update_preserves_growth_equipment_allocation_and_is_idempotent(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="custom_base_update"))
    template = GuestTemplate.objects.get(key="custom_guest_green")
    GuestTemplate.objects.filter(pk=template.pk).update(
        base_attack=30, base_intellect=30, base_defense=30, base_agility=30, base_luck=100
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=40,
        force=130,
        intellect=230,
        defense_stat=80,
        agility=330,
        luck=140,
        initial_force=30,
        initial_intellect=30,
        initial_defense=30,
        initial_agility=30,
        allocated_force=10,
        allocated_intellect=20,
        attribute_points=7,
        hp_bonus=100,
        current_hp=4500,
        custom_name="保留养成",
    )
    progress, _created = CustomGuestProgress.objects.update_or_create(
        manor=manor,
        defaults={
            "guest": guest,
            "card_issued": True,
            "weights": {"force": 3500, "intellect": 500, "defense": 500, "agility": 500},
            "failed_attempts": 2,
        },
    )
    for _ in range(2):
        with transaction.atomic():
            rules.update_custom_guest_base(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    template.refresh_from_db()
    progress.refresh_from_db()
    assert (guest.force, guest.intellect, guest.defense_stat, guest.agility, guest.luck) == (145, 250, 74, 352, 205)
    assert (guest.initial_force, guest.initial_intellect, guest.initial_defense, guest.initial_agility) == (
        45,
        50,
        24,
        52,
    )
    assert (guest.level, guest.allocated_force, guest.allocated_intellect, guest.attribute_points) == (40, 10, 20, 7)
    assert guest.hp_bonus == 100 and guest.current_hp == 4200
    assert guest.custom_name == "保留养成"
    assert progress.weights["force"] == 3500 and progress.failed_attempts == 2
    assert template.base_hp == 400 and template.archetype == "military"
    assert template.base_luck == 165
