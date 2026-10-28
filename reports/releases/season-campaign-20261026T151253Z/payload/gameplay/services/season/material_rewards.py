"""讨伐与NPC辎重共享物资收益档，按发兵日预留并冻结数值。"""

from datetime import datetime
from typing import Any

from django.utils import timezone

from .resource_rules import resource_rules


def _used_slots(member: Any, now: datetime) -> set[int]:
    rows = member.expeditions.filter(
        kind__in=("pve", "npc_intercept"),
        result__material_reward__date=timezone.localdate(now).isoformat(),
    )
    return {
        int(row.result["material_reward"]["slot"])
        for row in rows.only("result")
        if not row.result.get("quota_refunded") and not row.result.get("material_reward_refunded")
    }


def material_budget_summary(member: Any, now: datetime) -> dict[str, Any]:
    config = resource_rules(member.season)
    curve = config["pve"]["material_reward_multipliers"]
    used = _used_slots(member, now) if config["enabled"] else set()
    slot = 1
    while slot in used:
        slot += 1
    return {
        "enabled": config["enabled"],
        "used": len(used),
        "full_remaining": sum(v == 1 and i not in used for i, v in enumerate(curve, 1)),
        "half_remaining": sum(0 < v < 1 and i not in used for i, v in enumerate(curve, 1)),
        "next_slot": slot,
        "next_multiplier": curve[slot - 1] if slot <= len(curve) else config["pve"]["reward_after_budget"],
    }


def reserve_material_reward_locked(member: Any, mode: str, now: datetime) -> dict[str, Any]:
    config = resource_rules(member.season)
    if not config["enabled"] or mode not in {"pve", "npc_intercept"}:
        return {}
    pve = config["pve"]
    from .campaign_rules import campaign_rules

    merit_by_node = {
        key: {"success": pve.get("action_merit", spec["merit"]), "failure": spec["failure_merit"]}
        for key, spec in campaign_rules(member.season)["economy"]["pve"].items()
    }
    summary = material_budget_summary(member, now)
    return {
        "material_reward": {
            "version": 4,
            "merit_by_node": merit_by_node,
            "effective_material_merit": pve.get("effective_material_merit", False),
            "date": timezone.localdate(now).isoformat(),
            "slot": summary["next_slot"],
            "multiplier": summary["next_multiplier"],
            "failure_multiplier": pve["failure_resource_multiplier"],
            "nodes": {
                "mountain:inn": pve["grain_run"],
                "forest:granary": pve["grain_run"],
                "forest:lodge": pve["wood_run"],
                "mountain:ruins": pve["iron_run"],
                "river:docks": pve["mixed_run"],
            },
            "npc_resources": pve["mixed_run"],
        }
    }


def material_resources_from_snapshot(snapshot: dict[str, Any], node_key: str, won: bool) -> dict[str, int]:
    base = (
        snapshot.get("npc_resources", {})
        if node_key == "npc_intercept"
        else snapshot.get("nodes", {}).get(node_key, {})
    )
    multiplier = float(snapshot["multiplier"]) * (1 if won else float(snapshot["failure_multiplier"]))
    return {key: int(value * multiplier) for key, value in base.items()}


def refund_material_reward_locked(run: Any) -> None:
    if run.result.get("material_reward") and not run.report_id:
        run.result = {**run.result, "material_reward_refunded": True}
