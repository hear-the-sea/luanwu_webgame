from __future__ import annotations

import pytest
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import ArenaCoopContribution, ArenaCoopEvent, ItemTemplate, Message
from gameplay.services.arena.coop_core import run_due_arena_coop_events
from gameplay.services.arena.coop_rewards import build_reward_breakdown
from gameplay.services.manor.core import ensure_manor
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor


@pytest.mark.django_db
def test_run_due_arena_coop_events_creates_contributions_and_rewards(monkeypatch):
    template = create_guest_template("arena_coop_resolve_tpl")
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.PREPARING,
        player_limit=5,
        guest_limit_per_entry=3,
        prepare_duration_seconds=120,
        prepare_ends_at=timezone.now(),
        boss_template_key="arena_gl_top_zhang_wuji_boss",
        enemy_snapshot={
            "boss": {"template_key": "arena_gl_top_zhang_wuji_boss", "display_name": "张无忌"},
            "guards": [
                {"template_key": "arena_gl_top_yang_xiao_guard", "display_name": "杨逍"},
                {"template_key": "arena_gl_top_wei_yixiao_guard", "display_name": "韦一笑"},
            ],
        },
        reward_snapshot={
            "rewards": {
                "participation_coins": 30,
                "clear_coins": 40,
                "damage_tiers": [
                    {"min_share_bps": 1000, "coins": 20},
                    {"min_share_bps": 2000, "coins": 50},
                ],
                "rank_rewards": {1: 80, 2: 50, 3: 30},
            },
            "rare_drop": {
                "item_key": "equip_tulongdao",
                "chance_bps": 10,
                "enabled": True,
                "requires_clear": True,
                "requires_minimum_contribution": True,
            },
        },
        daily_rule_snapshot={
            "registration": {"daily_participation_limit": 2},
            "contribution": {"minimum_share_bps": 500},
        },
    )

    entry_ids = []
    for idx in range(5):
        user = User.objects.create_user(
            username=f"arena_coop_resolve_{idx}",
            password="pass123",
            email=f"arena_coop_resolve_{idx}@test.local",
        )
        manor = ensure_manor(user)
        fund_manor(manor)
        guests = [create_guest(manor, template, f"{idx}_{slot}") for slot in ["A", "B", "C"]]
        entry = event.entries.create(manor=manor)
        entry_ids.append(entry.id)
        for slot_index, guest in enumerate(guests):
            stat_block = guest.stat_block()
            entry.entry_guests.create(
                guest=guest,
                slot_index=slot_index,
                snapshot={
                    "guest_id": guest.id,
                    "template_key": guest.template.key,
                    "display_name": guest.display_name,
                    "level": guest.level,
                    "rarity": guest.rarity,
                    "attack": stat_block["attack"],
                    "defense": stat_block["defense"],
                    "max_hp": guest.max_hp,
                    "current_hp": guest.current_hp,
                    "agility": guest.agility,
                    "skill_keys": [],
                },
            )

    report = BattleReport.objects.create(
        manor=event.entries.order_by("id").first().manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "damage": 1200,
                        "actor_owner_entry_id": entry_ids[0],
                        "target_template_key": "arena_gl_top_zhang_wuji_boss",
                        "target_is_boss": True,
                    },
                    {
                        "damage": 900,
                        "actor_owner_entry_id": entry_ids[1],
                        "target_template_key": "arena_gl_top_zhang_wuji_boss",
                        "target_is_boss": True,
                    },
                    {
                        "damage": 700,
                        "actor_owner_entry_id": entry_ids[2],
                        "target_template_key": "arena_gl_top_yang_xiao_guard",
                        "target_is_boss": False,
                    },
                    {
                        "damage": 500,
                        "actor_owner_entry_id": entry_ids[3],
                        "target_template_key": "arena_gl_top_wei_yixiao_guard",
                        "target_is_boss": False,
                    },
                    {
                        "damage": 300,
                        "actor_owner_entry_id": entry_ids[4],
                        "target_template_key": "arena_gl_top_zhang_wuji_boss",
                        "target_is_boss": True,
                    },
                ],
            }
        ],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now(),
        completed_at=timezone.now(),
        seed=1,
    )

    monkeypatch.setattr(
        "gameplay.services.arena.coop_core._run_coop_battle_locked",
        lambda locked_event, now: report,
    )
    monkeypatch.setattr("gameplay.services.arena.coop_rewards.random.random", lambda: 0.0)

    processed = run_due_arena_coop_events(limit=10)

    assert processed == 1
    event.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.COMPLETED
    assert event.boss_defeated is True
    contributions = list(ArenaCoopContribution.objects.filter(event=event).order_by("damage_rank"))
    assert [row.damage_rank for row in contributions] == [1, 2, 3, 4, 5]
    assert contributions[0].boss_damage == 1200
    assert contributions[0].total_coins > contributions[1].total_coins
    assert contributions[0].rare_drop_item_key == "equip_tulongdao"
    assert contributions[0].rare_drop_granted is True


@pytest.mark.django_db
def test_run_due_arena_coop_events_sends_battle_and_settlement_messages(monkeypatch):
    ItemTemplate.objects.create(
        key="equip_tulongdao",
        name="屠龙刀",
        effect_type=ItemTemplate.EffectType.TOOL,
    )
    template = create_guest_template("arena_coop_message_tpl")
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.PREPARING,
        player_limit=5,
        guest_limit_per_entry=3,
        prepare_duration_seconds=120,
        prepare_ends_at=timezone.now(),
        boss_template_key="arena_gl_top_zhang_wuji_boss",
        boss_name="张无忌",
        enemy_snapshot={
            "boss": {"template_key": "arena_gl_top_zhang_wuji_boss", "display_name": "张无忌"},
            "guards": [],
        },
        reward_snapshot={
            "rewards": {
                "participation_coins": 30,
                "clear_coins": 40,
                "damage_tiers": [{"min_share_bps": 1000, "coins": 20}],
                "rank_rewards": {1: 80},
            },
            "rare_drop": {
                "item_key": "equip_tulongdao",
                "chance_bps": 10,
                "enabled": True,
                "requires_clear": True,
                "requires_minimum_contribution": True,
            },
        },
        daily_rule_snapshot={"contribution": {"minimum_share_bps": 500}},
    )

    manors = []
    entry_ids = []
    for idx in range(5):
        user = User.objects.create_user(
            username=f"arena_coop_message_{idx}",
            password="pass123",
            email=f"arena_coop_message_{idx}@test.local",
        )
        manor = ensure_manor(user)
        fund_manor(manor)
        manors.append(manor)
        guests = [create_guest(manor, template, f"{idx}_{slot}") for slot in ["A", "B", "C"]]
        entry = event.entries.create(manor=manor)
        entry_ids.append(entry.id)
        for slot_index, guest in enumerate(guests):
            stat_block = guest.stat_block()
            entry.entry_guests.create(
                guest=guest,
                slot_index=slot_index,
                snapshot={
                    "guest_id": guest.id,
                    "template_key": guest.template.key,
                    "display_name": guest.display_name,
                    "level": guest.level,
                    "rarity": guest.rarity,
                    "attack": stat_block["attack"],
                    "defense": stat_block["defense"],
                    "max_hp": guest.max_hp,
                    "current_hp": guest.current_hp,
                    "agility": guest.agility,
                    "skill_keys": [],
                },
            )

    report = BattleReport.objects.create(
        manor=manors[0],
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "damage": 2000,
                        "actor_owner_entry_id": entry_ids[0],
                        "target_template_key": "arena_gl_top_zhang_wuji_boss",
                        "target_is_boss": True,
                    },
                    {
                        "damage": 100,
                        "actor_owner_entry_id": entry_ids[1],
                        "target_template_key": "arena_gl_top_zhang_wuji_boss",
                        "target_is_boss": True,
                    },
                ],
            }
        ],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=timezone.now(),
        completed_at=timezone.now(),
        seed=2,
    )

    monkeypatch.setattr(
        "gameplay.services.arena.coop_core._run_coop_battle_locked",
        lambda locked_event, now: report,
    )
    monkeypatch.setattr("gameplay.services.arena.coop_rewards.random.random", lambda: 0.0)

    processed = run_due_arena_coop_events(limit=10)

    assert processed == 1
    top_messages = list(Message.objects.filter(manor=manors[0]).order_by("kind", "title"))
    assert len(top_messages) == 2
    battle_message = Message.objects.get(manor=manors[0], kind=Message.Kind.BATTLE)
    reward_message = Message.objects.get(manor=manors[0], kind=Message.Kind.REWARD)
    assert battle_message.battle_report_id == report.id
    assert "战报" in battle_message.title
    assert "围攻光明顶" in battle_message.body
    assert "总伤害 2000" in reward_message.body
    assert "Boss伤害 2000" in reward_message.body
    assert "排名第 1" in reward_message.body
    assert "角斗币 170" in reward_message.body
    assert "屠龙刀" in reward_message.body


@pytest.mark.django_db
def test_run_due_arena_coop_events_updates_boss_remaining_hp_on_failed_attempt(monkeypatch):
    template = create_guest_template("arena_coop_failed_attempt_tpl")
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.PREPARING,
        player_limit=5,
        guest_limit_per_entry=3,
        prepare_duration_seconds=120,
        prepare_ends_at=timezone.now(),
        boss_template_key="arena_gl_top_zhang_wuji_boss",
        boss_name="张无忌",
        boss_initial_hp=300000,
        boss_remaining_hp=300000,
        enemy_snapshot={
            "boss": {"template_key": "arena_gl_top_zhang_wuji_boss", "display_name": "张无忌"},
            "guards": [],
        },
        reward_snapshot={
            "rewards": {
                "participation_coins": 30,
                "clear_coins": 40,
                "damage_tiers": [{"min_share_bps": 1000, "coins": 20}],
                "rank_rewards": {1: 80},
            },
            "rare_drop": {
                "item_key": "equip_tulongdao",
                "chance_bps": 10,
                "enabled": True,
                "requires_clear": True,
                "requires_minimum_contribution": True,
            },
        },
        daily_rule_snapshot={"contribution": {"minimum_share_bps": 500}},
    )

    for idx in range(5):
        user = User.objects.create_user(
            username=f"arena_coop_failed_attempt_{idx}",
            password="pass123",
            email=f"arena_coop_failed_attempt_{idx}@test.local",
        )
        manor = ensure_manor(user)
        fund_manor(manor)
        guests = [create_guest(manor, template, f"{idx}_{slot}") for slot in ["A", "B", "C"]]
        entry = event.entries.create(manor=manor)
        for slot_index, guest in enumerate(guests):
            stat_block = guest.stat_block()
            entry.entry_guests.create(
                guest=guest,
                slot_index=slot_index,
                snapshot={
                    "guest_id": guest.id,
                    "template_key": guest.template.key,
                    "display_name": guest.display_name,
                    "level": guest.level,
                    "rarity": guest.rarity,
                    "attack": stat_block["attack"],
                    "defense": stat_block["defense"],
                    "max_hp": guest.max_hp,
                    "current_hp": guest.current_hp,
                    "agility": guest.agility,
                    "skill_keys": [],
                },
            )

    report = BattleReport.objects.create(
        manor=event.entries.order_by("id").first().manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[],
        attacker_troops={},
        defender_team=[
            {
                "name": "张无忌",
                "guest_id": None,
                "template_key": "arena_gl_top_zhang_wuji_boss",
                "initial_hp": 300000,
                "remaining_hp": 123456,
            }
        ],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="defender",
        starts_at=timezone.now(),
        completed_at=timezone.now(),
        seed=7,
    )

    monkeypatch.setattr(
        "gameplay.services.arena.coop_core._run_coop_battle_locked",
        lambda locked_event, now: report,
    )

    processed = run_due_arena_coop_events(limit=10)

    assert processed == 1
    event.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.COMPLETED
    assert event.boss_defeated is False
    assert event.boss_initial_hp == 300000
    assert event.boss_remaining_hp == 123456


def test_build_reward_breakdown_respects_optional_rare_drop_gates(monkeypatch):
    monkeypatch.setattr("gameplay.services.arena.coop_rewards.random.random", lambda: 0.0)

    rewards = build_reward_breakdown(
        {
            "damage_rank": 1,
            "damage_share_bps": 100,
            "met_minimum_contribution": False,
        },
        rules={
            "rewards": {
                "participation_coins": 30,
                "clear_coins": 40,
                "damage_tiers": [],
                "rank_rewards": {1: 80},
            },
            "rare_drop": {
                "enabled": True,
                "item_key": "equip_tulongdao",
                "chance_bps": 10000,
                "requires_clear": False,
                "requires_minimum_contribution": False,
            },
        },
        boss_defeated=False,
    )

    assert rewards["rare_drop_granted"] is True
    assert rewards["rare_drop_item_key"] == "equip_tulongdao"
