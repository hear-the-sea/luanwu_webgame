"""站内战报覆盖实际攻守参与者、NPC、波次、防重与事务回滚。"""

import pytest
from django.db import transaction
from django.test import Client
from django.urls import reverse

from gameplay.models import Message, SeasonExpedition
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.report_mail import send_report_mail_locked
from tests.test_season_expeditions import arrive, dispatch
from tests.test_season_expeditions import war as _war  # noqa: F401
from tests.test_season_rallies import army_payload, command, create_rally, join_rally, launch
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def war(request):
    return request.getfixturevalue("_war")


@pytest.fixture
def rally_world(request):
    return request.getfixturevalue("_rally_world")


def test_pve_sends_attack_mail_with_report_once_and_keeps_access(war):
    _, player, now = war
    member, guest = player()
    run = dispatch(member, guest)
    arrive(run)
    mail = Message.objects.get(manor=member.manor, battle_report=run.report)
    assert mail.title == "阵营任务-山脚驿站进攻战报"
    assert mail.kind == "battle" and not mail.is_read
    assert "本场贡献：+" in mail.body and "本场身份：进攻" in mail.body
    with transaction.atomic():
        send_report_mail_locked(run, run.report, now, 99)
    assert Message.objects.filter(manor=member.manor, battle_report=run.report).count() == 1
    client = Client()
    client.force_login(member.manor.user)
    assert client.get(reverse("battle:report_detail", args=[run.report_id])).status_code == 200


def test_group_wave_mails_every_actual_player_but_not_reserve_and_new_wave_is_new_mail(rally_world):
    season, player, clock = rally_world
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.save(update_fields=["owner"])
    defenders = []
    for _ in range(3):
        member, guests = player("history", count=1, force=1, hp=1)
        response = command(member, "dispatch", army_payload(guests, mode="garrison"))
        run = SeasonExpedition.objects.get(pk=response["run_id"])
        assert process_expedition(run.pk, run.due_at)
        defenders.append((member, run))
    first, guests = player(count=1, force=8000)
    second, others = player(count=1, force=8000)
    rally, run = create_rally(first, guests)
    teammate = join_rally(second, others, rally)
    launch(rally, run)
    clock[0] = rally.due_at
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    report = run.report
    mails = Message.objects.filter(battle_report=report)
    assert mails.count() == 4
    assert set(mails.filter(title="阵营任务-青石关进攻战报").values_list("manor_id", flat=True)) == {
        first.manor_id,
        second.manor_id,
    }
    assert set(mails.filter(title="阵营任务-青石关防守战报").values_list("manor_id", flat=True)) == {
        row[0].manor_id for row in defenders[:2]
    }
    assert not Message.objects.filter(manor=defenders[2][0].manor).exists()
    client = Client()
    client.force_login(defenders[0][0].manor.user)
    assert client.get(reverse("battle:report_detail", args=[report.pk])).status_code == 200
    assert not process_expedition(teammate.pk, clock[0])
    assert Message.objects.filter(battle_report=report).count() == 4
    rally.refresh_from_db()
    assert process_expedition(run.pk, rally.due_at)
    run.refresh_from_db()
    assert run.report_id != report.pk
    assert Message.objects.filter(battle_report=run.report).count() == 3


def test_mail_failure_rolls_back_battle_and_retry_sends_only_once(rally_world, monkeypatch):
    from gameplay.services.season import rallies, report_mail

    _, player, clock = rally_world
    member, guests = player(force=8000)
    rally, run = create_rally(member, guests)
    launch(rally, run)
    at = rally.due_at
    original = report_mail.create_message

    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("模拟邮件结算异常")

    with monkeypatch.context() as patch:
        patch.setattr(report_mail, "create_message", broken)
        assert rallies.advance_due_rallies(at)["rally_errors"] == 1
    assert not Message.objects.exists()
    assert not member.claims.filter(key__startswith="battle_mail:").exists()
    run.refresh_from_db()
    assert run.report_id is None
    rally.refresh_from_db()
    assert process_expedition(run.pk, rally.due_at)
    run.refresh_from_db()
    assert Message.objects.filter(manor=member.manor, battle_report=run.report).count() == 1


def test_season_page_removes_journal_and_links_to_mail(war):
    season, player, _ = war
    member, _ = player()
    client = Client()
    client.force_login(member.manor.user)
    response = client.get(reverse("gameplay:season"), {"season_id": season.pk})
    text = response.content.decode()
    assert response.status_code == 200
    assert "season-view-reports" not in text and "行旅与战报" not in text
    assert f'href="{reverse("gameplay:messages")}"' in text
    assert "邮件战报" in text
