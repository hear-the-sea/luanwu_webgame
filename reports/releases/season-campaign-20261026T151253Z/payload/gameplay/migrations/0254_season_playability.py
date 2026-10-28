"""为进行中的联合赛季追加少人协作规则，不追溯重分阵营或清空旧积分。"""

from copy import deepcopy

from django.db import migrations, transaction
from django.utils import timezone

PLAYABILITY = {
    "enabled": True,
    "balance": {
        "active_days": 7,
        "activity_weight": 45,
        "power_weight": 35,
        "prestige_weight": 20,
        "playtime_options": ["any", "day", "evening", "late"],
    },
    "npc": {
        "level_percent_by_players": {"1": 70, "2": 85, "3": 100, "4": 100, "5": 100},
        "max_guests_by_players": {"1": 1, "2": 2, "3": 3, "4": 3, "5": 3},
    },
    "reservation": {"maximum_hours": 24, "guest_limit": 3},
    "weekly": {
        "win_points": 10,
        "draw_points": 5,
        "minimum_contested_days": 1,
        "goal_per_active": 100,
        "goal_minimum": 200,
        "goal_maximum": 1200,
        "participation_merit": 50,
        "personal_items": {"experience_fruit": 3, "xuan_tie_essence": 2},
        "team_items": {"experience_fruit": 2, "xuan_tie_essence": 1},
        "ranking_base_items": {"experience_fruit": 25, "xuan_tie_essence": 20},
        "ranking_bonus_percent": {"elite": 20, "distinguished": 10, "participant": 0},
    },
    "site_effects": {"gate_march_reduction_percent": 10},
    "recommended_battle_hours": [20, 22],
}


def enable_playability(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    Faction = apps.get_model("gameplay", "SeasonFaction")
    database = schema_editor.connection.alias
    now = timezone.now()
    with transaction.atomic(using=database):
        for season in Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=now):
            rules = deepcopy(season.rules)
            campaign = rules.get("campaign", {})
            if not campaign.get("coalition", {}).get("enabled") or "playability" in campaign:
                continue
            campaign["playability"] = deepcopy(PLAYABILITY)
            season.rules = rules
            season.save(using=database, update_fields=["rules"])
            for state in Faction.objects.using(database).filter(season_id=season.pk):
                book = deepcopy(state.economy_state)
                book.setdefault(
                    "weekly_campaign",
                    {"activated_at": max(now, season.starts_at).isoformat(), "legacy_score": state.score, "weeks": {}},
                )
                state.economy_state = book
                state.save(using=database, update_fields=["economy_state"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0253_season_market_control")]
    operations = [migrations.RunPython(enable_playability, migrations.RunPython.noop)]
