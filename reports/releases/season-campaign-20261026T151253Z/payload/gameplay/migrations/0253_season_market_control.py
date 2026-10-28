"""为进行中的联合赛季开放集市占领；仅追加配置，不重置原战况。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone

MARKET_SITE = {
    "name": "沿岸集市",
    "unlock_day": 1,
    "guards": 2,
    "durability": 400,
    "control_required": 100,
    "score_per_tick": 1,
    "protection_seconds": 1800,
}

MARKET_CONTROL = {
    "enabled": True,
    "refresh_hours": 24,
    "offer_count": 3,
    "owner_return_bonus_percent": 5,
    "pool": [
        {"key": "fruit_pack", "name": "修习补给", "cost": 80, "limit": 1, "items": {"experience_fruit": 5}},
        {"key": "essence_pack", "name": "玄铁精华", "cost": 100, "limit": 1, "items": {"xuan_tie_essence": 3}},
        {"key": "qingmang_sword", "name": "青芒剑", "cost": 120, "limit": 1, "items": {"equip_qingmangjian": 1}},
        {"key": "duanma_sword", "name": "断马剑", "cost": 120, "limit": 1, "items": {"equip_duanmajian": 1}},
        {"key": "feather_helmet", "name": "飞羽盔", "cost": 250, "limit": 1, "items": {"equip_feiyukui": 1}},
        {"key": "officer_sword", "name": "校尉长剑", "cost": 250, "limit": 1, "items": {"equip_xiaoweichangjian": 1}},
    ],
}


def enable_market_control(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    Site = apps.get_model("gameplay", "SeasonSite")
    database = schema_editor.connection.alias
    for season in Season.objects.using(database).filter(closed=False, ends_at__gt=timezone.now()).iterator():
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign", {})
        # 未使用联合攻城的旧季保持旧入口，明确设置过开关的赛季不覆盖。
        if not campaign.get("coalition", {}).get("enabled") or "market_control" in campaign:
            continue
        campaign["market_control"] = deepcopy(MARKET_CONTROL)
        campaign.setdefault("sites", {}).setdefault("river:market", deepcopy(MARKET_SITE))
        campaign.setdefault("expeditions", {}).setdefault("enemy_levels", {}).setdefault("river:market", 15)
        season.rules = rules
        season.save(using=database, update_fields=["rules"])
        mode = season.population.get("mode", "standard")
        market_site = campaign["sites"]["river:market"]
        market_level = campaign["expeditions"]["enemy_levels"]["river:market"]
        ratio = {"pioneer": 0.34, "skirmish": 0.67}.get(mode, 1)
        level_ratio = {"pioneer": 0.6, "skirmish": 0.85}.get(mode, 1)
        profile = {
            "guards": max(1, round(market_site["guards"] * ratio)),
            "durability": max(1, round(market_site["durability"] * ratio)),
            "level": max(1, round(market_level * level_ratio)),
        }
        Site.objects.using(database).get_or_create(
            season_id=season.pk,
            key="river:market",
            defaults={
                "guards": profile["guards"],
                "durability": profile["durability"],
                "npc_profile": profile,
                "last_score_at": max(timezone.now(), season.starts_at),
            },
        )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0252_season_resource_economy")]
    operations = [migrations.RunPython(enable_market_control, migrations.RunPython.noop)]
