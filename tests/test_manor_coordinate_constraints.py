from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from gameplay.models import Manor


def _create_user(username: str):
    return get_user_model().objects.create_user(username=username, password="pass123")


def test_manor_location_constraint_is_portable_to_mysql():
    field = Manor._meta.get_field("occupied_region")

    assert field.generated is True
    assert field.db_persist is True

    constraint = next(
        constraint for constraint in Manor._meta.constraints if constraint.name == "unique_occupied_manor_location"
    )
    assert constraint.condition is None
    assert constraint.fields == ("occupied_region", "coordinate_x", "coordinate_y")


@pytest.mark.django_db
def test_manor_location_constraint_allows_multiple_unassigned_manors():
    first = _create_user("coordinate_unassigned_first").manor
    second = _create_user("coordinate_unassigned_second").manor

    Manor.objects.filter(pk__in=[first.pk, second.pk]).update(
        region="overseas",
        coordinate_x=0,
        coordinate_y=0,
    )
    first.refresh_from_db(fields=["coordinate_x", "coordinate_y"])
    second.refresh_from_db(fields=["coordinate_x", "coordinate_y"])

    assert (first.coordinate_x, first.coordinate_y) == (0, 0)
    assert (second.coordinate_x, second.coordinate_y) == (0, 0)


@pytest.mark.django_db
def test_manor_location_constraint_rejects_duplicate_assigned_location():
    first = _create_user("coordinate_assigned_first").manor
    second = _create_user("coordinate_assigned_second").manor

    first.region = "north"
    first.coordinate_x = 123
    first.coordinate_y = 456
    first.save(update_fields=["region", "coordinate_x", "coordinate_y"])

    second.region = "north"
    second.coordinate_x = 123
    second.coordinate_y = 456
    with pytest.raises(IntegrityError), transaction.atomic():
        second.save(update_fields=["region", "coordinate_x", "coordinate_y"])
