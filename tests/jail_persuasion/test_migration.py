from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_migration_0129_backfills_captured_loyalty_for_all_historical_statuses():
    migrate_from = [("gameplay", "0128_bot_maintenance_started_at")]
    migrate_to = [("gameplay", "0129_jail_persuasion_state")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        JailPrisoner = old_apps.get_model("gameplay", "JailPrisoner")
        GuestTemplate = old_apps.get_model("guests", "GuestTemplate")

        captor_user = User.objects.create(
            username="jail_persuasion_migration_captor",
            email="jail-persuasion-migration-captor@test.local",
            password="unused",
        )
        original_user = User.objects.create(
            username="jail_persuasion_migration_original",
            email="jail-persuasion-migration-original@test.local",
            password="unused",
        )
        captor = Manor.objects.create(user_id=captor_user.pk)
        original = Manor.objects.create(user_id=original_user.pk)
        template = GuestTemplate.objects.create(
            key="jail-persuasion-migration-template",
            name="迁移囚徒",
            archetype="civil",
            rarity="purple",
        )
        expected_loyalty = {
            "held": 25,
            "recruited": 60,
            "released": 90,
        }
        for status, loyalty in expected_loyalty.items():
            JailPrisoner.objects.create(
                captor_id=captor.pk,
                original_manor_id=original.pk,
                guest_template_id=template.pk,
                original_guest_name=status,
                loyalty=loyalty,
                status=status,
            )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedPrisoner = new_apps.get_model("gameplay", "JailPrisoner")

        migrated = {prisoner.status: prisoner for prisoner in MigratedPrisoner.objects.order_by("pk")}
        assert {status: prisoner.captured_loyalty for status, prisoner in migrated.items()} == expected_loyalty
        for prisoner in migrated.values():
            assert prisoner.affinity == 0
            assert prisoner.revealed_level == 0
            assert prisoner.milestone_stage == 0
            assert prisoner.interactions_today == 0
            assert prisoner.observed_at is None
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
