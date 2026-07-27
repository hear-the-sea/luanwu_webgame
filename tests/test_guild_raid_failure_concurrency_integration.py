from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import GuildValidationError
from guilds.models import Guild, GuildRaidRun, GuildTroopStorage, GuildWarehouse
from guilds.services.guild import disband_guild
from guilds.services.guild_raids import finalize_guild_raid, process_guild_raid_battle
from tests.guild_pvp_service.support import create_guild_with_leader

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_concurrent_guild_raid_failure_releases_troops_once(django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("guild raid failure concurrency requires MySQL select_for_update semantics")

    suffix = uuid.uuid4().hex[:8]
    attacker_guild, attacker_member, _attacker_manor = create_guild_with_leader(
        django_user_model,
        f"fail_a_{suffix}",
    )
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(
        django_user_model,
        f"fail_d_{suffix}",
    )
    troop_template = TroopTemplate.objects.create(
        key=f"guild_failure_guard_{suffix}",
        name="并发失败补偿护院",
    )
    storage = GuildTroopStorage.objects.create(
        guild=attacker_guild,
        troop_template=troop_template,
        count=0,
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        guest_ids=[1],
        guest_snapshots=["invalid-snapshot"],
        troop_loadout={troop_template.key: 6},
        battle_at=now,
        return_at=now + timedelta(minutes=1),
    )

    start = threading.Barrier(2)
    results: list[bool] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            local_run = GuildRaidRun.objects.get(pk=run.pk)
            start.wait(timeout=10)
            result = process_guild_raid_battle(local_run, now=now)
            with results_guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    run.refresh_from_db()
    storage.refresh_from_db()
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(results) == [False, True]
    assert run.status == GuildRaidRun.Status.FAILED
    assert run.failure_reason == GuildRaidRun.FailureReason.INVALID_GUEST_SNAPSHOT
    assert run.resources_released is True
    assert run.loot_settled is True
    assert storage.count == 6


@pytest.mark.django_db(transaction=True)
def test_disband_guild_racing_finalization_has_one_linearized_outcome(django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("guild disband concurrency requires MySQL select_for_update semantics")

    suffix = uuid.uuid4().hex[:8]
    attacker_guild, attacker_member, _attacker_manor = create_guild_with_leader(
        django_user_model,
        f"settle_a_{suffix}",
    )
    defender_guild, _defender_member, _defender_manor = create_guild_with_leader(
        django_user_model,
        f"settle_d_{suffix}",
    )
    now = timezone.now()
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_member,
        status=GuildRaidRun.Status.RETURNING,
        troop_loadout={},
        return_at=now,
        is_attacker_victory=True,
        loot_silver=700,
        loot_items={"guild_disband_race_loot": 3},
        loot_item_contribution_costs={"guild_disband_race_loot": 25},
        loot_settled=False,
    )

    start = threading.Barrier(2)
    finalize_results: list[bool] = []
    disband_results: list[bool] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _finalize_worker() -> None:
        close_old_connections()
        try:
            local_run = GuildRaidRun.objects.get(pk=run.pk)
            start.wait(timeout=10)
            result = finalize_guild_raid(local_run, now=now)
            with results_guard:
                finalize_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _disband_worker() -> None:
        close_old_connections()
        try:
            local_guild = Guild.objects.get(pk=attacker_guild.pk)
            local_operator = django_user_model.objects.get(pk=attacker_member.user_id)
            start.wait(timeout=10)
            try:
                disband_guild(local_guild, local_operator)
            except GuildValidationError:
                result = False
            else:
                result = True
            with results_guard:
                disband_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [
        threading.Thread(target=_finalize_worker, daemon=True),
        threading.Thread(target=_disband_worker, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    run.refresh_from_db()
    attacker_guild.refresh_from_db()
    loot = GuildWarehouse.objects.get(
        guild=attacker_guild,
        item_key="guild_disband_race_loot",
    )
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert finalize_results == [True]
    assert len(disband_results) == 1
    assert run.status == GuildRaidRun.Status.COMPLETED
    assert run.loot_settled is True
    assert attacker_guild.silver == 700
    assert loot.quantity == 3
    assert attacker_guild.is_active is (not disband_results[0])
