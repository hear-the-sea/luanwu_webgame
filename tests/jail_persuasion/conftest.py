from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, JailPrisoner, Manor
from guests.models import Guest, GuestTemplate


@pytest.fixture
def persuasion_world(django_user_model):
    captor_user = django_user_model.objects.create_user(username="persuasion_world_captor")
    original_user = django_user_model.objects.create_user(username="persuasion_world_original")
    captor = Manor.objects.get(user=captor_user)
    original = Manor.objects.get(user=original_user)
    captor.name = "招降庄园"
    captor.silver = 200_000
    captor.grain = 20_000
    captor.silver_capacity = 500_000
    captor.grain_capacity = 100_000
    captor.resource_updated_at = timezone.now()
    captor.save(
        update_fields=[
            "name",
            "silver",
            "grain",
            "silver_capacity",
            "grain_capacity",
            "resource_updated_at",
        ]
    )
    original.name = "原属庄园"
    original.save(update_fields=["name"])

    prisoner_template = GuestTemplate.objects.create(
        key="persuasion_world_prisoner",
        name="待招门客",
        rarity="green",
        archetype="civil",
        default_morality=70,
        base_attack=100,
        base_intellect=100,
    )
    prisoner = JailPrisoner.objects.create(
        captor=captor,
        original_manor=original,
        guest_template=prisoner_template,
        original_guest_name="阶下之客",
        original_level=20,
        loyalty=80,
        captured_loyalty=80,
    )

    def create_speaker(key, name, archetype, attack, intellect, loyalty=70):
        template = GuestTemplate.objects.create(
            key=key,
            name=name,
            rarity="gray",
            archetype=archetype,
            base_attack=attack,
            base_intellect=intellect,
        )
        return Guest.objects.create(
            manor=captor,
            template=template,
            custom_name=name,
            loyalty=loyalty,
        )

    strong_civil = create_speaker("persuasion_strong_civil", "纵横客", "civil", 80, 130)
    failed_civil = create_speaker("persuasion_failed_civil", "年轻辩士", "civil", 80, 80)
    weak_civil = create_speaker("persuasion_weak_civil", "生涩辩士", "civil", 50, 50)
    strong_military = create_speaker("persuasion_strong_military", "骁将", "military", 130, 80)
    weak_military = create_speaker("persuasion_weak_military", "新卒", "military", 50, 50)

    gold_template, _created = ItemTemplate.objects.get_or_create(
        key="gold_bar",
        defaults={"name": "金条", "effect_type": ItemTemplate.EffectType.TOOL},
    )
    InventoryItem.objects.create(
        manor=captor,
        template=gold_template,
        quantity=10,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    return SimpleNamespace(
        captor=captor,
        original=original,
        prisoner=prisoner,
        prisoner_template=prisoner_template,
        strong_civil=strong_civil,
        failed_civil=failed_civil,
        weak_civil=weak_civil,
        strong_military=strong_military,
        weak_military=weak_military,
        gold_template=gold_template,
    )
