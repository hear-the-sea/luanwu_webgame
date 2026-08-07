from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from battle.random_context import current_replay_metadata
from gameplay.models import ArenaTournament, ArenaVirtualDemand, ArenaVirtualReserveMember, BotProfile, RaidRun
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import battle as combat_battle


def _create_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass123")
    return ensure_manor(user)


def _create_bot_profile(manor, *, budget: int, state: str = BotProfile.State.ACTIVE) -> BotProfile:
    now = timezone.now()
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.RICH,
        state=state,
        prestige_band="junior",
        growth_seed=123,
        growth_stage=3,
        next_growth_at=now + timedelta(hours=1),
        abandon_at=now + timedelta(days=5),
        retire_at=now + timedelta(days=10),
        loot_budget_daily=budget,
        last_planned_at=now,
    )


@pytest.mark.parametrize(
    ("state", "should_retire"),
    [
        (BotProfile.State.ACTIVE, True),
        (BotProfile.State.SLOWING, True),
        (BotProfile.State.ABANDONED, True),
        (BotProfile.State.RETIRED, False),
        (BotProfile.State.STALE, False),
    ],
)
def test_exhausted_loot_budget_recommends_retirement_only_for_maintained_profiles(
    monkeypatch,
    state,
    should_retire,
):
    from gameplay.services import virtual_player_loot_limits

    profile = SimpleNamespace(loot_budget_daily=100, state=state, pk=7)
    profile_lookup = SimpleNamespace(first=lambda: profile)
    monkeypatch.setattr(virtual_player_loot_limits.BotProfile.objects, "filter", lambda **_kwargs: profile_lookup)
    monkeypatch.setattr(virtual_player_loot_limits, "_spent_from_bot_defender_today", lambda *_args, **_kwargs: 100)
    monkeypatch.setattr(virtual_player_loot_limits, "_is_bot_manor", lambda _manor: True)

    decision = virtual_player_loot_limits.clamp_bot_loot_resources(
        attacker=object(),
        defender=object(),
        loot_resources={"grain": 50},
    )

    assert dict(decision.resources) == {}
    assert decision.bot_profile_id == profile.pk
    assert decision.bot_budget_exhausted is True
    assert decision.retirement_recommended is should_retire


def test_loot_limit_module_has_no_profile_write_dependency():
    from gameplay.services import virtual_player_loot_limits

    source = Path(virtual_player_loot_limits.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_symbols = {
        "bulk_create",
        "bulk_update",
        "create",
        "delete",
        "mark_profile_retired",
        "profile_store",
        "retire_locked_virtual_player_if_unprotected",
        "retire_virtual_player_if_unprotected",
        "save",
        "update",
        "update_or_create",
    }
    imported_symbols = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    called_symbols = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }

    assert imported_symbols.isdisjoint(forbidden_symbols)
    assert called_symbols.isdisjoint(forbidden_symbols)


@pytest.mark.django_db
def test_apply_raid_loot_clamps_bot_defender_resources_to_daily_budget(monkeypatch, django_user_model):
    attacker = _create_manor(django_user_model, "bot_budget_attacker")
    defender = _create_manor(django_user_model, "bot_budget_defender")
    profile = _create_bot_profile(defender, budget=500)
    defender.grain = 10_000
    defender.silver = 10_000
    defender.save(update_fields=["grain", "silver"])

    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.BATTLING,
        **current_replay_metadata(101),
    )
    monkeypatch.setattr(combat_battle, "_calculate_loot", lambda *_args, **_kwargs: ({"grain": 700, "silver": 600}, {}))

    with transaction.atomic():
        decision = combat_battle._apply_raid_loot_if_needed(run, is_attacker_victory=True)

    profile.refresh_from_db()
    defender.refresh_from_db()

    assert sum(run.loot_resources.values()) == 500
    assert run.loot_resources == {"grain": 500}
    assert defender.grain == 9_500
    assert defender.silver == 10_000
    assert profile.state == BotProfile.State.ACTIVE
    assert profile.maintenance_stopped_at is None
    assert dict(decision.resources) == {"grain": 500}
    assert decision.bot_profile_id == profile.pk
    assert decision.bot_budget_exhausted is True
    assert decision.retirement_recommended is True


@pytest.mark.django_db
def test_bot_defender_daily_budget_accounts_for_prior_loot(django_user_model):
    from gameplay.services.virtual_player_loot_limits import clamp_bot_loot_resources

    attacker = _create_manor(django_user_model, "bot_budget_prior_attacker")
    defender = _create_manor(django_user_model, "bot_budget_prior_defender")
    _create_bot_profile(defender, budget=1_000)
    prior = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.RETURNING,
        is_attacker_victory=True,
        loot_resources={"grain": 800},
    )
    RaidRun.objects.filter(pk=prior.pk).update(started_at=timezone.now() - timedelta(hours=1))

    decision = clamp_bot_loot_resources(
        attacker=attacker,
        defender=defender,
        loot_resources={"grain": 500, "silver": 500},
        now=timezone.now(),
    )

    assert dict(decision.resources) == {"grain": 200}
    assert decision.bot_budget_exhausted is True
    assert decision.retirement_recommended is True


@pytest.mark.django_db
def test_bot_defender_prior_loot_exhaustion_returns_retirement_recommendation(django_user_model):
    from gameplay.services.virtual_player_loot_limits import clamp_bot_loot_resources

    attacker = _create_manor(django_user_model, "bot_budget_exhausted_attacker")
    defender = _create_manor(django_user_model, "bot_budget_exhausted_defender")
    profile = _create_bot_profile(defender, budget=1_000)
    prior = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.RETURNING,
        is_attacker_victory=True,
        loot_resources={"grain": 1_000},
    )
    RaidRun.objects.filter(pk=prior.pk).update(started_at=timezone.now() - timedelta(hours=1))

    decision = clamp_bot_loot_resources(
        attacker=attacker,
        defender=defender,
        loot_resources={"grain": 500},
        now=timezone.now(),
    )

    profile.refresh_from_db()
    assert dict(decision.resources) == {}
    assert decision.bot_profile_id == profile.pk
    assert decision.bot_budget_exhausted is True
    assert decision.retirement_recommended is True
    assert profile.state == BotProfile.State.ACTIVE
    assert profile.maintenance_stopped_at is None


@pytest.mark.django_db
def test_exhausted_loot_budget_does_not_retire_active_reserve_member(django_user_model):
    from gameplay.services.virtual_player_loot_limits import clamp_bot_loot_resources

    now = timezone.now()
    attacker = _create_manor(django_user_model, "reserved_budget_attacker")
    defender = _create_manor(django_user_model, "reserved_budget_defender")
    profile = _create_bot_profile(defender, budget=1_000)
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
    )
    demand = ArenaVirtualDemand.objects.create(
        tournament=tournament,
        missing_entry_count=1,
        reserve_target_count=1,
        warm_target_count=1,
        max_reserve_target_count=1,
        next_retry_at=now,
    )
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    prior = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.RETURNING,
        is_attacker_victory=True,
        loot_resources={"grain": 1_000},
    )
    RaidRun.objects.filter(pk=prior.pk).update(started_at=now - timedelta(hours=1))

    decision = clamp_bot_loot_resources(
        attacker=attacker,
        defender=defender,
        loot_resources={"grain": 500},
        now=now,
    )

    assert dict(decision.resources) == {}
    assert decision.retirement_recommended is True

    combat_battle._process_bot_loot_retirement(decision, now=now)

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE
    assert ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db
def test_real_attacker_daily_bot_resource_limit_spans_multiple_bots(settings, django_user_model):
    from gameplay.services.virtual_player_loot_limits import clamp_bot_loot_resources

    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "loot_limits": {
                "real_attacker_daily_resource_cap": 1_000,
            }
        }
    }
    attacker = _create_manor(django_user_model, "daily_bot_cap_attacker")
    first_defender = _create_manor(django_user_model, "daily_bot_cap_defender_1")
    second_defender = _create_manor(django_user_model, "daily_bot_cap_defender_2")
    _create_bot_profile(first_defender, budget=10_000)
    _create_bot_profile(second_defender, budget=10_000)
    prior = RaidRun.objects.create(
        attacker=attacker,
        defender=first_defender,
        status=RaidRun.Status.RETURNING,
        is_attacker_victory=True,
        loot_resources={"grain": 700},
    )
    RaidRun.objects.filter(pk=prior.pk).update(started_at=timezone.now() - timedelta(hours=1))

    decision = clamp_bot_loot_resources(
        attacker=attacker,
        defender=second_defender,
        loot_resources={"grain": 500, "silver": 500},
        now=timezone.now(),
    )

    assert sum(decision.resources.values()) == 300
    assert dict(decision.resources) == {"grain": 300}


@pytest.mark.django_db
def test_bot_attacker_does_not_consume_real_attacker_daily_limit(settings, django_user_model):
    from gameplay.services.virtual_player_loot_limits import clamp_bot_loot_resources

    settings.VIRTUAL_PLAYER_CONFIG = {
        "projection": {
            "loot_limits": {
                "real_attacker_daily_resource_cap": 1_000,
            }
        }
    }
    bot_attacker = _create_manor(django_user_model, "bot_attacker_cap_source")
    defender = _create_manor(django_user_model, "bot_attacker_cap_defender")
    _create_bot_profile(bot_attacker, budget=10_000)
    _create_bot_profile(defender, budget=10_000)
    prior = RaidRun.objects.create(
        attacker=bot_attacker,
        defender=defender,
        status=RaidRun.Status.RETURNING,
        is_attacker_victory=True,
        loot_resources={"grain": 900},
    )
    RaidRun.objects.filter(pk=prior.pk).update(started_at=timezone.now() - timedelta(hours=1))

    decision = clamp_bot_loot_resources(
        attacker=bot_attacker,
        defender=defender,
        loot_resources={"grain": 800, "silver": 800},
        now=timezone.now(),
    )

    assert dict(decision.resources) == {"grain": 800, "silver": 800}


@pytest.mark.django_db
def test_normal_player_defender_is_not_clamped_by_bot_budget(monkeypatch, django_user_model):
    attacker = _create_manor(django_user_model, "normal_budget_attacker")
    defender = _create_manor(django_user_model, "normal_budget_defender")
    defender.grain = 10_000
    defender.silver = 10_000
    defender.save(update_fields=["grain", "silver"])

    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.BATTLING,
        **current_replay_metadata(101),
    )
    monkeypatch.setattr(combat_battle, "_calculate_loot", lambda *_args, **_kwargs: ({"grain": 700, "silver": 600}, {}))

    with transaction.atomic():
        combat_battle._apply_raid_loot_if_needed(run, is_attacker_victory=True)

    assert run.loot_resources == {"grain": 700, "silver": 600}
