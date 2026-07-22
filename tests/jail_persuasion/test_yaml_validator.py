from __future__ import annotations

from pathlib import Path

import yaml

from core.utils.yaml_validators.jail_persuasion import validate_jail_persuasion_profiles


def _raw_profiles():
    return yaml.safe_load(Path("data/jail_persuasion_profiles.yaml").read_text(encoding="utf-8"))


def _messages(result) -> list[str]:
    return [error.message for error in result.errors]


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


def test_yaml_validator_rejects_empty_milestone_choice_label():
    raw = _raw_profiles()
    raw["milestones"]["kindness_35"]["options"]["aligned"]["label"] = ""

    result = validate_jail_persuasion_profiles(raw)

    assert any("field 'label' expected a non-empty string" in message for message in _messages(result))
