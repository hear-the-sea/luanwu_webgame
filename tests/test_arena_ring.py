from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from gameplay.models import (
    ArenaRing,
    ArenaRingDaily,
    ArenaRingInspection,
    ArenaRingMatch,
    ArenaRingTenure,
    PlayerTroop,
    ResourceEvent,
    TroopBankStorage,
)
from gameplay.services.arena.ring import (
    challenge_ring,
    inspect_ring,
    leave_ring,
    occupy_ring,
    refresh_due_rings,
    update_ring_lineup,
)
from gameplay.services.arena.ring_snapshots import ArenaRingError
from tests.arena_services.support import create_guest, create_guest_template

pytestmark = pytest.mark.django_db


@pytest.fixture
def ring_players(manor_factory):
    a, _ = manor_factory(username="ring_a")
    b, _ = manor_factory(username="ring_b")
    template = create_guest_template("ring_guest")
    ga = create_guest(a, template, "A")
    gb = create_guest(b, template, "B")
    a.silver = b.silver = 1000000
    a.save(update_fields=["silver"])
    b.save(update_fields=["silver"])
    for key, name in (("qinglong", "青龙擂"), ("baihu", "白虎擂")):
        ArenaRing.objects.get_or_create(key=key, defaults={"name": name})
    return a, b, ga, gb


def occupy(a, ga, troops=None):
    ring = ArenaRing.objects.get(key="qinglong")
    return occupy_ring(
        manor=a,
        ring_key=ring.key,
        guest_ids=[ga.pk],
        troop_loadout=troops or {},
        action_token=uuid4(),
        expected_version=ring.version,
    )


def target(manor, occupied, token=None):
    return dict(
        manor=manor,
        ring_key="qinglong",
        action_token=token or uuid4(),
        expected_version=occupied["version"],
        expected_tenure_id=occupied["tenure_id"],
    )


def ready_challenge_ring(**kwargs):
    ArenaRingMatch.objects.filter(tenure_id=kwargs["expected_tenure_id"], attacker=kwargs["manor"]).update(
        created_at=timezone.now() - timedelta(minutes=10)
    )
    return challenge_ring(**kwargs)


def test_paid_inspection_exact_transfer_idempotent_and_stale(ring_players):
    a, b, ga, _ = ring_players
    occupied = occupy(a, ga)
    kwargs = target(b, occupied)
    result = inspect_ring(**kwargs)
    assert inspect_ring(**kwargs) == result
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.silver == 1180000
    assert b.silver == 800000
    receipt = ArenaRingInspection.objects.get(pk=result["inspection_id"])
    assert receipt.buyer_id == b.pk and receipt.lineup["guests"][0]["guest_id"] == ga.pk
    assert ResourceEvent.objects.filter(manor=b, delta=-200000).count() == 1
    inspect_ring(**target(b, occupied))
    assert ArenaRingInspection.objects.count() == 2
    update_ring_lineup(**target(a, occupied), guest_ids=[ga.pk], troop_loadout={})
    with pytest.raises(ArenaRingError, match="已变化"):
        inspect_ring(**target(b, occupied))
    b.refresh_from_db()
    assert b.silver == 600000


def test_own_inspection_free_and_single_occupation(ring_players):
    a, _, ga, _ = ring_players
    occupied = occupy(a, ga)
    assert inspect_ring(**target(a, occupied))["lineup"]["guest_count"] == 1
    assert ArenaRingInspection.objects.count() == 0
    ring = ArenaRing.objects.get(key="baihu")
    with pytest.raises(ArenaRingError, match="最多"):
        occupy_ring(
            manor=a,
            ring_key=ring.key,
            guest_ids=[ga.pk],
            troop_loadout={},
            action_token=uuid4(),
            expected_version=ring.version,
        )


def test_paid_inspection_settles_accrued_silver_before_spending(ring_players, monkeypatch):
    a, b, ga, _ = ring_players
    occupied = occupy(a, ga)
    b.silver = 190000
    b.save(update_fields=["silver"])
    settlements = []

    def settle_accrued_silver(locked_manor, **_kwargs):
        settlements.append(locked_manor.pk)
        locked_manor.silver += 20000
        locked_manor.save(update_fields=["silver"])

    monkeypatch.setattr("gameplay.services.resources._sync_resource_production_locked", settle_accrued_silver)
    kwargs = target(b, occupied)
    result = inspect_ring(**kwargs)
    assert inspect_ring(**kwargs) == result
    a.refresh_from_db()
    b.refresh_from_db()
    assert settlements == [b.pk]
    assert b.silver == 10000
    assert a.silver == 1180000
    assert ResourceEvent.objects.filter(manor=b, delta=-200000).count() == 1


def test_inspection_rechecks_expiry_after_waiting_for_locks(ring_players, monkeypatch):
    from gameplay.services.arena import ring as ring_service

    a, b, ga, _ = ring_players
    occupied = occupy(a, ga)
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    lock_original = ring_service._lock_rings_and_manors

    def lock_after_tenure_expiry(manor_id):
        locked = lock_original(manor_id)
        monkeypatch.setattr(ring_service.timezone, "now", lambda: tenure.ends_at + timedelta(seconds=1))
        return locked

    monkeypatch.setattr(ring_service, "_lock_rings_and_manors", lock_after_tenure_expiry)
    with pytest.raises(ArenaRingError, match="守擂已结束"):
        inspect_ring(**target(b, occupied))
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.silver == b.silver == 1000000
    assert not ArenaRingInspection.objects.exists()


def test_time_rewards_expire_and_reoccupation_cooldown(ring_players):
    a, _, ga, _ = ring_players
    occupied = occupy(a, ga)
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    now = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
    tenure.started_at = tenure.settled_at = now
    tenure.ends_at = now + timedelta(hours=2)
    tenure.save(update_fields=["started_at", "settled_at", "ends_at"])
    refresh_due_rings(now=now + timedelta(minutes=20))
    refresh_due_rings(now=now + timedelta(minutes=20))
    daily = ArenaRingDaily.objects.get(manor=a)
    assert daily.seconds == 1200 and daily.time_coins == 8
    refresh_due_rings(now=now + timedelta(hours=3))
    tenure.refresh_from_db()
    a.refresh_from_db()
    assert tenure.ended_at == tenure.ends_at
    assert a.arena_coins == 48
    ring = ArenaRing.objects.get(key="qinglong")
    assert ring.current_tenure_id is None


def test_time_reward_crossing_daily_cap_awards_only_remaining_coins(ring_players):
    a, _, ga, _ = ring_players
    occupied = occupy(a, ga)
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    start = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
    tenure.started_at = tenure.settled_at = start
    tenure.ends_at = start + timedelta(hours=2)
    tenure.save(update_fields=["started_at", "settled_at", "ends_at"])
    daily = ArenaRingDaily.objects.create(manor=a, date=start.date(), seconds=17400, time_coins=116)
    refresh_due_rings(now=start + timedelta(minutes=20))
    refresh_due_rings(now=start + timedelta(minutes=20))
    refresh_due_rings(now=start + timedelta(minutes=40))
    daily.refresh_from_db()
    a.refresh_from_db()
    assert daily.seconds == 19800
    assert daily.time_coins == 120
    assert a.arena_coins == 4


def test_update_snapshot_and_bank_available_stock_no_depletion(ring_players):
    a, _, ga, _ = ring_players
    troop = TroopTemplate.objects.create(key="ring_test_troop", name="测试兵")
    PlayerTroop.objects.create(manor=a, troop_template=troop, count=10)
    TroopBankStorage.objects.create(manor=a, troop_template=troop, count=20)
    ga.current_hp = 1
    ga.save(update_fields=["current_hp"])
    occupied = occupy(a, ga, {troop.key: 30})
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    assert tenure.lineup["guests"][0]["current_hp"] == tenure.lineup["guests"][0]["max_hp"]
    new = update_ring_lineup(**target(a, occupied), guest_ids=[ga.pk], troop_loadout={troop.key: 10})
    tenure.refresh_from_db()
    assert tenure.lineup["troop_count"] == 10 and tenure.started_at == tenure.settled_at
    assert new["version"] > occupied["version"]
    assert PlayerTroop.objects.get(manor=a).count == 10
    assert TroopBankStorage.objects.get(manor=a).count == 20
    ga.refresh_from_db()
    assert ga.current_hp == 1
    update_ring_lineup(**target(a, new), guest_ids=[ga.pk], troop_loadout={troop.key: 100})
    tenure.refresh_from_db()
    assert tenure.lineup["troops"] == {troop.key: 100}
    assert PlayerTroop.objects.get(manor=a).count == 10
    assert TroopBankStorage.objects.get(manor=a).count == 20


def test_challenge_first_pair_rewards_and_no_real_damage(ring_players, monkeypatch):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)

    def simulate(**kwargs):
        assert kwargs["apply_damage"] is False and kwargs["fill_default_troops"] is False
        return BattleReport.objects.create(
            manor=b,
            opponent_name="A",
            winner="defender",
            battle_type="arena",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    kwargs = target(b, occupied)
    kwargs.update(guest_ids=[gb.pk], troop_loadout={})
    result = ready_challenge_ring(**kwargs)
    assert ready_challenge_ring(**kwargs) == result
    ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.arena_coins == 20 and b.arena_coins == 10
    assert ArenaRingMatch.objects.count() == 2
    assert ArenaRingDaily.objects.get(manor=b).challenges == 2
    assert ArenaRingTenure.objects.get(pk=occupied["tenure_id"]).defenses == 2
    update_ring_lineup(**target(a, occupied), guest_ids=[ga.pk], troop_loadout={})


def test_leave_keeps_rewards_and_cooldown(ring_players):
    a, _, ga, _ = ring_players
    occupied = occupy(a, ga)
    leave_ring(**target(a, occupied))
    with pytest.raises(ArenaRingError, match="10"):
        occupy(a, ga)


@pytest.mark.parametrize("troop_count,legacy_oversized", [(10, False), (100, False), (100, True)])
def test_real_battle_uses_full_technology_and_frozen_device_bonuses(
    ring_players, monkeypatch, troop_count, legacy_oversized
):
    from django.core.cache import cache

    import battle.execution as execution
    from gameplay.models import ItemTemplate, PlayerTechnology
    from guests.models import GearItem, GearTemplate

    a, b, ga, gb = ring_players
    troop, _ = TroopTemplate.objects.update_or_create(
        key="dao_ke",
        defaults={"name": "刀客", "base_attack": 6, "base_defense": 3, "base_hp": 18},
    )
    item = ItemTemplate.objects.create(
        key="ring_device",
        name="擂台兵器",
        effect_type="equip_device",
        effect_payload={"troop_stat_bonus": {"dao": {"attack_flat": 2}}},
    )
    gear_template = GearTemplate.objects.create(key=item.key, name=item.name, slot="device")
    for manor, guest in ((a, ga), (b, gb)):
        PlayerTroop.objects.create(manor=manor, troop_template=troop, count=10)
        PlayerTechnology.objects.update_or_create(manor=manor, tech_key="dao_attack", defaults={"level": 2})
        GearItem.objects.create(manor=manor, guest=guest, template=gear_template)
    cache.delete("troop_templates:db")
    if troop_count > 10:
        PlayerTroop.objects.filter(manor=b).delete()
    occupied = occupy(a, ga, {troop.key: troop_count})
    PlayerTechnology.objects.filter(manor=a, tech_key="dao_attack").update(level=9)
    GearItem.objects.filter(manor=a).update(guest=None)
    # 已报名的旧科技快照也要在下一场按临时规则满级。
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    tenure.lineup["technology"] = {"levels": {"dao_attack": 2}}
    if legacy_oversized:
        tenure.lineup["troops"] = {troop.key: 1000000}
    tenure.save(update_fields=["lineup"])
    seen = {}
    original = execution.build_troop_combatants

    def capture(loadout, **kwargs):
        units = original(loadout, **kwargs)
        seen[kwargs["side"]] = units
        from gameplay.services.arena.ring_snapshots import build_ring_technology_payload

        assert kwargs["tech_levels"] == build_ring_technology_payload()["levels"]
        return units

    monkeypatch.setattr(execution, "build_troop_combatants", capture)
    result = ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={troop.key: troop_count})
    report = BattleReport.objects.get(pk=result["battle_report_id"])
    assert seen["attacker"][0].attack == pytest.approx(16 * troop_count)
    assert seen["defender"][0].attack == pytest.approx(16 * (ga.troop_capacity if legacy_oversized else troop_count))
    assert report.attacker_equipment_bonuses and report.defender_equipment_bonuses
    assert PlayerTechnology.objects.get(manor=a, tech_key="dao_attack").level == 9
    assert PlayerTechnology.objects.get(manor=b, tech_key="dao_attack").level == 2
    assert PlayerTroop.objects.get(manor=a).count == 10
    assert list(PlayerTroop.objects.filter(manor=b).values_list("count", flat=True)) == (
        [10] if troop_count == 10 else []
    )
    ga.refresh_from_db()
    gb.refresh_from_db()
    assert ga.status == "idle" and gb.status == "idle"


def test_virtual_troops_ignore_stock_but_obey_dispatch_cap(ring_players):
    a, _, ga, _ = ring_players
    guests = [ga] + [create_guest(a, ga.template, str(i)) for i in range(12)]
    ring = ArenaRing.objects.get(key="qinglong")
    result = occupy_ring(
        manor=a,
        ring_key=ring.key,
        guest_ids=[g.pk for g in guests],
        troop_loadout={},
        action_token=uuid4(),
        expected_version=ring.version,
    )
    assert ArenaRingTenure.objects.get(pk=result["tenure_id"]).lineup["guest_count"] == 13
    troop = TroopTemplate.objects.create(key="ring_cap_troop", name="护院")
    with pytest.raises(ArenaRingError, match="带兵上限"):
        update_ring_lineup(**target(a, result), guest_ids=[ga.pk], troop_loadout={troop.key: ga.troop_capacity + 1})
    update_ring_lineup(**target(a, result), guest_ids=[ga.pk], troop_loadout={troop.key: ga.troop_capacity})
    tenure = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    assert tenure.lineup["troops"] == {troop.key: ga.troop_capacity}
    assert not PlayerTroop.objects.filter(manor=a).exists()
    assert not TroopBankStorage.objects.filter(manor=a).exists()


def test_battle_failure_rolls_back_challenge_and_rewards(ring_players, monkeypatch):
    from core.exceptions import BattlePreparationError

    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)

    def fail(**kwargs):
        raise BattlePreparationError("暂时无法开战")

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", fail)
    with pytest.raises(BattlePreparationError):
        ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    assert not ArenaRingMatch.objects.exists()
    assert not ArenaRingDaily.objects.filter(manor=b).exists()
    assert ArenaRing.objects.get(key="qinglong").current_tenure_id == occupied["tenure_id"]


def test_cross_day_time_rewards_are_split(ring_players):
    from datetime import datetime

    a, _, ga, _ = ring_players
    occupied = occupy(a, ga)
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    start = timezone.make_aware(datetime(2026, 10, 2, 23, 50))
    tenure.started_at = tenure.settled_at = start
    tenure.ends_at = start + timedelta(hours=2)
    tenure.save(update_fields=["started_at", "settled_at", "ends_at"])
    refresh_due_rings(now=start + timedelta(minutes=20))
    rows = list(ArenaRingDaily.objects.filter(manor=a).order_by("date"))
    assert [(r.seconds, r.time_coins) for r in rows] == [(600, 4), (600, 4)]


def test_only_two_named_rings_are_accessible(ring_players):
    a, _, ga, _ = ring_players
    ArenaRing.objects.create(key="third", name="额外擂台")
    with pytest.raises(ArenaRingError, match="不存在"):
        occupy_ring(
            manor=a,
            ring_key="third",
            guest_ids=[ga.pk],
            troop_loadout={},
            action_token=uuid4(),
            expected_version=0,
        )


def test_challenge_quota_and_reward_caps(ring_players, monkeypatch):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    day = timezone.localdate()
    ArenaRingDaily.objects.create(manor=a, date=day, seconds=18000, time_coins=120, defense_rewards=10)
    ArenaRingDaily.objects.create(manor=b, date=day, participation_rewards=5, challenges=9)

    def simulate(**kwargs):
        return BattleReport.objects.create(
            manor=b,
            opponent_name="A",
            winner="defender",
            battle_type="arena",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.arena_coins == 0 and b.arena_coins == 0
    with pytest.raises(ArenaRingError, match="次数已用完"):
        ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    assert ArenaRingMatch.objects.count() == 1


def test_pair_rewards_do_not_reset_between_rings_or_role_swap(ring_players, monkeypatch):
    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)

    def simulate(**kwargs):
        return BattleReport.objects.create(
            manor=kwargs["manor"],
            opponent_name="对手",
            winner="defender",
            battle_type="arena",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    leave_ring(**target(a, occupied))
    ring = ArenaRing.objects.get(key="baihu")
    second = occupy_ring(
        manor=b,
        ring_key=ring.key,
        guest_ids=[gb.pk],
        troop_loadout={},
        action_token=uuid4(),
        expected_version=ring.version,
    )
    challenge_ring(
        manor=a,
        ring_key="baihu",
        action_token=uuid4(),
        expected_version=second["version"],
        expected_tenure_id=second["tenure_id"],
        guest_ids=[ga.pk],
        troop_loadout={},
    )
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.arena_coins == 20 and b.arena_coins == 10
    assert ArenaRingMatch.objects.count() == 2


def test_registration_preserves_busy_status_and_battle_uses_saved_guest_snapshot(ring_players, monkeypatch):
    from guests.models import GuestStatus

    a, b, ga, gb = ring_players
    ga.status = GuestStatus.WORKING
    ga.save(update_fields=["status"])
    occupied = occupy(a, ga)
    ga.refresh_from_db()
    assert ga.status == GuestStatus.WORKING
    saved = ArenaRingTenure.objects.get(pk=occupied["tenure_id"]).lineup["guests"][0]
    ga.level += 20
    ga.status = GuestStatus.DEPLOYED
    ga.save(update_fields=["level", "status"])
    gb.status = GuestStatus.VACATION
    gb.save(update_fields=["status"])

    def simulate(**kwargs):
        defender = kwargs["defender_guests"][0]
        assert defender.attack == saved["attack"]
        assert defender.max_hp == saved["max_hp"]
        return BattleReport.objects.create(
            manor=b,
            opponent_name="A",
            winner="defender",
            battle_type="arena",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    ga.refresh_from_db()
    gb.refresh_from_db()
    assert ga.status == GuestStatus.DEPLOYED
    assert gb.status == GuestStatus.VACATION


@pytest.mark.parametrize("threshold,bonus", [(3, 30), (5, 60), (10, 120)])
def test_streak_milestones_repeat_opponent_and_break_reward(ring_players, manor_factory, monkeypatch, threshold, bonus):
    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.services.arena.ring_rewards import effective_streak

    ItemTemplate.objects.get_or_create(key="chunqiu_coin", defaults={"name": "春秋币"})

    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    today = timezone.localdate()
    for index in range(threshold - 1):
        challenger, _ = manor_factory(username=f"streak_challenger_{index}")
        low, high = sorted((a.pk, challenger.pk))
        ArenaRingMatch.objects.create(
            tenure=tenure,
            attacker=challenger,
            defender=a,
            winner=a,
            pair_low=low,
            pair_high=high,
            reward_date=today,
            created_at=timezone.now(),
        )
    tenure.defenses = threshold - 1
    tenure.save(update_fields=["defenses"])
    winner = "defender"

    def simulate(**kwargs):
        return BattleReport.objects.create(
            manor=kwargs["manor"],
            opponent_name="A",
            winner=winner,
            battle_type="arena",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    kwargs = dict(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    result = ready_challenge_ring(**kwargs)
    assert result["streak_coins"] == bonus
    assert result["streak_chunqiu_coins"] == bonus // 10
    assert ready_challenge_ring(**kwargs) == result
    ready_challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    assert effective_streak(tenure) == threshold
    winner = "draw"
    drawing_player, _ = manor_factory(username="ring_draw_player")
    drawing_guest = create_guest(drawing_player, ga.template, "平局挑战者")
    drawn = ready_challenge_ring(**target(drawing_player, occupied), guest_ids=[drawing_guest.pk], troop_loadout={})
    assert not drawn["won"]
    assert effective_streak(tenure) == threshold
    a.refresh_from_db()
    assert a.arena_coins == 20 + bonus
    assert InventoryItem.objects.get(manor=a, template__key="chunqiu_coin").quantity == bonus // 10
    breaker, _ = manor_factory(username="streak_breaker")
    guest = create_guest(breaker, ga.template, "终结者")
    winner = "attacker"
    broken = ready_challenge_ring(**target(breaker, occupied), guest_ids=[guest.pk], troop_loadout={})
    assert broken["break_streak_coins"] == bonus
    breaker.refresh_from_db()
    assert breaker.arena_coins == 10 + bonus
    assert not InventoryItem.objects.filter(manor=breaker, template__key="chunqiu_coin").exists()
    assert ArenaRingTenure.objects.get(pk=broken["tenure_id"]).defenses == 0


def test_streak_daily_limits_are_shared_across_tenures_and_reset_next_day(ring_players):
    from gameplay.services.arena.ring_rewards import credit_streak_reward

    a, _, _, _ = ring_players
    today = timezone.localdate()
    assert credit_streak_reward(a, 120, today) == 120
    assert credit_streak_reward(a, 120, today) == 90
    assert credit_streak_reward(a, 30, today) == 0
    assert credit_streak_reward(a, 60, today, breaking=True) == 60
    assert credit_streak_reward(a, 120, today, breaking=True) == 60
    assert credit_streak_reward(a, 30, today, breaking=True) == 0
    ResourceEvent.objects.filter(manor=a).update(created_at=timezone.now() - timedelta(days=1))
    assert credit_streak_reward(a, 30, today) == 30
    assert credit_streak_reward(a, 30, today, breaking=True) == 30


def test_twenty_guest_limit_for_registration_update_and_challenge(ring_players):
    a, b, ga, gb = ring_players
    defenders = [ga] + [create_guest(a, ga.template, f"守擂门客{i}") for i in range(20)]
    ring = ArenaRing.objects.get(key="qinglong")
    with pytest.raises(ArenaRingError, match="最多上阵 20 名门客"):
        occupy_ring(
            manor=a,
            ring_key=ring.key,
            guest_ids=[g.pk for g in defenders],
            troop_loadout={},
            action_token=uuid4(),
            expected_version=ring.version,
        )
    result = occupy_ring(
        manor=a,
        ring_key=ring.key,
        guest_ids=[g.pk for g in defenders[:20]],
        troop_loadout={},
        action_token=uuid4(),
        expected_version=ring.version,
    )
    tenure = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    assert tenure.lineup["guest_count"] == 20
    with pytest.raises(ArenaRingError, match="最多上阵 20 名门客"):
        update_ring_lineup(**target(a, result), guest_ids=[g.pk for g in defenders], troop_loadout={})
    attackers = [gb] + [create_guest(b, gb.template, f"攻擂门客{i}") for i in range(20)]
    with pytest.raises(ArenaRingError, match="最多上阵 20 名门客"):
        ready_challenge_ring(**target(b, result), guest_ids=[g.pk for g in attackers], troop_loadout={})
    tenure.refresh_from_db()
    assert tenure.lineup["guest_count"] == 20
    assert not tenure.matches.exists()


def test_admin_reset_clears_return_cooldowns_without_deleting_history(ring_players, monkeypatch):
    from gameplay.services.arena.ring import _end

    a, b, ga, gb = ring_players
    first = occupy(a, ga)
    leave_ring(**target(a, first))
    old = ArenaRingTenure.objects.get(pk=first["tenure_id"])
    ended_at = old.ended_at
    second = occupy(b, gb)
    ring = ArenaRing.objects.get(key="qinglong")
    current = ArenaRingTenure.objects.get(pk=second["tenure_id"])
    _end(ring, current, b, timezone.now(), "admin_reset")
    old.end_reason = "admin_reset"
    old.save(update_fields=["end_reason"])
    third = occupy(a, ga)
    old.refresh_from_db()
    assert old.ended_at == ended_at
    assert old.lineup["guest_count"] == 1

    def simulate(**kwargs):
        return BattleReport.objects.create(
            manor=b,
            opponent_name="A",
            winner="defender",
            battle_type="arena",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    result = ready_challenge_ring(**target(b, third), guest_ids=[gb.pk], troop_loadout={})
    assert result["battle_report_id"]
    assert ArenaRingTenure.objects.filter(pk=first["tenure_id"]).exists()
    assert ArenaRingTenure.objects.filter(pk=second["tenure_id"]).exists()


def test_ring_cooldown_expires_at_ten_minutes(ring_players, monkeypatch):
    from gameplay.services.arena import ring as service

    a, _, ga, _ = ring_players
    occupied = occupy(a, ga)
    leave_ring(**target(a, occupied))
    ended = ArenaRingTenure.objects.get(pk=occupied["tenure_id"]).ended_at
    monkeypatch.setattr(service.timezone, "now", lambda: ended + timedelta(seconds=599))
    with pytest.raises(ArenaRingError, match="10 分钟"):
        occupy(a, ga)
    monkeypatch.setattr(service.timezone, "now", lambda: ended + timedelta(seconds=600))
    assert occupy(a, ga)["tenure_id"] != occupied["tenure_id"]


def test_ring_rejects_unknown_and_invalid_virtual_troops(ring_players):
    a, _, ga, _ = ring_players
    with pytest.raises(ArenaRingError, match="兵种不存在"):
        occupy(a, ga, {"unknown_ring_troop": 1000000})
    for count in (-1, "1.5", True):
        with pytest.raises(ArenaRingError, match="配置无效"):
            occupy(a, ga, {"unknown_ring_troop": count})


def test_chunqiu_streak_daily_cap_and_next_day(ring_players):
    from django.db import transaction

    from gameplay.models import ArenaRingAction, InventoryItem, ItemTemplate
    from gameplay.services.arena.ring_rewards import credit_streak_chunqiu_reward

    a, b, _, _ = ring_players
    ItemTemplate.objects.get_or_create(key="chunqiu_coin", defaults={"name": "春秋币"})
    today = timezone.localdate()
    with transaction.atomic():
        for streak, expected in ((10, 12), (10, 9), (3, 0)):
            granted = credit_streak_chunqiu_reward(a, streak, today)
            assert granted == expected
            ArenaRingAction.objects.create(
                manor=b,
                token=uuid4(),
                kind="challenge",
                result={
                    "streak_reward_manor_id": a.pk,
                    "streak_reward_date": today.isoformat(),
                    "streak_chunqiu_coins": granted,
                },
            )
        assert credit_streak_chunqiu_reward(a, 3, today + timedelta(days=1)) == 3
    assert InventoryItem.objects.get(manor=a, template__key="chunqiu_coin").quantity == 24


def test_ring_troop_capacity_is_total_across_types_and_selected_guests(ring_players):
    a, _, ga, _ = ring_players
    extra = create_guest(a, ga.template, "加派门客")
    t1 = TroopTemplate.objects.create(key="ring_cap_one", name="护院一")
    t2 = TroopTemplate.objects.create(key="ring_cap_two", name="护院二")
    from django.db import transaction

    from gameplay.services.arena.ring_snapshots import build_ring_lineup

    with transaction.atomic():
        with pytest.raises(ArenaRingError, match="带兵上限"):
            build_ring_lineup(a, [ga.pk], {t1.key: ga.troop_capacity, t2.key: 1})
        lineup = build_ring_lineup(
            a,
            [ga.pk, extra.pk],
            {
                t1.key: ga.troop_capacity,
                t2.key: extra.troop_capacity,
            },
        )
    assert lineup["troop_count"] == ga.troop_capacity + extra.troop_capacity


@pytest.mark.parametrize(
    "troops,capacity,expected",
    [
        ({"a": 100, "b": 200}, 100, {"a": 33, "b": 67}),
        ({"a": 100, "b": 100}, 1, {"a": 1}),
        ({"a": 100}, 0, {}),
        ({"a": 10}, 100, {"a": 10}),
    ],
)
def test_old_ring_lineup_is_scaled_to_snapshot_capacity(troops, capacity, expected):
    from gameplay.services.arena.ring_snapshots import ring_battle_troops

    lineup = {"guests": [{"troop_capacity": capacity}], "troops": troops.copy()}
    assert ring_battle_troops(lineup) == expected
    assert lineup["troops"] == troops


def test_two_challenges_per_holder_with_ten_minute_cooldown(ring_players, manor_factory, monkeypatch):
    from gameplay.services.arena import ring as service

    a, b, ga, gb = ring_players
    occupied = occupy(a, ga)
    start = timezone.now()
    monkeypatch.setattr(service.timezone, "now", lambda: start)

    def simulate(**kwargs):
        return BattleReport.objects.create(
            manor=kwargs["manor"],
            opponent_name="擂主",
            winner="defender",
            battle_type="arena",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )

    monkeypatch.setattr(service, "simulate_report", simulate)
    kwargs = dict(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    first = challenge_ring(**kwargs)
    assert challenge_ring(**kwargs) == first
    assert ArenaRingMatch.objects.filter(attacker=b).count() == 1
    monkeypatch.setattr(service.timezone, "now", lambda: start + timedelta(seconds=599))
    with pytest.raises(ArenaRingError, match="攻擂冷却"):
        challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    other, _ = manor_factory(username="ring_other_attacker")
    guest = create_guest(other, ga.template, "其他攻擂者")
    challenge_ring(**target(other, occupied), guest_ids=[guest.pk], troop_loadout={})
    monkeypatch.setattr(service.timezone, "now", lambda: start + timedelta(seconds=600))
    second = challenge_ring(**target(b, occupied), guest_ids=[gb.pk], troop_loadout={})
    current = second
    for _ in range(3):
        current = update_ring_lineup(**target(a, current), guest_ids=[ga.pk], troop_loadout={})
    monkeypatch.setattr(service.timezone, "now", lambda: start + timedelta(seconds=1200))
    with pytest.raises(ArenaRingError, match="最多挑战 2 次"):
        challenge_ring(**target(b, current), guest_ids=[gb.pk], troop_loadout={})
    assert ArenaRingDaily.objects.get(manor=b).challenges == 2
    leave_ring(**target(a, current))
    monkeypatch.setattr(service.timezone, "now", lambda: start + timedelta(seconds=1800))
    new = occupy(a, ga)
    challenge_ring(**target(b, new), guest_ids=[gb.pk], troop_loadout={})
    assert ArenaRingMatch.objects.filter(attacker=b, tenure_id=new["tenure_id"]).count() == 1
    assert ArenaRingDaily.objects.get(manor=b).challenges == 3


def test_invalid_lineup_does_not_consume_update_or_reset_cooldown(ring_players):
    a, _, ga, _ = ring_players
    occupied = occupy(a, ga)
    ready_at = timezone.now() - timedelta(minutes=10)
    ArenaRingTenure.objects.filter(pk=occupied["tenure_id"]).update(lineup_updated_at=ready_at)
    with pytest.raises(ArenaRingError):
        update_ring_lineup(**target(a, occupied), guest_ids=[], troop_loadout={})
    tenure = ArenaRingTenure.objects.get(pk=occupied["tenure_id"])
    assert tenure.version == 1 and tenure.lineup_updated_at == ready_at
