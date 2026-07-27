"""
AI guest generator.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Mapping, Sequence

from guests.models import Guest

from .cache import get_all_guest_templates


def _normalize_ai_guest_level(level: int) -> int:
    if isinstance(level, bool):
        raise AssertionError(f"invalid ai guest level: {level!r}")
    try:
        parsed_level = int(level)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid ai guest level: {level!r}") from exc
    if parsed_level <= 0:
        raise AssertionError(f"invalid ai guest level: {level!r}")
    return parsed_level


def _get_max_squad() -> int:
    """Get MAX_SQUAD from constants, with fallback."""
    try:
        from battle.constants import MAX_SQUAD as SQUAD

        return SQUAD
    except ImportError:
        return 5


def allocate_ai_attribute_points(guest: Guest, total_points: int, *, rng: random.Random) -> Dict[str, int]:
    """
    Allocate attribute points for AI guest (by archetype weights).

    Args:
        guest: Guest instance (for archetype check)
        total_points: Total points to allocate

    Returns:
        Allocation dict {"force": X, "intellect": Y, "defense": Z, "agility": W}
    """
    from guests.utils.attribute_growth import CIVIL_ATTRIBUTE_WEIGHTS, MILITARY_ATTRIBUTE_WEIGHTS

    if guest.archetype == "military":
        weights = MILITARY_ATTRIBUTE_WEIGHTS
    else:
        weights = CIVIL_ATTRIBUTE_WEIGHTS

    choices = []
    for attr, weight in weights.items():
        choices.extend([attr] * weight)

    allocation = {"force": 0, "intellect": 0, "defense": 0, "agility": 0}
    for _ in range(total_points):
        attr = rng.choice(choices)
        allocation[attr] += 1

    return allocation


def build_named_ai_guests(
    guest_keys: Sequence[str | Mapping[str, Any]],
    level: int = 50,
    *,
    rng: random.Random | None = None,
) -> List[Guest]:
    """
    Build AI guests from specified templates with random attribute growth.

    Args:
        guest_keys: Guest config list, supports two formats:
            - string: template key (backward compatible)
            - dict: {"key": "template_key", "skills": ["skill1", "skill2"]}
        level: Guest level (default 50)

    Returns:
        AI guest list with attributes grown based on level
    """
    from guests.growth_rules import RARITY_SKILL_POINT_GAINS
    from guests.utils.attribute_growth import allocate_level_up_attributes

    normalized_level = _normalize_ai_guest_level(level)
    parsed_configs: List[Dict[str, Any]] = []
    template_keys_to_fetch: List[str] = []

    for entry in guest_keys:
        if isinstance(entry, str):
            if not entry.strip():
                raise AssertionError(f"invalid ai guest config entry: {entry!r}")
            parsed_configs.append({"key": entry, "skills": None, "label": None})
            template_keys_to_fetch.append(entry)
        elif isinstance(entry, dict):
            key = entry.get("key", "")
            if not isinstance(key, str) or not key.strip():
                raise AssertionError(f"invalid ai guest config entry: {entry!r}")
            skills = entry.get("skills")
            if skills is not None:
                if not isinstance(skills, (list, tuple, set)):
                    raise AssertionError(f"invalid ai guest config skills: {skills!r}")
                for skill in skills:
                    if not isinstance(skill, str) or not skill.strip():
                        raise AssertionError(f"invalid ai guest config skills entry: {skill!r}")
            label = entry.get("label")
            if label is not None and (not isinstance(label, str) or not label.strip()):
                raise AssertionError(f"invalid ai guest config label: {label!r}")
            parsed_configs.append(
                {"key": key, "skills": skills, "label": label.strip() if isinstance(label, str) else None}
            )
            template_keys_to_fetch.append(key)
        else:
            raise AssertionError(f"invalid ai guest config entry: {entry!r}")

    all_templates = get_all_guest_templates()
    templates = {key: all_templates[key] for key in template_keys_to_fetch if key in all_templates}
    guests: List[Guest] = []

    for config in parsed_configs:
        template_key = config["key"]
        override_skills = config["skills"]
        display_name_override = config.get("label")

        template = templates.get(template_key)
        if not template:
            raise AssertionError(f"unknown ai guest template key: {template_key!r}")
        if rng is None:
            raise AssertionError("build_named_ai_guests requires an explicit rng")
        dummy_guest = Guest(
            template=template,
            level=normalized_level,
            attack_bonus=40,
            defense_bonus=40,
            force=template.base_attack,
            intellect=template.base_intellect,
            defense_stat=template.base_defense,
            agility=template.base_agility,
            luck=template.base_luck,
            gender=template.default_gender,
            morality=template.default_morality,
        )

        if normalized_level > 1:
            growth_levels = normalized_level - 1

            growth = allocate_level_up_attributes(dummy_guest, levels=growth_levels, rng=rng)
            dummy_guest.force += growth.get("force", 0)
            dummy_guest.intellect += growth.get("intellect", 0)
            dummy_guest.defense_stat += growth.get("defense", 0)
            dummy_guest.agility += growth.get("agility", 0)
            per_level_points = int(RARITY_SKILL_POINT_GAINS.get(template.rarity, 1))  # type: ignore[arg-type,call-overload]
            total_attribute_points = per_level_points * growth_levels

            if total_attribute_points > 0:
                attr_allocation = allocate_ai_attribute_points(dummy_guest, total_attribute_points, rng=rng)
                dummy_guest.force += attr_allocation.get("force", 0)
                dummy_guest.intellect += attr_allocation.get("intellect", 0)
                dummy_guest.defense_stat += attr_allocation.get("defense", 0)
                dummy_guest.agility += attr_allocation.get("agility", 0)

        # Unsaved AI guests keep the model default current_hp=0 unless we explicitly
        # initialize it, which would make battle reports show them as near-dead.
        dummy_guest.current_hp = dummy_guest.max_hp

        if override_skills is not None:
            setattr(dummy_guest, "_override_skills", override_skills)
        if display_name_override is not None:
            setattr(dummy_guest, "_display_name_override", display_name_override)

        guests.append(dummy_guest)

    return guests


def build_ai_guests(rng: random.Random) -> List[Guest]:
    """Build random AI guests for testing."""
    all_templates = get_all_guest_templates()
    templates = list(all_templates.values())
    rng.shuffle(templates)
    guests: List[Guest] = []
    max_squad = _get_max_squad()
    for template in templates[:max_squad]:
        dummy_guest = Guest(
            template=template,
            level=10,
            attack_bonus=20,
            defense_bonus=20,
        )
        dummy_guest.current_hp = dummy_guest.max_hp
        guests.append(dummy_guest)
    return guests


def generate_ai_loadout(rng: random.Random) -> Dict[str, int]:
    """Generate random AI troop loadout."""
    from battle.troops import load_troop_templates

    templates = load_troop_templates()
    loadout: Dict[str, int] = {}
    for key, definition in templates.items():
        base = definition.get("default_count", 120)
        jitter = rng.randint(-int(base * 0.2), int(base * 0.2)) if base else 0
        loadout[key] = max(0, int(base + jitter))
    return loadout
