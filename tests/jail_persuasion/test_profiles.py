from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from gameplay.services.jail_persuasion.profiles import (
    METHOD_BRIBE,
    METHOD_KINDNESS,
    METHOD_MIGHT,
    METHOD_REASON,
    calculate_affinities,
    choose_stance,
    choose_taboo,
    get_clue_keys,
    load_jail_persuasion_profiles,
    normalize_profiles,
    rarity_difficulty,
    validate_copy_placeholders,
)


def _template(**overrides):
    values = {
        "default_morality": 70,
        "archetype": "civil",
        "rarity": "green",
        "is_hermit": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _raw_profiles():
    return yaml.safe_load(Path("data/jail_persuasion_profiles.yaml").read_text(encoding="utf-8"))


def test_calculate_affinities_uses_existing_structured_fields():
    scores = calculate_affinities(_template(), captured_loyalty=80, original_level=40)

    assert scores == {
        METHOD_KINDNESS: 73,
        METHOD_BRIBE: 27,
        METHOD_REASON: 74,
        METHOD_MIGHT: 60,
    }


def test_choose_stance_is_stable_and_only_uses_top_two_methods():
    scores = {
        METHOD_KINDNESS: 73,
        METHOD_BRIBE: 27,
        METHOD_REASON: 74,
        METHOD_MIGHT: 60,
    }
    captured_at = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    first = choose_stance(
        prisoner_id=7,
        template_key="hero",
        captured_at=captured_at,
        scores=scores,
    )
    second = choose_stance(
        prisoner_id=7,
        template_key="hero",
        captured_at=captured_at,
        scores=scores,
    )

    assert first == second
    assert first in {METHOD_KINDNESS, METHOD_REASON}


def test_choose_taboo_uses_fixed_method_order_for_ties_and_threshold():
    assert (
        choose_taboo(
            {
                METHOD_KINDNESS: 60,
                METHOD_BRIBE: 39,
                METHOD_REASON: 39,
                METHOD_MIGHT: 60,
            }
        )
        == METHOD_BRIBE
    )
    assert choose_taboo({method: 40 for method in (METHOD_KINDNESS, METHOD_BRIBE, METHOD_REASON, METHOD_MIGHT)}) == ""


@pytest.mark.parametrize(
    ("rarity", "is_hermit", "expected"),
    [
        ("black", False, 0),
        ("gray", False, 0),
        ("green", False, 1),
        ("red", False, 2),
        ("blue", False, 3),
        ("purple", False, 4),
        ("orange", False, 5),
        ("black", True, 5),
    ],
)
def test_rarity_difficulty_matches_design(rarity, is_hermit, expected):
    assert rarity_difficulty(_template(rarity=rarity, is_hermit=is_hermit)) == expected


def test_clue_keys_are_stable_non_repeating_and_reveal_by_level():
    clue_keys = get_clue_keys(
        stance_method=METHOD_REASON,
        revealed_level=3,
        prisoner_id=7,
        template_key="hero",
        captured_at=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert len(clue_keys) == 3
    assert len(set(clue_keys)) == 3
    assert clue_keys[0].startswith("clue.reason.subtle.")
    assert clue_keys[1].startswith("clue.reason.subtle.")
    assert clue_keys[2].startswith("clue.reason.explicit.")


def test_profile_catalog_contains_all_required_copy_groups():
    profiles = load_jail_persuasion_profiles()

    assert set(profiles["methods"]) == {METHOD_KINDNESS, METHOD_BRIBE, METHOD_REASON, METHOD_MIGHT}
    assert all(len(profiles["clues"][method]["subtle"]) == 5 for method in profiles["methods"])
    assert all(len(profiles["clues"][method]["explicit"]) == 3 for method in profiles["methods"])
    assert len(profiles["milestones"]) == 8
    assert all(len(profiles["recruitment_copy"][mode]) == 3 for mode in ("standard", "negotiated", "heartfelt"))
    assert all(len(profiles["recruitment_failure_copy"][mode]) == 3 for mode in ("standard", "negotiated", "heartfelt"))
    assert all(
        len(profiles["feedback"][method][outcome]) >= 3
        for method in profiles["methods"]
        for outcome in ("matched", "neutral", "taboo")
    )
    assert all(
        len(profiles["feedback"][method][outcome]) >= 3
        for method in (METHOD_REASON, METHOD_MIGHT)
        for outcome in ("failed", "backfire")
    )


def test_copy_placeholder_validation_rejects_unknown_names():
    with pytest.raises(ValueError, match="未知占位符"):
        validate_copy_placeholders("bad.copy", "{unknown_name} 不受支持")


def test_profile_rejects_unknown_resource_cost_instead_of_silently_ignoring_it():
    raw = _raw_profiles()
    raw["methods"]["kindness"]["cost"]["prestige"] = 1

    with pytest.raises(ValueError, match="未知资源"):
        normalize_profiles(raw)


def test_profile_rejects_unknown_method_instead_of_silently_ignoring_it():
    raw = _raw_profiles()
    raw["methods"]["flatter"] = {
        "label": "阿谀奉承",
        "cost": {},
        "heart_delta": -1,
        "affinity_delta": 1,
    }

    with pytest.raises(ValueError, match="未知方法"):
        normalize_profiles(raw)


def test_profile_rejects_copy_key_reused_across_groups():
    raw = _raw_profiles()
    raw["feedback"]["bribe"]["matched"][0]["key"] = raw["feedback"]["kindness"]["matched"][0]["key"]

    with pytest.raises(ValueError, match="重复文案键"):
        normalize_profiles(raw)


def test_profile_rejects_removal_of_published_copy_key():
    raw = _raw_profiles()
    raw["feedback"]["kindness"]["matched"].pop()

    with pytest.raises(ValueError, match="缺少兼容文案键"):
        normalize_profiles(raw)


def test_profile_rejects_removal_of_recruitment_failure_copy():
    raw = _raw_profiles()
    raw["recruitment_failure_copy"]["standard"][0]["key"] = "recruitment.failure.standard.unpublished"

    with pytest.raises(ValueError, match="缺少兼容文案键") as exc_info:
        normalize_profiles(raw)
    assert "recruitment.failure.standard.1" in str(exc_info.value)


def test_profile_rejects_unknown_recruitment_failure_placeholder():
    raw = _raw_profiles()
    raw["recruitment_failure_copy"]["heartfelt"][0]["text"] = "{speaker_name} 未能说服 {prisoner_name}"

    with pytest.raises(ValueError, match="未知占位符"):
        normalize_profiles(raw)


def test_profile_rejects_nested_recruitment_failure_placeholder():
    raw = _raw_profiles()
    raw["recruitment_failure_copy"]["heartfelt"][0]["text"] = "{prisoner_name:{success_percent}}"

    with pytest.raises(ValueError, match="未知占位符") as exc_info:
        normalize_profiles(raw)
    assert "success_percent" in str(exc_info.value)


def test_profile_rejects_empty_milestone_choice_label():
    raw = _raw_profiles()
    raw["milestones"]["kindness_35"]["options"]["aligned"]["label"] = ""

    with pytest.raises(ValueError, match="label 不能为空"):
        normalize_profiles(raw)
