from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Literal, Mapping, Tuple

from core.exceptions import ItemNotFoundError
from guests.models import Guest

from ..constants import PVPConstants

logger = logging.getLogger(__name__)


def _require_mapping(raw: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise AssertionError(f"invalid battle salvage {field_name}: {raw!r}")
    return raw


def _require_list(raw: Any, *, field_name: str) -> list[Any]:
    if not isinstance(raw, list):
        raise AssertionError(f"invalid battle salvage {field_name}: {raw!r}")
    return raw


def _require_non_empty_string(raw: Any, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise AssertionError(f"invalid battle salvage {field_name}: {raw!r}")
    return raw.strip()


def _require_int(raw: Any, *, field_name: str, minimum: int | None = None) -> int:
    if isinstance(raw, bool):
        raise AssertionError(f"invalid battle salvage {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid battle salvage {field_name}: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise AssertionError(f"invalid battle salvage {field_name}: {raw!r}")
    return value


def _normalize_side(side: str | None) -> Literal["attacker", "defender"] | None:
    if side is None:
        return None
    if not isinstance(side, str) or not side.strip():
        raise AssertionError(f"invalid battle salvage side: {side!r}")
    normalized = side.strip().lower()
    if normalized == "attacker":
        return "attacker"
    if normalized == "defender":
        return "defender"
    raise AssertionError(f"invalid battle salvage side: {side!r}")


def _resolve_report_seed(report: Any) -> int:
    return _require_int(getattr(report, "seed", 0), field_name="report.seed")


def _resolve_casualty_entry(entry: Any) -> tuple[str, int]:
    payload = _require_mapping(entry, field_name="casualty entry")
    key = _require_non_empty_string(payload.get("key"), field_name="casualty key")
    lost = _require_int(payload.get("lost"), field_name="casualty lost", minimum=0)
    return key, lost


def _resolve_member_entry(member: Any) -> Mapping[str, Any]:
    return _require_mapping(member, field_name="team member")


def _collect_guest_template_keys(report) -> set[str]:
    attacker_team = _require_list(getattr(report, "attacker_team", []), field_name="report.attacker_team")
    defender_team = _require_list(getattr(report, "defender_team", []), field_name="report.defender_team")
    template_keys: set[str] = set()
    for member in list(attacker_team) + list(defender_team):
        payload = _resolve_member_entry(member)
        raw_key = payload.get("template_key")
        if raw_key in (None, ""):
            continue
        template_keys.add(_require_non_empty_string(raw_key, field_name="team member template_key"))
    return template_keys


def _collect_city_defense_template_keys(report) -> set[str]:
    attacker_city_defenses = _require_list(
        getattr(report, "attacker_city_defenses", []),
        field_name="report.attacker_city_defenses",
    )
    defender_city_defenses = _require_list(
        getattr(report, "defender_city_defenses", []),
        field_name="report.defender_city_defenses",
    )
    template_keys: set[str] = set()
    for entry in list(attacker_city_defenses) + list(defender_city_defenses):
        payload = _require_mapping(entry, field_name="city defense entry")
        raw_key = payload.get("key")
        if raw_key in (None, ""):
            continue
        template_keys.add(_require_non_empty_string(raw_key, field_name="city defense key"))
    return template_keys


def _collect_casualties(report, side: str | None = None) -> List[Dict[str, Any]]:
    raw_losses = getattr(report, "losses", None)
    losses = _require_mapping(raw_losses, field_name="report.losses")
    normalized_side = _normalize_side(side)
    if normalized_side:
        side_losses = _require_mapping(losses.get(normalized_side, {}), field_name=f"report.losses.{normalized_side}")
        return list(
            _require_list(side_losses.get("casualties", []), field_name=f"report.losses.{normalized_side}.casualties")
        )

    attacker_losses = _require_mapping(losses.get("attacker", {}), field_name="report.losses.attacker")
    defender_losses = _require_mapping(losses.get("defender", {}), field_name="report.losses.defender")
    casualties: List[Dict[str, Any]] = []
    casualties.extend(
        _require_list(attacker_losses.get("casualties", []), field_name="report.losses.attacker.casualties")
    )
    casualties.extend(
        _require_list(defender_losses.get("casualties", []), field_name="report.losses.defender.casualties")
    )
    return casualties


def _calculate_troop_exp_fruit(
    casualties: List[Dict[str, Any]],
    *,
    guest_template_keys: set[str],
    ignored_template_keys: set[str],
) -> float:
    from gameplay.services.recruitment.recruitment import get_troop_template

    troop_exp_fruit = 0.0
    for entry in casualties:
        key, lost = _resolve_casualty_entry(entry)
        if lost <= 0:
            continue
        if key in guest_template_keys or key in ignored_template_keys:
            continue

        troop_config = get_troop_template(key)
        if not troop_config:
            raise AssertionError(f"invalid battle salvage troop template: {key!r}")

        recruit = _require_mapping(troop_config.get("recruit", {}), field_name=f"troop template recruit[{key}]")
        base_duration = _require_int(
            recruit.get("base_duration"), field_name=f"troop template base_duration[{key}]", minimum=1
        )
        troop_exp_fruit += lost * (base_duration / 3600) * 0.1
    return troop_exp_fruit


def _calculate_equipment_recovery(
    casualties: List[Dict[str, Any]],
    rng: random.Random,
    *,
    guest_template_keys: set[str],
    ignored_template_keys: set[str],
) -> Dict[str, int]:
    from gameplay.services.recruitment.recruitment import get_troop_template

    equipment_recovery: Dict[str, int] = {}
    for entry in casualties:
        key, lost = _resolve_casualty_entry(entry)
        if lost <= 0:
            continue
        if key in guest_template_keys or key in ignored_template_keys:
            continue

        troop_config = get_troop_template(key)
        if not troop_config:
            raise AssertionError(f"invalid battle salvage troop template: {key!r}")

        recruit = _require_mapping(troop_config.get("recruit", {}), field_name=f"troop template recruit[{key}]")
        equipment_list = _require_list(recruit.get("equipment", []), field_name=f"troop template equipment[{key}]")
        for equip_key in equipment_list:
            normalized_equip_key = _require_non_empty_string(
                equip_key, field_name=f"troop template equipment key[{key}]"
            )
            recovered = 0
            for _ in range(lost):
                if rng.random() < PVPConstants.EQUIPMENT_RECOVERY_CHANCE:
                    recovered += 1
            if recovered > 0:
                equipment_recovery[normalized_equip_key] = equipment_recovery.get(normalized_equip_key, 0) + recovered

    return equipment_recovery


def _member_exp_fruit(member: Dict[str, Any]) -> float:
    payload = _resolve_member_entry(member)
    remaining_hp = _require_int(payload.get("remaining_hp"), field_name="team member remaining_hp", minimum=0)
    if remaining_hp > 0:
        return 0.0

    level = _require_int(payload.get("level"), field_name="team member level", minimum=1)
    rarity = _require_non_empty_string(payload.get("rarity"), field_name="team member rarity")
    rarity_mult = PVPConstants.RARITY_EXP_MULTIPLIER.get(rarity, 1.0)
    if rarity not in PVPConstants.RARITY_EXP_MULTIPLIER:
        raise AssertionError(f"invalid battle salvage team member rarity: {rarity!r}")

    max_hp_raw = payload.get("max_hp", payload.get("hp"))
    max_hp = _require_int(max_hp_raw, field_name="team member max_hp", minimum=1)
    initial_hp = _require_int(payload.get("initial_hp", max_hp), field_name="team member initial_hp", minimum=0)
    if initial_hp > max_hp:
        raise AssertionError(f"invalid battle salvage team member initial_hp: {initial_hp!r}")
    hp_ratio = initial_hp / max_hp

    return level * rarity_mult * hp_ratio * 0.05


def _calculate_guest_recovery(report) -> float:
    attacker_team = _require_list(getattr(report, "attacker_team", []), field_name="report.attacker_team")
    defender_team = _require_list(getattr(report, "defender_team", []), field_name="report.defender_team")
    all_members = list(attacker_team) + list(defender_team)
    return sum(_member_exp_fruit(member) for member in all_members)


def calculate_battle_salvage(
    report,
    attacker_guests: List[Guest] | None = None,
    defender_guests: List[Guest] | None = None,
    *,
    equipment_casualty_side: str | None = None,
) -> Tuple[int, Dict[str, int]]:
    """
    根据战报计算“胜利方战斗回收”奖励（经验果 + 护院装备回收）。

    当前规则以本模块实现为准：
    - 经验果按战损与阵亡门客快照计算
    - 装备回收按阵亡护院配置与随机种子计算
    `equipment_casualty_side` 仅用于限定“装备回收”所依据的阵亡方，不影响经验果计算。
    """
    if report is None:
        raise AssertionError("invalid battle salvage report: None")

    # 历史兼容参数，当前不参与计算。
    _ = attacker_guests, defender_guests

    rng = random.Random(_resolve_report_seed(report))

    all_casualties = _collect_casualties(report)
    guest_template_keys = _collect_guest_template_keys(report)
    ignored_template_keys = _collect_city_defense_template_keys(report)
    troop_exp_fruit = _calculate_troop_exp_fruit(
        all_casualties,
        guest_template_keys=guest_template_keys,
        ignored_template_keys=ignored_template_keys,
    )
    normalized_equipment_side = _normalize_side(equipment_casualty_side)
    if not normalized_equipment_side:
        equipment_casualties = all_casualties
    else:
        equipment_casualties = _collect_casualties(report, side=normalized_equipment_side)
    equipment_recovery = _calculate_equipment_recovery(
        equipment_casualties,
        rng,
        guest_template_keys=guest_template_keys,
        ignored_template_keys=ignored_template_keys,
    )
    guest_exp_fruit = _calculate_guest_recovery(report)

    total_exp_fruit = int(troop_exp_fruit + guest_exp_fruit)
    return total_exp_fruit, equipment_recovery


def grant_battle_salvage(manor, exp_fruit_count: int, equipment_recovery: Dict[str, int]) -> None:
    """
    发放“战斗回收”奖励到庄园仓库（经验果 + 装备回收）。
    """
    from .inventory.core import add_item_to_inventory

    normalized_exp_fruit_count = _require_int(exp_fruit_count, field_name="exp_fruit_count", minimum=0)
    recovery_mapping = _require_mapping(equipment_recovery, field_name="equipment_recovery")

    if normalized_exp_fruit_count > 0:
        add_item_to_inventory(manor, "experience_fruit", normalized_exp_fruit_count)

    for equip_key, count in recovery_mapping.items():
        normalized_equip_key = _require_non_empty_string(equip_key, field_name="equipment_recovery key")
        normalized_count = _require_int(count, field_name="equipment_recovery quantity", minimum=0)
        if normalized_count <= 0:
            continue
        try:
            add_item_to_inventory(manor, normalized_equip_key, normalized_count)
        except ItemNotFoundError:
            logger.warning("Unknown equipment template for recovery: %s", normalized_equip_key)
