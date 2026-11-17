"""使用冻结阵容模拟赛季战斗，真实生命与护院损耗由出征结算统一落库。"""

from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from battle.combatants_pkg import build_named_ai_guests
from battle.execution import BattleOptions, prepare_snapshot_battle_teams
from battle.services import simulate_report
from core.exceptions import GameError
from gameplay.services.arena.snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot
from gameplay.services.battle_snapshots import BattleGuestSnapshotProxy
from gameplay.services.missions_impl.constants import PLAYER_MISSION_TROOP_LOSS_RATE
from guests.guest_combat_stats import resolve_guest_combat_stats, snapshot_effective_combat_stats
from guests.guest_rules import compute_guest_attack_attribute_component
from guests.models import Guest, GuestStatus

from .playability_rules import playability_rules
from .technology import season_technology_levels

PVE_LEVELS = {"mountain:inn": 3, "mountain:ruins": 10, "river:docks": 15, "forest:granary": 25, "forest:lodge": 20}


def snapshot_guest_strength(snapshot: dict[str, Any]) -> int:
    attack, defense, _agility = snapshot_effective_combat_stats(snapshot)
    return attack + defense + snapshot["current_hp"]


def npc_opening_profile(
    season: Any,
    lineup: list[dict[str, Any]],
    member_count: int,
    base_level: int,
    *,
    rules_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = playability_rules(season)
    if rules_override is None and not config["enabled"]:
        return {}
    rules = deepcopy(rules_override if rules_override is not None else config["npc"])
    players = max(1, min(5, member_count))
    percent = rules["level_percent_by_players"][str(players)]
    count = min(
        rules["max_guests_by_players"][str(players)],
        max(1, sum(row.get("current_hp", 0) > 0 for row in lineup)),
    )
    return {
        "tier": f"players_{players}",
        "label": f"{players}人开拓 · 守军等级{percent}% · 最多{count}名守军",
        "players": players,
        "base_level": base_level,
        "level": max(1, base_level * percent // 100),
        "count": count,
        "rules": rules,
    }


def build_lineup(manor: Any, guest_ids: list[int], maximum: int = 3) -> dict[str, Any]:
    if not guest_ids or len(guest_ids) > maximum or len(set(guest_ids)) != len(guest_ids):
        raise GameError(f"请选择 1—{maximum} 名不同门客")
    guests = list(
        Guest.objects.select_for_update()
        .filter(manor=manor, id__in=guest_ids)
        .select_related("template")
        .prefetch_related("skills")
        .order_by("id")
    )
    if len(guests) != len(guest_ids) or any(guest.status == GuestStatus.INJURED for guest in guests):
        raise GameError("所选门客不存在或处于重伤状态")
    snapshots = []
    for guest in guests:
        snapshot = build_entry_guest_snapshot(guest)
        snapshot.update(guest_id=guest.pk, current_hp=snapshot["max_hp"])
        snapshots.append(snapshot)
    return {"guests": snapshots, "power": sum(snapshot_guest_strength(s) for s in snapshots)}


def proxies(lineup: dict[str, Any], *, preserve_state: bool = False) -> list[Any]:
    result = []
    for snapshot in lineup.get("guests", []):
        # 新出征复用任务快照保留器械效果；已在途的旧版阵容和NPC保持兼容。
        proxy: Any = (
            BattleGuestSnapshotProxy(snapshot, include_guest_identity=preserve_state)
            if snapshot.get("snapshot_version", 1) >= 2
            else ArenaGuestSnapshotProxy(snapshot)
        )
        if "attack_attribute_component" in snapshot:
            proxy.attack_attribute_component = snapshot["attack_attribute_component"]
        proxy.is_battle_snapshot_proxy = True
        proxy.current_hp = snapshot["current_hp"] if preserve_state else proxy.max_hp
        if preserve_state:
            proxy.id = proxy.pk = snapshot.get("guest_id")
            proxy.troop_capacity = snapshot.get("troop_capacity", 0)
        result.append(proxy)
    return result


def pve_lineup(
    node_key: str,
    enemy_label: str,
    wins: int,
    seed: int,
    *,
    level_override: int | None = None,
    opening_profile: dict[str, Any] | None = None,
    encounter: dict[str, Any] | None = None,
    wave_index: int = 0,
) -> dict[str, Any]:
    if level_override is not None:
        level = level_override
    elif encounter:
        level = encounter["base_level"]
    else:
        level = PVE_LEVELS[node_key] + (min(wins, 20) * 2 if node_key == "mountain:ruins" else 0)
    count = 1 if node_key == "mountain:inn" else 2 if node_key.startswith("mountain:") else 3
    if opening_profile:
        level, count = opening_profile["level"], opening_profile["count"]
    rng = random.Random(seed)
    troops = {}
    skill_sets = None
    if encounter:
        if encounter["category"] == "collection":
            wave = encounter["waves"][min(wave_index, len(encounter["waves"]) - 1)]
            level = encounter["base_level"] + wave["level_bonus"]
            count, rarity, skills, troops = wave["count"], wave["rarity"], wave["skills"], dict(wave["troops"])
            skill_sets = wave.get("skill_sets")
        else:
            players = max(1, min(5, int(encounter.get("player_count", 1))))
            attackers = max(1, int(encounter.get("attacker_guest_count", players)))
            count = min(encounter["max_guests"], players * encounter["guests_per_player"], attackers)
            ratio = (opening_profile or {}).get("rules", {}).get("level_percent_by_players", {}).get(str(players), 100)
            level = max(1, encounter["base_level"] * ratio // 100)
            rarity, skills = encounter["rarity"], encounter["skills"]
            troops = {
                key: min(3000, amount * count // encounter["guests_per_player"])
                for key, amount in encounter["troops_per_player"].items()
            }
        from battle.combatants_pkg.cache import get_all_guest_templates
        from guests.models import Skill

        available = get_all_guest_templates()
        pool = encounter["pools"][rarity]
        if any(key not in available or available[key].rarity != rarity for key in pool):
            raise GameError("赛季中立守军门客模板未加载或品质不匹配，请联系管理员")
        all_skills = {skill for group in (skill_sets if skill_sets is not None else [skills]) for skill in group}
        if all_skills != set(Skill.objects.filter(key__in=all_skills).values_list("key", flat=True)):
            raise GameError("赛季中立守军技能未加载，请联系管理员")
        # 同一批优先使用不同人物；人数超过可用模板时再重新抽取。
        chosen: list[str] = []
        while len(chosen) < count:
            chosen.extend(rng.sample(pool, len(pool)))
        configs = [
            {
                "key": key,
                "label": f"{enemy_label}·{available[key].name}",
                "skills": list(skill_sets[index] if skill_sets is not None else skills),
            }
            for index, key in enumerate(chosen[:count])
        ]
    else:
        configs = [
            {"key": "base_black_military", "label": f"{enemy_label}·{index + 1}", "skills": []}
            for index in range(count)
        ]
    enemies = build_named_ai_guests(configs, level=level, rng=rng)
    snapshots = []
    for enemy in enemies:
        # AI 门客未保存，不能读取其多对多技能管理器。
        stats = resolve_guest_combat_stats(enemy)
        snapshots.append(
            {
                "display_name": getattr(enemy, "_display_name_override", enemy.display_name),
                "template_key": enemy.template.key,
                "rarity": enemy.rarity,
                "archetype": enemy.archetype,
                "level": enemy.level,
                "force": enemy.force,
                "intellect": enemy.intellect,
                "defense_stat": enemy.defense_stat,
                "agility": enemy.agility,
                "luck": enemy.luck,
                "attack": stats.attack,
                "attack_attribute_component": compute_guest_attack_attribute_component(enemy),
                "defense": stats.defense,
                "max_hp": stats.max_hp,
                "current_hp": stats.max_hp,
                "skill_keys": list(getattr(enemy, "_override_skills", [])),
            }
        )
    return {"guests": snapshots, **({"troops": troops} if encounter else {})}


def prepare_combat(
    manor: Any,
    attacker: dict[str, Any],
    defender: dict[str, Any],
    opponent_name: str,
    seed: int,
    *,
    preserve_state: bool = False,
    allow_empty_defenders: bool = False,
) -> dict[str, Any]:
    attack, defend = proxies(attacker, preserve_state=preserve_state), proxies(defender, preserve_state=preserve_state)
    if not attack or (not defend and not allow_empty_defenders):
        raise GameError("阵容为空，请先调整赛季阵容")
    options = BattleOptions(
        battle_type="arena",
        seed=seed,
        troop_loadout=attacker.get("troops", {}),
        fill_default_troops=False,
        defender_guests=defend,
        defender_setup={
            "troop_loadout": defender.get("troops", {}),
            "technology": {"levels": season_technology_levels(defender.get("technology", {}))},
        },
        attacker_tech_levels=season_technology_levels(attacker.get("technology", {})),
        attacker_manor=None,
        attacker_guest_bonuses={},
        limit=len(attack),
        defender_limit=len(defend),
        auto_reward=False,
        send_message=False,
        apply_damage=False,
        apply_victory_loyalty=False,
        recover_live_guest_hp=False,
        persist_report=False,
        attacker_troop_loss_rate=PLAYER_MISSION_TROOP_LOSS_RATE if preserve_state else None,
        defender_troop_loss_rate=PLAYER_MISSION_TROOP_LOSS_RATE if preserve_state else None,
        opponent_name=opponent_name,
    )
    return {
        "attack": attack,
        "defend": defend,
        "prepared": prepare_snapshot_battle_teams(manor, attack, options, allow_empty_defenders=allow_empty_defenders),
        "opponent_name": opponent_name,
    }


def simulate_combat(manor: Any, prepared: dict[str, Any], seed: int) -> Any:
    return simulate_report(
        manor=manor,
        battle_type="arena",
        seed=seed,
        troop_loadout={},
        fill_default_troops=False,
        attacker_guests=prepared["attack"],
        defender_guests=prepared["defend"],
        defender_setup={"troop_loadout": {}, "technology": {"levels": {}}},
        attacker_tech_levels={},
        attacker_manor=None,
        attacker_guest_bonuses={},
        max_squad=len(prepared["attack"]),
        defender_max_squad=len(prepared["defend"]),
        auto_reward=False,
        send_message=False,
        apply_damage=False,
        use_lock=False,
        persist_report=False,
        prepared_teams=prepared["prepared"],
        opponent_name=prepared["opponent_name"],
    )
