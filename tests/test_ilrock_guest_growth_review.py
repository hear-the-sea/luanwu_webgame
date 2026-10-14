import json
from importlib import import_module
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.core.management import call_command
from django.db import connections

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, RecruitmentCandidate, RecruitmentPool, RecruitmentPoolEntry

ROOT = Path(__file__).parents[1]
REPORT = json.loads((ROOT / "reports/guest_growth_review_ilrock_20261003.json").read_text())
ROWS = {row["key"]: row for row in REPORT["entries"]}
FIELDS = ("base_attack", "base_intellect", "base_defense", "base_agility", "base_luck", "base_hp")
ATTRIBUTES = ("force", "intellect", "defense_stat", "agility")
INITIALS = ("initial_force", "initial_intellect", "initial_defense", "initial_agility")
MILITARY = {"blue": (51, 15, 44, 37, 50, 600), "green": (41, 12, 35, 29, 47, 400)}
CIVIL = {"blue": (36, 40, 19, 42, 120, 600), "green": (29, 32, 15, 33, 112, 400)}


def _migration():
    return import_module("guests.migrations.0082_review_ilrock_guest_growth")


def _run():
    models = {model.__name__: model for model in (GuestTemplate, Guest, RecruitmentCandidate, RecruitmentPoolEntry)}
    _migration().apply_reviewed_growth(
        SimpleNamespace(get_model=lambda app, name: models[name]),
        SimpleNamespace(connection=connections["default"]),
    )


def _template(key, *, yaml_first=False):
    row = ROWS[key]
    metadata = row["after" if yaml_first else "before"]
    profiles = yaml.safe_load((ROOT / "data/guest_templates.yaml").read_text())["attribute_profiles"]
    stats = profiles[row["rarity"]][metadata["archetype"]]
    hp = {"green": 400, "blue": 600, "purple": 800, "orange": 1000}[row["rarity"]]
    return GuestTemplate.objects.create(
        key=key,
        name=row["name"],
        rarity=row["rarity"],
        archetype=metadata["archetype"],
        growth_range=metadata["growth_range"],
        attribute_weights=metadata["attribute_weights"],
        **dict(zip(FIELDS, [stats[field] for field in ("force", "intellect", "defense", "agility", "luck")] + [hp])),
    )


def _guest(manor, template, profile, hp_state):
    attributes = {field: profile[i] + (i + 1) * 10 for i, field in enumerate(ATTRIBUTES)}
    initials = {field: profile[i] + i + 1 for i, field in enumerate(INITIALS)}
    maximum = template.base_hp + 75 + attributes["defense_stat"] * 50
    hp = {"full": maximum, "wounded": maximum // 2, "zero": 0}[hp_state]
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=9,
        force=attributes.pop("force"),
        luck=profile[4] + 3,
        current_hp=hp,
        hp_bonus=75,
        allocated_force=7,
        allocated_defense=4,
        attribute_points=9,
        **attributes,
        **initials,
    )
    if hp_state == "zero":
        Guest.objects.filter(pk=guest.pk).update(current_hp=0)
    return guest, maximum, hp


def test_review_covers_the_exact_batch_and_keeps_total_growth_and_identity():
    manifest = json.loads((ROOT / "reports/guest_expansion_ilrock_20261002.json").read_text())
    assert len(REPORT["entries"]) == len(ROWS) == REPORT["reviewed_count"] == 200
    assert ROWS.keys() == {row["template_key"] for row in manifest["selected"]}
    changed = {row["key"]: row for row in ROWS.values() if row["changed"]}
    assert len(changed) == REPORT["changed_count"] == 119
    assert {row[0] for row in _migration().CHANGES} == changed.keys()
    for key, old_type, new_type, old_weights, new_weights in _migration().CHANGES:
        row = changed[key]
        assert (old_type, new_type) == (row["before"]["archetype"], row["after"]["archetype"])
        assert old_weights == tuple(row["before"]["attribute_weights"].values())
        assert new_weights == tuple(row["after"]["attribute_weights"].values())
    for row in ROWS.values():
        roster = yaml.safe_load((ROOT / "data/guests" / row["file"]).read_text())["heroes"]
        matches = [
            (rarity, entry) for rarity, entries in roster.items() for entry in entries if entry["key"] == row["key"]
        ]
        assert len(matches) == 1
        rarity, entry = matches[0]
        assert (rarity, entry["name"]) == (row["rarity"], row["name"])
        assert entry["growth_range"] == row["before"]["growth_range"] == row["after"]["growth_range"]
        assert entry["archetype"] == row["after"]["archetype"]
        assert entry["attribute_weights"] == row["after"]["attribute_weights"]
        assert row["reason"]
    assert (
        ROWS["hero_ilrock_04574"]["after"]["attribute_weights"]["agility"]
        > ROWS["hero_ilrock_04575"]["after"]["attribute_weights"]["agility"]
    )
    assert ROWS["hero_ilrock_04834"]["after"]["attribute_weights"]["agility"] == 50
    assert ROWS["hero_ilrock_04873"]["after"]["attribute_weights"]["agility"] == 46


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["hero_ilrock_03376", "hero_ilrock_04874"])
@pytest.mark.parametrize("yaml_first", [False, True])
@pytest.mark.parametrize("hp_state", ["full", "wounded", "zero"])
def test_type_changes_preserve_growth_allocations_health_and_conversion_descendants(
    django_user_model, key, yaml_first, hp_state
):
    manor = ensure_manor(django_user_model.objects.create_user(username="growth_review", password="pass"))
    source = _template(key, yaml_first=yaml_first)
    rarity = ROWS[key]["rarity"]
    old_profile, new_profile = MILITARY[rarity], CIVIL[rarity]
    pool = RecruitmentPool.objects.create(key="growth_review_pool", name="成长复审")
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=source,
        display_name=source.name,
        rarity=rarity,
        archetype="military",
    )
    entry = RecruitmentPoolEntry.objects.create(pool=pool, template=source, archetype="military")
    # Upgrade pills copy source stats/weights. Preserve converted rarities and
    # default growth ranges while propagating the reviewed intelligence type.
    previous = source
    templates = [source]
    for target_rarity in ("purple", "orange"):
        converted = GuestTemplate.objects.create(
            key=f"guest_rarity_conversion_{target_rarity}_{previous.pk}",
            name=source.name,
            rarity=target_rarity,
            archetype="military",
            growth_range=[],
            recruitable=False,
            attribute_weights=ROWS[key]["before"]["attribute_weights"],
            **dict(zip(FIELDS, old_profile)),
        )
        templates.append(converted)
        previous = converted
    guests = [_guest(manor, template, old_profile, hp_state) for template in templates]

    _run()

    for template, (guest, old_max, old_hp) in zip(templates, guests):
        template.refresh_from_db()
        guest.refresh_from_db()
        assert template.archetype == guest.archetype == "civil"
        assert template.attribute_weights == ROWS[key]["after"]["attribute_weights"]
        assert tuple(getattr(template, field) for field in FIELDS) == new_profile
        assert tuple(getattr(guest, field) for field in ATTRIBUTES) == tuple(
            new_profile[i] + (i + 1) * 10 for i in range(4)
        )
        assert tuple(getattr(guest, field) for field in INITIALS) == tuple(new_profile[i] + i + 1 for i in range(4))
        assert guest.luck == new_profile[4] + 3
        assert (
            guest.level,
            guest.hp_bonus,
            guest.allocated_force,
            guest.allocated_defense,
            guest.attribute_points,
        ) == (9, 75, 7, 4, 9)
        if hp_state == "zero":
            assert guest.current_hp == 0
        elif hp_state == "full":
            assert guest.current_hp == guest.max_hp
        else:
            assert guest.current_hp == old_hp * guest.max_hp // old_max
    for template, target_rarity in zip(templates[1:], ("purple", "orange")):
        assert template.rarity == target_rarity
        assert template.growth_range == []
        assert template.recruitable is False
    candidate.refresh_from_db()
    entry.refresh_from_db()
    assert candidate.archetype == entry.archetype == "civil"
    assert candidate.rarity == rarity


@pytest.mark.django_db
def test_distribution_only_review_never_rewrites_owned_stats_and_is_repeatable(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="growth_weights", password="pass"))
    source = _template("hero_ilrock_04574")
    guest, _, _ = _guest(manor, source, (59, 17, 51, 42, 62, 800), "wounded")
    before = Guest.objects.filter(pk=guest.pk).values().get()
    old_template = GuestTemplate.objects.filter(pk=source.pk).values().get()
    _run()
    _run()
    after_template = GuestTemplate.objects.filter(pk=source.pk).values().get()
    assert Guest.objects.filter(pk=guest.pk).values().get() == before
    assert {k: v for k, v in old_template.items() if k != "attribute_weights"} == {
        k: v for k, v in after_template.items() if k != "attribute_weights"
    }
    assert after_template["attribute_weights"] == ROWS[source.key]["after"]["attribute_weights"]


@pytest.mark.django_db
def test_batch_validation_rejects_unknown_configuration_before_any_updates():
    first = _template("hero_ilrock_04574")
    invalid = _template("hero_ilrock_03376")
    invalid.attribute_weights = {"force": 99, "intellect": 1}
    invalid.save(update_fields=["attribute_weights"])
    with pytest.raises(ValueError, match="Unexpected growth configuration"):
        _run()
    first.refresh_from_db()
    assert first.attribute_weights == ROWS[first.key]["before"]["attribute_weights"]


@pytest.mark.django_db
def test_full_batch_migration_and_default_import_agree_without_new_templates():
    for key in ROWS:
        _template(key)
    before = set(GuestTemplate.objects.values_list("key", flat=True))
    _run()
    assert set(GuestTemplate.objects.values_list("key", flat=True)) == before
    for template in GuestTemplate.objects.filter(key__in=ROWS):
        row = ROWS[template.key]
        assert template.attribute_weights == row["after"]["attribute_weights"]
        assert template.archetype == row["after"]["archetype"]
        assert template.growth_range == row["before"]["growth_range"]
        assert template.rarity == row["rarity"]
    call_command("load_guest_templates", skip_images=True, verbosity=0, stdout=StringIO())
    assert GuestTemplate.objects.filter(key__in=ROWS).count() == 200
    for template in GuestTemplate.objects.filter(key__in=ROWS):
        assert template.attribute_weights == ROWS[template.key]["after"]["attribute_weights"]
        assert template.archetype == ROWS[template.key]["after"]["archetype"]


@pytest.mark.django_db
def test_fresh_database_review_leaves_template_creation_to_importer():
    before = GuestTemplate.objects.count()
    _run()
    assert GuestTemplate.objects.count() == before
