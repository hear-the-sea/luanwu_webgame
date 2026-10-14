from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.db import connections

from gameplay.services.manor.core import ensure_manor
from guests.growth_engine import get_expected_growth
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool

OLD_PROFILE = (59, 17, 51, 42, 62, 800)
NEW_PROFILES = {"孙武": (45, 50, 24, 52, 165, 1000), "项羽": (64, 18, 55, 46, 85, 1000)}
NEW_ARCHETYPES = {"孙武": "civil", "项羽": "military"}
TEMPLATE_FIELDS = ("base_attack", "base_intellect", "base_defense", "base_agility", "base_luck", "base_hp")
ATTRIBUTES = ("force", "intellect", "defense_stat", "agility")
INITIALS = ("initial_force", "initial_intellect", "initial_defense", "initial_agility")
CASES = (
    ("hist_xianqin_0008", "孙武", [7, 11], [9, 14], (1, 6, 2, 1)),
    ("hist_qinhan_0023", "项羽", [7, 12], [8, 14], (3, 0, 2, 1)),
)


def _migration():
    return import_module("guests.migrations.0081_promote_sunwu_xiangyu")


def _run():
    models = {"GuestTemplate": GuestTemplate, "Guest": Guest, "RecruitmentCandidate": RecruitmentCandidate}
    _migration().promote_guests(
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
@pytest.mark.parametrize("case", CASES, ids=["sunwu", "xiangyu"])
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
    assert (guest.level, guest.allocated_force, guest.allocated_defense, guest.attribute_points, guest.hp_bonus) == (
        5,
        7,
        4,
        9,
        75,
    )
    expected_hp = {"zero": 0, "full": guest.max_hp, "wounded": old_current * guest.max_hp // old_max}[hp_state]
    assert guest.current_hp == expected_hp
    assert candidate.template_id == template.pk


@pytest.mark.django_db
def test_unknown_second_target_fails_before_changing_first():
    first = _template(CASES[0])
    second = _template(CASES[1])
    second.base_attack = 999
    second.save(update_fields=["base_attack"])
    with pytest.raises(ValueError, match="Unexpected template state for hist_qinhan_0023"):
        _run()
    first.refresh_from_db()
    assert first.rarity == "purple"
    assert first.growth_range == [7, 11]
    assert first.base_attack == 59


@pytest.mark.django_db
def test_fresh_database_promotion_leaves_template_loading_to_yaml():
    before = GuestTemplate.objects.count()
    _run()
    assert GuestTemplate.objects.count() == before


def test_roster_identity_and_growth_ranking_match_migration():
    records = {}
    for path in (Path(__file__).parents[1] / "data/guests").glob("*.yaml"):
        payload = yaml.safe_load(path.read_text()) or {}
        for rarity, entries in (payload.get("heroes") or {}).items():
            for entry in entries or []:
                if entry.get("name") in {"孙武", "项羽", "李世民", "铁木真", "诸葛亮"} and not entry["key"].startswith(
                    "task_"
                ):
                    assert entry["name"] not in records
                    records[entry["name"]] = (rarity, entry)
    expected_totals = {}
    expected_attributes = {}
    for promotion, case in zip(_migration().PROMOTIONS, CASES):
        rarity, entry = records[case[1]]
        assert rarity == "orange"
        assert entry["key"] == promotion["key"]
        assert entry["growth_range"] == list(promotion["new_growth"])
        assert entry["archetype"] == NEW_ARCHETYPES[case[1]]
        assert list(entry["attribute_weights"].values()) == list(promotion["weights"])
        expected = get_expected_growth(
            rarity, entry["archetype"], growth_range=entry["growth_range"], attribute_weights=entry["attribute_weights"]
        )
        expected_totals[case[1]] = sum(expected.values())
        expected_attributes[case[1]] = expected
    for name in ("李世民", "铁木真", "诸葛亮"):
        rarity, entry = records[name]
        assert rarity == "orange"
        expected = get_expected_growth(
            rarity, entry["archetype"], growth_range=entry["growth_range"], attribute_weights=entry["attribute_weights"]
        )
        expected_totals[name] = sum(expected.values())
        expected_attributes[name] = expected
    assert expected_totals["孙武"] == expected_totals["李世民"] == 11.5
    assert expected_totals["项羽"] == expected_totals["铁木真"] == 11
    # Equal total growth must still support a clear primary attack direction.
    sunwu = expected_attributes["孙武"]
    xiangyu = expected_attributes["项羽"]
    assert sunwu["intellect"] > expected_attributes["诸葛亮"]["intellect"]
    assert sunwu["intellect"] >= 5 * sunwu["force"]
    assert xiangyu["force"] > expected_attributes["铁木真"]["force"]
    assert xiangyu["force"] >= 5 * xiangyu["intellect"]
    assert sunwu["agility"] > xiangyu["agility"]
    assert sunwu["agility"] > expected_attributes["诸葛亮"]["agility"]
    assert xiangyu["defense"] > sunwu["defense"]
