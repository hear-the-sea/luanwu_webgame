from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_migration_0108_removes_style_probe_task():
    migrate_from = [("gameplay", "0107_four_continents_regions")]
    migrate_to = [("gameplay", "0108_remove_style_probe_task_mission")]

    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
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
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0187_sets_restricted_mission_card_limits():
    migrate_from = [("gameplay", "0186_update_intermediate_work_requirements")]
    migrate_to = [("gameplay", "0187_missiontemplate_mission_card_daily_limit")]

    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)

        old_apps = executor.loader.project_state(migrate_from).apps
        MissionTemplate = old_apps.get_model("gameplay", "MissionTemplate")
        MissionTemplate.objects.create(key="jiguanshou_chuxian", name="机关兽初现")
        MissionTemplate.objects.create(key="taozi_fenban", name="我的桃子分你一半")
        MissionTemplate.objects.create(key="ordinary_mission", name="普通任务")

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        MissionTemplate = new_apps.get_model("gameplay", "MissionTemplate")
        assert MissionTemplate.objects.get(key="jiguanshou_chuxian").mission_card_daily_limit == 1
        assert MissionTemplate.objects.get(key="taozi_fenban").mission_card_daily_limit == 0
        assert MissionTemplate.objects.get(key="ordinary_mission").mission_card_daily_limit == 5
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
