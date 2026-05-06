from __future__ import annotations

import random

import pytest
from django.utils import timezone

from battle.services import _build_defender_guest_and_loadout, _extract_defender_tech_profile
from core.exceptions import GameError


def test_extract_defender_tech_profile_rejects_invalid_technology_payload():
    with pytest.raises(AssertionError, match="invalid battle defender technology payload"):
        _extract_defender_tech_profile({"technology": "bad-config"})


def test_extract_defender_tech_profile_rejects_invalid_guest_level():
    with pytest.raises(GameError, match="invalid battle defender guest_level"):
        _extract_defender_tech_profile({"technology": {"guest_level": "bad"}})


def test_extract_defender_tech_profile_rejects_invalid_guest_skills():
    with pytest.raises(AssertionError, match="invalid battle defender guest_skills"):
        _extract_defender_tech_profile({"technology": {"guest_skills": "not-a-list"}})


def test_build_defender_guest_and_loadout_rejects_invalid_defender_setup(monkeypatch):
    monkeypatch.setattr("battle.services.generate_ai_loadout", lambda _rng: {"archer": 1})
    monkeypatch.setattr("battle.services.build_ai_guests", lambda _rng: ["ai-guest"])
    monkeypatch.setattr(
        "battle.services.build_guest_combatants",
        lambda _guests, **_kwargs: ["combatant"],
    )

    with pytest.raises(AssertionError, match="invalid battle defender setup payload"):
        _build_defender_guest_and_loadout(
            defender_guests=None,
            defender_setup="bad-config",
            defender_limit=3,
            fill_default_troops=True,
            rng=random.Random(1),
            now=timezone.now(),
            defender_guest_level=50,
            defender_guest_bonuses={},
            defender_guest_skills=None,
        )


def test_build_defender_guest_and_loadout_rejects_invalid_nested_fields(monkeypatch):
    state = {}

    monkeypatch.setattr("battle.services.generate_ai_loadout", lambda _rng: {"archer": 1})
    monkeypatch.setattr("battle.services.build_ai_guests", lambda _rng: ["ai-guest"])
    monkeypatch.setattr(
        "battle.services.build_named_ai_guests",
        lambda keys, level: state.update({"keys": keys, "level": level}) or ["named-ai"],
    )
    monkeypatch.setattr(
        "battle.services.build_guest_combatants",
        lambda _guests, **_kwargs: ["combatant"],
    )
    monkeypatch.setattr(
        "battle.services.normalize_troop_loadout",
        lambda loadout, **_kwargs: state.update({"loadout_arg": loadout}) or {"safe": 1},
    )

    with pytest.raises(AssertionError, match="invalid battle defender guest_keys payload"):
        _build_defender_guest_and_loadout(
            defender_guests=None,
            defender_setup={"guest_keys": "bad-guests", "troop_loadout": "bad-loadout"},
            defender_limit=3,
            fill_default_troops=True,
            rng=random.Random(1),
            now=timezone.now(),
            defender_guest_level=50,
            defender_guest_bonuses={},
            defender_guest_skills=None,
        )

    assert state == {}
