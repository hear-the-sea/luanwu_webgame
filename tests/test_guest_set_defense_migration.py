from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_set_defense_migration_moves_existing_bonus_and_preserves_missing_hp(monkeypatch):
    monkeypatch.setattr(import_module("guests.migrations.0076_move_set_defense_to_attribute"), "BATCH_SIZE", 2)
    previous = [("guests", "0075_guest_avatar_asset_key")]
    current = [("guests", "0076_move_set_defense_to_attribute")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(previous)
        old_apps = executor.loader.project_state(previous).apps
        user = old_apps.get_model("accounts", "User").objects.create(username="set_defense_migration_owner")
        manor = old_apps.get_model("gameplay", "Manor").objects.create(user_id=user.pk)
        template = old_apps.get_model("guests", "GuestTemplate").objects.create(
            key="set_defense_migration_guest", name="套装防御迁移门客", base_hp=1000
        )
        OldGuest = old_apps.get_model("guests", "Guest")
        wounded = OldGuest.objects.create(
            manor_id=manor.pk,
            template_id=template.pk,
            defense_stat=100,
            defense_bonus=43,
            current_hp=5000,
            gear_set_bonus={"defense": 30, "attack": 10},
        )
        defeated = OldGuest.objects.create(
            manor_id=manor.pk,
            template_id=template.pk,
            defense_stat=80,
            defense_bonus=20,
            current_hp=0,
            gear_set_bonus={"defense": 20},
        )
        unaffected = OldGuest.objects.create(
            manor_id=manor.pk,
            template_id=template.pk,
            defense_stat=90,
            defense_bonus=7,
            current_hp=4000,
            gear_set_bonus={"force": 10},
        )

        executor = MigrationExecutor(connection)
        executor.migrate(current)
        NewGuest = executor.loader.project_state(current).apps.get_model("guests", "Guest")
        saved = NewGuest.objects.get(pk=wounded.pk)
        assert (saved.defense_stat, saved.defense_bonus, saved.current_hp) == (130, 13, 6500)
        assert saved.gear_set_bonus == {"defense": 30, "attack": 10}
        saved = NewGuest.objects.get(pk=defeated.pk)
        assert (saved.defense_stat, saved.defense_bonus, saved.current_hp) == (100, 0, 0)
        saved = NewGuest.objects.get(pk=unaffected.pk)
        assert (saved.defense_stat, saved.defense_bonus, saved.current_hp) == (90, 7, 4000)

        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        RestoredGuest = executor.loader.project_state(previous).apps.get_model("guests", "Guest")
        saved = RestoredGuest.objects.get(pk=wounded.pk)
        assert (saved.defense_stat, saved.defense_bonus, saved.current_hp) == (100, 43, 5000)
    finally:
        MigrationExecutor(connection).migrate(latest)
