from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_migration_0108_removes_style_probe_task():
    migrate_from = [("gameplay", "0107_four_continents_regions")]
    migrate_to = [("gameplay", "0108_remove_style_probe_task_mission")]

    executor = MigrationExecutor(connection)
    executor.migrate(migrate_from)

    old_apps = executor.loader.project_state(migrate_from).apps
    MissionTemplate = old_apps.get_model("gameplay", "MissionTemplate")
    MissionTemplate.objects.create(
        key="style_probe_task",
        name="样式探针任务",
        description="样式探针",
        difficulty="junior",
        daily_limit=3,
    )

    executor = MigrationExecutor(connection)
    executor.migrate(migrate_to)

    new_apps = executor.loader.project_state(migrate_to).apps
    MissionTemplate = new_apps.get_model("gameplay", "MissionTemplate")

    assert MissionTemplate.objects.filter(key="style_probe_task").exists() is False
