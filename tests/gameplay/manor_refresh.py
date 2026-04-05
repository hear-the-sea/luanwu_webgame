from datetime import timedelta

import pytest
from django_redis.exceptions import ConnectionInterrupted

import gameplay.services.manor.core as manor_service
import gameplay.services.resources as resource_service
from gameplay.models import RaidRun, ScoutRecord
from gameplay.services.manor.core import ensure_manor, project_manor_activity_for_read, refresh_manor_state


@pytest.mark.django_db
def test_refresh_manor_state_defaults_to_read_projection_only(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_refresh_projection_only", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 0

    calls = {"finalize": 0, "resource": 0, "mission": 0, "scout": 0, "raid": 0}
    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor, prefer_async=False: calls.__setitem__("mission", calls["mission"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_scout_records",
        lambda _manor, prefer_async=False: calls.__setitem__("scout", calls["scout"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_raid_runs",
        lambda _manor, prefer_async=False: calls.__setitem__("raid", calls["raid"] + 1),
    )

    refresh_manor_state(manor)

    assert calls == {"finalize": 1, "resource": 1, "mission": 0, "scout": 0, "raid": 0}


@pytest.mark.django_db
def test_project_manor_activity_for_read_runs_resource_projection_only(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_project_activity_read", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 0

    calls = {"project": 0, "mission": 0, "scout": 0, "raid": 0, "arena": 0}
    monkeypatch.setattr(
        "gameplay.services.resources.project_resource_production_for_read",
        lambda _manor: calls.__setitem__("project", calls["project"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor, prefer_async=False: calls.__setitem__("mission", calls["mission"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_scout_records",
        lambda _manor, prefer_async=False: calls.__setitem__("scout", calls["scout"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_raid_runs",
        lambda _manor, prefer_async=False: calls.__setitem__("raid", calls["raid"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.arena.core.refresh_arena_activity",
        lambda _manor, now=None: calls.__setitem__("arena", calls["arena"] + 1),
    )

    project_manor_activity_for_read(manor)

    assert calls == {"project": 1, "mission": 0, "scout": 0, "raid": 0, "arena": 0}


@pytest.mark.django_db
def test_project_resource_production_for_read_does_not_refresh_work_assignments(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="player_project_resource_read", password="pass12345")
    manor = ensure_manor(user)

    calls = {"resource": 0, "work": 0}
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor, persist=False: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.work.refresh_work_assignments",
        lambda _manor: calls.__setitem__("work", calls["work"] + 1),
    )

    resource_service.project_resource_production_for_read(manor)

    assert calls == {"resource": 1, "work": 0}


@pytest.mark.django_db
def test_refresh_manor_state_local_fallback_throttles_when_cache_unavailable(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_refresh_fallback", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5

    manor_service._LOCAL_REFRESH_FALLBACK.clear()

    monkeypatch.setattr(
        manor_service.cache, "add", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
    )

    calls = {"finalize": 0, "resource": 0, "mission": 0}

    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor: calls.__setitem__("mission", calls["mission"] + 1),
    )

    refresh_manor_state(manor, include_activity_refresh=True)
    refresh_manor_state(manor, include_activity_refresh=True)

    assert calls == {"finalize": 2, "resource": 1, "mission": 1}


@pytest.mark.django_db
def test_refresh_manor_state_local_fallback_allows_after_interval(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_refresh_interval", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5

    manor_service._LOCAL_REFRESH_FALLBACK.clear()

    monkeypatch.setattr(
        manor_service.cache, "add", lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache down"))
    )

    monotonic_values = iter([100.0, 102.0, 106.1])
    monkeypatch.setattr(manor_service.time, "monotonic", lambda: next(monotonic_values))

    calls = {"finalize": 0, "resource": 0, "mission": 0}

    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor: calls.__setitem__("mission", calls["mission"] + 1),
    )

    refresh_manor_state(manor, include_activity_refresh=True)
    refresh_manor_state(manor, include_activity_refresh=True)
    refresh_manor_state(manor, include_activity_refresh=True)

    assert calls == {"finalize": 3, "resource": 2, "mission": 2}


@pytest.mark.django_db
def test_refresh_manor_state_runtime_marker_cache_error_bubbles_up(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_refresh_runtime_marker", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    manor_service._LOCAL_REFRESH_FALLBACK.clear()

    monkeypatch.setattr(
        manor_service.cache, "add", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cache down"))
    )

    with pytest.raises(RuntimeError, match="cache down"):
        refresh_manor_state(manor, include_activity_refresh=True)


@pytest.mark.django_db
def test_refresh_manor_state_bypasses_cache_throttle_when_upgrade_due(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_refresh_due_upgrade", password="pass12345")
    manor = ensure_manor(user)
    building = manor.buildings.first()
    assert building is not None
    building.is_upgrading = True
    building.upgrade_complete_at = manor_service.timezone.now() - timedelta(seconds=1)
    building.save(update_fields=["is_upgrading", "upgrade_complete_at"])

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    monkeypatch.setattr(manor_service.cache, "add", lambda *args, **kwargs: False)

    calls = {"finalize": 0, "resource": 0, "mission": 0}
    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor: calls.__setitem__("mission", calls["mission"] + 1),
    )

    refresh_manor_state(manor, include_activity_refresh=True)
    assert calls == {"finalize": 1, "resource": 0, "mission": 0}


@pytest.mark.django_db
def test_refresh_manor_state_still_throttles_when_cache_hit_and_no_due_work(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_refresh_no_due", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    monkeypatch.setattr(manor_service.cache, "add", lambda *args, **kwargs: False)

    calls = {"finalize": 0, "resource": 0, "mission": 0}
    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor: calls.__setitem__("mission", calls["mission"] + 1),
    )

    refresh_manor_state(manor, include_activity_refresh=True)
    assert calls == {"finalize": 1, "resource": 0, "mission": 0}


@pytest.mark.django_db
def test_refresh_manor_state_falls_back_to_local_throttle_when_cache_backend_unavailable(
    django_user_model, settings, monkeypatch
):
    user = django_user_model.objects.create_user(username="player_refresh_cache_unavailable", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    monkeypatch.setattr(
        manor_service.cache,
        "add",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionInterrupted("redis unavailable")),
    )

    calls = {"finalize": 0, "resource": 0, "mission": 0, "scout": 0, "raid": 0}
    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor: calls.__setitem__("mission", calls["mission"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_scout_records",
        lambda _manor: calls.__setitem__("scout", calls["scout"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_raid_runs",
        lambda _manor: calls.__setitem__("raid", calls["raid"] + 1),
    )

    refresh_manor_state(manor, include_activity_refresh=True)
    refresh_manor_state(manor, include_activity_refresh=True)

    assert calls == {"finalize": 2, "resource": 1, "mission": 1, "scout": 1, "raid": 1}


@pytest.mark.django_db
def test_refresh_manor_state_bypasses_cache_throttle_when_scout_due(django_user_model, settings, monkeypatch):
    attacker = django_user_model.objects.create_user(username="player_refresh_due_scout", password="pass12345")
    defender = django_user_model.objects.create_user(username="player_refresh_due_scout_target", password="pass12345")
    manor = ensure_manor(attacker)
    target_manor = ensure_manor(defender)

    ScoutRecord.objects.create(
        attacker=manor,
        defender=target_manor,
        status=ScoutRecord.Status.SCOUTING,
        scout_cost=1,
        success_rate=0.5,
        travel_time=60,
        complete_at=manor_service.timezone.now() - timedelta(seconds=1),
    )

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    monkeypatch.setattr(manor_service.cache, "add", lambda *args, **kwargs: False)

    calls = {"finalize": 0, "resource": 0, "mission": 0, "scout": 0, "raid": 0}
    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor: calls.__setitem__("mission", calls["mission"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_scout_records",
        lambda _manor: calls.__setitem__("scout", calls["scout"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_raid_runs",
        lambda _manor: calls.__setitem__("raid", calls["raid"] + 1),
    )

    refresh_manor_state(manor, include_activity_refresh=True)
    assert calls == {"finalize": 1, "resource": 1, "mission": 1, "scout": 1, "raid": 1}


@pytest.mark.django_db
def test_refresh_manor_state_bypasses_cache_throttle_when_raid_due(django_user_model, settings, monkeypatch):
    attacker = django_user_model.objects.create_user(username="player_refresh_due_raid", password="pass12345")
    defender = django_user_model.objects.create_user(username="player_refresh_due_raid_target", password="pass12345")
    manor = ensure_manor(attacker)
    target_manor = ensure_manor(defender)

    RaidRun.objects.create(
        attacker=manor,
        defender=target_manor,
        status=RaidRun.Status.MARCHING,
        travel_time=60,
        battle_at=manor_service.timezone.now() - timedelta(seconds=1),
    )

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    monkeypatch.setattr(manor_service.cache, "add", lambda *args, **kwargs: False)

    calls = {"finalize": 0, "resource": 0, "mission": 0, "scout": 0, "raid": 0}
    monkeypatch.setattr(
        manor_service, "finalize_upgrades", lambda _manor: calls.__setitem__("finalize", calls["finalize"] + 1)
    )
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor: calls.__setitem__("mission", calls["mission"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_scout_records",
        lambda _manor: calls.__setitem__("scout", calls["scout"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_raid_runs",
        lambda _manor: calls.__setitem__("raid", calls["raid"] + 1),
    )

    refresh_manor_state(manor, include_activity_refresh=True)
    assert calls == {"finalize": 1, "resource": 1, "mission": 1, "scout": 1, "raid": 1}


@pytest.mark.django_db
def test_refresh_manor_state_propagates_prefer_async_to_all_refresh_hooks(django_user_model, settings, monkeypatch):
    user = django_user_model.objects.create_user(username="player_refresh_async_hooks", password="pass12345")
    manor = ensure_manor(user)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 0

    calls = {"resource": 0, "mission": [], "scout": [], "raid": []}
    monkeypatch.setattr(manor_service, "finalize_upgrades", lambda _manor: None)
    monkeypatch.setattr(
        "gameplay.services.resources.sync_resource_production",
        lambda _manor: calls.__setitem__("resource", calls["resource"] + 1),
    )
    monkeypatch.setattr(
        "gameplay.services.missions.refresh_mission_runs",
        lambda _manor, prefer_async=False: calls["mission"].append(prefer_async),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_scout_records",
        lambda _manor, prefer_async=False: calls["scout"].append(prefer_async),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.refresh_raid_runs",
        lambda _manor, prefer_async=False: calls["raid"].append(prefer_async),
    )

    refresh_manor_state(manor, prefer_async=True, include_activity_refresh=True)
    assert calls == {"resource": 1, "mission": [True], "scout": [True], "raid": [True]}
