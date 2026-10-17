from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.db import connections

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool

OLD_PROFILE = (59, 17, 51, 42, 62, 800)
NEW_PROFILES = {"朱棣": (64, 18, 55, 46, 85, 1000)}
NEW_ARCHETYPES = {"朱棣": "military"}
TEMPLATE_FIELDS = (
    "base_attack",
    "base_intellect",
    "base_defense",
    "base_agility",
    "base_luck",
    "base_hp",
)
ATTRIBUTES = ("force", "intellect", "defense_stat", "agility")
INITIALS = ("initial_force", "initial_intellect", "initial_defense", "initial_agility")
CASES = (("hist_mingqing_0002", "朱棣", [7, 12], [8, 14], (3, 1, 1, 1)),)


def _migration():
    return import_module("guests.migrations.0086_promote_zhudi")


def _run():
    models = {
        "GuestTemplate": GuestTemplate,
        "Guest": Guest,
        "RecruitmentCandidate": RecruitmentCandidate,
    }
    _migration().promote_zhudi(
        SimpleNamespace(get_model=lambda app, name: models[name]),
        SimpleNamespace(connection=connections["default"]),
    )


def _template(case, yaml_first=False):
    key, name, old_growth, new_growth, _ = case
    return GuestTemplate.objects.create(
        key=key,
        name=name,
        archetype=NEW_ARCHETYPES[name] if yaml_first else "military",
        rarity="orange" if yaml_first else "purple",
        growth_range=new_growth if yaml_first else old_growth,
        **dict(zip(TEMPLATE_FIELDS, NEW_PROFILES[name] if yaml_first else OLD_PROFILE)),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("case", CASES, ids=["zhudi"])
@pytest.mark.parametrize("yaml_first", [False, True])
@pytest.mark.parametrize("hp_state", ["full", "wounded", "zero"])
def test_promotion_preserves_investments_and_health(django_user_model, case, yaml_first, hp_state):
    manor = ensure_manor(django_user_model.objects.create_user(username="promotion", password="pass"))
    template = _template(case, yaml_first)
    new_profile = NEW_PROFILES[case[1]]
    # Recruitment variance, prior growth, equipment and player allocation are
    # all preserved by additive rebasing rather than resetting the attributes.
    attrs = {field: OLD_PROFILE[i] + (i + 1) * 10 for i, field in enumerate(ATTRIBUTES)}
    initials = {field: OLD_PROFILE[i] + i + 1 for i, field in enumerate(INITIALS)}
    old_max = template.base_hp + 75 + attrs["defense_stat"] * 50
    old_current = old_max if hp_state == "full" else old_max // 2
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=5,
        luck=65,
        hp_bonus=75,
        current_hp=old_current,
        allocated_force=7,
        allocated_defense=4,
        attribute_points=9,
        **attrs,
        **initials,
    )
    if hp_state == "zero":
        Guest.objects.filter(pk=guest.pk).update(current_hp=0)
    pool = RecruitmentPool.objects.create(key="promotion_pool", name="升阶测试")
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name=case[1],
        rarity="purple",
        archetype="military",
    )

    _run()

    guest.refresh_from_db()
    template.refresh_from_db()
    candidate.refresh_from_db()
    assert template.rarity == candidate.rarity == guest.rarity == "orange"
    assert tuple(getattr(template, field) for field in TEMPLATE_FIELDS) == new_profile
    assert template.growth_range == case[3]
    assert tuple(getattr(guest, field) for field in ATTRIBUTES) == tuple(
        new_profile[i] + (i + 1) * 10 + case[4][i] for i in range(4)
    )
    assert tuple(getattr(guest, field) for field in INITIALS) == tuple(new_profile[i] + i + 1 for i in range(4))
    assert guest.luck == new_profile[4] + 3
    assert guest.archetype == template.archetype == candidate.archetype == NEW_ARCHETYPES[case[1]]
    assert (
        guest.level,
        guest.allocated_force,
        guest.allocated_defense,
        guest.attribute_points,
        guest.hp_bonus,
    ) == (
        5,
        7,
        4,
        9,
        75,
    )
    expected_hp = {
        "zero": 0,
        "full": guest.max_hp,
        "wounded": old_current * guest.max_hp // old_max,
    }[hp_state]
    assert guest.current_hp == expected_hp
    assert candidate.template_id == template.pk


@pytest.mark.django_db
def test_unknown_profile_is_rejected_without_changes():
    template = _template(CASES[0])
    template.base_attack = 999
    template.save(update_fields=["base_attack"])
    with pytest.raises(ValueError, match="Unexpected template state"):
        _run()
    template.refresh_from_db()
    assert template.rarity == "purple"
    assert template.growth_range == [7, 12]


@pytest.mark.django_db
def test_fresh_database_leaves_loading_to_yaml():
    before = GuestTemplate.objects.count()
    _run()
    assert GuestTemplate.objects.count() == before


def test_zhudi_roster_matches_orange_peer_and_migration():
    path = Path(__file__).parents[1] / "data/guests/history_mingqing_01.yaml"
    heroes = yaml.safe_load(path.read_text())["heroes"]
    zhudi = next(entry for entry in heroes["orange"] if entry["key"] == "hist_mingqing_0002")
    peer = next(entry for entry in heroes["orange"] if entry["name"] == "朱元璋")
    assert zhudi["growth_range"] == peer["growth_range"] == list(_migration().NEW_GROWTH)
    assert tuple(zhudi["attribute_weights"].values()) == _migration().WEIGHTS
    assert zhudi["archetype"] == "military"
    assert not any(entry["key"] == zhudi["key"] for entry in heroes["purple"])
