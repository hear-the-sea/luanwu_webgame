from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_migration_0157_backfills_in_flight_growth_claims_before_adding_constraint() -> None:
    migrate_from = [("gameplay", "0156_arena_virtual_reserve_roster_target")]
    migrate_to = [("gameplay", "0157_arena_growth_effective_progress")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        BotProfile = old_apps.get_model("gameplay", "BotProfile")
        Tournament = old_apps.get_model("gameplay", "ArenaTournament")
        Demand = old_apps.get_model("gameplay", "ArenaVirtualDemand")
        Member = old_apps.get_model("gameplay", "ArenaVirtualReserveMember")
        GuestTemplate = old_apps.get_model("guests", "GuestTemplate")
        Guest = old_apps.get_model("guests", "Guest")

        now = timezone.now()
        tournament = Tournament.objects.create()
        demand = Demand.objects.create(tournament_id=tournament.pk)
        template = GuestTemplate.objects.create(
            key="arena-growth-progress-migration-template",
            name="迁移门客",
            archetype="civil",
            rarity="gray",
            flavor="",
        )

        member_ids: list[int] = []
        for index in range(2):
            user = User.objects.create(
                username=f"arena_growth_progress_migration_{index}",
                email=f"arena-growth-progress-migration-{index}@test.local",
                password="unused",
            )
            manor = Manor.objects.create(
                user_id=user.pk,
                coordinate_x=index,
                coordinate_y=0,
            )
            profile = BotProfile.objects.create(
                manor_id=manor.pk,
                prestige_band="newbie",
                growth_seed=index + 1,
                next_growth_at=now,
                abandon_at=now + timedelta(days=1),
                retire_at=now + timedelta(days=2),
            )
            if index == 0:
                Guest.objects.create(
                    manor_id=manor.pk,
                    template_id=template.pk,
                    custom_name="可参赛一",
                    status="idle",
                )
                Guest.objects.create(
                    manor_id=manor.pk,
                    template_id=template.pk,
                    custom_name="可参赛二",
                    status="idle",
                )
                Guest.objects.create(
                    manor_id=manor.pk,
                    template_id=template.pk,
                    custom_name="训练中",
                    status="training",
                )
                member = Member.objects.create(
                    demand_id=demand.pk,
                    profile_id=profile.pk,
                    growth_operation_id="arena-growth-migration",
                    growth_attempt_ordinal=1,
                    growth_claim_token=uuid4(),
                    growth_claimed_at=now,
                    growth_claim_expires_at=now + timedelta(minutes=5),
                    growth_requested_at=now,
                    growth_demand_version=1,
                    growth_member_version=1,
                    growth_power_before=100,
                    growth_minimum_guest_count=2,
                    growth_minimum_guest_level=1,
                )
            else:
                member = Member.objects.create(
                    demand_id=demand.pk,
                    profile_id=profile.pk,
                )
            member_ids.append(int(member.pk))

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedMember = new_apps.get_model("gameplay", "ArenaVirtualReserveMember")

        claimed = MigratedMember.objects.get(pk=member_ids[0])
        unclaimed = MigratedMember.objects.get(pk=member_ids[1])
        assert claimed.growth_eligible_guest_count_before == 2
        assert unclaimed.growth_eligible_guest_count_before is None
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0162_backfills_member_leases_and_seeds_paused_routing_boundary() -> None:
    migrate_from = [("gameplay", "0161_arena_growth_digest_schema")]
    migrate_to = [("gameplay", "0162_arena_admission_probe_and_member_lease")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        BotProfile = old_apps.get_model("gameplay", "BotProfile")
        Tournament = old_apps.get_model("gameplay", "ArenaTournament")
        Demand = old_apps.get_model("gameplay", "ArenaVirtualDemand")
        Member = old_apps.get_model("gameplay", "ArenaVirtualReserveMember")
        Routing = old_apps.get_model("gameplay", "BotRuntimeRoutingState")

        now = timezone.now()
        Routing.objects.create(
            key="virtual_players",
            bootstrap_mode="v2_active",
            maintenance_mode="v2_paused",
        )
        tournament = Tournament.objects.create()
        demand = Demand.objects.create(tournament_id=tournament.pk)

        def create_profile(suffix: str, coordinate_x: int):
            user = User.objects.create(
                username=f"arena_lease_migration_{suffix}",
                email=f"arena-lease-migration-{suffix}@test.local",
                password="unused",
            )
            manor = Manor.objects.create(
                user_id=user.pk,
                coordinate_x=coordinate_x,
                coordinate_y=400,
            )
            return BotProfile.objects.create(
                manor_id=manor.pk,
                prestige_band="newbie",
                growth_seed=coordinate_x,
                next_growth_at=now,
                abandon_at=now + timedelta(days=1),
                retire_at=now + timedelta(days=2),
            )

        training = Member.objects.create(
            demand_id=demand.pk,
            profile_id=create_profile("training", 400).pk,
            state="training",
        )
        ready = Member.objects.create(
            demand_id=demand.pk,
            profile_id=create_profile("ready", 401).pk,
            state="ready",
        )
        Member.objects.filter(pk=training.pk).update(
            created_at=now - timedelta(days=2),
        )
        Member.objects.filter(pk=ready.pk).update(
            created_at=now - timedelta(days=2),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedMember = new_apps.get_model("gameplay", "ArenaVirtualReserveMember")

        migrated_training = MigratedMember.objects.get(pk=training.pk)
        migrated_ready = MigratedMember.objects.get(pk=ready.pk)
        assert migrated_training.lease_paused_at is not None
        assert migrated_training.lease_paused_at <= timezone.now()
        assert migrated_training.lease_expires_at > migrated_training.lease_paused_at
        assert migrated_ready.lease_paused_at is None
        assert migrated_ready.lease_expires_at > migrated_ready.created_at

        with pytest.raises(IntegrityError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE gameplay_arenavirtualreservemember " "SET lease_paused_at = %s WHERE id = %s",
                [now, ready.pk],
            )

        executor.migrate(migrate_from)
        round_trip_apps = executor.loader.project_state(migrate_from).apps
        RoundTripMember = round_trip_apps.get_model("gameplay", "ArenaVirtualReserveMember")
        assert RoundTripMember.objects.filter(pk=training.pk).exists()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0158_backfills_admission_high_water_without_charging_legacy_exhausted_members() -> None:
    migrate_from = [("gameplay", "0157_arena_growth_effective_progress")]
    migrate_to = [("gameplay", "0158_arena_growth_budget_and_admission_high_water")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        BotProfile = old_apps.get_model("gameplay", "BotProfile")
        Tournament = old_apps.get_model("gameplay", "ArenaTournament")
        Demand = old_apps.get_model("gameplay", "ArenaVirtualDemand")
        Member = old_apps.get_model("gameplay", "ArenaVirtualReserveMember")

        tournament = Tournament.objects.create()
        legacy_counter_demand = Demand.objects.create(
            tournament_id=tournament.pk,
            created_profile_count=7,
        )
        baseline_tournament = Tournament.objects.create(status="completed")
        baseline_demand = Demand.objects.create(
            tournament_id=baseline_tournament.pk,
            created_profile_count=0,
        )
        now = timezone.now()
        member_states = ["ready", "training", "exhausted", "exhausted", "exhausted"]
        for index, state in enumerate(member_states):
            user = User.objects.create(
                username=f"arena_admission_baseline_{index}",
                email=f"arena-admission-baseline-{index}@test.local",
                password="unused",
            )
            manor = Manor.objects.create(
                user_id=user.pk,
                coordinate_x=100 + index,
                coordinate_y=100,
            )
            profile = BotProfile.objects.create(
                manor_id=manor.pk,
                prestige_band="newbie",
                growth_seed=100 + index,
                next_growth_at=now,
                abandon_at=now + timedelta(days=1),
                retire_at=now + timedelta(days=2),
            )
            Member.objects.create(
                demand_id=baseline_demand.pk,
                profile_id=profile.pk,
                state=state,
            )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedDemand = new_apps.get_model("gameplay", "ArenaVirtualDemand")

        migrated_legacy_counter = MigratedDemand.objects.get(pk=legacy_counter_demand.pk)
        migrated_baseline = MigratedDemand.objects.get(pk=baseline_demand.pk)
        assert migrated_legacy_counter.admission_attempt_high_water == 7
        assert migrated_legacy_counter.admission_legacy_exhausted_baseline_count == 0
        assert migrated_baseline.admission_attempt_high_water == 2
        assert migrated_baseline.admission_legacy_exhausted_baseline_count == 3
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0159_adds_consistent_admission_guard_fields() -> None:
    migrate_from = [("gameplay", "0158_arena_growth_budget_and_admission_high_water")]
    migrate_to = [("gameplay", "0159_arena_virtual_demand_admission_guard")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        Tournament = old_apps.get_model("gameplay", "ArenaTournament")
        Demand = old_apps.get_model("gameplay", "ArenaVirtualDemand")
        tournament = Tournament.objects.create()
        demand = Demand.objects.create(tournament_id=tournament.pk)

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedDemand = new_apps.get_model("gameplay", "ArenaVirtualDemand")
        migrated = MigratedDemand.objects.get(pk=demand.pk)

        assert migrated.admission_paused_at is None
        assert migrated.admission_pause_reason == ""
        with pytest.raises(IntegrityError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE gameplay_arenavirtualdemand "
                "SET admission_pause_reason = %s, admission_paused_at = NULL "
                "WHERE id = %s",
                ["no_effective_progress", migrated.pk],
            )
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0160_backfills_only_claimed_growth_objectives() -> None:
    migrate_from = [("gameplay", "0159_arena_virtual_demand_admission_guard")]
    migrate_to = [("gameplay", "0160_arena_growth_objective_snapshot")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        BotProfile = old_apps.get_model("gameplay", "BotProfile")
        Tournament = old_apps.get_model("gameplay", "ArenaTournament")
        Demand = old_apps.get_model("gameplay", "ArenaVirtualDemand")
        Member = old_apps.get_model("gameplay", "ArenaVirtualReserveMember")

        now = timezone.now()

        def create_profile(*, suffix: str, coordinate_x: int):
            user = User.objects.create(
                username=f"arena_growth_objective_migration_{suffix}",
                email=f"arena-growth-objective-migration-{suffix}@test.local",
                password="unused",
            )
            manor = Manor.objects.create(
                user_id=user.pk,
                coordinate_x=coordinate_x,
                coordinate_y=200,
            )
            return BotProfile.objects.create(
                manor_id=manor.pk,
                prestige_band="newbie",
                growth_seed=coordinate_x,
                next_growth_at=now,
                abandon_at=now + timedelta(days=1),
                retire_at=now + timedelta(days=2),
            )

        profile = create_profile(suffix="claimed", coordinate_x=200)
        tournament = Tournament.objects.create(player_limit=4)
        demand = Demand.objects.create(
            tournament_id=tournament.pk,
            target_guest_count=2,
            target_team_power=600,
            version=3,
        )
        claimed = Member.objects.create(
            demand_id=demand.pk,
            profile_id=profile.pk,
            roster_target_count=3,
            growth_operation_id="arena-growth-migration-objective",
            growth_attempt_ordinal=1,
            growth_claim_token=uuid4(),
            growth_claimed_at=now,
            growth_claim_expires_at=now + timedelta(minutes=5),
            growth_requested_at=now,
            growth_demand_version=3,
            growth_member_version=3,
            growth_power_before=450,
            growth_eligible_guest_count_before=1,
            growth_minimum_guest_count=2,
            growth_minimum_guest_level=20,
            growth_guest_rarity_cap="purple",
        )
        unclaimed = Member.objects.create(
            demand_id=demand.pk,
            profile_id=create_profile(suffix="unclaimed", coordinate_x=201).pk,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedMember = new_apps.get_model("gameplay", "ArenaVirtualReserveMember")

        claimed_payload = MigratedMember.objects.get(pk=claimed.pk).growth_objective_payload
        assert claimed_payload == {
            "critical_guest_count": 2,
            "preferred_guest_count": 3,
            "selected_power_lower_bound": 480,
            "selected_power_upper_bound": 720,
            "selected_power_before": 450,
            "target_team_power": 600,
            "lineup_mode": "tournament",
            "lineup_event_id": tournament.pk,
            "lineup_max_size": 10,
            "minimum_guest_level": 20,
            "recruitment_rarity_cap": "purple",
            "max_guest_level_step": 6,
        }
        assert MigratedMember.objects.get(pk=unclaimed.pk).growth_objective_payload == {}
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0161_marks_only_in_flight_claims_as_legacy_digest() -> None:
    migrate_from = [("gameplay", "0160_arena_growth_objective_snapshot")]
    migrate_to = [("gameplay", "0161_arena_growth_digest_schema")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        BotProfile = old_apps.get_model("gameplay", "BotProfile")
        Tournament = old_apps.get_model("gameplay", "ArenaTournament")
        Demand = old_apps.get_model("gameplay", "ArenaVirtualDemand")
        Member = old_apps.get_model("gameplay", "ArenaVirtualReserveMember")

        now = timezone.now()
        tournament = Tournament.objects.create()
        demand = Demand.objects.create(tournament_id=tournament.pk)

        def create_profile(*, suffix: str, coordinate_x: int):
            user = User.objects.create(
                username=f"arena_growth_digest_migration_{suffix}",
                email=f"arena-growth-digest-migration-{suffix}@test.local",
                password="unused",
            )
            manor = Manor.objects.create(
                user_id=user.pk,
                coordinate_x=coordinate_x,
                coordinate_y=300,
            )
            return BotProfile.objects.create(
                manor_id=manor.pk,
                prestige_band="newbie",
                growth_seed=coordinate_x,
                next_growth_at=now,
                abandon_at=now + timedelta(days=1),
                retire_at=now + timedelta(days=2),
            )

        claimed = Member.objects.create(
            demand_id=demand.pk,
            profile_id=create_profile(suffix="claimed", coordinate_x=300).pk,
            growth_operation_id="arena-growth-digest-migration",
            growth_attempt_ordinal=1,
            growth_claim_token=uuid4(),
            growth_claimed_at=now,
            growth_claim_expires_at=now + timedelta(minutes=5),
            growth_requested_at=now,
            growth_demand_version=1,
            growth_member_version=1,
            growth_power_before=100,
            growth_eligible_guest_count_before=1,
            growth_minimum_guest_count=1,
            growth_minimum_guest_level=1,
            growth_objective_payload={
                "critical_guest_count": 1,
                "preferred_guest_count": 1,
                "selected_power_lower_bound": 1,
                "selected_power_upper_bound": 2,
                "selected_power_before": 100,
                "target_team_power": 1,
                "lineup_mode": "tournament",
                "lineup_event_id": tournament.pk,
                "lineup_max_size": 10,
                "minimum_guest_level": 1,
                "recruitment_rarity_cap": None,
                "max_guest_level_step": 6,
            },
        )
        unclaimed = Member.objects.create(
            demand_id=demand.pk,
            profile_id=create_profile(suffix="unclaimed", coordinate_x=301).pk,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedMember = new_apps.get_model("gameplay", "ArenaVirtualReserveMember")

        assert MigratedMember.objects.get(pk=claimed.pk).growth_request_digest_schema == 1
        assert MigratedMember.objects.get(pk=unclaimed.pk).growth_request_digest_schema == 2
        with pytest.raises(IntegrityError), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE gameplay_arenavirtualreservemember " "SET growth_request_digest_schema = %s " "WHERE id = %s",
                [1, unclaimed.pk],
            )
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
