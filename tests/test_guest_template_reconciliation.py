"""Tests for reconciling mismatched player/bot guest stats and downgrade compensation."""

from __future__ import annotations

import importlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone

from core.config import GUEST
from gameplay.models import BotProfile, ItemTemplate, Message
from guests.growth_engine import extract_current_guest_growth
from guests.models import GearItem, GearTemplate, Guest, GuestStatus, GuestTemplate
from guests.services.template_reconciliation import (
    DOWNGRADE_COMPENSATION_ITEM_KEY,
    DOWNGRADE_COMPENSATION_TITLE,
    reconcile_mismatched_guests,
)

migration_0102 = importlib.import_module("guests.migrations.0102_reconcile_guest_attributes_to_templates")


@pytest.mark.django_db
def test_reconcile_updates_guests_and_compensates_only_downgraded_human_manors(django_user_model, manor_with_user):
    manor, _ = manor_with_user

    ItemTemplate.objects.get_or_create(
        key=DOWNGRADE_COMPENSATION_ITEM_KEY,
        defaults={
            "name": "超级门客召唤卡",
            "effect_type": "tool",
            "rarity": "orange",
            "effect_payload": {"action": "summon_orange_guest"},
        },
    )

    purple_civil_tpl = GuestTemplate.objects.create(
        key="reconcile_up_purple_civil",
        name="汤显祖",
        rarity="purple",
        archetype="civil",
        base_attack=42,
        base_intellect=46,
        base_defense=22,
        base_agility=48,
        base_luck=141,
        base_hp=800,
        growth_range=[6, 11],
        attribute_weights={"force": 15, "intellect": 45, "defense": 15, "agility": 25},
    )
    upgraded = Guest.objects.create(
        manor=manor,
        template=purple_civil_tpl,
        level=50,
        initial_force=36,
        initial_intellect=40,
        initial_defense=19,
        initial_agility=42,
        allocated_force=10,
        allocated_intellect=25,
        allocated_defense=5,
        allocated_agility=8,
        force=36 + 10 + 100 + 30 + 12,
        intellect=40 + 25 + 120 + 18,
        defense_stat=19 + 5 + 60 + 9,
        agility=42 + 8 + 80 + 20 + 7,
        luck=120 + 6,
        hp_bonus=500,
        attribute_points=17,
        super_disguise_applied=True,
        status=GuestStatus.IDLE,
        current_hp=5000,
    )
    ItemTemplate.objects.create(
        key="reconcile_gear_item",
        name="测试佩饰",
        effect_type="equip_ornament",
        rarity="blue",
        effect_payload={"force": 12, "intellect": 18, "defense": 9, "agility": 7, "luck": 6},
    )
    gear_tpl = GearTemplate.objects.create(
        key="reconcile_gear_item",
        name="测试佩饰",
        slot="ornament",
        rarity="blue",
        extra_stats={"force": 12, "intellect": 18, "defense": 9, "agility": 7, "luck": 6},
    )
    GearItem.objects.create(manor=manor, guest=upgraded, template=gear_tpl)
    old_up_max_hp = upgraded.max_hp
    upgraded.current_hp = old_up_max_hp // 2
    upgraded.save(update_fields=["current_hp"])

    # 降档玩家庄园（名下同时拥有 2 名降档门客，只应收到 1 张补偿卡）
    down_user = django_user_model.objects.create_user(username="reconcile_down_user", password="pw")
    down_manor = down_user.manor
    purple_mil_tpl = GuestTemplate.objects.create(
        key="reconcile_down_purple_mil",
        name="柴玉关",
        rarity="purple",
        archetype="military",
        base_attack=59,
        base_intellect=17,
        base_defense=51,
        base_agility=42,
        base_luck=62,
        base_hp=800,
        growth_range=[6, 11],
        attribute_weights={"force": 45, "intellect": 10, "defense": 25, "agility": 20},
    )
    blue_mil_tpl = GuestTemplate.objects.create(
        key="reconcile_down_blue_mil",
        name="周泰",
        rarity="blue",
        archetype="military",
        base_attack=51,
        base_intellect=15,
        base_defense=44,
        base_agility=37,
        base_luck=50,
        base_hp=600,
        growth_range=[5, 9],
        attribute_weights={"force": 45, "intellect": 10, "defense": 25, "agility": 20},
    )
    downgraded = Guest.objects.create(
        manor=down_manor,
        template=purple_mil_tpl,
        level=100,
        initial_force=64,
        initial_intellect=18,
        initial_defense=55,
        initial_agility=46,
        force=64 + 600,
        intellect=18 + 140,
        defense_stat=55 + 250,
        agility=46 + 150,
        luck=85,
        xisuidan_used=5,
        current_hp=999999,
    )
    Guest.objects.create(
        manor=down_manor,
        template=blue_mil_tpl,
        level=20,
        initial_force=59,
        initial_intellect=17,
        initial_defense=51,
        initial_agility=42,
        force=59 + 80,
        intellect=17 + 15,
        defense_stat=51 + 40,
        agility=42 + 30,
        luck=62,
        current_hp=1000,
    )

    # 仅同稀有度文武切换庄园（紫武转紫文，init_sum=169 == purple_mil_max），不应获发降档补偿
    flip_user = django_user_model.objects.create_user(username="reconcile_flip_user", password="pw")
    flip_manor = flip_user.manor
    flipped_tpl = GuestTemplate.objects.create(
        key="reconcile_flip_civil",
        name="郑和",
        rarity="purple",
        archetype="civil",
        base_attack=42,
        base_intellect=46,
        base_defense=22,
        base_agility=48,
        base_luck=141,
        base_hp=800,
        growth_range=[6, 11],
        attribute_weights={"force": 15, "intellect": 45, "defense": 15, "agility": 25},
    )
    flipped = Guest.objects.create(
        manor=flip_manor,
        template=flipped_tpl,
        level=80,
        initial_force=59,
        initial_intellect=17,
        initial_defense=51,
        initial_agility=42,
        force=59 + 300,
        intellect=17 + 80,
        defense_stat=51 + 170,
        agility=42 + 120,
        luck=62,
        current_hp=1,
    )
    Guest.objects.filter(pk=flipped.pk).update(current_hp=0)
    flipped.refresh_from_db()

    # 虚拟玩家庄园：属性应重匹配，但不应收到补偿邮件
    bot_user = django_user_model.objects.create_user(username="reconcile_bot_user", password="pw")
    bot_manor = bot_user.manor
    now = timezone.now()
    BotProfile.objects.create(
        manor=bot_manor,
        prestige_band="novice",
        growth_seed=1,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )
    bot_downgraded = Guest.objects.create(
        manor=bot_manor,
        template=blue_mil_tpl,
        level=10,
        initial_force=59,
        initial_intellect=17,
        initial_defense=51,
        initial_agility=42,
        force=59 + 30,
        intellect=17 + 10,
        defense_stat=51 + 15,
        agility=42 + 15,
        luck=62,
        current_hp=2000,
    )

    matched = Guest.objects.create(
        manor=manor,
        template=purple_mil_tpl,
        level=10,
        initial_force=59,
        initial_intellect=17,
        initial_defense=51,
        initial_agility=42,
        force=59 + 35,
        intellect=17 + 8,
        defense_stat=51 + 20,
        agility=42 + 12,
        luck=64,
        current_hp=1234,
    )
    custom_tpl, _ = GuestTemplate.objects.get_or_create(
        key="custom_guest_green",
        defaults={
            "name": "自建门客",
            "rarity": "green",
            "archetype": "military",
            "base_attack": 41,
            "base_intellect": 12,
            "base_defense": 35,
            "base_agility": 29,
            "base_luck": 47,
            "base_hp": 400,
        },
    )
    custom = Guest.objects.create(
        manor=manor,
        template=custom_tpl,
        level=20,
        initial_force=45,
        initial_intellect=50,
        initial_defense=24,
        initial_agility=52,
        force=95,
        intellect=100,
        defense_stat=74,
        agility=102,
        luck=165,
        current_hp=3000,
    )
    conv_tpl = GuestTemplate.objects.create(
        key=f"guest_rarity_conversion_blue_{custom_tpl.pk}",
        name="升阶测试",
        rarity="blue",
        archetype="civil",
        base_attack=29,
        base_intellect=32,
        base_defense=15,
        base_agility=33,
        base_luck=112,
        base_hp=400,
    )
    converted = Guest.objects.create(
        manor=manor,
        template=conv_tpl,
        level=20,
        initial_force=27,
        initial_intellect=34,
        initial_defense=14,
        initial_agility=34,
        force=60,
        intellect=90,
        defense_stat=35,
        agility=60,
        luck=110,
        rarity_pill_used=True,
        current_hp=1500,
    )

    migration_0102.apply_reconcile_guest_attributes(apps, SimpleNamespace(connection=connection))

    for guest in (upgraded, downgraded, flipped, bot_downgraded, matched, custom, converted):
        guest.refresh_from_db()

    # 1) 升阶：初始四维与运势匹配紫卡基值，成长落在 [6, 11] * 49，且手动加点/装备/超级易容保留
    assert (
        upgraded.initial_force + upgraded.initial_intellect + upgraded.initial_defense + upgraded.initial_agility
    ) == (42 + 46 + 22 + 48)
    assert 141 - 5 + 6 <= upgraded.luck <= 141 + 5 + 6
    assert (
        upgraded.allocated_force,
        upgraded.allocated_intellect,
        upgraded.allocated_defense,
        upgraded.allocated_agility,
    ) == (10, 25, 5, 8)
    assert upgraded.hp_bonus == 500
    assert upgraded.attribute_points == 17
    up_growth = extract_current_guest_growth(upgraded)
    assert 49 * 6 <= sum(up_growth.values()) <= 49 * 11
    assert upgraded.force >= upgraded.initial_force + 10 + 30 + 12
    assert upgraded.intellect >= upgraded.initial_intellect + 25 + 18
    expected_hp_ratio = (old_up_max_hp // 2) * upgraded.max_hp // old_up_max_hp
    assert upgraded.current_hp == expected_hp_ratio

    # 2) 降档：初始与成长严格收敛到新区间，且满血保持新上限
    assert (
        downgraded.initial_force
        + downgraded.initial_intellect
        + downgraded.initial_defense
        + downgraded.initial_agility
    ) == (59 + 17 + 51 + 42)
    assert 62 - 5 <= downgraded.luck <= 62 + 5
    down_growth = extract_current_guest_growth(downgraded)
    assert 99 * 6 <= sum(down_growth.values()) <= 99 * 11
    assert downgraded.current_hp == downgraded.max_hp
    assert downgraded.xisuidan_used == 5

    # 3) 文武切换与虚拟玩家降档：均完成属性对齐
    assert (flipped.initial_force + flipped.initial_intellect + flipped.initial_defense + flipped.initial_agility) == (
        42 + 46 + 22 + 48
    )
    assert flipped.initial_intellect > flipped.initial_force
    flip_growth = extract_current_guest_growth(flipped)
    assert 79 * 6 <= sum(flip_growth.values()) <= 79 * 11
    assert flip_growth["intellect"] > flip_growth["force"]
    assert flipped.current_hp == 0
    assert (
        bot_downgraded.initial_force
        + bot_downgraded.initial_intellect
        + bot_downgraded.initial_defense
        + bot_downgraded.initial_agility
    ) == (51 + 15 + 44 + 37)

    # 4) 已匹配/自建/升阶卡门客完全不受影响
    assert (
        matched.force,
        matched.intellect,
        matched.defense_stat,
        matched.agility,
        matched.luck,
        matched.current_hp,
    ) == (
        94,
        25,
        71,
        54,
        64,
        1234,
    )
    assert (custom.force, custom.intellect, custom.luck, custom.current_hp) == (95, 100, 165, 3000)
    assert (converted.initial_force, converted.force, converted.intellect, converted.current_hp) == (27, 60, 90, 1500)

    # 5) 补偿邮件：仅降档真人庄园收到 1 封超级门客召唤卡×1 奖励邮件；仅升阶/仅文武切换/虚拟玩家不发
    down_msgs = list(Message.objects.filter(manor=down_manor, title=DOWNGRADE_COMPENSATION_TITLE))
    assert len(down_msgs) == 1
    assert down_msgs[0].kind == "reward"
    assert down_msgs[0].attachments == {"bound_items": {DOWNGRADE_COMPENSATION_ITEM_KEY: 1}}
    assert down_msgs[0].is_deletion_protected is True
    assert Message.objects.filter(title=DOWNGRADE_COMPENSATION_TITLE).count() == 1
    assert not Message.objects.filter(manor=manor, title=DOWNGRADE_COMPENSATION_TITLE).exists()
    assert not Message.objects.filter(manor=flip_manor, title=DOWNGRADE_COMPENSATION_TITLE).exists()
    assert not Message.objects.filter(manor=bot_manor, title=DOWNGRADE_COMPENSATION_TITLE).exists()

    # 6) 幂等性：再次执行迁移与重匹配均不改属性且不重复发邮件
    snapshot_before = (
        upgraded.force,
        upgraded.intellect,
        upgraded.defense_stat,
        upgraded.agility,
        downgraded.force,
        flipped.intellect,
    )
    migration_0102.apply_reconcile_guest_attributes(apps, SimpleNamespace(connection=connection))
    assert reconcile_mismatched_guests() == 0
    assert Message.objects.filter(title=DOWNGRADE_COMPENSATION_TITLE).count() == 1
    for guest in (upgraded, downgraded, flipped):
        guest.refresh_from_db()
    assert (
        upgraded.force,
        upgraded.intellect,
        upgraded.defense_stat,
        upgraded.agility,
        downgraded.force,
        flipped.intellect,
    ) == snapshot_before
    assert GUEST.DEFENSE_TO_HP_MULTIPLIER == 40
