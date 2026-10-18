"""帮会资产页的护院数量查询。"""

from collections import defaultdict
from typing import Any

from battle.models import TroopTemplate
from core.game_data.technology import troop_display_sort_key

from ..models import Guild, GuildMissionRun, GuildRaidRun, GuildTroopStorage
from .cavalry import CAVALRY_ITEM_KEY, CAVALRY_NAME
from .guild_troops import calculate_surviving_guild_troops


def get_guild_troop_assets(guild: Guild, *, cavalry: dict[str, Any]) -> dict[str, Any]:
    """按归属与当前状态汇总，不把成员个人护院或尚未完成的训练计入现有兵力。"""
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"stationed": 0, "deployed": 0, "redeemable": 0, "training": 0}
    )
    for key, count in GuildTroopStorage.objects.filter(guild=guild, count__gt=0).values_list(
        "troop_template__key", "count"
    ):
        counts[key]["stationed"] += count

    missions = GuildMissionRun.objects.filter(guild=guild, status=GuildMissionRun.Status.ACTIVE).select_related(
        "battle_report"
    )
    for mission in missions:
        for key, count in calculate_surviving_guild_troops(mission.troop_loadout, mission.battle_report).items():
            counts[key]["deployed"] += count
    raids = GuildRaidRun.objects.filter(
        attacker_guild=guild,
        status__in=[
            GuildRaidRun.Status.MARCHING,
            GuildRaidRun.Status.BATTLING,
            GuildRaidRun.Status.RETURNING,
            GuildRaidRun.Status.RETREATED,
        ],
        completed_at__isnull=True,
    ).select_related("battle_report")
    for raid in raids:
        report = None if raid.status == GuildRaidRun.Status.RETREATED else raid.battle_report
        for key, count in calculate_surviving_guild_troops(raid.troop_loadout, report).items():
            counts[key]["deployed"] += count

    if cavalry["stock"]:
        counts[CAVALRY_ITEM_KEY]["redeemable"] = cavalry["stock"]
    if cavalry["training"]:
        counts[CAVALRY_ITEM_KEY]["training"] = cavalry["training"].quantity
    templates = {
        template.key: template
        for template in TroopTemplate.objects.filter(key__in=counts).only("key", "name", "avatar")
    }
    names = {key: template.name for key, template in templates.items()}
    names.setdefault(CAVALRY_ITEM_KEY, CAVALRY_NAME)
    rows: list[dict[str, Any]] = [
        {
            "key": key,
            "name": names.get(key, "未知护院"),
            "avatar_url": templates[key].avatar.url if key in templates and templates[key].avatar else "",
            **counts[key],
            "total": counts[key]["stationed"] + counts[key]["deployed"] + counts[key]["redeemable"],
        }
        for key in sorted(counts, key=troop_display_sort_key)
    ]
    return {
        "rows": rows,
        "kinds": len(rows),
        "total": sum(row["total"] for row in rows),
        **{field: sum(row[field] for row in rows) for field in ("stationed", "deployed", "redeemable", "training")},
    }
