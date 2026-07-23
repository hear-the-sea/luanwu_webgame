from __future__ import annotations

from pathlib import Path

import yaml

from core.utils.yaml_validators.jail_persuasion import validate_jail_persuasion_profiles


def _raw_profiles():
    return yaml.safe_load(Path("data/jail_persuasion_profiles.yaml").read_text(encoding="utf-8"))


def _messages(result) -> list[str]:
    return [error.message for error in result.errors]


def test_yaml_validator_accepts_current_profile():
    result = validate_jail_persuasion_profiles(_raw_profiles())

    assert result.is_valid, [str(error) for error in result.errors]


def test_yaml_validator_rejects_missing_success_probability():
    raw = _raw_profiles()
    raw["recruitment"].pop("success_probability")

    result = validate_jail_persuasion_profiles(raw)

    assert any(
        error.path == "recruitment.success_probability" and error.message == "expected a mapping"
        for error in result.errors
    )


def test_yaml_validator_rejects_missing_success_probability_field():
    raw = _raw_profiles()
    raw["recruitment"]["success_probability"]["negotiated"].pop("heart_bonus_max")

    result = validate_jail_persuasion_profiles(raw)

    assert any("field 'heart_bonus_max' expected int, got NoneType" in message for message in _messages(result))


def test_yaml_validator_rejects_missing_success_probability_rarity():
    raw = _raw_profiles()
    raw["recruitment"]["success_probability"]["rarity_penalty"].pop("gray")

    result = validate_jail_persuasion_profiles(raw)

    assert any("field 'gray' expected int, got NoneType" in message for message in _messages(result))


def test_yaml_validator_rejects_out_of_range_success_probability_field():
    raw = _raw_profiles()
    raw["recruitment"]["success_probability"]["final_maximum"] = 101

    result = validate_jail_persuasion_profiles(raw)

    assert any("field 'final_maximum' must be between 0 and 100, got 101" in message for message in _messages(result))


def test_yaml_validator_rejects_copy_key_reused_across_groups():
    raw = _raw_profiles()
    raw["feedback"]["bribe"]["matched"][0]["key"] = raw["feedback"]["kindness"]["matched"][0]["key"]

    result = validate_jail_persuasion_profiles(raw)

    assert any("duplicate copy key" in message for message in _messages(result))


def test_yaml_validator_rejects_removal_of_published_copy_key():
    raw = _raw_profiles()
    raw["feedback"]["kindness"]["matched"].pop()

    result = validate_jail_persuasion_profiles(raw)

    assert any("missing compatibility copy key" in message for message in _messages(result))


def test_yaml_validator_rejects_removal_of_recruitment_failure_copy():
    raw = _raw_profiles()
    raw["recruitment_failure_copy"]["standard"][0]["key"] = "recruitment.failure.standard.unpublished"

    result = validate_jail_persuasion_profiles(raw)

    assert any(
        "missing compatibility copy key 'recruitment.failure.standard.1'" in message for message in _messages(result)
    )


def test_yaml_validator_rejects_unknown_recruitment_failure_placeholder():
    raw = _raw_profiles()
    raw["recruitment_failure_copy"]["heartfelt"][0]["text"] = "{speaker_name} 未能说服 {prisoner_name}"

    result = validate_jail_persuasion_profiles(raw)

    assert any("unknown placeholders: speaker_name" in message for message in _messages(result))


def test_yaml_validator_rejects_nested_recruitment_failure_placeholder():
    raw = _raw_profiles()
    raw["recruitment_failure_copy"]["heartfelt"][0]["text"] = "{prisoner_name:{success_percent}}"

    result = validate_jail_persuasion_profiles(raw)

    assert any("unknown placeholders: success_percent" in message for message in _messages(result))


def test_yaml_validator_rejects_empty_milestone_choice_label():
    raw = _raw_profiles()
    raw["milestones"]["kindness_35"]["options"]["aligned"]["label"] = ""

    result = validate_jail_persuasion_profiles(raw)

    assert any("field 'label' expected a non-empty string" in message for message in _messages(result))
