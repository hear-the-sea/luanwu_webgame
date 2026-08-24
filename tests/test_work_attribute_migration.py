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

UPDATED_SENIOR_WORK_REQUIREMENTS = {
    "biaoju": (45, 210, 0, 120, 0),
    "jingwumeng": (50, 230, 0, 0, 120),
    "shenfengyi": (45, 0, 0, 130, 180),
    "guozijian": (50, 0, 220, 0, 100),
}

UPDATED_INTERMEDIATE_WORK_REQUIREMENTS = {
    "wuguan": (20, 120, 0, 0, 0),
    "shanghang": (25, 0, 125, 0, 0),
    "guanfu": (20, 0, 110, 0, 70),
    "qianzhuang": (25, 0, 130, 100, 0),
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


@pytest.mark.django_db(transaction=True)
def test_senior_work_requirement_migration_updates_only_target_templates():
    migrate_from = [("gameplay", "0184_missiontemplate_display_order")]
    migrate_to = [("gameplay", "0185_update_senior_work_requirements")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        LegacyWorkTemplate = old_apps.get_model("gameplay", "WorkTemplate")

        for key in UPDATED_SENIOR_WORK_REQUIREMENTS:
            old_requirements = EXPECTED_WORK_REQUIREMENTS[key]
            LegacyWorkTemplate.objects.create(
                key=key,
                name=key,
                required_level=old_requirements[0],
                required_force=old_requirements[1],
                required_intellect=old_requirements[2],
                required_defense=old_requirements[3],
                required_agility=old_requirements[4],
            )
        untouched = LegacyWorkTemplate.objects.create(
            key="custom-senior-work",
            name="自定义高级工作",
            required_level=88,
            required_force=777,
            required_intellect=666,
            required_defense=555,
            required_agility=444,
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
            for row in MigratedWorkTemplate.objects.filter(key__in=UPDATED_SENIOR_WORK_REQUIREMENTS)
        }
        assert actual == UPDATED_SENIOR_WORK_REQUIREMENTS

        migrated_untouched = MigratedWorkTemplate.objects.get(pk=untouched.pk)
        assert (
            migrated_untouched.required_level,
            migrated_untouched.required_force,
            migrated_untouched.required_intellect,
            migrated_untouched.required_defense,
            migrated_untouched.required_agility,
        ) == (88, 777, 666, 555, 444)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_intermediate_work_requirement_migration_updates_only_target_templates():
    migrate_from = [("gameplay", "0185_update_senior_work_requirements")]
    migrate_to = [("gameplay", "0186_update_intermediate_work_requirements")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        LegacyWorkTemplate = old_apps.get_model("gameplay", "WorkTemplate")

        for key in UPDATED_INTERMEDIATE_WORK_REQUIREMENTS:
            old_requirements = EXPECTED_WORK_REQUIREMENTS[key]
            LegacyWorkTemplate.objects.create(
                key=key,
                name=key,
                required_level=old_requirements[0],
                required_force=old_requirements[1],
                required_intellect=old_requirements[2],
                required_defense=old_requirements[3],
                required_agility=old_requirements[4],
            )
        untouched = LegacyWorkTemplate.objects.create(
            key="custom-intermediate-work",
            name="自定义中级工作",
            required_level=77,
            required_force=666,
            required_intellect=555,
            required_defense=444,
            required_agility=333,
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
            for row in MigratedWorkTemplate.objects.filter(key__in=UPDATED_INTERMEDIATE_WORK_REQUIREMENTS)
        }
        assert actual == UPDATED_INTERMEDIATE_WORK_REQUIREMENTS

        migrated_untouched = MigratedWorkTemplate.objects.get(pk=untouched.pk)
        assert (
            migrated_untouched.required_level,
            migrated_untouched.required_force,
            migrated_untouched.required_intellect,
            migrated_untouched.required_defense,
            migrated_untouched.required_agility,
        ) == (77, 666, 555, 444, 333)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
