"""用于区分守军波次与仍存活的建筑目标。"""


def cleared_guard_wave(report) -> bool:
    """守军门客全灭、进攻门客存活，箭塔仍在且本场不是城门战。"""
    defenses = report.defender_city_defenses or []
    if any(row.get("key") == "wall" for row in defenses):
        return False
    tower_alive = any(row.get("key") == "arrow_tower" and row.get("hp", 0) > 0 for row in defenses)
    defenders = report.defender_team or []
    return bool(
        tower_alive
        and defenders
        and all(row.get("remaining_hp", 0) <= 0 for row in defenders)
        and any(row.get("guest_id") and row.get("remaining_hp", 0) > 0 for row in report.attacker_team or [])
    )


def season_guard_wave_cleared(report) -> bool:
    """新战报读取标记；旧战报用可证明的车轮战流水只读兼容。"""
    if report.battle_type != "season":
        return False
    if (report.losses or {}).get("season_effects", {}).get("guard_wave_cleared"):
        return True
    if report.winner == "attacker" or not cleared_guard_wave(report):
        return False
    from gameplay.models import SeasonAction

    return SeasonAction.objects.filter(
        report=report,
        kind="combat",
        request_hash="settled-expedition",
        result__message="集结军车轮战",
        result__combat_side="attacker",
    ).exists()
