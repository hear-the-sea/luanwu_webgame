from datetime import timedelta

import pytest
from django.db import OperationalError, connection
from django.db.models import CharField, Exists, OuterRef
from django.db.models.functions import Cast
from django.utils import timezone

from gameplay.models import BotMaintenanceRecovery, BotPopulationRecomputeDemand
from gameplay.services.virtual_player_core.population_runtime import claim_next_population_recompute_demand
from gameplay.services.virtual_player_core.recovery import exclude_blocked_entity_recoveries


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_population_claim_with_mismatched_mysql_recovery_collation() -> None:
    if connection.vendor != "mysql" or connection.mysql_is_mariadb:
        pytest.skip("requires MySQL 8 collations")

    now = timezone.now()
    blocked = BotPopulationRecomputeDemand.objects.create(
        region="north", prestige_band="newbie", requested_revision=1, available_at=now
    )
    ready = BotPopulationRecomputeDemand.objects.create(
        region="south", prestige_band="newbie", requested_revision=1, available_at=now
    )
    BotMaintenanceRecovery.objects.create(
        scope="population_cell",
        entity_key=str(blocked.pk),
        status=BotMaintenanceRecovery.Status.QUARANTINED,
        failure_code="programmer_error",
        failure_digest="a" * 64,
        first_failed_at=now,
        last_failed_at=now,
        quarantined_at=now,
    )

    with connection.cursor() as cursor:
        cursor.execute("SELECT @@SESSION.collation_connection")
        original_collation = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            [BotMaintenanceRecovery._meta.db_table, "entity_key"],
        )
        column_collation = cursor.fetchone()[0]
        mismatched_collation = (
            "utf8mb4_0900_ai_ci" if column_collation == "utf8mb4_unicode_ci" else "utf8mb4_unicode_ci"
        )
        cursor.execute("SET SESSION collation_connection = %s", [mismatched_collation])

    try:
        # Establish that this connection reproduces the production failure.
        legacy_blocked = BotMaintenanceRecovery.objects.filter(
            scope="population_cell", entity_key=Cast(OuterRef("pk"), output_field=CharField())
        )
        with pytest.raises(OperationalError) as error:
            BotPopulationRecomputeDemand.objects.filter(Exists(legacy_blocked)).count()
        assert error.value.args[0] == 1267

        eligible = exclude_blocked_entity_recoveries(
            BotPopulationRecomputeDemand.objects.all(), scope="population_cell", now=now
        )
        assert eligible.count() == 1
        assert list(eligible.values_list("pk", flat=True)) == [ready.pk]
        claim = claim_next_population_recompute_demand(now=now + timedelta(seconds=1))
        assert claim is not None
        assert claim.demand_id == ready.pk
        blocked.refresh_from_db()
        assert blocked.claimed_revision is None
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION collation_connection = %s", [original_collation])
