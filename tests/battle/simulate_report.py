from __future__ import annotations

from types import SimpleNamespace

import pytest

import battle.setup as battle_setup
from battle.models import BattleReport
from battle.services import simulate_report
from core.exceptions import BattlePreparationError
from gameplay.services.battle_snapshots import build_guest_battle_snapshots, build_guest_snapshot_proxies
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.battle.support import recruit_frontline


@pytest.mark.django_db
def test_simulate_report_creates_battle(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="general", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save()
    recruit_frontline(manor)
    troop_loadout = {"dao_jie": 100, "qiang_ling": 100, "archer": 100, "fist_master": 100, "jian_shi": 100}
    report = simulate_report(manor, seed=2, troop_loadout=troop_loadout)
    assert isinstance(report, BattleReport)
    assert len(report.rounds) > 0
    assert report.battle_type == "skirmish"
    assert "attacker" in report.losses
    assert "defender" in report.losses
    assert isinstance(report.drops, dict)
    first_round = report.rounds[0]
    assert first_round["round"] == 1
    assert first_round["events"]
    orders = [event["order"] for event in first_round["events"]]
    assert orders == list(range(1, len(orders) + 1))
    charging_events = [event for event in first_round["events"] if event.get("status") == "charging"]
    assert charging_events
    assert all(event["actor_state"]["side"] == event["side"] for event in charging_events)
    assert any(event.get("preemptive") for event in first_round["events"])
    assert all("agility" in event for event in first_round["events"] if "damage" in event)
    assert sum(report.attacker_troops.values()) > 0
    assert report.attacker_team and all("initial_hp" in entry and "level" in entry for entry in report.attacker_team)
    assert report.defender_team and all("initial_hp" in entry and "level" in entry for entry in report.defender_team)


@pytest.mark.django_db
def test_simulate_report_attacker_victory_increases_guest_loyalty(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="battle_loyalty", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save()
    recruit_frontline(manor, draws=4)

    for guest in manor.guests.all():
        guest.level = 50
        guest.attack_bonus = 800
        guest.defense_bonus = 800
        guest.intellect = 800
        guest.force = 800
        guest.defense_stat = 300
        guest.current_hp = guest.max_hp
        guest.loyalty = 50
        guest.save()

    troop_loadout = {"dao_jie": 150, "qiang_ling": 150, "archer": 150, "fist_master": 150, "jian_shi": 150}
    report = simulate_report(
        manor, seed=1, max_squad=getattr(manor, "max_squad_size", None), troop_loadout=troop_loadout
    )

    assert report.winner == "attacker"
    assert set(manor.guests.values_list("loyalty", flat=True)) == {51}


@pytest.mark.django_db
def test_simulate_report_rejects_foreign_attacker_guests(game_data, django_user_model):
    attacker_user = django_user_model.objects.create_user(username="battle_owner", password="pass123")
    foreign_user = django_user_model.objects.create_user(username="battle_foreign", password="pass123")
    attacker_manor = ensure_manor(attacker_user)
    foreign_manor = ensure_manor(foreign_user)

    recruit_frontline(attacker_manor, draws=1)
    recruit_frontline(foreign_manor, draws=1)
    foreign_guest = foreign_manor.guests.first()

    with pytest.raises(BattlePreparationError, match="攻击方门客必须属于当前庄园"):
        simulate_report(attacker_manor, attacker_guests=[foreign_guest], troop_loadout={})


@pytest.mark.django_db
def test_simulate_report_rejects_attacker_lineup_over_limit_before_rewards(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="battle_attacker_limit", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save(update_fields=["silver"])
    recruit_frontline(manor, draws=2)
    guests = list(manor.guests.all())
    manor.guests.update(loyalty=50)

    with pytest.raises(BattlePreparationError, match="最多只能派出 1 名门客出征"):
        simulate_report(
            manor,
            attacker_guests=guests,
            max_squad=1,
            troop_loadout={},
            fill_default_troops=False,
            auto_reward=False,
            send_message=False,
            use_lock=False,
        )

    assert set(manor.guests.values_list("loyalty", flat=True)) == {50}


@pytest.mark.django_db
def test_simulate_report_rejects_defender_lineup_over_limit(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="battle_defender_limit", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save(update_fields=["silver"])
    recruit_frontline(manor, draws=2)
    guests = list(manor.guests.all())

    with pytest.raises(BattlePreparationError, match="最多只能派出 1 名门客出征"):
        simulate_report(
            manor,
            attacker_guests=[guests[0]],
            defender_guests=guests,
            defender_max_squad=1,
            troop_loadout={},
            fill_default_troops=False,
            auto_reward=False,
            send_message=False,
            use_lock=False,
        )


@pytest.mark.django_db
def test_simulate_report_rejects_invalid_defender_setup(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="battle_bad_defender_setup", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save(update_fields=["silver"])
    recruit_frontline(manor, draws=1)

    with pytest.raises(AssertionError, match="invalid battle defender setup payload"):
        simulate_report(
            manor,
            seed=1,
            troop_loadout={},
            fill_default_troops=False,
            defender_setup="bad-config",
        )


def test_validate_attacker_guest_ownership_programming_error_bubbles_up_for_invalid_guest_id():
    manor = SimpleNamespace(pk=1)
    guest = SimpleNamespace(pk="bad-pk", id="bad-pk", manor_id=1)

    with pytest.raises(AssertionError, match="broken battle attacker guest id contract"):
        battle_setup.validate_attacker_guest_ownership(manor, [guest])


def test_validate_attacker_guest_ownership_programming_error_bubbles_up_for_invalid_guest_manor_id():
    manor = SimpleNamespace(pk=1)
    guest = SimpleNamespace(pk=1, id=1, manor_id="bad-manor-id")

    with pytest.raises(AssertionError, match="broken battle attacker guest manor id contract"):
        battle_setup.validate_attacker_guest_ownership(manor, [guest])


@pytest.mark.django_db
def test_simulate_report_accepts_legacy_snapshot_guests_without_db_ownership_lookup(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="battle_snapshot_owner", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save(update_fields=["silver"])
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()

    snapshots = build_guest_battle_snapshots([guest], include_identity=True)
    assert snapshots[0]["manor_id"] == manor.pk

    legacy_snapshot = dict(snapshots[0])
    legacy_snapshot.pop("manor_id")
    guest.delete()

    snapshot_guest = build_guest_snapshot_proxies([legacy_snapshot], include_guest_identity=True)[0]
    report = simulate_report(
        manor,
        seed=7,
        troop_loadout={},
        fill_default_troops=False,
        attacker_guests=[snapshot_guest],
        auto_reward=False,
        send_message=False,
        apply_damage=False,
        use_lock=False,
    )

    assert isinstance(report, BattleReport)


@pytest.mark.django_db
def test_simulate_report_rewards_on_victory(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="champion", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save()
    recruit_frontline(manor, draws=4)
    for guest in manor.guests.all():
        guest.level = 50
        guest.attack_bonus = 800
        guest.defense_bonus = 800
        guest.intellect = 800
        guest.force = 800
        guest.defense_stat = 300
        guest.current_hp = guest.max_hp
        guest.save()
    before = manor.resource_dict()
    troop_loadout = {"dao_jie": 150, "qiang_ling": 150, "archer": 150, "fist_master": 150, "jian_shi": 150}
    report = simulate_report(
        manor, seed=1, max_squad=getattr(manor, "max_squad_size", None), troop_loadout=troop_loadout
    )
    assert report.winner == "attacker"
    assert report.drops
    manor.refresh_from_db()
    after = manor.resource_dict()
    for resource, amount in report.drops.items():
        assert after[resource] == before[resource] + amount


@pytest.mark.django_db
def test_simulate_report_requires_idle_guests(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="busy", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 3000
    manor.save()
    recruit_frontline(manor, draws=2)
    manor.guests.update(status=GuestStatus.WORKING)

    with pytest.raises(BattlePreparationError) as exc:
        simulate_report(manor, seed=5)

    assert "空闲" in str(exc.value) or "重伤" in str(exc.value)


@pytest.mark.django_db
def test_defeated_guest_becomes_injured(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="defeated", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save()
    recruit_frontline(manor, draws=3)
    for guest in manor.guests.all():
        guest.current_hp = 1
        guest.status = GuestStatus.IDLE
        guest.save()
    troop_loadout = {"dao_jie": 100, "qiang_ling": 100, "archer": 100, "fist_master": 100, "jian_shi": 100}
    defender_setup = {"troop_loadout": {key: 5000 for key in troop_loadout}}
    report = simulate_report(manor, seed=99, troop_loadout=troop_loadout, defender_setup=defender_setup)
    assert report.winner == "defender"
    manor.refresh_from_db()
    injured_guests = manor.guests.filter(status=GuestStatus.INJURED)
    assert injured_guests.exists()
    for guest in manor.guests.all():
        guest.refresh_from_db()
        if guest.status == GuestStatus.INJURED:
            assert guest.current_hp == 1


@pytest.mark.django_db
def test_injured_guest_cannot_deploy(django_user_model):
    user = django_user_model.objects.create_user(username="injured", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 5000
    manor.save()
    recruit_frontline(manor, draws=3)
    for guest in manor.guests.all():
        guest.status = GuestStatus.INJURED
        guest.current_hp = 1
        guest.save()
    troop_loadout = {"dao_jie": 100, "qiang_ling": 100, "archer": 100, "fist_master": 100, "jian_shi": 100}
    with pytest.raises(BattlePreparationError) as exc:
        simulate_report(manor, seed=1, troop_loadout=troop_loadout)
    assert "重伤" in str(exc.value)
