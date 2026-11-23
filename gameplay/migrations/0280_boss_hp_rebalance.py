"""同步下调高血量 BOSS 门客模板的基础生命值（-20%），匹配 v11 战斗引擎伤害水位。"""

from __future__ import annotations

from django.core.cache import cache
from django.db import migrations, transaction

BOSS_BASE_HP_UPDATES: dict[str, tuple[int, int]] = {
    # 斩龙任务链 BOSS
    "task_zhanlong_tiebeixiong": (650000, 520000),
    "task_zhanlong_caochun": (800000, 640000),
    "task_zhanlong_zhangfei_bridge": (850000, 680000),
    "task_zhanlong_guanyu": (250000, 200000),
    "task_zhanlong_zhangfei": (180000, 144000),
    "task_zhanlong_zhaoyun": (280000, 224000),
    "task_zhanlong_machao": (160000, 128000),
    "task_zhanlong_huangzhong": (100000, 80000),
    # 个人帮会民间传说 BOSS
    "task_legend_crane_attendant": (80000, 64000),
    "task_legend_deer_attendant": (62000, 49600),
    "task_legend_crab_general": (128000, 102400),
    "task_legend_shrimp_vanguard": (122000, 97600),
    "task_legend_east_sea_dragon": (300000, 240000),
    "task_legend_meishan_guard": (360000, 288000),
    "task_legend_celestial_hound": (280000, 224000),
    "task_legend_erlang_shen": (400000, 320000),
    # 姓氏传说与修行突破护法
    "task_surname_li_elai": (300000, 240000),
    "task_cultivation_vajra_guard": (100000, 80000),
    "task_cultivation_mind_guard": (100000, 80000),
    "task_cultivation_demon_guard": (90000, 72000),
    # 常规与帮会副本高血量首领
    "task_matchmaking_bear": (19600, 15680),
    "task_fox_spirit_keeper": (12060, 9650),
    "task_heaven_cave_sentinel": (150000, 120000),
    "guild_wulinzhai_zhai_zhu": (24800, 19840),
    "task_simian_chuge_hanxin_boss": (80000, 64000),
    "task_simian_chuge_pengyue_boss": (80000, 64000),
    "task_simian_chuge_yingbu_boss": (80000, 64000),
    "task_barbarian_chanyu": (100000, 80000),
}


def _sync_boss_base_hp(apps, schema_editor, *, reverse: bool = False) -> None:
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    database = schema_editor.connection.alias
    for key, (old_hp, new_hp) in BOSS_BASE_HP_UPDATES.items():
        target_hp = old_hp if reverse else new_hp
        GuestTemplate.objects.using(database).filter(key=key).update(base_hp=target_hp)
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


def apply_boss_hp_rebalance(apps, schema_editor) -> None:
    _sync_boss_base_hp(apps, schema_editor, reverse=False)


def revert_boss_hp_rebalance(apps, schema_editor) -> None:
    _sync_boss_base_hp(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0279_equipment_callback_rebalance"),
    ]

    operations = [
        migrations.RunPython(apply_boss_hp_rebalance, revert_boss_hp_rebalance, atomic=True),
    ]
