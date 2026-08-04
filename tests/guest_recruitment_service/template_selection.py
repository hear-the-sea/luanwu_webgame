from __future__ import annotations

import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import guests.services.recruitment_queries as recruitment_query_service
import guests.services.recruitment_shared as recruitment_shared
import guests.services.recruitment_templates as recruitment_template_service
import guests.utils.recruitment_utils as recruitment_utils
from guests.models import GuestRarity
from guests.utils.recruitment_utils import HERMIT_RARITY, RARITY_DISTRIBUTION, RARITY_WEIGHTS, TOTAL_WEIGHT


def test_get_excluded_template_ids_excludes_green_and_above():
    manor = MagicMock()
    manor.guests.values_list.return_value = [
        (1, GuestRarity.GREEN, False),
        (2, GuestRarity.BLUE, False),
        (3, GuestRarity.GRAY, False),
    ]

    excluded = recruitment_query_service.get_excluded_template_ids(manor)

    assert 1 in excluded
    assert 2 in excluded
    assert 3 not in excluded


def test_get_excluded_template_ids_excludes_black_hermit():
    manor = MagicMock()
    manor.guests.values_list.return_value = [
        (1, GuestRarity.BLACK, True),
        (2, GuestRarity.BLACK, False),
    ]

    excluded = recruitment_query_service.get_excluded_template_ids(manor)

    assert 1 in excluded
    assert 2 not in excluded


def test_get_excluded_template_ids_empty_when_no_guests():
    manor = MagicMock()
    manor.guests.values_list.return_value = []

    excluded = recruitment_query_service.get_excluded_template_ids(manor)

    assert excluded == set()


def test_non_repeatable_rarities_includes_expected():
    expected = {
        GuestRarity.GREEN,
        GuestRarity.BLUE,
        GuestRarity.RED,
        GuestRarity.PURPLE,
        GuestRarity.ORANGE,
    }
    assert recruitment_shared.NON_REPEATABLE_RARITIES == expected


def test_non_repeatable_rarities_excludes_gray_and_black():
    assert GuestRarity.GRAY not in recruitment_shared.NON_REPEATABLE_RARITIES
    assert GuestRarity.BLACK not in recruitment_shared.NON_REPEATABLE_RARITIES


def test_recruitment_weights_match_production_rhythm_config():
    # 正式配置节奏目标（按 ~170 抽/周估算）：
    # 蓝一周一蓝、紫一月一紫、橙三月一橙、绿两天一绿，红色保持关闭
    weight_map = dict(RARITY_WEIGHTS)
    assert weight_map[HERMIT_RARITY] == 1000
    assert weight_map[GuestRarity.RED] == 0
    assert weight_map[GuestRarity.BLUE] == 6000
    assert weight_map[GuestRarity.PURPLE] == 1500
    assert weight_map[GuestRarity.ORANGE] == 500
    assert weight_map[GuestRarity.GREEN] == 20000


def test_recruitment_rarity_distribution_keeps_total_weight():
    assert sum(weight for _, weight in RARITY_DISTRIBUTION) == TOTAL_WEIGHT


def test_filter_templates_removes_excluded():
    t1 = SimpleNamespace(id=1)
    t2 = SimpleNamespace(id=2)
    t3 = SimpleNamespace(id=3)
    templates = [t1, t2, t3]
    excluded = {2}

    result = recruitment_template_service._filter_templates(templates, excluded)

    assert len(result) == 2
    assert t1 in result
    assert t2 not in result
    assert t3 in result


def test_filter_templates_returns_all_when_no_exclusions():
    t1 = SimpleNamespace(id=1)
    t2 = SimpleNamespace(id=2)
    templates = [t1, t2]

    result = recruitment_template_service._filter_templates(templates, set())

    assert result == templates


def test_build_rarity_search_order_starts_with_target_and_covers_all_rarities():
    order = recruitment_template_service._build_rarity_search_order(GuestRarity.BLUE)

    assert order[0] == GuestRarity.BLUE
    assert len(order) == len(set(order))
    assert set(order) >= {
        GuestRarity.BLACK,
        GuestRarity.GRAY,
        GuestRarity.GREEN,
        GuestRarity.RED,
        GuestRarity.BLUE,
        GuestRarity.PURPLE,
        GuestRarity.ORANGE,
    }


def test_choose_template_by_rarity_cached_falls_back_to_next_available_rarity():
    green_template = SimpleNamespace(id=11, key="green_tpl")
    blue_template = SimpleNamespace(id=12, key="blue_tpl")

    result = recruitment_template_service._choose_template_by_rarity_cached(
        GuestRarity.RED,
        excluded_ids=set(),
        rng=__import__("random").Random(1),
        templates_by_rarity={
            GuestRarity.GREEN: [green_template],
            GuestRarity.BLUE: [blue_template],
        },
    )

    assert result in [green_template, blue_template]


def test_core_pool_tiers_has_expected_tiers():
    from guests.models import RecruitmentPool

    expected = (
        RecruitmentPool.Tier.CUNMU,
        RecruitmentPool.Tier.XIANGSHI,
        RecruitmentPool.Tier.HUISHI,
        RecruitmentPool.Tier.DIANSHI,
    )
    assert recruitment_shared.CORE_POOL_TIERS == expected


def test_choose_template_from_entries_rejects_missing_explicit_template_relation():
    entry = SimpleNamespace(template_id=7, template=None, rarity=None, archetype=None, weight=1)

    with pytest.raises(AssertionError, match="invalid recruitment pool entry template"):
        recruitment_template_service.choose_template_from_entries([entry], random.Random(1), templates_by_rarity={})


def test_choose_template_from_entries_rejects_non_recruitable_explicit_template(monkeypatch):
    template = SimpleNamespace(id=11, key="bad_tpl", rarity=GuestRarity.GREEN, recruitable=False)
    entry = SimpleNamespace(template_id=11, template=template, rarity=None, archetype=None, weight=1)
    monkeypatch.setattr(recruitment_template_service, "choose_rarity", lambda _rng: GuestRarity.GREEN)

    with pytest.raises(AssertionError, match="invalid recruitment pool entry template"):
        recruitment_template_service.choose_template_from_entries([entry], random.Random(1), templates_by_rarity={})


def test_choose_template_from_entries_rejects_entry_without_template_or_rarity(monkeypatch):
    entry = SimpleNamespace(template_id=None, template=None, rarity=None, archetype=None, weight=1)
    monkeypatch.setattr(recruitment_template_service, "choose_rarity", lambda _rng: GuestRarity.GREEN)

    with pytest.raises(AssertionError, match="invalid recruitment pool entry rarity"):
        recruitment_template_service.choose_template_from_entries([entry], random.Random(1), templates_by_rarity={})


def test_weighted_choice_defaults_missing_weight_to_one():
    entry = SimpleNamespace()

    chosen = recruitment_utils.weighted_choice([entry], random.Random(1))

    assert chosen is entry


def test_weighted_choice_rejects_invalid_entry_weight():
    bad_entry = SimpleNamespace(weight=0)

    with pytest.raises(AssertionError, match="invalid recruitment pool entry weight"):
        recruitment_utils.weighted_choice([bad_entry], random.Random(1))


def test_weighted_choice_rejects_non_integer_entry_weight():
    bad_entry = SimpleNamespace(weight="bad")

    with pytest.raises(AssertionError, match="invalid recruitment pool entry weight"):
        recruitment_utils.weighted_choice([bad_entry], random.Random(1))


def test_load_rarity_distribution_rejects_invalid_total_weight(monkeypatch):
    monkeypatch.setattr(
        recruitment_utils,
        "load_yaml_data",
        lambda *_args, **_kwargs: {"total_weight": "bad", "weights": {}},
    )
    recruitment_utils.clear_recruitment_rarity_cache()

    try:
        with pytest.raises(AssertionError, match="invalid recruitment rarity weights total_weight"):
            recruitment_utils._load_rarity_distribution()
    finally:
        recruitment_utils.clear_recruitment_rarity_cache()


def test_load_rarity_distribution_rejects_negative_weight(monkeypatch):
    monkeypatch.setattr(
        recruitment_utils,
        "load_yaml_data",
        lambda *_args, **_kwargs: {"total_weight": 1000000, "weights": {GuestRarity.GREEN: -1}},
    )
    recruitment_utils.clear_recruitment_rarity_cache()

    try:
        with pytest.raises(AssertionError, match=r"invalid recruitment rarity weights weights\.green"):
            recruitment_utils._load_rarity_distribution()
    finally:
        recruitment_utils.clear_recruitment_rarity_cache()
