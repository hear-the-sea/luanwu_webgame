from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted

import gameplay.services.raid as raid_service
from gameplay.selectors.home import _normalize_hourly_rates, get_home_context
from gameplay.services.manor.core import ensure_manor
from gameplay.services.resources import ResourceProductionBasis
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildRaidRun


def test_normalize_hourly_rates_coerces_invalid_values():
    normalized = _normalize_hourly_rates(
        {
            "grain": "120",
            "silver": "invalid",
            "stone": None,
            "wood": -8,
            "iron": 3.8,
            123: 456,
        }
    )

    assert normalized == {
        "grain": 120,
        "silver": 0,
        "stone": 0,
        "wood": 0,
        "iron": 3,
    }


def test_normalize_hourly_rates_rejects_non_mapping_input():
    assert _normalize_hourly_rates(None) == {}
    assert _normalize_hourly_rates("bad") == {}


class _FakeQuerySet(list):
    def all(self):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def select_related(self, *_args, **_kwargs):
        return self

    def prefetch_related(self, *_args, **_kwargs):
        return self


def _patch_home_context_dependencies(monkeypatch) -> None:
    monkeypatch.setattr("gameplay.selectors.home.optimize_guest_queryset", lambda qs: qs)
    monkeypatch.setattr("gameplay.selectors.home.get_technology_template", lambda *_a, **_k: {})
    monkeypatch.setattr("gameplay.selectors.home.cache.get", lambda *_a, **_k: None)
    monkeypatch.setattr("gameplay.selectors.home.cache.set", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.get_hourly_rates",
        lambda *_a, **_k: {"grain": "12", "silver": 8},
    )
    monkeypatch.setattr("gameplay.utils.resource_calculator.get_personnel_grain_cost_per_hour", lambda *_a, **_k: 3)
    monkeypatch.setattr(raid_service, "get_active_scouts", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_active_raids", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_incoming_raids", lambda *_a, **_k: [])


def test_get_home_context_tolerates_cache_backend_failure(monkeypatch):
    monkeypatch.setattr("gameplay.selectors.home.optimize_guest_queryset", lambda qs: qs)
    monkeypatch.setattr("gameplay.selectors.home.get_technology_template", lambda *_a, **_k: {})
    monkeypatch.setattr("gameplay.selectors.home.can_retreat", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "gameplay.selectors.home.cache.get",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )
    monkeypatch.setattr(
        "gameplay.selectors.home.cache.set",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.get_hourly_rates", lambda *_a, **_k: {"grain": "12", "silver": 8}
    )
    monkeypatch.setattr("gameplay.utils.resource_calculator.get_personnel_grain_cost_per_hour", lambda *_a, **_k: 3)
    monkeypatch.setattr(raid_service, "get_active_scouts", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_active_raids", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_incoming_raids", lambda *_a, **_k: [])

    manor = SimpleNamespace(
        pk=1,
        grain=100,
        silver=200,
        retainer_count=3,
        retainer_capacity=10,
        guests=_FakeQuerySet(),
        mission_runs=_FakeQuerySet(),
        buildings=_FakeQuerySet(),
        technologies=_FakeQuerySet(),
        troops=_FakeQuerySet(),
    )

    context = get_home_context(manor)

    assert context["grain_production"] == 12
    assert context["personnel_grain_cost"] == 3
    assert context["building_income"] == [
        {"resource": "grain", "label": "粮食", "rate": 12},
        {"resource": "silver", "label": "银两", "rate": 8},
    ]


def test_get_home_context_reuses_read_production_basis_on_cache_miss(monkeypatch):
    _patch_home_context_dependencies(monkeypatch)
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.get_hourly_rates",
        lambda *_args, **_kwargs: pytest.fail("hourly rates should be reused from the read projection"),
    )
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.get_personnel_grain_cost_per_hour",
        lambda *_args, **_kwargs: pytest.fail("personnel cost should be reused from the read projection"),
    )

    production_basis = ResourceProductionBasis(
        hourly_rates=(("grain", 12.0), ("silver", 8.0)),
        personnel_grain_cost_per_hour=3,
    )
    manor = SimpleNamespace(
        pk=1,
        grain=100,
        silver=200,
        retainer_count=3,
        retainer_capacity=10,
        guests=_FakeQuerySet(),
        mission_runs=_FakeQuerySet(),
        buildings=_FakeQuerySet(),
        technologies=_FakeQuerySet(),
        troops=_FakeQuerySet(),
    )

    context = get_home_context(manor, production_basis=production_basis)

    assert context["grain_production"] == 12
    assert context["personnel_grain_cost"] == 3


def test_get_home_context_runtime_marker_cache_error_bubbles_up(monkeypatch):
    monkeypatch.setattr("gameplay.selectors.home.optimize_guest_queryset", lambda qs: qs)
    monkeypatch.setattr("gameplay.selectors.home.get_technology_template", lambda *_a, **_k: {})
    monkeypatch.setattr("gameplay.selectors.home.can_retreat", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "gameplay.selectors.home.cache.get",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache down")),
    )
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.get_hourly_rates", lambda *_a, **_k: {"grain": "12", "silver": 8}
    )
    monkeypatch.setattr("gameplay.utils.resource_calculator.get_personnel_grain_cost_per_hour", lambda *_a, **_k: 3)
    monkeypatch.setattr(raid_service, "get_active_scouts", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_active_raids", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_incoming_raids", lambda *_a, **_k: [])

    manor = SimpleNamespace(
        pk=1,
        grain=100,
        silver=200,
        retainer_count=3,
        retainer_capacity=10,
        guests=_FakeQuerySet(),
        mission_runs=_FakeQuerySet(),
        buildings=_FakeQuerySet(),
        technologies=_FakeQuerySet(),
        troops=_FakeQuerySet(),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        get_home_context(manor)


def test_get_home_context_runtime_marker_cache_set_error_bubbles_up(monkeypatch):
    monkeypatch.setattr("gameplay.selectors.home.optimize_guest_queryset", lambda qs: qs)
    monkeypatch.setattr("gameplay.selectors.home.get_technology_template", lambda *_a, **_k: {})
    monkeypatch.setattr("gameplay.selectors.home.can_retreat", lambda *_a, **_k: False)
    monkeypatch.setattr("gameplay.selectors.home.cache.get", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "gameplay.selectors.home.cache.set",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache set failed")),
    )
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.get_hourly_rates", lambda *_a, **_k: {"grain": "12", "silver": 8}
    )
    monkeypatch.setattr("gameplay.utils.resource_calculator.get_personnel_grain_cost_per_hour", lambda *_a, **_k: 3)
    monkeypatch.setattr(raid_service, "get_active_scouts", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_active_raids", lambda *_a, **_k: [])
    monkeypatch.setattr(raid_service, "get_incoming_raids", lambda *_a, **_k: [])

    manor = SimpleNamespace(
        pk=1,
        grain=100,
        silver=200,
        retainer_count=3,
        retainer_capacity=10,
        guests=_FakeQuerySet(),
        mission_runs=_FakeQuerySet(),
        buildings=_FakeQuerySet(),
        technologies=_FakeQuerySet(),
        troops=_FakeQuerySet(),
    )

    with pytest.raises(RuntimeError, match="cache set failed"):
        get_home_context(manor)


@pytest.mark.django_db
def test_get_home_context_uses_real_guild_mission_retreat_eligibility(django_user_model, monkeypatch):
    _patch_home_context_dependencies(monkeypatch)
    monkeypatch.setattr("gameplay.selectors.home.can_retreat", lambda *_args, **_kwargs: False)

    user = django_user_model.objects.create_user(username="home_guild_mission_user", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会任务", founder=user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(key="home_guild_mission_tpl", name="首页任务")
    now = timezone.now()
    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=member,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=1,
        started_at=now - timedelta(seconds=30),
        battle_at=now + timedelta(seconds=30),
        return_at=now + timedelta(seconds=90),
    )

    context = get_home_context(manor)

    assert context["active_guild_mission"].id == run.id
    assert context["active_guild_mission"].can_retreat_from_home is True


@pytest.mark.django_db
def test_get_home_context_returns_projected_active_guild_pvp_run_for_battling_state(
    django_user_model,
    monkeypatch,
):
    _patch_home_context_dependencies(monkeypatch)
    monkeypatch.setattr("gameplay.selectors.home.can_retreat", lambda *_args, **_kwargs: False)

    user = django_user_model.objects.create_user(username="home_guild_pvp_user", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会PVP", founder=user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    defender = django_user_model.objects.create_user(username="home_guild_pvp_defender", password="pass12345")
    defender_guild = Guild.objects.create(name="首页守方帮会", founder=defender, is_active=True)
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=guild,
        defender_guild=defender_guild,
        started_by=member,
        status=GuildRaidRun.Status.BATTLING,
        selected_guest_count=1,
        battle_at=now - timedelta(seconds=10),
        return_at=now + timedelta(seconds=120),
    )

    context = get_home_context(manor)

    projected_run = context["active_guild_pvp_run"]

    assert projected_run.run.id == run.id
    assert projected_run.display_status_key == "battling"
    assert projected_run.display_status_label == "战斗中"
    assert projected_run.display_hint == f"已抵达{defender_guild.name}，正在交战"
    assert projected_run.display_eta_at == run.return_at
    assert projected_run.display_eta_label == "结束"
    assert projected_run.can_retreat is False
    assert not hasattr(projected_run, "can_retreat_from_home")
    assert not hasattr(projected_run, "next_state_at")
    assert not hasattr(projected_run, "get_status_display")
    assert not hasattr(run, "next_state_at")


@pytest.mark.django_db
def test_get_home_context_returns_projected_incoming_guild_pvp_runs_and_keeps_overdue_visible(
    django_user_model,
    monkeypatch,
):
    _patch_home_context_dependencies(monkeypatch)
    monkeypatch.setattr("gameplay.selectors.home.can_retreat", lambda *_args, **_kwargs: False)

    defender_user = django_user_model.objects.create_user(username="home_incoming_defender", password="pass12345")
    manor = ensure_manor(defender_user)
    defender_guild = Guild.objects.create(name="首页守方", founder=defender_user, is_active=True)
    defender_member = GuildMember.objects.create(
        guild=defender_guild, user=defender_user, position="leader", is_active=True
    )
    attacker_user = django_user_model.objects.create_user(username="home_incoming_attacker", password="pass12345")
    attacker_guild = Guild.objects.create(name="首页攻方", founder=attacker_user, is_active=True)
    attacker_member = GuildMember.objects.create(
        guild=attacker_guild, user=attacker_user, position="leader", is_active=True
    )
    now = timezone.now()
    overdue_marching = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        battle_at=now - timedelta(seconds=5),
        return_at=now + timedelta(seconds=120),
    )
    battling = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.BATTLING,
        selected_guest_count=1,
        battle_at=now - timedelta(seconds=30),
        return_at=now + timedelta(seconds=180),
    )

    context = get_home_context(manor)

    projected_runs = context["incoming_guild_pvp_runs"]
    projected_by_id = {projected.run.id: projected for projected in projected_runs}

    assert defender_member.guild_id == defender_guild.id
    assert set(projected_by_id) == {overdue_marching.id, battling.id}
    assert projected_by_id[overdue_marching.id].display_status_key == "arrived"
    assert projected_by_id[overdue_marching.id].display_hint == "敌方帮会已抵达，正在交战"
    assert projected_by_id[overdue_marching.id].display_eta_at == overdue_marching.return_at
    assert projected_by_id[battling.id].display_status_key == "battling"
    assert projected_by_id[battling.id].display_hint == "敌方帮会已抵达，正在交战"
    assert not hasattr(projected_by_id[overdue_marching.id], "home_status_label")
    assert not hasattr(projected_by_id[overdue_marching.id], "next_state_at")
    assert not hasattr(overdue_marching, "home_status_label")
    assert not hasattr(overdue_marching, "next_state_at")
