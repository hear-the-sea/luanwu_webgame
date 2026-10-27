"""赛季队伍的只读视觉资料；不推演战斗或读取隐藏敌军。"""

from typing import Any


def expedition_portraits(run: Any, guests: dict[int, Any]) -> list[dict[str, Any]]:
    result = []
    for snapshot in run.lineup.get("guests", [])[:3]:
        guest = guests.get(snapshot.get("guest_id"))
        result.append(
            {
                "name": snapshot.get("display_name", "门客"),
                "avatar": snapshot.get("avatar_url") or (guest.avatar_url if guest else ""),
                "rarity_key": guest.rarity if guest else "black",
            }
        )
    return result


def rally_slots(rally: Any, runs: list[Any], guests: dict[int, Any], target: int) -> list[dict[str, Any]]:
    slots = []
    seen = set()
    for run in runs:
        if run.member_id in seen:
            continue
        seen.add(run.member_id)
        portraits = expedition_portraits(run, guests)
        slots.append(
            {
                "name": run.member.manor.display_name,
                "avatar": portraits[0]["avatar"] if portraits else "",
                "occupied": True,
                "is_leader": run.member_id == rally.leader_id,
                "portraits": portraits,
            }
        )
    size = min(5, max(target, len(slots)))
    slots.extend(
        {"name": "待加入", "avatar": "", "occupied": False, "is_leader": False} for _ in range(size - len(slots))
    )
    return slots[:5]
