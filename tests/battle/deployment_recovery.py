from __future__ import annotations

import pytest

from battle import deployment as battle_deployment
from battle.combatants_pkg import serialize_skills
from battle.services import BATTLE_ORPHANED_DEPLOYED_RECOVERY_COUNTER, recover_orphaned_deployed_guests
from core.exceptions import GuestNotIdleError
from gameplay.models import ArenaEntry, ArenaEntryGuest, ArenaTournament, MissionRun, MissionTemplate, RaidRun
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from guests.services.health import INJURY_RECOVERY_THRESHOLD, heal_guest
from tests.battle.support import recruit_frontline


@pytest.mark.django_db
def test_lock_guests_for_battle_marks_and_releases_defender_guests(game_data, django_user_model):
    from battle.services import lock_guests_for_battle

    attacker_user = django_user_model.objects.create_user(username="battle_lock_a", password="pass123")
    defender_user = django_user_model.objects.create_user(username="battle_lock_b", password="pass123")
    attacker_manor = ensure_manor(attacker_user)
    defender_manor = ensure_manor(defender_user)
    recruit_frontline(attacker_manor, draws=1)
    recruit_frontline(defender_manor, draws=1)

    attacker_guest = attacker_manor.guests.first()
    defender_guest = defender_manor.guests.first()

    with lock_guests_for_battle([attacker_guest], manor=attacker_manor, other_guests=[defender_guest]):
        attacker_guest.refresh_from_db(fields=["status"])
        defender_guest.refresh_from_db(fields=["status"])
        assert attacker_guest.status == GuestStatus.DEPLOYED
        assert defender_guest.status == GuestStatus.DEPLOYED

    attacker_guest.refresh_from_db(fields=["status"])
    defender_guest.refresh_from_db(fields=["status"])
    assert attacker_guest.status == GuestStatus.IDLE
    assert defender_guest.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_lock_guests_for_battle_releases_transaction_before_battle_body(game_data, django_user_model, monkeypatch):
    import battle.services as battle_services
    from battle.services import lock_guests_for_battle

    user = django_user_model.objects.create_user(username="battle_lock_txn", password="pass123")
    manor = ensure_manor(user)
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()

    atomic_depth = {"value": 0}
    real_atomic = battle_services.transaction.atomic

    class _RecordingAtomic:
        def __call__(self, *args, **kwargs):
            context = real_atomic(*args, **kwargs)

            class _WrappedContext:
                def __enter__(self_inner):
                    atomic_depth["value"] += 1
                    return context.__enter__()

                def __exit__(self_inner, exc_type, exc, tb):
                    try:
                        return context.__exit__(exc_type, exc, tb)
                    finally:
                        atomic_depth["value"] -= 1

            return _WrappedContext()

    monkeypatch.setattr(battle_services.transaction, "atomic", _RecordingAtomic())
    with lock_guests_for_battle([guest], manor=manor):
        assert atomic_depth["value"] == 0


@pytest.mark.django_db
def test_lock_guests_for_battle_recovers_orphaned_deployed_guest(game_data, django_user_model):
    from battle.services import lock_guests_for_battle

    user = django_user_model.objects.create_user(username="battle_lock_orphaned", password="pass123")
    manor = ensure_manor(user)
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])

    with lock_guests_for_battle([guest], manor=manor):
        guest.refresh_from_db(fields=["status"])
        assert guest.status == GuestStatus.DEPLOYED

    guest.refresh_from_db(fields=["status"])
    assert guest.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_recover_orphaned_deployed_guests_resets_untracked_guest(game_data, django_user_model, caplog):
    user = django_user_model.objects.create_user(username="battle_recover_orphaned", password="pass123")
    manor = ensure_manor(user)
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])

    caplog.set_level("WARNING", logger="battle.services")
    recovered = recover_orphaned_deployed_guests(guest_ids=[guest.id])

    guest.refresh_from_db(fields=["status"])
    assert recovered == 1
    assert guest.status == GuestStatus.IDLE
    assert any(
        "Recovered orphaned deployed guests before battle reuse" in record.getMessage() for record in caplog.records
    )
    assert any(BATTLE_ORPHANED_DEPLOYED_RECOVERY_COUNTER in record.getMessage() for record in caplog.records)


@pytest.mark.django_db
def test_recover_orphaned_deployed_guests_locks_before_active_reference_lookup() -> None:
    calls: list[str] = []

    class _FakeQuerySet:
        def filter(self, **kwargs):
            return self

        def select_for_update(self):
            calls.append("lock")
            return self

        def order_by(self, _field):
            calls.append("order")
            return self

        def __iter__(self):
            return iter((type("_FakeGuest", (), {"id": 1})(),))

    class _FakeGuestModel:
        objects = _FakeQuerySet()

    def find_orphaned(_candidate_ids):
        calls.append("find")
        return []

    recovered = battle_deployment.recover_orphaned_deployed_guests(
        guest_model=_FakeGuestModel,
        deployed_status=GuestStatus.DEPLOYED,
        idle_status=GuestStatus.IDLE,
        find_orphaned_deployed_guest_ids_fn=find_orphaned,
        record_orphaned_guest_recovery_fn=lambda _ids, _count: None,
    )

    assert recovered == 0
    assert calls == ["lock", "order", "find"]


@pytest.mark.django_db
def test_recover_orphaned_deployed_guests_records_monitoring_signal(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="battle_recover_monitor", password="pass123")
    manor = ensure_manor(user)
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])

    calls: list[str] = []
    monkeypatch.setattr("battle.services.increment_degraded_counter", lambda component: calls.append(component))

    recovered = recover_orphaned_deployed_guests(guest_ids=[guest.id])

    assert recovered == 1
    assert calls == [BATTLE_ORPHANED_DEPLOYED_RECOVERY_COUNTER]


@pytest.mark.django_db
@pytest.mark.parametrize("deployment_kind", ["mission", "raid", "arena"])
def test_recover_orphaned_deployed_guests_keeps_active_deployments(
    game_data,
    django_user_model,
    deployment_kind,
):
    user = django_user_model.objects.create_user(
        username=f"battle_recover_active_{deployment_kind}", password="pass123"
    )
    manor = ensure_manor(user)
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()
    guest.status = GuestStatus.ARENA if deployment_kind == "arena" else GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])

    if deployment_kind == "mission":
        mission = MissionTemplate.objects.create(key="battle_recover_active_mission", name="Battle Recover Mission")
        run = MissionRun.objects.create(manor=manor, mission=mission, status=MissionRun.Status.ACTIVE)
        run.guests.add(guest)
    elif deployment_kind == "raid":
        defender_user = django_user_model.objects.create_user(
            username=f"battle_recover_active_defender_{deployment_kind}",
            password="pass123",
        )
        defender = ensure_manor(defender_user)
        run = RaidRun.objects.create(attacker=manor, defender=defender, status=RaidRun.Status.MARCHING)
        run.guests.add(guest)
    else:
        tournament = ArenaTournament.objects.create(status=ArenaTournament.Status.RECRUITING, player_limit=8)
        entry = ArenaEntry.objects.create(
            tournament=tournament,
            manor=manor,
            status=ArenaEntry.Status.REGISTERED,
        )
        ArenaEntryGuest.objects.create(entry=entry, guest=guest, snapshot={"guest_id": guest.id})

    recovered = recover_orphaned_deployed_guests(guest_ids=[guest.id])

    guest.refresh_from_db(fields=["status"])
    assert recovered == 0
    expected_status = GuestStatus.ARENA if deployment_kind == "arena" else GuestStatus.DEPLOYED
    assert guest.status == expected_status


@pytest.mark.django_db
def test_heal_guest_cures_injury(django_user_model):
    user = django_user_model.objects.create_user(username="healer", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 3000
    manor.save()
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()
    guest.status = GuestStatus.INJURED
    guest.current_hp = 1
    guest.save()

    max_hp = guest.max_hp
    threshold_hp = int(max_hp * INJURY_RECOVERY_THRESHOLD)

    heal_amount = threshold_hp - 10
    if heal_amount > 0:
        result = heal_guest(guest, heal_amount)
        guest.refresh_from_db()
        assert guest.status == GuestStatus.INJURED
        assert not result["injury_cured"]

    guest.current_hp = 1
    guest.status = GuestStatus.INJURED
    guest.save()
    heal_amount = threshold_hp + 10
    result = heal_guest(guest, heal_amount)
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE
    assert result["injury_cured"]


@pytest.mark.django_db
def test_heal_guest_rejects_busy_non_injured_status(django_user_model):
    user = django_user_model.objects.create_user(username="busy_healer", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 3000
    manor.save(update_fields=["silver"])
    recruit_frontline(manor, draws=1)
    guest = manor.guests.first()
    guest.status = GuestStatus.WORKING
    guest.current_hp = max(1, guest.max_hp - 100)
    guest.save(update_fields=["status", "current_hp"])

    with pytest.raises(GuestNotIdleError):
        heal_guest(guest, 50)


def test_serialize_skills_returns_empty_for_unsaved_guest():
    guest = type("_UnsavedGuest", (), {"pk": None})()

    assert serialize_skills(guest) == []


def test_serialize_skills_bubbles_up_programming_value_error():
    class _BrokenSkills:
        @staticmethod
        def all():
            raise ValueError("broken skills manager")

    guest = type("_BrokenGuest", (), {"pk": 1, "skills": _BrokenSkills()})()

    with pytest.raises(ValueError, match="broken skills manager"):
        serialize_skills(guest)
