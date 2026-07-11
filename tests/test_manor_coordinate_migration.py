from __future__ import annotations

import pytest
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q

LEGACY_LOCATION_CONSTRAINT = "unique_manor_location"
OCCUPIED_LOCATION_CONSTRAINT = "unique_occupied_manor_location"


def _assert_legacy_location_state(Manor) -> None:
    with pytest.raises(FieldDoesNotExist):
        Manor._meta.get_field("occupied_region")

    relevant_constraints = {
        constraint.name
        for constraint in Manor._meta.constraints
        if constraint.name in {LEGACY_LOCATION_CONSTRAINT, OCCUPIED_LOCATION_CONSTRAINT}
    }
    assert relevant_constraints == {LEGACY_LOCATION_CONSTRAINT}
    legacy_constraint = next(
        constraint for constraint in Manor._meta.constraints if constraint.name == LEGACY_LOCATION_CONSTRAINT
    )
    assert legacy_constraint.fields == ("region", "coordinate_x", "coordinate_y")
    assert legacy_constraint.condition == Q(coordinate_x__gt=0, coordinate_y__gt=0)


def _assert_occupied_location_state(Manor) -> None:
    field = Manor._meta.get_field("occupied_region")
    assert field.generated is True
    assert field.db_persist is True

    relevant_constraints = {
        constraint.name
        for constraint in Manor._meta.constraints
        if constraint.name in {LEGACY_LOCATION_CONSTRAINT, OCCUPIED_LOCATION_CONSTRAINT}
    }
    assert relevant_constraints == {OCCUPIED_LOCATION_CONSTRAINT}
    occupied_constraint = next(
        constraint for constraint in Manor._meta.constraints if constraint.name == OCCUPIED_LOCATION_CONSTRAINT
    )
    assert occupied_constraint.fields == ("occupied_region", "coordinate_x", "coordinate_y")
    assert occupied_constraint.condition is None


def _assert_location_data(Manor, expected_locations) -> None:
    assert list(
        Manor.objects.filter(pk__in=expected_locations)
        .order_by("pk")
        .values_list("pk", "region", "coordinate_x", "coordinate_y")
    ) == [
        (pk, region, coordinate_x, coordinate_y)
        for pk, (region, coordinate_x, coordinate_y) in sorted(expected_locations.items())
    ]


def _assert_positive_location_is_unique(Manor, *, unassigned_pk: int) -> None:
    original_region, original_x, original_y = Manor.objects.values_list(
        "region",
        "coordinate_x",
        "coordinate_y",
    ).get(pk=unassigned_pk)
    assert (original_x, original_y) == (0, 0)

    try:
        with pytest.raises(IntegrityError), transaction.atomic():
            Manor.objects.filter(pk=unassigned_pk).update(
                region="north",
                coordinate_x=341,
                coordinate_y=651,
            )
    finally:
        restored_rows = Manor.objects.filter(pk=unassigned_pk).update(
            region=original_region,
            coordinate_x=0,
            coordinate_y=0,
        )
        assert restored_rows == 1

    assert Manor.objects.filter(
        pk=unassigned_pk,
        region=original_region,
        coordinate_x=0,
        coordinate_y=0,
    ).exists()


def _location_constraint_names(Manor) -> set[str]:
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, Manor._meta.db_table)
    return {name for name in constraints if name in {LEGACY_LOCATION_CONSTRAINT, OCCUPIED_LOCATION_CONSTRAINT}}


def _restore_legacy_location_constraint_if_needed(
    Manor,
    legacy_constraint,
    *,
    removal_attempted: bool,
) -> None:
    if not removal_attempted or _location_constraint_names(Manor):
        return
    with connection.schema_editor() as schema_editor:
        schema_editor.add_constraint(Manor, legacy_constraint)


@pytest.mark.django_db(transaction=True)
def test_positive_location_probe_restores_unassigned_row_when_constraint_is_missing():
    migrate_0119 = [("gameplay", "0119_botprofile_band_semantics")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    Manor = None
    legacy_constraint = None
    legacy_removal_attempted = False
    unassigned_pk = None

    try:
        executor.migrate(migrate_0119)
        apps_0119 = executor.loader.project_state(migrate_0119).apps
        User = apps_0119.get_model("accounts", "User")
        Manor = apps_0119.get_model("gameplay", "Manor")
        legacy_constraint = next(
            constraint for constraint in Manor._meta.constraints if constraint.name == LEGACY_LOCATION_CONSTRAINT
        )
        legacy_removal_attempted = True
        with connection.schema_editor() as schema_editor:
            schema_editor.remove_constraint(Manor, legacy_constraint)

        occupied_user = User.objects.create(
            username="coordinate_probe_occupied",
            email="coordinate-probe-occupied@test.local",
            password="unused",
        )
        unassigned_user = User.objects.create(
            username="coordinate_probe_unassigned",
            email="coordinate-probe-unassigned@test.local",
            password="unused",
        )
        Manor.objects.create(
            user_id=occupied_user.pk,
            region="north",
            coordinate_x=341,
            coordinate_y=651,
        )
        unassigned = Manor.objects.create(
            user_id=unassigned_user.pk,
            region="west",
            coordinate_x=0,
            coordinate_y=0,
        )
        unassigned_pk = unassigned.pk

        with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
            _assert_positive_location_is_unique(Manor, unassigned_pk=unassigned_pk)

        assert Manor.objects.filter(
            pk=unassigned_pk,
            region="west",
            coordinate_x=0,
            coordinate_y=0,
        ).exists()
    finally:
        try:
            try:
                if Manor is not None and unassigned_pk is not None:
                    Manor.objects.filter(pk=unassigned_pk).update(
                        region="west",
                        coordinate_x=0,
                        coordinate_y=0,
                    )
            finally:
                if Manor is not None and legacy_constraint is not None:
                    _restore_legacy_location_constraint_if_needed(
                        Manor,
                        legacy_constraint,
                        removal_attempted=legacy_removal_attempted,
                    )
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0120_rejects_duplicate_assigned_locations_without_moving_manors():
    migrate_from = [("gameplay", "0119_botprofile_band_semantics")]
    migrate_to = [("gameplay", "0120_manor_occupied_region_unique")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    legacy_constraint = None
    legacy_removal_attempted = False
    injected_second_pk = None
    Manor = None

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Manor = old_apps.get_model("gameplay", "Manor")
        legacy_constraint = next(
            constraint for constraint in Manor._meta.constraints if constraint.name == LEGACY_LOCATION_CONSTRAINT
        )
        legacy_removal_attempted = True
        with connection.schema_editor() as schema_editor:
            schema_editor.remove_constraint(Manor, legacy_constraint)

        first_user = User.objects.create(
            username="coordinate_migration_first",
            email="coordinate-migration-first@test.local",
            password="unused",
        )
        second_user = User.objects.create(
            username="coordinate_migration_second",
            email="coordinate-migration-second@test.local",
            password="unused",
        )
        first = Manor.objects.create(
            user_id=first_user.pk,
            region="north",
            coordinate_x=321,
            coordinate_y=654,
        )
        second = Manor.objects.create(
            user_id=second_user.pk,
            region="north",
            coordinate_x=321,
            coordinate_y=654,
        )
        injected_second_pk = second.pk

        executor = MigrationExecutor(connection)
        with pytest.raises(RuntimeError) as exc_info:
            executor.migrate(migrate_to)

        error_message = str(exc_info.value)
        assert "duplicate assigned manor locations" in error_message
        assert str(first.pk) in error_message
        assert str(second.pk) in error_message
        assert list(
            Manor.objects.filter(pk__in=[first.pk, second.pk])
            .order_by("pk")
            .values_list("region", "coordinate_x", "coordinate_y")
        ) == [("north", 321, 654), ("north", 321, 654)]
    finally:
        try:
            try:
                if Manor is not None and injected_second_pk is not None:
                    Manor.objects.filter(pk=injected_second_pk).delete()
            finally:
                if Manor is not None and legacy_constraint is not None:
                    _restore_legacy_location_constraint_if_needed(
                        Manor,
                        legacy_constraint,
                        removal_attempted=legacy_removal_attempted,
                    )
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)


@pytest.mark.django_db(transaction=True)
def test_migration_0120_round_trip_preserves_location_semantics_and_data():
    migrate_0119 = [("gameplay", "0119_botprofile_band_semantics")]
    migrate_0120 = [("gameplay", "0120_manor_occupied_region_unique")]
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()

    try:
        executor.migrate(migrate_0119)
        apps_0119 = executor.loader.project_state(migrate_0119).apps
        User0119 = apps_0119.get_model("accounts", "User")
        Manor0119 = apps_0119.get_model("gameplay", "Manor")
        _assert_legacy_location_state(Manor0119)

        users = [
            User0119.objects.create(
                username=f"coordinate_round_trip_{suffix}",
                email=f"coordinate-round-trip-{suffix}@test.local",
                password="unused",
            )
            for suffix in ("zero_first", "zero_second", "occupied")
        ]
        zero_first = Manor0119.objects.create(
            user_id=users[0].pk,
            region="north",
            coordinate_x=0,
            coordinate_y=0,
        )
        zero_second = Manor0119.objects.create(
            user_id=users[1].pk,
            region="north",
            coordinate_x=0,
            coordinate_y=0,
        )
        occupied = Manor0119.objects.create(
            user_id=users[2].pk,
            region="north",
            coordinate_x=341,
            coordinate_y=651,
        )
        expected_locations = {
            zero_first.pk: ("north", 0, 0),
            zero_second.pk: ("north", 0, 0),
            occupied.pk: ("north", 341, 651),
        }
        _assert_location_data(Manor0119, expected_locations)

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_0120)
        apps_0120 = executor.loader.project_state(migrate_0120).apps
        Manor0120 = apps_0120.get_model("gameplay", "Manor")
        _assert_occupied_location_state(Manor0120)
        _assert_location_data(Manor0120, expected_locations)
        assert list(
            Manor0120.objects.filter(pk__in=expected_locations).order_by("pk").values_list("occupied_region", flat=True)
        ) == [None, None, "north"]
        _assert_positive_location_is_unique(Manor0120, unassigned_pk=zero_second.pk)

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_0119)
        rolled_back_apps = executor.loader.project_state(migrate_0119).apps
        RolledBackManor = rolled_back_apps.get_model("gameplay", "Manor")
        _assert_legacy_location_state(RolledBackManor)
        _assert_location_data(RolledBackManor, expected_locations)
        _assert_positive_location_is_unique(RolledBackManor, unassigned_pk=zero_second.pk)

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_0120)
        reapplied_apps = executor.loader.project_state(migrate_0120).apps
        ReappliedManor = reapplied_apps.get_model("gameplay", "Manor")
        _assert_occupied_location_state(ReappliedManor)
        _assert_location_data(ReappliedManor, expected_locations)
        assert list(
            ReappliedManor.objects.filter(pk__in=expected_locations)
            .order_by("pk")
            .values_list("occupied_region", flat=True)
        ) == [None, None, "north"]
        _assert_positive_location_is_unique(ReappliedManor, unassigned_pk=zero_second.pk)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
