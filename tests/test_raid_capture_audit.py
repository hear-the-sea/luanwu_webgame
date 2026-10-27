"""真实踢馆战斗到监牢的俘虏链路验证。"""

from datetime import timedelta

import pytest
from django.utils import timezone

from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION, CURRENT_RNG_VERSION
from gameplay.models import Building, BuildingType, JailPrisoner, Message, RaidRun
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import battle
from gameplay.services.virtual_player_core.contracts import BotLootClampDecision
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("attacker_wins", [True, False])
@pytest.mark.parametrize("capture_rate", [0.0, 1.0])
def test_real_raid_battle_captures_losing_guest(attacker_wins, capture_rate, django_user_model, monkeypatch, settings):
    settings.RAID_ATTACKER_DEFEAT_CAPTURE_RATE = capture_rate
    settings.RAID_DEFENDER_DEFEAT_CAPTURE_RATE = capture_rate
    attacker = ensure_manor(django_user_model.objects.create_user(username="audit_capture_attacker"))
    defender = ensure_manor(django_user_model.objects.create_user(username="audit_capture_defender"))
    jail_type, _ = BuildingType.objects.get_or_create(key="jail", defaults={"name": "监牢"})
    for manor in (attacker, defender):
        Building.objects.update_or_create(manor=manor, building_type=jail_type, defaults={"level": 1})
        manor.invalidate_building_cache()
    template = GuestTemplate.objects.create(
        key="audit_capture_guest", name="测试门客", rarity="green", archetype="military"
    )

    def create_guest(manor, strong, status):
        stat = 3000 if strong else 1
        guest = Guest.objects.create(
            manor=manor,
            template=template,
            level=50,
            force=stat,
            intellect=stat,
            defense_stat=stat,
            agility=stat,
            current_hp=1,
            status=status,
        )
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])
        return guest

    attacking_guest = create_guest(attacker, attacker_wins, GuestStatus.DEPLOYED)
    defending_guest = create_guest(defender, not attacker_wins, GuestStatus.IDLE)
    target = defending_guest if attacker_wins else attacking_guest
    winner = attacker if attacker_wins else defender
    now = timezone.now()
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots=build_guest_battle_snapshots([attacking_guest], include_identity=True),
        troop_loadout={},
        base_seed=1234,
        rng_version=CURRENT_RNG_VERSION,
        battle_engine_version=CURRENT_BATTLE_ENGINE_VERSION,
        battle_at=now,
        return_at=now + timedelta(minutes=1),
        travel_time=60,
    )
    run.guests.add(attacking_guest)
    monkeypatch.setattr(battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        battle, "_apply_raid_loot_if_needed", lambda *_args, **_kwargs: BotLootClampDecision(resources={})
    )
    monkeypatch.setattr(battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(battle, "_dismiss_marching_raids_if_protected", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(battle, "_dispatch_complete_raid_task", lambda *_args, **_kwargs: None)

    battle.process_raid_battle(run, now=now)

    run.refresh_from_db()
    assert run.is_attacker_victory is attacker_wins
    assert run.battle_report.winner == ("attacker" if attacker_wins else "defender")
    losing_team = run.battle_report.defender_team if attacker_wins else run.battle_report.attacker_team
    assert target.pk in [row["guest_id"] for row in losing_team]
    if capture_rate:
        assert run.battle_rewards["capture"]["template_key"] == template.key
        assert JailPrisoner.objects.filter(captor=winner, raid_run=run, guest_template=template, status="held").exists()
        assert not Guest.objects.filter(pk=target.pk).exists()
        assert Message.objects.filter(manor=winner, title="门客俘获通知", battle_report=run.battle_report).exists()
    else:
        assert "capture" not in run.battle_rewards
        assert not JailPrisoner.objects.filter(raid_run=run).exists()
        assert Guest.objects.filter(pk=target.pk).exists()
