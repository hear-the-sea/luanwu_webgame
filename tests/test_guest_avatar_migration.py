import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_guest_avatar_migration_preserves_existing_guest_and_template():
    previous = [("guests", "0074_guest_super_disguise")]
    current = [("guests", "0075_guest_avatar_asset_key")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        user = apps.get_model("accounts", "User").objects.create(username="avatar_migration_owner")
        manor = apps.get_model("gameplay", "Manor").objects.create(user_id=user.pk)
        template = apps.get_model("guests", "GuestTemplate").objects.create(
            key="avatar_migration_legacy", name="旧门客", avatar="guests/legacy.webp"
        )
        guest = apps.get_model("guests", "Guest").objects.create(
            manor_id=manor.pk, template_id=template.pk, custom_name="保留门客", force=123
        )

        executor = MigrationExecutor(connection)
        executor.migrate(current)
        upgraded = executor.loader.project_state(current).apps.get_model("guests", "Guest")
        saved = upgraded.objects.select_related("template").get(pk=guest.pk)
        assert saved.avatar_asset_key == ""
        assert saved.custom_name == "保留门客"
        assert saved.force == 123
        assert saved.template.avatar.name == "guests/legacy.webp"
        upgraded.objects.filter(pk=guest.pk).update(avatar_asset_key="guests/new.webp")

        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        reverted = executor.loader.project_state(previous).apps.get_model("guests", "Guest")
        assert reverted.objects.get(pk=guest.pk).force == 123

        executor = MigrationExecutor(connection)
        executor.migrate(current)
        restored = executor.loader.project_state(current).apps.get_model("guests", "Guest")
        assert restored.objects.get(pk=guest.pk).avatar_asset_key == ""
    finally:
        MigrationExecutor(connection).migrate(latest)
