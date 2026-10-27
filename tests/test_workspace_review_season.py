"""工作区复核：维修预留周期与异常重试后的古城抵达资格。"""

import pytest

from gameplay.models import SeasonExpedition
from gameplay.services.season import expeditions
from gameplay.services.season.recovery import next_repair_plan, repair_material_reserve
from tests.test_season_city_arrival_window import sunday_at
from tests.test_season_occupation_recovery import capture
from tests.test_season_occupation_recovery import occupation_world as _occupation_world_fixture  # noqa: F401
from tests.test_season_rallies import army_payload, command
from tests.test_season_rallies import rally_world as _rally_world_fixture  # noqa: F401
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("cycles", [1, 2, 4])
def test_repair_reserve_counts_configured_cycles_once(request, cycles):
    world = request.getfixturevalue("_occupation_world_fixture")
    season, site, _player, _clock = world
    capture(world)
    season.rules["campaign"]["occupation"]["repair_reserved_cycles"] = cycles
    season.save(update_fields=["rules"])
    site.supplies = {}
    site.save(update_fields=["supplies"])
    _wall, _tower, actual_cost = next_repair_plan(site, cycles=cycles)

    assert repair_material_reserve(season, "fiction") == actual_cost


def test_city_garrison_retry_preserves_legal_sunday_arrival(request, monkeypatch):
    season, player, clock = request.getfixturevalue("_rally_world_fixture")
    sunday_at(clock, 23, 50)
    monkeypatch.setattr(expeditions, "travel_time_seconds", lambda *args: 599)
    member, guests = player()
    site = season.sites.get(key="river:town")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    result = command(member, "dispatch", army_payload(guests, key=site.key, mode="garrison"))
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    clock[0] = run.due_at

    with monkeypatch.context() as patch:

        def temporarily_unavailable(*args, **kwargs):
            raise RuntimeError("temporary failure before arrival settlement")

        patch.setattr(expeditions, "process_expedition", temporarily_unavailable)
        assert expeditions.advance_due_expeditions(clock[0]) == {"processed": 0, "errors": 1}

    run.refresh_from_db()
    clock[0] = run.due_at
    assert expeditions.process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    assert run.status == "stationed"
