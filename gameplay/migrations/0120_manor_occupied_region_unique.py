from __future__ import annotations

from django.db import migrations, models
from django.db.models import Count


def assert_assigned_locations_are_unique(apps, schema_editor) -> None:
    Manor = apps.get_model("gameplay", "Manor")
    database_alias = schema_editor.connection.alias
    duplicates = list(
        Manor.objects.using(database_alias)
        .filter(coordinate_x__gt=0, coordinate_y__gt=0)
        .values("region", "coordinate_x", "coordinate_y")
        .annotate(location_count=Count("id"))
        .filter(location_count__gt=1)
        .order_by("region", "coordinate_x", "coordinate_y")
    )
    if not duplicates:
        return

    conflicts: list[str] = []
    for duplicate in duplicates:
        manor_ids = list(
            Manor.objects.using(database_alias)
            .filter(
                region=duplicate["region"],
                coordinate_x=duplicate["coordinate_x"],
                coordinate_y=duplicate["coordinate_y"],
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        conflicts.append(f"{duplicate['region']}:{duplicate['coordinate_x']},{duplicate['coordinate_y']}={manor_ids}")

    raise RuntimeError("duplicate assigned manor locations must be resolved before migration: " + "; ".join(conflicts))


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0119_botprofile_band_semantics"),
    ]

    operations = [
        migrations.RunPython(assert_assigned_locations_are_unique, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="manor",
            name="unique_manor_location",
        ),
        migrations.AddField(
            model_name="manor",
            name="occupied_region",
            field=models.GeneratedField(
                blank=True,
                db_persist=True,
                expression=models.Case(
                    models.When(
                        coordinate_x__gt=0,
                        coordinate_y__gt=0,
                        then=models.F("region"),
                    ),
                    default=models.Value(None),
                ),
                output_field=models.CharField(max_length=32, null=True),
                verbose_name="已占用坐标地区",
            ),
        ),
        migrations.AddConstraint(
            model_name="manor",
            constraint=models.UniqueConstraint(
                fields=("occupied_region", "coordinate_x", "coordinate_y"),
                name="unique_occupied_manor_location",
            ),
        ),
    ]
