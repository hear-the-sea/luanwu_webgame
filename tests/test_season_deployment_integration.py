"""真实赛季派遣在其他门客入口中的占用边界。"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from battle.deployment import collect_active_deployment_guest_ids, find_orphaned_deployed_guest_ids
from battle.services import lock_guests_for_battle, recover_orphaned_deployed_guests
from core.exceptions import BattlePreparationError, GuestNotIdleError
from gameplay.models import Manor, Season, SeasonAction, SeasonExpedition, SeasonMember
from guests.models import Guest, GuestDefection, GuestStatus, GuestTemplate
from guests.services.health import heal_guest
from guests.services.roster import dismiss_guest
from guests.tasks import _process_defection_batch

pytestmark = pytest.mark.django_db


@pytest.fixture
def deployed_campaign_guest(user_factory):
    user = user_factory()
    manor, _created = Manor.objects.get_or_create(user=user)
    template = GuestTemplate.objects.create(
        key="season_deployment_boundary", name="守关门客", rarity="green", archetype="military"
    )
    guest = Guest.objects.create(manor=manor, template=template, status=GuestStatus.DEPLOYED, current_hp=100, loyalty=8)
    now = timezone.now()
    season = Season.objects.create(name="派遣占用测试", starts_at=now, ends_at=now + timedelta(days=90))
    member = SeasonMember.objects.create(season=season, manor=manor, faction="fiction")
    expedition = SeasonExpedition.objects.create(
        member=member,
        node_key="mountain:gate",
        kind="garrison",
        status="outbound",
        guest_ids=[guest.pk],
        launched_at=now,
        due_at=now + timedelta(minutes=10),
    )
    expedition.guests.add(guest)
    return manor, guest, expedition


@pytest.mark.parametrize("status", ["outbound", "stationed", "transporting", "returning", "failed"])
def test_unreturned_campaign_guests_are_never_recovered_as_orphans(deployed_campaign_guest, status):
    _manor, guest, expedition = deployed_campaign_guest
    expedition.status = status
    expedition.save(update_fields=["status"])

    assert collect_active_deployment_guest_ids([guest.pk]) == {guest.pk}
    assert find_orphaned_deployed_guest_ids([guest.pk]) == []
    assert recover_orphaned_deployed_guests(guest_ids=[guest.pk]) == 0
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED


def test_completed_campaign_record_does_not_prevent_orphan_recovery(deployed_campaign_guest):
    _manor, guest, expedition = deployed_campaign_guest
    expedition.status = "completed"
    expedition.save(update_fields=["status"])

    assert collect_active_deployment_guest_ids([guest.pk]) == set()
    assert recover_orphaned_deployed_guests(guest_ids=[guest.pk]) == 1
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


def test_campaign_deployment_cannot_be_reused_by_generic_battle(deployed_campaign_guest):
    manor, guest, _expedition = deployed_campaign_guest
    with pytest.raises(BattlePreparationError):
        with lock_guests_for_battle([guest], manor=manor):
            pytest.fail("赛季出征门客不应进入其他战斗")
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED


def test_campaign_guest_cannot_be_dismissed_or_healed_while_deployed(deployed_campaign_guest):
    _manor, guest, _expedition = deployed_campaign_guest
    with pytest.raises(GuestNotIdleError):
        dismiss_guest(guest)
    with pytest.raises(GuestNotIdleError):
        heal_guest(guest, 100)
    assert Guest.objects.filter(pk=guest.pk).exists()


@pytest.mark.parametrize("status", ["outbound", "returning", "failed"])
def test_campaign_guest_defection_waits_until_return(deployed_campaign_guest, status):
    _manor, guest, expedition = deployed_campaign_guest
    expedition.status = status
    expedition.save(update_fields=["status"])
    messages = []

    assert _process_defection_batch([guest.pk], create_message=lambda **message: messages.append(message)) == 0
    assert Guest.objects.filter(pk=guest.pk).exists()
    assert not GuestDefection.objects.filter(guest_id=guest.pk).exists()
    assert messages == []

    expedition.status = "completed"
    expedition.save(update_fields=["status"])
    assert _process_defection_batch([guest.pk], create_message=lambda **message: messages.append(message)) == 1
    assert not Guest.objects.filter(pk=guest.pk).exists()
    assert GuestDefection.objects.filter(guest_id=guest.pk).count() == 1
    assert len(messages) == 1


def test_roster_projection_keeps_campaign_guest_busy_without_writing(deployed_campaign_guest, monkeypatch):
    from guests.views import read_helpers

    manor, guest, _expedition = deployed_campaign_guest
    # 旧数据中状态同步延迟时，读取只修正展示，不应写回数据库。
    Guest.objects.filter(pk=guest.pk).update(status=GuestStatus.IDLE)
    guest.refresh_from_db()
    monkeypatch.setattr(read_helpers, "get_prepared_manor_for_read", lambda *_args, **_kwargs: manor)
    request = RequestFactory().get("/guests/")

    _manor, guests = read_helpers.get_prepared_guest_roster_for_read(
        request, logger=None, source="season-test", available_guests_fn=lambda _manor: [guest]
    )

    assert guests[0].status == GuestStatus.DEPLOYED
    assert Guest.objects.get(pk=guest.pk).status == GuestStatus.IDLE


def test_defender_keeps_access_to_old_campaign_reports_but_observer_does_not(deployed_campaign_guest, user_factory):
    from tests.battle_report_view.support import create_report

    defender_manor, _guest, expedition = deployed_campaign_guest
    attacker_user = user_factory()
    attacker_manor, _ = Manor.objects.get_or_create(user=attacker_user)
    observer_user = user_factory()
    Manor.objects.get_or_create(user=observer_user)
    now = timezone.now()
    reports = []
    for _ in range(2):
        report = create_report(
            manor=attacker_manor,
            opponent_name="防守阵营",
            battle_type="season",
            attacker_team=[{"name": "来敌", "guest_id": None, "template_key": "attacker"}],
            defender_team=[{"name": "驻军", "guest_id": None, "template_key": "defender"}],
        )
        SeasonAction.objects.create(
            member=expedition.member,
            token=uuid4(),
            request_hash="defense-settlement",
            kind="combat",
            date=timezone.localdate(now),
            report=report,
            result={"run_id": expedition.pk},
        )
        reports.append(report)
    expedition.report = reports[-1]
    expedition.status = "completed"
    expedition.save(update_fields=["report", "status"])

    defender = Client()
    defender.force_login(defender_manor.user)
    attacker = Client()
    attacker.force_login(attacker_user)
    observer = Client()
    observer.force_login(observer_user)
    for report in reports:
        url = reverse("battle:report_detail", kwargs={"pk": report.pk})
        response = defender.get(url)
        assert response.status_code == 200
        assert response.context["player_side"] == "defender"
        assert attacker.get(url).status_code == 200
        assert observer.get(url).status_code == 404


def test_campaign_combat_preserves_frozen_devices_hp_and_both_sides(deployed_campaign_guest):
    from battle.models import TroopTemplate
    from gameplay.models import ItemTemplate
    from gameplay.services.battle_snapshots import build_guest_battle_snapshots
    from gameplay.services.season.combat import prepare_combat
    from guests.models import GearItem, GearTemplate

    manor, guest, _expedition = deployed_campaign_guest
    TroopTemplate.objects.update_or_create(
        key="dao_ke", defaults={"name": "刀客", "base_attack": 6, "base_defense": 3, "base_hp": 18}
    )
    device = ItemTemplate.objects.create(
        key="campaign_frozen_device",
        name="赛季器械",
        effect_type="equip_device",
        effect_payload={"troop_stat_bonus": {"dao": {"attack_flat": 7}}},
    )
    gear_template = GearTemplate.objects.create(key=device.key, name=device.name, slot="device")
    gear = GearItem.objects.create(manor=manor, guest=guest, template=gear_template)
    lineup = {"guests": build_guest_battle_snapshots([guest]), "troops": {"dao_ke": 1}, "technology": {}}
    saved_hp = lineup["guests"][0]["current_hp"]
    # 快照后卸装、改装备效果和生命，不应改变已出发队伍的模拟配置。
    gear.guest = None
    gear.save(update_fields=["guest"])
    device.effect_payload = {"troop_stat_bonus": {"dao": {"attack_flat": 99}}}
    device.save(update_fields=["effect_payload"])
    Guest.objects.filter(pk=guest.pk).update(current_hp=1)

    prepared = prepare_combat(manor, lineup, lineup, "驻军", 10, preserve_state=True)["prepared"]
    assert prepared.attacker_guests[0].hp == prepared.defender_guests[0].hp == saved_hp
    assert prepared.attacker_guests[0].guest_id == guest.pk
    assert prepared.attacker_troops[0].unit_attack == prepared.defender_troops[0].unit_attack == 13
    assert prepared.attacker_equipment[0]["template_key"] == device.key
    assert prepared.defender_equipment[0]["template_key"] == device.key
    guest.refresh_from_db()
    assert guest.current_hp == 1
    assert guest.status == GuestStatus.DEPLOYED


def test_campaign_legacy_snapshot_keeps_hp_identity_and_capacity(deployed_campaign_guest):
    from gameplay.services.arena.snapshots import build_entry_guest_snapshot
    from gameplay.services.season.combat import proxies

    _manor, guest, _expedition = deployed_campaign_guest
    snapshot = build_entry_guest_snapshot(guest)
    snapshot.update(guest_id=guest.pk, current_hp=7, troop_capacity=123)
    proxy = proxies({"guests": [snapshot]}, preserve_state=True)[0]
    assert proxy.id == guest.pk
    assert proxy.current_hp == 7
    assert proxy.troop_capacity == 123
