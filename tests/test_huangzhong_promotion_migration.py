from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.db import connections

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool

OLD_PROFILE = (51, 15, 44, 37, 50, 600)
NEW_PROFILE = (59, 17, 51, 42, 62, 800)
TEMPLATE_FIELDS = ("base_attack", "base_intellect", "base_defense", "base_agility", "base_luck", "base_hp")
ATTRIBUTE_FIELDS = ("force", "intellect", "defense_stat", "agility")
INITIAL_FIELDS = ("initial_force", "initial_intellect", "initial_defense", "initial_agility")
EXPECTED_WEIGHTS = {"force": 42, "intellect": 10, "defense": 20, "agility": 28}


def _run_promotion():
    migration = import_module("guests.migrations.0079_promote_huangzhong")
    models = {"GuestTemplate": GuestTemplate, "Guest": Guest, "RecruitmentCandidate": RecruitmentCandidate}
    apps = SimpleNamespace(get_model=lambda _app_label, model_name: models[model_name])
    schema_editor = SimpleNamespace(connection=connections["default"])
    migration.promote_huangzhong(apps, schema_editor)


def _create_template(*, key, rarity, profile, growth_range):
    return GuestTemplate.objects.create(
        key=key,
        name="黄忠",
        archetype="military",
        rarity=rarity,
        growth_range=growth_range,
        **dict(zip(TEMPLATE_FIELDS, profile)),
    )


def _create_guest(manor, template, *, profile, hp_state):
    # Distinct existing rolls and allocations must survive the promotion.
    attrs = {field: profile[index] + (index + 1) * 10 for index, field in enumerate(ATTRIBUTE_FIELDS)}
    initials = {field: profile[index] + index + 1 for index, field in enumerate(INITIAL_FIELDS)}
    old_max_hp = template.base_hp + 75 + attrs["defense_stat"] * 50
    current_hp = {"full": old_max_hp, "wounded": old_max_hp // 2, "zero": 0}[hp_state]
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=5,
        luck=profile[4] + 3,
        hp_bonus=75,
        current_hp=current_hp,
        allocated_force=7,
        allocated_defense=4,
        attribute_points=9,
        **attrs,
        **initials,
    )
    if hp_state == "zero":
        # New guests initialize zero HP to full; persist a defeated existing
        # guest directly so the migration receives a genuine zero-HP row.
        Guest.objects.filter(pk=guest.pk).update(current_hp=0)
        guest.current_hp = 0
    return guest, old_max_hp, current_hp


def _assert_promoted_template(template):
    assert template.rarity == "purple"
    assert tuple(getattr(template, field) for field in TEMPLATE_FIELDS) == NEW_PROFILE
    assert template.growth_range == [6, 12]
    assert template.attribute_weights == EXPECTED_WEIGHTS


def _assert_guest(guest, *, growth_deltas, hp_state, old_max_hp, old_current_hp):
    guest.refresh_from_db()
    guest.template.refresh_from_db()
    _assert_promoted_template(guest.template)
    assert tuple(getattr(guest, field) for field in ATTRIBUTE_FIELDS) == tuple(
        NEW_PROFILE[index] + (index + 1) * 10 + growth_deltas[index] for index in range(4)
    )
    assert tuple(getattr(guest, field) for field in INITIAL_FIELDS) == tuple(
        NEW_PROFILE[index] + index + 1 for index in range(4)
    )
    assert guest.luck == NEW_PROFILE[4] + 3
    assert guest.level == 5
    assert guest.hp_bonus == 75
    assert guest.allocated_force == 7
    assert guest.allocated_defense == 4
    assert guest.attribute_points == 9
    if hp_state == "full":
        assert guest.current_hp == guest.max_hp
    elif hp_state == "zero":
        assert guest.current_hp == 0
    else:
        assert guest.current_hp == old_current_hp * guest.max_hp // old_max_hp


@pytest.mark.django_db
@pytest.mark.parametrize("hp_state", ["full", "wounded", "zero"])
@pytest.mark.parametrize("template_state", ["blue", "previous_purple", "yaml_purple"])
def test_huangzhong_promotion_preserves_owned_rolls_and_updates_candidates(django_user_model, template_state, hp_state):
    manor = ensure_manor(django_user_model.objects.create_user(username="huangzhong_original", password="pass"))
    profile = OLD_PROFILE if template_state == "blue" else NEW_PROFILE
    growth_range = {"blue": [6, 9], "previous_purple": [6, 11], "yaml_purple": [6, 12]}[template_state]
    template = _create_template(
        key="hist_sljnbc_0059",
        rarity="blue" if template_state == "blue" else "purple",
        profile=profile,
        growth_range=growth_range,
    )
    # Importing YAML changes template HP and scales current HP, but does not
    # rebase owned attributes; the migration must perform that exactly once.
    guest, old_max_hp, old_current_hp = _create_guest(manor, template, profile=OLD_PROFILE, hp_state=hp_state)
    pool = RecruitmentPool.objects.create(key="huangzhong_promotion_pool", name="升阶测试")
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="黄忠",
        rarity="blue",
        archetype="military",
    )

    _run_promotion()

    # Four level-ups gain six extra expected points: 2 force, 1 intellect,
    # 1 defense, and 2 agility according to the frozen attribute weights.
    _assert_guest(
        guest,
        growth_deltas=(2, 1, 1, 2),
        hp_state=hp_state,
        old_max_hp=old_max_hp,
        old_current_hp=old_current_hp,
    )
    candidate.refresh_from_db()
    assert candidate.rarity == "purple"
    assert candidate.template_id == template.pk


@pytest.mark.django_db
@pytest.mark.parametrize("hp_state", ["full", "wounded", "zero"])
@pytest.mark.parametrize("converted_state", ["old_default", "old_explicit", "new_default", "already_current"])
def test_huangzhong_promotion_updates_existing_conversion_without_duplicate_growth(
    django_user_model, converted_state, hp_state
):
    manor = ensure_manor(django_user_model.objects.create_user(username="huangzhong_converted", password="pass"))
    source = _create_template(key="hist_sljnbc_0059", rarity="blue", profile=OLD_PROFILE, growth_range=[6, 9])
    old_base = converted_state in {"old_default", "old_explicit"}
    profile = OLD_PROFILE if old_base else NEW_PROFILE
    converted_range = {
        "old_default": [],
        "old_explicit": [6, 11],
        "new_default": [6, 11],
        "already_current": [6, 12],
    }[converted_state]
    converted = _create_template(
        key=f"guest_rarity_conversion_purple_{source.pk}",
        rarity="purple",
        profile=profile,
        growth_range=converted_range,
    )
    converted.recruitable = False
    converted.save(update_fields=["recruitable"])
    guest, old_max_hp, old_current_hp = _create_guest(manor, converted, profile=profile, hp_state=hp_state)

    _run_promotion()

    # Converted guests already grew as purple (6-11), so only two expected
    # points are due at level 5. A previously synced 6-12 template adds none.
    _assert_guest(
        guest,
        growth_deltas=(0, 0, 0, 0) if converted_state == "already_current" else (1, 0, 0, 1),
        hp_state=hp_state,
        old_max_hp=old_max_hp,
        old_current_hp=old_current_hp,
    )
    source.refresh_from_db()
    _assert_promoted_template(source)
    assert guest.template.recruitable is False


@pytest.mark.django_db
def test_huangzhong_promotion_is_safe_before_new_database_loads_templates():
    assert not GuestTemplate.objects.filter(key="hist_sljnbc_0059").exists()
    before_count = GuestTemplate.objects.count()

    _run_promotion()

    assert GuestTemplate.objects.count() == before_count
    assert not GuestTemplate.objects.filter(key="hist_sljnbc_0059").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_field,invalid_value", [("growth_range", [6, 13]), ("base_attack", 999)])
def test_huangzhong_promotion_rejects_unknown_source_state_without_mutation(invalid_field, invalid_value):
    template = _create_template(key="hist_sljnbc_0059", rarity="blue", profile=OLD_PROFILE, growth_range=[6, 9])
    setattr(template, invalid_field, invalid_value)
    template.save(update_fields=[invalid_field])

    with pytest.raises(ValueError, match="Unexpected template state for hist_sljnbc_0059"):
        _run_promotion()

    template.refresh_from_db()
    assert template.rarity == "blue"
    assert getattr(template, invalid_field) == invalid_value
