from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from django.apps import apps

from common.constants.resources import ResourceType
from gameplay.utils.template_loader import get_item_template_names_by_keys
from guests.models import Guest, GuestTemplate, SkillBook

if TYPE_CHECKING:
    from .models import BattleReport

_RESOURCE_LABELS = {key: label for key, label in ResourceType.choices}


def _load_mission_run_for_report(report: "BattleReport"):
    MissionRun = apps.get_model("gameplay", "MissionRun")
    return MissionRun.objects.filter(battle_report=report).select_related("mission").first()


def _load_raid_run_for_report(report: "BattleReport"):
    RaidRun = apps.get_model("gameplay", "RaidRun")
    return RaidRun.objects.filter(battle_report=report).first()


def _load_arena_match_for_report(report: "BattleReport"):
    ArenaMatch = apps.get_model("gameplay", "ArenaMatch")
    return ArenaMatch.objects.select_related("attacker_entry", "defender_entry").filter(battle_report=report).first()


def _load_arena_coop_event_for_report(report: "BattleReport"):
    ArenaCoopEvent = apps.get_model("gameplay", "ArenaCoopEvent")
    return ArenaCoopEvent.objects.filter(battle_report=report).first()


def resolve_report_runtime_context(report: "BattleReport", *, manor_id: int) -> dict[str, Any]:
    mission_run = _load_mission_run_for_report(report)
    if mission_run and mission_run.mission.is_defense:
        return {"player_side": "defender", "raid_run": None}

    raid_run = _load_raid_run_for_report(report)
    if raid_run:
        side = "defender" if raid_run.defender_id == manor_id else "attacker"
        return {"player_side": side, "raid_run": raid_run}

    arena_match = _load_arena_match_for_report(report)
    if arena_match:
        if arena_match.defender_entry_id and getattr(arena_match.defender_entry, "manor_id", None) == manor_id:
            return {"player_side": "defender", "raid_run": None}
        if getattr(arena_match.attacker_entry, "manor_id", None) == manor_id:
            return {"player_side": "attacker", "raid_run": None}
        return {"player_side": "spectator", "raid_run": None}

    arena_coop_event = _load_arena_coop_event_for_report(report)
    if arena_coop_event:
        return {"player_side": "attacker", "raid_run": None}

    inferred_side = infer_side_from_guest_ownership(report, manor_id)
    if inferred_side:
        return {"player_side": inferred_side, "raid_run": None}

    if report.manor_id == manor_id:
        return {"player_side": "attacker", "raid_run": None}
    if report.messages.filter(manor_id=manor_id).exists():
        return {"player_side": "defender", "raid_run": None}
    return {"player_side": "attacker", "raid_run": None}


def collect_template_keys(attacker_team: list[dict[str, Any]], defender_team: list[dict[str, Any]]) -> set[str]:
    template_keys: set[str] = set()
    for member in attacker_team + defender_team:
        key = str(member.get("template_key") or "").strip()
        if key:
            template_keys.add(key)
    return template_keys


def extract_valid_guest_ids(team: list[dict[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for member in team:
        raw_id = member.get("guest_id")
        if raw_id is None:
            continue
        if not isinstance(raw_id, (int, str)):
            continue
        try:
            guest_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if guest_id > 0:
            ids.add(guest_id)
    return ids


def load_avatar_map(template_keys: set[str]) -> dict[str, str]:
    avatar_map: dict[str, str] = {}
    if not template_keys:
        return avatar_map

    for template in GuestTemplate.objects.filter(key__in=template_keys):
        if template.avatar:
            avatar_map[template.key] = template.avatar.url
    return avatar_map


def attach_avatar_urls(team: list[dict[str, Any]], avatar_map: dict[str, str]) -> None:
    for member in team:
        member["avatar_url"] = avatar_map.get(str(member.get("template_key") or ""), "")


def resolve_perspective(
    report: "BattleReport",
    player_side: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], dict[str, int], dict[str, Any], dict[str, Any]]:
    losses = report.losses or {}
    if player_side == "spectator":
        return (
            report.attacker_team or [],
            report.defender_team or [],
            report.attacker_troops or {},
            report.defender_troops or {},
            losses.get("attacker", {}),
            losses.get("defender", {}),
        )
    if player_side == "defender":
        return (
            report.defender_team or [],
            report.attacker_team or [],
            report.defender_troops or {},
            report.attacker_troops or {},
            losses.get("defender", {}),
            losses.get("attacker", {}),
        )
    return (
        report.attacker_team or [],
        report.defender_team or [],
        report.attacker_troops or {},
        report.defender_troops or {},
        losses.get("attacker", {}),
        losses.get("defender", {}),
    )


def serialize_troops(troops_raw: dict[str, int], troop_definitions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": troop_definitions.get(key, {}).get("label", key),
            "count": count,
            "avatar": troop_definitions.get(key, {}).get("avatar"),
        }
        for key, count in troops_raw.items()
        if count
    ]


def merge_nonzero_drops(target: dict[str, int], source: dict[str, Any]) -> None:
    for key, amount in source.items():
        if amount:
            target[key] = target.get(key, 0) + int(amount)


def load_reward_label_maps(drop_keys: Iterable[str], loss_keys: Iterable[str]) -> tuple[dict[str, str], dict[str, str]]:
    all_keys = {str(key).strip() for key in tuple(drop_keys) + tuple(loss_keys) if str(key).strip()}
    if not all_keys:
        return {}, {}

    item_labels = get_item_template_names_by_keys(all_keys)
    skill_book_labels = {book.key: book.name for book in SkillBook.objects.filter(key__in=all_keys)}
    return item_labels, skill_book_labels


def build_drop_items(
    drops: dict[str, int],
    *,
    item_template_names_by_key: dict[str, str],
    skill_book_names_by_key: dict[str, str],
) -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": _RESOURCE_LABELS.get(key)
            or item_template_names_by_key.get(key)
            or skill_book_names_by_key.get(key)
            or key,
            "amount": amount,
        }
        for key, amount in drops.items()
    ]


def build_report_title(report: "BattleReport", *, player_side: str, viewer_manor_id: int) -> str:
    is_spectator = player_side == "spectator"
    if is_spectator:
        left_name = getattr(report.manor, "display_name", "") or "进攻方"
        right_name = (report.opponent_name or "").strip() or "防守方"
        return f"{left_name} vs {right_name} 战报"
    if player_side == "defender" and report.manor_id != viewer_manor_id:
        attacker_name = getattr(report.manor, "display_name", "") or ""
        return f"{attacker_name or report.opponent_name} 战报"
    return f"{report.opponent_name} 战报"


def build_side_labels(*, player_side: str, winner: str | None) -> dict[str, Any]:
    is_spectator = player_side == "spectator"
    context: dict[str, Any] = {
        "player_side": player_side,
        "is_spectator": is_spectator,
        "player_won": (winner == player_side) if not is_spectator else False,
        "my_side": "attacker" if is_spectator else player_side,
        "is_attacker": player_side == "attacker",
        "is_defender": player_side == "defender",
    }
    context["enemy_side"] = "defender" if context["my_side"] == "attacker" else "attacker"
    if is_spectator:
        context.update(
            {
                "left_team_title": "进攻方",
                "right_team_title": "防守方",
                "left_loss_title": "进攻方损失",
                "right_loss_title": "防守方损失",
                "spectator_result": (
                    "本场结果：进攻方胜利"
                    if winner == "attacker"
                    else "本场结果：防守方胜利" if winner == "defender" else "本场结果：不分胜负"
                ),
            }
        )
        return context

    context.update(
        {
            "left_team_title": "我方",
            "right_team_title": "敌方",
            "left_loss_title": "我方损失",
            "right_loss_title": "敌方损失",
        }
    )
    return context


def build_reward_context(
    *,
    drops: dict[str, int],
    loss_map: dict[str, int],
    capture_loss_label: str = "",
) -> dict[str, Any]:
    item_template_names, skill_book_names = load_reward_label_maps(drops.keys(), loss_map.keys())
    drop_items = build_drop_items(
        drops,
        item_template_names_by_key=item_template_names,
        skill_book_names_by_key=skill_book_names,
    )
    loss_items = build_drop_items(
        loss_map,
        item_template_names_by_key=item_template_names,
        skill_book_names_by_key=skill_book_names,
    )
    if capture_loss_label:
        loss_items.append({"key": "captured_guest", "label": capture_loss_label})
    return {
        "drop_items": drop_items,
        "has_drops": bool(drop_items),
        "loss_items": loss_items,
    }


def infer_side_from_guest_ownership(report: "BattleReport", manor_id: int) -> str | None:
    attacker_ids = extract_valid_guest_ids(report.attacker_team or [])
    defender_ids = extract_valid_guest_ids(report.defender_team or [])
    candidate_ids = attacker_ids | defender_ids
    if not candidate_ids:
        return None

    owned_ids = set(Guest.objects.filter(manor_id=manor_id, id__in=candidate_ids).values_list("id", flat=True))
    if not owned_ids:
        return None

    attacker_owned_count = len(attacker_ids & owned_ids)
    defender_owned_count = len(defender_ids & owned_ids)
    if attacker_owned_count > defender_owned_count:
        return "attacker"
    if defender_owned_count > attacker_owned_count:
        return "defender"
    return None


def resolve_report_raid_run(report: "BattleReport"):
    return _load_raid_run_for_report(report)


def resolve_display_drops(
    report: "BattleReport",
    *,
    player_won: bool,
    player_side: str,
    raid_run=None,
) -> dict[str, int]:
    raid_run = raid_run or resolve_report_raid_run(report)
    if not raid_run:
        return report.drops or {}

    drops: dict[str, int] = {}
    if not player_won:
        return drops

    if player_side == "attacker":
        merge_nonzero_drops(drops, raid_run.loot_resources or {})
        merge_nonzero_drops(drops, raid_run.loot_items or {})

    battle_rewards = raid_run.battle_rewards or {}
    exp_fruit = battle_rewards.get("exp_fruit", 0)
    if exp_fruit:
        drops["experience_fruit"] = drops.get("experience_fruit", 0) + int(exp_fruit)

    equipment = battle_rewards.get("equipment", {}) or {}
    merge_nonzero_drops(drops, equipment)
    return drops


def resolve_display_losses(*, player_won: bool, player_side: str, raid_run) -> dict[str, int]:
    if player_won or player_side == "spectator" or not raid_run:
        return {}

    losses: dict[str, int] = {}
    if player_side == "defender":
        merge_nonzero_drops(losses, raid_run.loot_resources or {})
        merge_nonzero_drops(losses, raid_run.loot_items or {})
    return losses


def resolve_capture_loss_label(*, player_side: str, raid_run) -> str:
    if not raid_run:
        return ""
    battle_rewards = raid_run.battle_rewards or {}
    capture_payload = battle_rewards.get("capture")
    if not isinstance(capture_payload, dict):
        return ""
    capture_from = str(capture_payload.get("from") or "").strip()
    if capture_from != player_side:
        return ""
    guest_name = str(capture_payload.get("guest_name") or "").strip()
    if not guest_name:
        return ""
    return f"门客被俘（{guest_name}）"


def determine_player_side(report: "BattleReport", *, manor_id: int) -> str:
    return str(resolve_report_runtime_context(report, manor_id=manor_id)["player_side"])
