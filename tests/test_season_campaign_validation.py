from copy import deepcopy
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from gameplay.services.season.campaign_rules import coalition_rules, load_campaign_rules, validate_campaign_rules
from gameplay.services.season.catalog import load_rules, validate_rules


@pytest.mark.parametrize(
    "path,value",
    [
        (("expeditions", "travel_seconds", "mountain:inn"), None),
        (("economy", "technology_keys"), ["not_a_technology"]),
        (("economy", "pve", "unknown:place"), {"resources": {"grain": 1}, "merit": 1, "failure_merit": 0}),
        (("sites", "river:town", "name"), ""),
        (("sites", "river:town", "unlock_day"), 90),
        (("expeditions", "enemy_levels", "mountain:inn"), None),
        (("registration", "days"), 14),
    ],
)
def test_invalid_campaign_references_are_rejected_before_opening(path, value):
    config = deepcopy(load_campaign_rules())
    target = config
    for key in path[:-1]:
        target = target[key]
    if value is None:
        target.pop(path[-1])
    else:
        target[path[-1]] = value
    if path == ("registration", "days"):
        config["expeditions"]["combat_days"] = 14
        config["economy"]["level_unlock_days"] = [1, 2, 3, 4, 5, 6]
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


def test_unknown_troop_and_missing_reward_label_are_rejected():
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    for field in ("initial_troops", "troop_caps", "troop_rates"):
        config["economy"][field]["unknown_army"] = 1
    with pytest.raises(ImproperlyConfigured, match="兵种"):
        validate_campaign_rules(config)
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["rewards"]["tiers"][0].pop("name")
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


@pytest.mark.parametrize("count", [0, True, 11, "3"])
def test_invalid_summon_pick_count_is_rejected(count):
    rules = load_rules()
    rules["summon_pick_count"] = count
    with pytest.raises(ImproperlyConfigured):
        validate_rules(rules)


@pytest.mark.parametrize("node", ["river:market", "river:warehouse"])
def test_market_allows_contesting_but_warehouse_and_both_pve_remain_invalid(node):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["sites"][node] = deepcopy(config["sites"]["river:town"])
    config["expeditions"]["enemy_levels"][node] = 1
    if node == "river:market":
        validate_campaign_rules(config)
    else:
        with pytest.raises(ImproperlyConfigured):
            validate_campaign_rules(config)
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["economy"]["pve"][node] = deepcopy(config["economy"]["pve"]["river:docks"])
    config["expeditions"]["enemy_levels"][node] = 1
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


@pytest.mark.parametrize("section", ["sites", "pve"])
def test_transport_docks_configuration_cannot_be_removed(section):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    (config["sites"] if section == "sites" else config["economy"]["pve"]).pop("river:docks")
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


@pytest.mark.parametrize("field,value", [("site_key", "river:warehouse"), ("rate", 0), ("rate", True)])
def test_special_army_requires_contested_site_and_positive_rate(field, value):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["economy"]["special_troops"]["bloodthirsty_cavalry"][field] = value
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


def test_special_army_cannot_be_granted_without_capturing_building():
    for field in ("initial_troops", "troop_rates"):
        config = load_campaign_rules()
        config["simplification"]["enabled"] = False
        config["economy"][field]["bloodthirsty_cavalry"] = 1
        with pytest.raises(ImproperlyConfigured):
            validate_campaign_rules(config)


@pytest.mark.parametrize(
    "thresholds",
    [
        [],
        [0, 1000],
        [1, 1000, 3000, 6000, 10000, 15000],
        [0, 1000, 1000, 6000, 10000, 15000],
        [0, -1, 3000, 6000, 10000, 15000],
        [0, True, 3000, 6000, 10000, 15000],
        [0, "1000", 3000, 6000, 10000, 15000],
    ],
)
def test_auto_development_rejects_invalid_level_thresholds(thresholds):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["economy"]["auto_development"]["level_thresholds"] = thresholds
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


@pytest.mark.parametrize("points", [-1, True, "20", 1000001])
def test_auto_development_rejects_invalid_activity_points(points):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["economy"]["auto_development"]["activity_points"]["pve_win"] = points
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


@pytest.mark.parametrize("key", ["pve_win", "unknown_activity"])
def test_auto_development_requires_complete_supported_activity_mapping(key):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    activity = config["economy"]["auto_development"]["activity_points"]
    if key == "unknown_activity":
        activity[key] = 1
    else:
        activity.pop(key)
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


def test_old_snapshot_without_auto_development_remains_valid():
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["economy"].pop("auto_development")
    assert validate_campaign_rules(config) is config


@pytest.mark.parametrize(
    "key,value",
    [
        ("enabled", 1),
        ("enabled", "true"),
        ("rally_seconds", 0),
        ("rally_seconds", True),
        ("rally_seconds", 86401),
        ("min_members", 0),
        ("max_members", 0),
        ("max_members", 6),
        ("max_members", "20"),
        ("frontline_capacity", 0),
        ("frontline_capacity", 100001),
        ("commander_guest_limit", 0),
        ("commander_guest_limit", 4),
        ("reinforcement_interval_seconds", 0),
        ("reinforcement_interval_seconds", 86401),
        ("reinforcement_per_tick", 0),
        ("reinforcement_per_tick", 3001),
        ("wave_interval_seconds", 0),
        ("wave_interval_seconds", True),
        ("wave_interval_seconds", 86401),
        ("max_waves", 0),
        ("max_waves", True),
        ("max_waves", 1001),
    ],
)
def test_coalition_rejects_invalid_limits(key, value):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["coalition"][key] = value
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


def test_coalition_requires_valid_member_range_and_complete_parameters():
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config["coalition"]["min_members"] = 5
    config["coalition"]["max_members"] = 4
    with pytest.raises(ImproperlyConfigured, match="人数上限"):
        validate_campaign_rules(config)
    for mutation in ("missing", "unknown"):
        config = load_campaign_rules()
        config["simplification"]["enabled"] = False
        if mutation == "missing":
            config["coalition"].pop("frontline_capacity")
        else:
            config["coalition"]["unlimited"] = True
        with pytest.raises(ImproperlyConfigured):
            validate_campaign_rules(config)


def test_new_coalitions_are_enabled_and_old_snapshots_remain_disabled():
    assert coalition_rules()["enabled"] is True
    assert coalition_rules(SimpleNamespace(rules={}))["enabled"] is False
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    config.pop("coalition")
    old_season = SimpleNamespace(rules={"campaign": config})
    first = coalition_rules(old_season)
    assert first["enabled"] is False
    assert first["frontline_capacity"] == 3000
    first["frontline_capacity"] = 1
    assert coalition_rules(old_season)["frontline_capacity"] == 3000
    config["coalition"] = deepcopy(coalition_rules())
    current_season = SimpleNamespace(rules={"campaign": config})
    current = coalition_rules(current_season)
    current["min_members"] = 5
    assert coalition_rules(current_season)["min_members"] == 1


def test_rally_defaults_allow_solo_timeout_and_cap_waves_at_five_players_per_side():
    rules = load_campaign_rules()
    coalition = coalition_rules()
    assert coalition["rally_seconds"] == 900
    assert coalition["min_members"] == 1
    assert coalition["max_members"] == 5
    assert coalition["commander_guest_limit"] == 3
    assert coalition["wave_interval_seconds"] == 60
    assert coalition["max_waves"] == 50
    assert rules["expeditions"]["garrison_capacity"] == 100
    solo = deepcopy(rules)
    solo["coalition"]["max_members"] = 1
    assert validate_campaign_rules(solo) is solo


@pytest.mark.parametrize(
    "section,path,value",
    [
        ("population", ("hotspot_multiplier",), 0),
        ("population", ("tiers", 0, "rate"), float("nan")),
        ("population", ("tiers", 1, "maximum"), 1),
        ("activities", ("intercept", "loot_percent"), 101),
        ("activities", ("transport", "npc_reserve_percent"), 101),
        ("contribution", ("damage_weight",), 99),
        ("contribution", ("daily_cap",), 0),
    ],
)
def test_development_rules_reject_invalid_balance_values(section, path, value):
    config = load_campaign_rules()
    config["simplification"]["enabled"] = False
    target = config[section]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ImproperlyConfigured, match=section):
        validate_campaign_rules(config)
