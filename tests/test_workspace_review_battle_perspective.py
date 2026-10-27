"""工作区审查：联合进攻成员的战报身份须沿用结算流水。"""

from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from battle.models import BattleReport
from battle.view_helpers import resolve_report_runtime_context
from gameplay.models import SeasonAction
from gameplay.services.season.expeditions import process_expedition
from tests.test_season_rallies import create_rally, join_rally, launch
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def rally_world(request):
    return request.getfixturevalue("_rally_world")


def test_coalition_attacker_teammate_keeps_attack_perspective(rally_world):
    _season, player, clock = rally_world
    leader, guests = player(count=1, force=9000)
    teammate, own_guests = player(count=1, force=8000)
    rally, run = create_rally(leader, guests)
    join_rally(teammate, own_guests, rally)
    launch(rally, run)
    clock[0] = rally.due_at
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    report = run.report
    assert report.manor_id == leader.manor_id
    action = SeasonAction.objects.get(report=report, member=teammate, kind="combat")
    assert action.result["combat_side"] == "attacker"

    assert resolve_report_runtime_context(report, manor_id=teammate.manor_id)["player_side"] == "attacker"
    client = Client()
    client.force_login(teammate.manor.user)
    response = client.get(reverse("battle:report_detail", args=[report.pk]))
    assert response.status_code == 200
    assert response.context["player_side"] == "attacker"

    own_guests[0].delete()
    response = client.get(reverse("battle:report_detail", args=[report.pk]))
    assert response.status_code == 200
    assert response.context["player_side"] == "attacker"


@pytest.mark.parametrize(
    ("saved_side", "expected_side"),
    [
        ("attacker", "attacker"),
        ("defender", "defender"),
        (None, "defender"),
        ("spectator", "defender"),
        (["attacker"], "defender"),
        ({"side": "attacker"}, "defender"),
    ],
)
def test_season_report_saved_identity_and_legacy_fallback_preserve_permissions(rally_world, saved_side, expected_side):
    _season, player, clock = rally_world
    leader, _guests = player()
    participant, _own_guests = player()
    observer, _spare_guests = player()
    report = BattleReport.objects.create(
        manor=leader.manor, battle_type="season", starts_at=clock[0], completed_at=clock[0]
    )
    SeasonAction.objects.create(
        member=participant,
        token=uuid4(),
        request_hash="settled-expedition",
        kind="combat",
        date=clock[0].date(),
        report=report,
        result={"combat_side": saved_side} if saved_side else {},
    )

    assert resolve_report_runtime_context(report, manor_id=leader.manor_id)["player_side"] == "attacker"
    assert resolve_report_runtime_context(report, manor_id=participant.manor_id)["player_side"] == expected_side
    client = Client()
    client.force_login(participant.manor.user)
    response = client.get(reverse("battle:report_detail", args=[report.pk]))
    assert response.status_code == 200
    assert response.context["player_side"] == expected_side
    client.force_login(observer.manor.user)
    assert client.get(reverse("battle:report_detail", args=[report.pk])).status_code == 404
