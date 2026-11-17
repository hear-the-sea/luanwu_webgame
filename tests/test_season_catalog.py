from pathlib import Path

import pytest
import yaml
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from gameplay.services.season import catalog
from gameplay.services.season.campaign_rules import load_campaign_rules
from guests.models import Guest, GuestTemplate


def test_faction_sources_are_explicit_disjoint_and_exclude_other_rosters():
    history = catalog.faction_template_keys("history")
    fiction = catalog.faction_template_keys("fiction")
    assert "hist_qinhan_0001" in history
    assert "hero_yin_zhiping" in fiction
    assert not history & fiction
    excluded = set()
    for name in ("base.yaml", "cultivation.yaml", "original.yaml", "folk_legends.yaml"):
        data = yaml.safe_load((Path(settings.BASE_DIR) / "data" / "guests" / name).read_text())
        for entries in data.get("heroes", {}).values():
            excluded.update(entry["key"] for entry in entries)
    assert not (excluded - history) & fiction


def test_every_reward_and_shop_item_exists_in_real_item_catalog():
    data = yaml.safe_load((Path(settings.BASE_DIR) / "data" / "item_templates.yaml").read_text())
    actual_keys = {entry["key"] for entry in data["items"]}
    rules = catalog.load_rules()
    for faction in catalog.FACTIONS:
        for entry in catalog.reward_catalog(faction, rules) + catalog.shop_catalog(faction, rules):
            assert set(entry["items"]) <= actual_keys
    assert set(rules["summon_fallback_items"]) <= actual_keys
    for entry in load_campaign_rules()["market_control"]["pool"]:
        assert set(entry["items"]) <= actual_keys


def test_returned_rules_and_catalog_entries_cannot_mutate_cached_configuration():
    rules = catalog.load_rules()
    rules["daily_actions"] = 100
    assert catalog.load_rules()["daily_actions"] == 6
    rewards = catalog.reward_catalog("fiction")
    rewards[0]["items"].clear()
    assert catalog.reward_catalog("fiction")[0]["items"]
    shop = catalog.shop_catalog("history")
    shop[0]["cost"] = 1
    assert catalog.shop_catalog("history")[0]["cost"] == 150


@pytest.mark.parametrize("field,value", [("daily_actions", True), ("max_guests", 0), ("weekly_pvp_cap", -1)])
def test_invalid_action_and_contribution_limits_are_rejected(field, value):
    rules = catalog.load_rules()
    rules[field] = value
    with pytest.raises(ImproperlyConfigured):
        catalog.validate_rules(rules)


@pytest.mark.parametrize("source", ["../special.yaml", "cultivation.yaml", "folk_legends.yaml", {}])
def test_unapproved_roster_sources_are_rejected(source):
    rules = catalog.load_rules()
    rules["roster"]["fiction_files"] = [source]
    with pytest.raises(ImproperlyConfigured):
        catalog.validate_rules(rules)


def test_negative_prices_duplicate_rewards_and_unknown_factions_are_rejected():
    rules = catalog.load_rules()
    rules["shop"]["fiction"][0]["cost"] = -1
    with pytest.raises(ImproperlyConfigured):
        catalog.validate_rules(rules)
    rules = catalog.load_rules()
    rules["rewards"][1]["key"] = rules["rewards"][0]["key"]
    with pytest.raises(ImproperlyConfigured):
        catalog.validate_rules(rules)
    with pytest.raises(ValueError):
        catalog.faction_template_keys("other")


@pytest.mark.django_db
def test_summon_candidates_recheck_ownership_recruitability_and_special_flags(django_user_model, monkeypatch):
    manor = django_user_model.objects.create_user(username="season_catalog").manor
    keys = ["season_available_green", "season_owned", "season_closed", "season_hermit", "season_unique"]
    templates = [GuestTemplate.objects.create(key=key, name=key, rarity="green", archetype="military") for key in keys]
    templates[2].recruitable = False
    templates[2].save(update_fields=["recruitable"])
    templates[3].is_hermit = True
    templates[3].save(update_fields=["is_hermit"])
    templates[4].is_world_unique = True
    templates[4].save(update_fields=["is_world_unique"])
    Guest.objects.create(manor=manor, template=templates[1])
    monkeypatch.setattr(catalog, "faction_template_keys", lambda faction, rules=None: frozenset(keys))
    assert [entry.key for entry in catalog.available_summon_templates(manor, "fiction")] == [keys[0]]


@pytest.mark.django_db
def test_summon_candidates_exclude_owned_upgraded_identity(django_user_model, monkeypatch):
    manor = django_user_model.objects.create_user(username="season_catalog_upgraded").manor
    base, _ = GuestTemplate.objects.update_or_create(
        key="hist_sljnbc_0589",
        defaults={"name": "邢道荣", "rarity": "green", "archetype": "military", "recruitable": True},
    )
    upgraded, _ = GuestTemplate.objects.update_or_create(
        key="hist_sljnbc_0589_purple",
        defaults={"name": "邢道荣", "rarity": "purple", "archetype": "military", "recruitable": False},
    )
    Guest.objects.create(manor=manor, template=upgraded)
    monkeypatch.setattr(catalog, "faction_template_keys", lambda faction, rules=None: frozenset({base.key}))
    assert catalog.available_summon_templates(manor, "history") == []


def test_faction_source_selection_uses_rules_snapshot_instead_of_current_configuration():
    rules = catalog.load_rules()
    rules["roster"]["fiction_files"] = ["jinyong.yaml"]
    jinyong_keys = catalog.faction_template_keys("fiction", rules=rules)
    assert "hero_yin_zhiping" in jinyong_keys
    assert jinyong_keys < catalog.faction_template_keys("fiction")
    other_rules = catalog.load_rules()
    other_rules["roster"]["fiction_files"] = ["gulong.yaml"]
    assert "hero_yin_zhiping" not in catalog.faction_template_keys("fiction", rules=other_rules)
    assert catalog.faction_template_keys("fiction", rules=rules) == jinyong_keys


@pytest.mark.django_db
def test_existing_season_freezes_roster_once_even_with_stale_instances(monkeypatch):
    from datetime import timedelta

    from django.db import transaction
    from django.utils import timezone

    from gameplay.models.season import Season

    now = timezone.now()
    season = Season.objects.create(
        name="旧赛季", starts_at=now, ends_at=now + timedelta(days=90), rules=catalog.load_rules()
    )
    stale = Season.objects.get(pk=season.pk)
    initial = {"fiction": ["hero_a"], "history": ["hist_a"]}
    monkeypatch.setattr(catalog, "build_faction_roster_snapshot", lambda rules: initial)
    with transaction.atomic():
        catalog.ensure_faction_roster_snapshot_locked(season)
    monkeypatch.setattr(
        catalog, "build_faction_roster_snapshot", lambda rules: {"fiction": ["hero_b"], "history": ["hist_b"]}
    )
    with transaction.atomic():
        catalog.ensure_faction_roster_snapshot_locked(stale)
    season.refresh_from_db()
    assert season.rules["faction_roster_keys"] == initial

    def reject_source_read(*args):
        raise AssertionError("冻结后不应读取当前人物来源")

    monkeypatch.setattr(catalog, "_faction_keys_at", reject_source_read)
    assert catalog.faction_template_keys("fiction", rules=stale.rules) == {"hero_a"}
    assert catalog.faction_template_keys("history", rules=stale.rules) == {"hist_a"}


@pytest.mark.django_db
def test_catalog_presence_distinguishes_missing_imports_from_full_collection(django_user_model, monkeypatch):
    manor = django_user_model.objects.create_user(username="season_catalog_presence").manor
    rules = catalog.load_rules()
    requested_rules = []

    def keys(faction, rules=None):
        requested_rules.append(rules)
        return frozenset({"season_catalog_presence"})

    monkeypatch.setattr(catalog, "faction_template_keys", keys)
    assert not catalog.has_summon_catalog("fiction", rules=rules)
    template = GuestTemplate.objects.create(
        key="season_catalog_presence", name="在野人物", rarity="green", archetype="military", recruitable=False
    )
    assert not catalog.has_summon_catalog("fiction", rules=rules)
    template.recruitable = True
    template.save(update_fields=["recruitable"])
    Guest.objects.create(manor=manor, template=template)
    assert catalog.has_summon_catalog("fiction", rules=rules)
    assert catalog.available_summon_templates(manor, "fiction", rules=rules) == []
    assert requested_rules and all(value is rules for value in requested_rules)
