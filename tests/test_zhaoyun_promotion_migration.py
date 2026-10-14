from importlib import import_module
from types import SimpleNamespace

import pytest
from django.db import connections, transaction

from gameplay.models import ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate


@pytest.mark.django_db
def test_zhaoyun_promotion_preserves_allocations_and_rebases_owned_guest(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="zhaoyun_promotion"))
    template = GuestTemplate.objects.create(
        key="hist_sljnbc_0012",
        name="赵云",
        rarity="purple",
        archetype="military",
        base_attack=59,
        base_intellect=17,
        base_defense=51,
        base_agility=42,
        base_luck=62,
        base_hp=800,
        growth_range=[7, 12],
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=100,
        force=459,
        intellect=217,
        defense_stat=351,
        agility=341,
        luck=62,
        initial_force=59,
        initial_intellect=17,
        initial_defense=51,
        initial_agility=42,
        current_hp=18350,
    )
    scroll = ItemTemplate.objects.create(key="zhaoyun_guest_scroll", name="赵云招募卷轴", rarity="purple")
    models = {m.__name__: m for m in (GuestTemplate, Guest, RecruitmentCandidate, ItemTemplate)}
    apps = SimpleNamespace(get_model=lambda app, name: models[name])
    migration = import_module("guests.migrations.0084_promote_zhaoyun")
    with transaction.atomic():
        migration.promote_zhaoyun(apps, SimpleNamespace(connection=connections["default"]))
    guest.refresh_from_db()
    template.refresh_from_db()
    scroll.refresh_from_db()
    assert template.rarity == scroll.rarity == "orange"
    assert template.growth_range == [8, 14]
    assert (guest.force, guest.intellect, guest.defense_stat, guest.agility) == (523, 242, 391, 375)
    assert (guest.initial_force, guest.initial_intellect, guest.initial_defense, guest.initial_agility) == (
        64,
        18,
        55,
        46,
    )
    assert guest.luck == 85
    assert guest.level == 100
    assert guest.current_hp == 1000 + guest.defense_stat * 50
    assert "橙色" in scroll.description
