from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

EXPECTED_WORK_REQUIREMENTS = {
    "jiulou": (3, 0, 0, 0, 30),
    "yiguan": (5, 0, 45, 0, 0),
    "yizhan": (7, 0, 0, 0, 42),
    "shuyuan": (9, 0, 60, 0, 0),
    "chaguan": (11, 0, 70, 0, 0),
    "matou": (13, 80, 0, 0, 0),
    "wuguan": (14, 100, 0, 0, 0),
    "shanghang": (15, 0, 105, 0, 0),
    "guanfu": (16, 0, 105, 0, 60),
    "qianzhuang": (17, 0, 110, 55, 0),
    "biaoju": (18, 115, 0, 75, 0),
    "jingwumeng": (23, 140, 0, 0, 80),
    "shenfengyi": (28, 0, 0, 90, 100),
    "guozijian": (33, 0, 200, 0, 95),
}


@pytest.mark.django_db(transaction=True)
def test_work_attribute_migration_updates_only_official_templates():
    migrate_from = [("gameplay", "0131_split_personal_red_ruby_to_chunqiu_coin")]
    migrate_to = [("gameplay", "0132_worktemplate_attribute_requirements")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        LegacyWorkTemplate = old_apps.get_model("gameplay", "WorkTemplate")

        for key in EXPECTED_WORK_REQUIREMENTS:
            LegacyWorkTemplate.objects.create(
                key=key,
                name=key,
                required_level=1,
                required_force=1,
                required_intellect=1,
            )
        custom = LegacyWorkTemplate.objects.create(
            key="custom-work",
            name="自定义工作",
            required_level=88,
            required_force=777,
            required_intellect=666,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        MigratedWorkTemplate = new_apps.get_model("gameplay", "WorkTemplate")

        actual = {
            row.key: (
                row.required_level,
                row.required_force,
                row.required_intellect,
                row.required_defense,
                row.required_agility,
            )
            for row in MigratedWorkTemplate.objects.filter(key__in=EXPECTED_WORK_REQUIREMENTS)
        }
        assert actual == EXPECTED_WORK_REQUIREMENTS

        migrated_custom = MigratedWorkTemplate.objects.get(pk=custom.pk)
        assert migrated_custom.required_level == 88
        assert migrated_custom.required_force == 777
        assert migrated_custom.required_intellect == 666
        assert migrated_custom.required_defense == 0
        assert migrated_custom.required_agility == 0
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
