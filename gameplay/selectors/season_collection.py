"""只读整理多场资源收集的进度；旧队伍没有多场快照时沿用原显示。"""

from typing import Any


def collection_progress(run: Any) -> dict[str, int]:
    snapshot = run.result.get("collection_waves", {})
    if run.kind != "pve" or not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        return {}
    maximum = max(1, int(snapshot["max_waves"]))
    completed = max(0, min(maximum, int(snapshot.get("completed", 0))))
    wins = max(0, min(completed, int(snapshot.get("wins", 0))))
    return {
        "maximum": maximum,
        "completed": completed,
        "wins": wins,
        "next_wave": min(maximum, completed + 1),
        "reward_percent": wins * max(0, int(snapshot["win_percent"])),
    }
