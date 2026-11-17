"""有效驻守计入经济人口和分组活跃，唯一凭证与旧流水不重复统计。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import SeasonAction
from gameplay.services.season.playability_rules import playability_rules
from gameplay.services.season.population import refresh_population_locked
from gameplay.services.season.registration import _balance_profiles
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def resource_campaign(request):
    return request.getfixturevalue("_resource_campaign")


def test_completed_quiet_garrison_is_economic_and_registration_activity(resource_campaign):
    season, player, clock = resource_campaign
    member, guest = player("fiction")
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    run = issue(member, guest, clock, mode="garrison", node=site.key)
    advance_resource_run(run, clock)
    advance_resource_run(run, clock)
    advance_resource_run(run, clock)
    member.refresh_from_db()
    assert run.status == "completed" and run.report_id is None
    assert member.merit == 10
    assert not member.actions.filter(kind__in=("combat", "logistics", "exploration")).exists()
    with transaction.atomic():
        profile = refresh_population_locked(season, clock[0], force=True)
    assert profile["factions"][member.faction]["count"] == 1
    assert profile["factions"][member.faction]["economic_count"] > 0
    profiles = _balance_profiles([member], clock[0], playability_rules(season)["balance"])
    assert profiles[member.manor_id]["activity_days"] == 1


def action(member, clock, *, kind="merit_credit", days=0, result=None):
    return SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="participation-regression",
        kind=kind,
        date=timezone.localdate(clock[0]) - timedelta(days=days),
        result=result or {"merit": 0, "requested_merit": 0},
    )


def test_zero_value_receipts_and_non_effective_deployments_do_not_make_economic_population(resource_campaign):
    season, player, clock = resource_campaign
    member, _ = player()
    action(member, clock)
    action(member, clock, kind="combat")
    with transaction.atomic():
        profile = refresh_population_locked(season, clock[0], force=True)
    assert profile["factions"][member.faction]["count"] == 0
    assert profile["factions"][member.faction]["economic_count"] == 0
    profiles = _balance_profiles([member], clock[0], playability_rules(season)["balance"])
    assert profiles[member.manor_id]["activity_days"] == 0


def test_unique_receipts_and_unlinked_legacy_activity_count_without_mirrored_display_days(resource_campaign):
    season, player, clock = resource_campaign
    member, _ = player()
    credit = action(member, clock, result={"merit": 10, "requested_merit": 10})
    action(member, clock, kind="logistics", days=1, result={"merit": 10})
    action(member, clock, kind="combat", days=2, result={"merit": 10, "merit_credit_id": credit.pk})
    action(member, clock, days=3)
    expired = action(member, clock, days=4, result={"merit": 10})
    SeasonAction.objects.filter(pk=expired.pk).update(created_at=clock[0] - timedelta(days=8))
    with transaction.atomic():
        profile = refresh_population_locked(season, clock[0], force=True)
    assert profile["factions"][member.faction]["count"] == 1
    profiles = _balance_profiles([member], clock[0], playability_rules(season)["balance"])
    assert profiles[member.manor_id]["activity_days"] == 2
