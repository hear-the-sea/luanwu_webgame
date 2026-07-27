from __future__ import annotations

import random

import pytest

from battle.combatants_pkg.ai_generator import build_ai_guests, build_named_ai_guests
from guests.models import GuestTemplate


def test_build_named_ai_guests_rejects_mapping_entry_without_key():
    with pytest.raises(AssertionError, match="invalid ai guest config entry"):
        build_named_ai_guests([{"skills": ["slash"]}])


def test_build_named_ai_guests_rejects_invalid_mapping_skills():
    with pytest.raises(AssertionError, match="invalid ai guest config skills"):
        build_named_ai_guests([{"key": "enemy_guest", "skills": "bad-skills"}])


def test_build_named_ai_guests_rejects_invalid_mapping_skill_entry():
    with pytest.raises(AssertionError, match="invalid ai guest config skills entry"):
        build_named_ai_guests([{"key": "enemy_guest", "skills": [""]}])


def test_build_named_ai_guests_rejects_invalid_level():
    with pytest.raises(AssertionError, match="invalid ai guest level"):
        build_named_ai_guests([], level=0)


def test_build_named_ai_guests_rejects_unknown_template_key(monkeypatch):
    monkeypatch.setattr("battle.combatants_pkg.ai_generator.get_all_guest_templates", lambda: {})

    with pytest.raises(AssertionError, match="unknown ai guest template key"):
        build_named_ai_guests(["enemy_guest"])


@pytest.mark.django_db
def test_build_named_ai_guests_starts_generated_enemy_at_full_hp(monkeypatch):
    template = GuestTemplate.objects.create(
        key="enemy_full_hp_tpl",
        name="满血敌将",
        archetype="military",
        rarity="purple",
        base_attack=200,
        base_intellect=100,
        base_defense=180,
        base_agility=120,
        base_luck=70,
        base_hp=2400,
        default_gender="unknown",
        default_morality=50,
    )
    monkeypatch.setattr(
        "battle.combatants_pkg.ai_generator.get_all_guest_templates",
        lambda: {template.key: template},
    )

    guests = build_named_ai_guests([template.key], level=82, rng=random.Random(82))

    assert len(guests) == 1
    assert guests[0].current_hp == guests[0].max_hp


@pytest.mark.django_db
def test_build_ai_guests_starts_generated_enemy_at_full_hp(monkeypatch):
    template = GuestTemplate.objects.create(
        key="enemy_random_full_hp_tpl",
        name="随机满血敌将",
        archetype="military",
        rarity="blue",
        base_attack=120,
        base_intellect=80,
        base_defense=90,
        base_agility=75,
        base_luck=50,
        base_hp=1800,
        default_gender="unknown",
        default_morality=50,
    )
    monkeypatch.setattr(
        "battle.combatants_pkg.ai_generator.get_all_guest_templates",
        lambda: {template.key: template},
    )

    guests = build_ai_guests(random.Random(7))

    assert len(guests) == 1
    assert guests[0].current_hp == guests[0].max_hp
