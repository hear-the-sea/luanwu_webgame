"""把下一关入场凭证从任务宝箱改为上一关直接掉落。"""

from copy import deepcopy

from django.db import migrations

# 上一关 -> 直接掉落、供下一关入场消耗的凭证。
PROGRESSION_DROPS = {
    "sishui_huaxiong_chengwei": "sishui_battle_report",
    "hulao_qunying_zhan_lvbu": "hulao_war_banner",
    "biyou_candeng": "wanyin_flag_fragment",
    "shier_xianyin": "yuxu_broken_seal",
    "zhanlong_yanshan_shizi": "zhanlong_white_horse_token",
    "zhanlong_changban_tuwei": "zhanlong_changban_guard_token",
    "zhanlong_changban_duanqiao": "zhanlong_dangyang_valor_letter",
}

# 仍由当前任务掉落的 v3 宝箱：移除凭证，只保留补给与随机奖励。
# 历史代次宝箱保持原 payload，兼容玩家仓库中的旧箱子。
CHEST_TOKENS = {
    "mission_chest_v3_sishui_huaxiong_chengwei": "sishui_battle_report",
    "mission_chest_v3_hulao_qunying_zhan_lvbu": "hulao_war_banner",
    "mission_chest_v3_biyou_candeng": "wanyin_flag_fragment",
    "mission_chest_v3_shier_xianyin": "yuxu_broken_seal",
    "mission_chest_v3_zhanlong_yanshan_shizi": "zhanlong_white_horse_token",
    "mission_chest_v3_zhanlong_changban_tuwei": "zhanlong_changban_guard_token",
    "mission_chest_v3_zhanlong_changban_duanqiao": "zhanlong_dangyang_valor_letter",
}

CHEST_TEXT_UPDATES = {
    "mission_chest_v3_sishui_huaxiong_chengwei": {
        "reward_theme": "飞将入口：必得军阵补给，有机会获得军阵兵器",
    },
    "mission_chest_v3_hulao_qunying_zhan_lvbu": {
        "reward_theme": "飞将中层：必得军阵补给，有机会获得吕布专属名器",
    },
    "mission_chest_v3_biyou_candeng": {
        "reward_theme": "封神入口：必得仙家补给，有机会获得校尉军备与狐仙图纸",
    },
    "mission_chest_v3_shier_xianyin": {
        "reward_theme": "封神中层：必得仙家补给，有机会获得阵法秘术与太清玉册",
    },
    "mission_chest_v3_zhanlong_yanshan_shizi": {
        "description": "燕山护民之行的任务宝箱，内有行军补给。",
        "reward_theme": "燕山识英雄：必得行军补给",
    },
    "mission_chest_v3_zhanlong_changban_tuwei": {
        "description": "长坂接应之行的任务宝箱，有机会获得照夜玉狮子。",
        "reward_theme": "长坂见忠勇：必得行军补给，有机会获得照夜玉狮子",
    },
    "mission_chest_v3_zhanlong_changban_duanqiao": {
        "description": "长坂桥头较量的任务宝箱，内有行军补给。",
        "reward_theme": "桥头试胆：必得行军补给",
    },
}


def apply_direct_progression_tokens(apps, schema_editor):
    database = schema_editor.connection.alias
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")

    for mission_key, token_key in PROGRESSION_DROPS.items():
        mission = MissionTemplate.objects.using(database).filter(key=mission_key).first()
        if mission is None:
            continue
        drops = dict(mission.drop_table or {})
        drops[token_key] = 1
        MissionTemplate.objects.using(database).filter(pk=mission.pk).update(drop_table=drops)

    for chest_key, token_key in CHEST_TOKENS.items():
        chest = ItemTemplate.objects.using(database).filter(key=chest_key).first()
        if chest is None:
            continue
        payload = deepcopy(chest.effect_payload or {})
        payload["item_rewards"] = [row for row in payload.get("item_rewards") or [] if row.get("item_key") != token_key]
        text = CHEST_TEXT_UPDATES[chest_key]
        payload["reward_theme"] = text["reward_theme"]
        ItemTemplate.objects.using(database).filter(pk=chest.pk).update(
            effect_payload=payload,
            description=text.get("description", chest.description),
        )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0272_season_verbose_names")]

    operations = [
        migrations.RunPython(apply_direct_progression_tokens, migrations.RunPython.noop, atomic=True),
    ]
