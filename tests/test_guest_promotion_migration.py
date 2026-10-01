from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.db import connections

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool


@pytest.mark.django_db
@pytest.mark.parametrize("yaml_imported_first", [False, True])
def test_dianwei_promotion_keeps_owned_guests_full_hp_and_updates_candidates(django_user_model, yaml_imported_first):
    manor = ensure_manor(
        django_user_model.objects.create_user(username=f"dianwei_promotion_{yaml_imported_first}", password="pass")
    )
    old_profile = (51, 15, 44, 37, 50, 600)
    new_profile = (59, 17, 51, 42, 62, 800)
    template_profile = new_profile if yaml_imported_first else old_profile
    template = GuestTemplate.objects.create(
        key="hist_sljnbc_0068",
        name="典韦",
        archetype="military",
        rarity="purple" if yaml_imported_first else "blue",
        base_attack=template_profile[0],
        base_intellect=template_profile[1],
        base_defense=template_profile[2],
        base_agility=template_profile[3],
        base_luck=template_profile[4],
        base_hp=template_profile[5],
        growth_range=[6, 11] if yaml_imported_first else [6, 9],
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=3,
        force=old_profile[0],
        intellect=old_profile[1],
        defense_stat=old_profile[2],
        agility=old_profile[3],
        luck=old_profile[4],
        initial_force=old_profile[0],
        initial_intellect=old_profile[1],
        initial_defense=old_profile[2],
        initial_agility=old_profile[3],
        current_hp=template_profile[5] + old_profile[2] * 50,
    )
    converted_template = GuestTemplate.objects.create(
        key=f"guest_rarity_conversion_purple_{template.pk}",
        name="典韦升阶",
        archetype="military",
        rarity="purple",
        base_attack=old_profile[0],
        base_intellect=old_profile[1],
        base_defense=old_profile[2],
        base_agility=old_profile[3],
        base_luck=old_profile[4],
        base_hp=old_profile[5],
        growth_range=[],
        recruitable=False,
    )
    converted_guest = Guest.objects.create(
        manor=manor,
        template=converted_template,
        force=old_profile[0],
        intellect=old_profile[1],
        defense_stat=old_profile[2],
        agility=old_profile[3],
        luck=old_profile[4],
        initial_force=old_profile[0],
        initial_intellect=old_profile[1],
        initial_defense=old_profile[2],
        initial_agility=old_profile[3],
        current_hp=old_profile[5] + old_profile[2] * 50,
    )
    pool = RecruitmentPool.objects.create(key="dianwei_promotion_pool", name="升阶测试")
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="典韦",
        rarity="blue",
        archetype="military",
    )

    promotion = import_module("guests.migrations.0078_promote_dianwei_baiqi")
    models = {"GuestTemplate": GuestTemplate, "Guest": Guest, "RecruitmentCandidate": RecruitmentCandidate}
    apps = SimpleNamespace(get_model=lambda _app_label, model_name: models[model_name])
    schema_editor = SimpleNamespace(connection=connections["default"])
    promotion.promote_guests(apps, schema_editor)

    guest = Guest.objects.select_related("template").get(pk=guest.pk)
    converted_guest = Guest.objects.select_related("template").get(pk=converted_guest.pk)
    candidate.refresh_from_db()

    assert guest.template.rarity == "purple"
    assert guest.template.base_attack == 59
    assert guest.template.growth_range == [6, 11]
    assert guest.initial_force == 59
    assert guest.initial_defense == 51
    assert guest.force == 60
    assert guest.defense_stat == 52
    assert guest.current_hp == guest.max_hp == 3400
    assert converted_guest.template.base_hp == 800
    assert converted_guest.initial_force == 59
    assert converted_guest.current_hp == converted_guest.max_hp
    assert candidate.rarity == "purple"


@pytest.mark.django_db
def test_baiqi_promotion_scales_wounded_hp_and_updates_rarity(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="baiqi_promotion", password="pass"))
    template = GuestTemplate.objects.create(
        key="hist_xianqin_0031",
        name="白起",
        archetype="military",
        rarity="purple",
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
        force=59,
        intellect=17,
        defense_stat=51,
        agility=42,
        luck=62,
        initial_force=59,
        initial_intellect=17,
        initial_defense=51,
        initial_agility=42,
        current_hp=1675,
    )

    promotion = import_module("guests.migrations.0078_promote_dianwei_baiqi")
    models = {"GuestTemplate": GuestTemplate, "Guest": Guest, "RecruitmentCandidate": RecruitmentCandidate}
    apps = SimpleNamespace(get_model=lambda _app_label, model_name: models[model_name])
    schema_editor = SimpleNamespace(connection=connections["default"])
    promotion.promote_guests(apps, schema_editor)

    guest = Guest.objects.select_related("template").get(pk=guest.pk)
    assert guest.template.rarity == "orange"
    assert guest.template.growth_range == [7, 13]
    assert guest.initial_force == guest.force == 64
    assert guest.initial_defense == guest.defense_stat == 55
    assert guest.max_hp == 3750
    assert guest.current_hp == 1875
