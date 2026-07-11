from __future__ import annotations

import importlib

import pytest
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.query import QuerySet


def _create_legacy_message_cases(apps):
    User = apps.get_model("accounts", "User")
    Manor = apps.get_model("gameplay", "Manor")
    Message = apps.get_model("gameplay", "Message")
    user = User.objects.create(
        username="message_protection_migration",
        email="message-protection-migration@test.local",
        password="unused",
    )
    manor = Manor.objects.create(user_id=user.pk)
    cases = {
        "resource protected": ({"resources": {"silver": 1}}, False, True),
        "item protected": ({"items": {"migration_item": 1}}, False, True),
        "claimed asset": ({"resources": {"grain": 1}}, True, False),
        "metadata only": ({"metadata": {"source": "legacy"}}, False, False),
        "empty buckets": ({"resources": {}, "items": {}}, False, False),
        "non dict buckets": ({"resources": ["legacy"], "items": "legacy"}, False, False),
    }
    for title, (attachments, is_claimed, _expected) in cases.items():
        Message.objects.create(
            manor_id=manor.pk,
            kind="reward",
            title=title,
            attachments=attachments,
            is_claimed=is_claimed,
        )
    return cases


@pytest.mark.django_db(transaction=True)
def test_migrations_0121_to_0123_split_schema_backfill_and_index_responsibilities():
    migrate_from = [("gameplay", "0120_manor_occupied_region_unique")]
    migrate_to = [("gameplay", "0123_message_deletion_protection_index")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        migration_0121 = executor.loader.get_migration("gameplay", "0121_message_deletion_protection")
        migration_0122 = executor.loader.get_migration("gameplay", "0122_message_deletion_protection_backfill")
        migration_0123 = executor.loader.get_migration("gameplay", "0123_message_deletion_protection_index")
        assert [type(operation).__name__ for operation in migration_0121.operations] == ["AddField"]
        assert migration_0122.atomic is False
        assert [type(operation).__name__ for operation in migration_0122.operations] == ["RunPython"]
        assert [type(operation).__name__ for operation in migration_0123.operations] == ["AddIndex"]

        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        cases = _create_legacy_message_cases(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedMessage = new_apps.get_model("gameplay", "Message")

        actual = dict(MigratedMessage.objects.order_by("pk").values_list("title", "is_deletion_protected"))
        assert actual == {title: expected for title, (_attachments, _is_claimed, expected) in cases.items()}

        cleanup_index = next(
            index for index in MigratedMessage._meta.indexes if index.name == "message_protected_cleanup_idx"
        )
        assert cleanup_index.fields == ["is_deletion_protected", "created_at", "id"]
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, MigratedMessage._meta.db_table)
        assert constraints["message_protected_cleanup_idx"]["index"] is True
        assert constraints["message_protected_cleanup_idx"]["columns"] == [
            "is_deletion_protected",
            "created_at",
            "id",
        ]
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0122_backfill_is_batch_safe_and_rerunnable_after_partial_progress(monkeypatch):
    migrate_from = [("gameplay", "0120_manor_occupied_region_unique")]
    field_target = [("gameplay", "0121_message_deletion_protection")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        _create_legacy_message_cases(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(field_target)
        field_apps = executor.loader.project_state(field_target).apps
        Message = field_apps.get_model("gameplay", "Message")
        Message.objects.filter(title="resource protected").update(is_deletion_protected=True)
        Message.objects.filter(title__in=["claimed asset", "metadata only"]).update(is_deletion_protected=True)
        assert dict(Message.objects.values_list("title", "is_deletion_protected"))["item protected"] is False

        backfill_migration = importlib.import_module("gameplay.migrations.0122_message_deletion_protection_backfill")
        monkeypatch.setattr(backfill_migration, "BACKFILL_BATCH_SIZE", 1)
        atomic_aliases = []
        select_for_update_aliases = []
        original_select_for_update = QuerySet.select_for_update

        class TransactionProbe:
            @staticmethod
            def atomic(*, using=None):
                atomic_aliases.append(using)
                return transaction.atomic(using=using)

        def tracking_select_for_update(queryset, *args, **kwargs):
            if queryset.model._meta.label == "gameplay.Message":
                select_for_update_aliases.append(queryset.db)
            return original_select_for_update(queryset, *args, **kwargs)

        monkeypatch.setattr(backfill_migration, "transaction", TransactionProbe, raising=False)
        monkeypatch.setattr(QuerySet, "select_for_update", tracking_select_for_update)
        with connection.schema_editor() as schema_editor:
            backfill_migration.backfill_message_deletion_protection(field_apps, schema_editor)

        expected = {
            "resource protected": True,
            "item protected": True,
            "claimed asset": False,
            "metadata only": False,
            "empty buckets": False,
            "non dict buckets": False,
        }
        assert dict(Message.objects.order_by("pk").values_list("title", "is_deletion_protected")) == expected
        assert atomic_aliases
        assert set(atomic_aliases) == {connection.alias}
        assert len(select_for_update_aliases) >= len(expected)
        assert set(select_for_update_aliases) == {connection.alias}

        with connection.schema_editor() as schema_editor:
            backfill_migration.backfill_message_deletion_protection(field_apps, schema_editor)
        assert dict(Message.objects.order_by("pk").values_list("title", "is_deletion_protected")) == expected
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
