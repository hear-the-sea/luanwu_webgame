from datetime import timedelta
from uuid import uuid4

import pytest
from django.templatetags.static import static
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport, TroopTemplate
from gameplay.models import ArenaRing, ArenaRingInspection, ArenaRingMatch, ArenaRingTenure, PlayerTroop
from gameplay.services.arena.ring import occupy_ring
from tests.arena_services.support import create_guest, create_guest_template

pytestmark = pytest.mark.django_db


@pytest.fixture
def ring_view_world(manor_factory):
    owner, owner_user = manor_factory(username="ring_view_owner")
    viewer, viewer_user = manor_factory(username="ring_view_spectator")
    guest = create_guest(owner, create_guest_template("ring_view_hidden"), "秘密守擂门客")
    viewer_guest = create_guest(viewer, create_guest_template("ring_view_attacker"), "挑战者")
    for manor in (owner, viewer):
        manor.silver = 1000000
        manor.save(update_fields=["silver"])
    for key, name in (("qinglong", "青龙擂"), ("baihu", "白虎擂")):
        ArenaRing.objects.get_or_create(key=key, defaults={"name": name})
    ring = ArenaRing.objects.get(key="qinglong")
    result = occupy_ring(
        manor=owner,
        ring_key=ring.key,
        guest_ids=[guest.pk],
        troop_loadout={},
        action_token=uuid4(),
        expected_version=ring.version,
    )
    viewer_client, owner_client = Client(), Client()
    viewer_client.force_login(viewer_user)
    owner_client.force_login(owner_user)
    return owner, viewer, guest, viewer_guest, result, owner_client, viewer_client


def inspection_payload(result, token=None):
    return {
        "action": "inspect",
        "action_token": str(token or uuid4()),
        "expected_version": result["version"],
        "expected_tenure_id": result["tenure_id"],
    }


def test_ring_public_pages_hide_uninspected_lineup(ring_view_world):
    owner, _, guest, _, result, _, client = ring_view_world
    response = client.get(reverse("gameplay:arena_rings"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "青龙擂" in body and "白虎擂" in body
    assert "查看阵容" in body
    assert "查看阵容 · 20万" not in body
    assert "纯玩家对战测试" in body
    assert "演武台" in body and "龙虎擂台" not in body
    assert 'class="ring-loadout-overlay ring-loadout-overlay--qinglong tw-modal-overlay"' in body
    assert 'role="dialog" aria-modal="false"' in body
    assert "<dialog" not in body
    assert static("js/guild-modal.js") in body
    assert 'data-max-guests="20"' in body
    assert "门客最多 20 名" in body
    assert 'data-ring-open="ring-loadout-qinglong"' in body
    assert "按报名阵容出战，不占用门客。" in body
    assert '<details class="ring-entry">' not in body
    assert static("css/arena-rings.css") in body
    assert "ring_daily_max_coins" not in response.context
    assert guest.display_name not in body
    assert static("images/buildings/青龙擂.webp") in body
    detail_url = reverse("gameplay:arena_ring_event_detail", args=[result["tenure_id"]])
    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert guest.display_name not in detail.content.decode()
    assert owner.display_name in detail.content.decode()
    events = client.get(reverse("gameplay:arena_events"))
    assert detail_url in events.content.decode()


def test_free_inspection_post_is_idempotent_and_receipt_is_private(ring_view_world):
    owner, viewer, guest, _, result, owner_client, client = ring_view_world
    url = reverse("gameplay:arena_ring_action", args=["qinglong"])
    payload = inspection_payload(result)
    response = client.post(url, payload)
    assert response.status_code == 302
    receipt = ArenaRingInspection.objects.get(buyer=viewer)
    receipt_url = reverse("gameplay:arena_ring_inspection", args=[receipt.pk])
    assert response.url == receipt_url
    assert client.get(receipt_url).status_code == 200
    assert guest.display_name in client.get(receipt_url).content.decode()
    assert owner_client.get(receipt_url).status_code == 404
    second = client.post(url, payload)
    assert second.url == receipt_url
    assert ArenaRingInspection.objects.filter(buyer=viewer).count() == 1
    owner.refresh_from_db()
    viewer.refresh_from_db()
    assert owner.silver == viewer.silver == 1000000


def test_stale_inspection_post_does_not_charge(ring_view_world):
    owner, viewer, guest, _, result, owner_client, client = ring_view_world
    url = reverse("gameplay:arena_ring_action", args=["qinglong"])
    update = inspection_payload(result)
    update.update(action="update", guest_ids=[guest.pk])
    assert owner_client.post(url, update).status_code == 302
    assert client.post(url, inspection_payload(result)).url == reverse("gameplay:arena_rings")
    assert not ArenaRingInspection.objects.exists()
    owner.refresh_from_db()
    viewer.refresh_from_db()
    assert owner.silver == viewer.silver == 1000000


@pytest.mark.parametrize("hours_ago,visible", [(23.9, True), (24.1, False)])
def test_ring_event_and_inspection_receipt_retained_24_hours(ring_view_world, hours_ago, visible):
    _, _, _, _, result, _, client = ring_view_world
    url = reverse("gameplay:arena_ring_action", args=["qinglong"])
    receipt_response = client.post(url, inspection_payload(result))
    tenure = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    tenure.ended_at = timezone.now() - timedelta(hours=hours_ago)
    tenure.save(update_fields=["ended_at"])
    detail_url = reverse("gameplay:arena_ring_event_detail", args=[tenure.pk])
    expected_status = 200 if visible else 404
    assert client.get(detail_url).status_code == expected_status
    assert client.get(receipt_response.url).status_code == expected_status
    assert (detail_url in client.get(reverse("gameplay:arena_events")).content.decode()) == visible


def test_owner_can_update_troops_after_completed_defense(ring_view_world, monkeypatch):
    owner, viewer, guest, viewer_guest, result, owner_client, client = ring_view_world
    troop = TroopTemplate.objects.create(key="ring_view_troop", name="擂台测试护院")
    PlayerTroop.objects.create(manor=owner, troop_template=troop, count=10)

    def simulate(**kwargs):
        assert kwargs["apply_damage"] is False
        now = timezone.now()
        return BattleReport.objects.create(
            manor=viewer,
            winner="defender",
            opponent_name=owner.display_name,
            battle_type="arena",
            starts_at=now,
            completed_at=now,
        )

    monkeypatch.setattr("gameplay.services.arena.ring.simulate_report", simulate)
    url = reverse("gameplay:arena_ring_action", args=["qinglong"])
    challenge = inspection_payload(result)
    challenge.update(action="challenge", guest_ids=[viewer_guest.pk])
    assert client.post(url, challenge).status_code == 302
    update = inspection_payload(result)
    update.update(action="update", guest_ids=[guest.pk], troop_ring_view_troop=7)
    assert owner_client.post(url, update).status_code == 302
    tenure = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    assert tenure.lineup["troops"] == {troop.key: 7}
    assert tenure.defenses == 1
    assert PlayerTroop.objects.get(manor=owner, troop_template=troop).count == 10


@pytest.mark.parametrize("hours_ago,visible", [(None, True), (23.9, True), (24.1, False)])
def test_ring_reports_allow_spectators_only_in_retention_window(ring_view_world, manor_factory, hours_ago, visible):
    owner, viewer, _, _, result, _, _ = ring_view_world
    _, spectator_user = manor_factory(username="ring_report_spectator")
    spectator = Client()
    spectator.force_login(spectator_user)
    now = timezone.now()
    tenure = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    if hours_ago is not None:
        tenure.ended_at = now - timedelta(hours=hours_ago)
        tenure.save(update_fields=["ended_at"])
    report = BattleReport.objects.create(
        manor=viewer,
        winner="defender",
        opponent_name=owner.display_name,
        battle_type="arena",
        starts_at=now,
        completed_at=now,
    )
    low, high = sorted((owner.pk, viewer.pk))
    ArenaRingMatch.objects.create(
        tenure=tenure,
        attacker=viewer,
        defender=owner,
        winner=owner,
        battle_report=report,
        pair_low=low,
        pair_high=high,
        reward_date=timezone.localdate(now),
        created_at=now,
    )
    response = spectator.get(reverse("battle:report_detail", args=[report.pk]))
    assert response.status_code == (200 if visible else 404)
    if visible:
        assert response.context["is_spectator"] is True


def test_own_inspection_post_does_not_charge_or_crash(ring_view_world):
    owner, _, _, _, result, owner_client, _ = ring_view_world
    response = owner_client.post(reverse("gameplay:arena_ring_action", args=["qinglong"]), inspection_payload(result))
    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena_rings")
    owner.refresh_from_db()
    assert owner.silver == 1000000
    assert not ArenaRingInspection.objects.exists()


def test_recent_ring_history_remains_accessible_beyond_50_events(ring_view_world):
    owner, _, _, _, result, _, client = ring_view_world
    active = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    now = timezone.now()
    history = ArenaRingTenure.objects.bulk_create(
        [
            ArenaRingTenure(
                ring=active.ring,
                manor=owner,
                lineup=active.lineup,
                started_at=now - timedelta(minutes=index + 10),
                ends_at=now - timedelta(minutes=index),
                ended_at=now - timedelta(minutes=index),
                settled_at=now - timedelta(minutes=index),
                lineup_updated_at=now - timedelta(minutes=index + 10),
            )
            for index in range(1, 52)
        ]
    )
    first = client.get(reverse("gameplay:arena_events"))
    group = next(group for group in first.context["ring_history_groups"] if group["ring"].key == "qinglong")
    assert group["page"].paginator.count == 52
    assert first.context["event_page"].paginator.count == 0
    assert "下一页" in first.content.decode()
    second = client.get(reverse("gameplay:arena_events"), {"qinglong_page": 6})
    assert second.status_code == 200
    assert reverse("gameplay:arena_ring_event_detail", args=[history[-1].pk]) in second.content.decode()


def test_ring_challenge_history_paginates_without_dropping_matches(ring_view_world):
    owner, viewer, _, _, result, _, client = ring_view_world
    now = timezone.now()
    low, high = sorted((owner.pk, viewer.pk))
    ArenaRingMatch.objects.bulk_create(
        [
            ArenaRingMatch(
                tenure_id=result["tenure_id"],
                attacker=viewer,
                defender=owner,
                winner=owner,
                pair_low=low,
                pair_high=high,
                reward_date=timezone.localdate(now),
                created_at=now - timedelta(seconds=index),
            )
            for index in range(105)
        ]
    )
    url = reverse("gameplay:arena_ring_event_detail", args=[result["tenure_id"]])
    response = client.get(url, {"page": 4})
    assert response.status_code == 200
    assert response.context["event_page"].paginator.count == 105
    assert len(response.context["ring_event_matches"]) == 15
    assert "上一页" in response.content.decode()


def test_ring_loadout_combines_manor_and_bank_troops(ring_view_world):
    from gameplay.models import TroopBankStorage

    owner, _, _, _, _, client, _ = ring_view_world
    shared = TroopTemplate.objects.create(key="ring_shared_stock", name="合计护院")
    bank_only = TroopTemplate.objects.create(key="ring_bank_only_stock", name="钱庄护院")
    PlayerTroop.objects.create(manor=owner, troop_template=shared, count=10)
    TroopBankStorage.objects.create(manor=owner, troop_template=shared, count=20)
    TroopBankStorage.objects.create(manor=owner, troop_template=bank_only, count=15)
    response = client.get(reverse("gameplay:arena_rings"))
    body = response.content.decode()
    assert "持有 30（庄园 10 / 钱庄 20）" in body
    assert "持有 15（庄园 0 / 钱庄 15）" in body
    rows = {row["key"]: row for row in response.context["ring_troop_rows"]}
    assert rows[shared.key]["available"] == 30
    assert rows[bank_only.key]["available"] == 15


def test_ring_lists_unowned_troops_and_keeps_virtual_selected_count(ring_view_world):
    owner, _, guest, _, result, client, _ = ring_view_world
    troop = TroopTemplate.objects.create(key="ring_virtual_only", name="虚拟护院")
    from gameplay.services.arena.ring import update_ring_lineup

    update_ring_lineup(
        manor=owner,
        ring_key="qinglong",
        guest_ids=[guest.pk],
        troop_loadout={troop.key: guest.troop_capacity},
        action_token=uuid4(),
        expected_version=result["version"],
        expected_tenure_id=result["tenure_id"],
    )
    response = client.get(reverse("gameplay:arena_rings"))
    row = next(row for row in response.context["ring_troop_rows"] if row["key"] == troop.key)
    assert row["available"] == 0 and row["selected"] == guest.troop_capacity
    assert 'max="0"' not in response.content.decode()


def test_ring_old_oversized_lineup_display_is_capped(ring_view_world):
    _, _, guest, _, result, client, _ = ring_view_world
    troop = TroopTemplate.objects.create(key="ring_legacy_huge", name="旧版护院")
    tenure = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    tenure.lineup["troops"] = {troop.key: 1000000}
    tenure.save(update_fields=["lineup"])
    response = client.get(reverse("gameplay:arena_rings"))
    row = next(row for row in response.context["ring_troop_rows"] if row["key"] == troop.key)
    assert row["selected"] == guest.troop_capacity
    assert response.context["ring_rows"][0]["own_lineup"]["troops"][0]["count"] == guest.troop_capacity


@pytest.mark.parametrize(
    "attempts,cooling,remaining,allowed",
    [(0, False, 2, True), (1, True, 1, False), (1, False, 1, True), (2, False, 0, False)],
)
def test_challenger_quota_and_cooldown_display(ring_view_world, attempts, cooling, remaining, allowed):
    owner, viewer, _, _, result, owner_client, client = ring_view_world
    low, high = sorted((owner.pk, viewer.pk))
    for _ in range(attempts):
        ArenaRingMatch.objects.create(
            tenure_id=result["tenure_id"],
            attacker=viewer,
            defender=owner,
            winner=owner,
            pair_low=low,
            pair_high=high,
            reward_date=timezone.localdate(),
            created_at=timezone.now() - timedelta(minutes=1 if cooling else 10),
        )
    response = client.get(reverse("gameplay:arena_rings"))
    row = next(row for row in response.context["ring_rows"] if row["tenure"])
    assert row["tenure_challenges_remaining"] == remaining
    assert row["can_enter_action"] is allowed
    body = response.content.decode()
    assert f"对本任擂主剩余 {remaining} 次挑战" in body
    button = body.split('data-ring-open="ring-loadout-qinglong"', 1)[1].split(">", 1)[0]
    assert ("disabled" in button) is not allowed
    own = owner_client.get(reverse("gameplay:arena_rings"))
    own_row = next(row for row in own.context["ring_rows"] if row["is_mine"])
    assert own_row["can_enter_action"] is True


@pytest.mark.parametrize("hours_ago,visible", [(23.9, True), (24.1, False)])
def test_ring_history_groups_are_independent_and_retained_24_hours(ring_view_world, hours_ago, visible):
    owner, _, _, _, result, _, client = ring_view_world
    active = ArenaRingTenure.objects.get(pk=result["tenure_id"])
    white = ArenaRing.objects.get(key="baihu")
    now = timezone.now()
    old = ArenaRingTenure.objects.create(
        ring=white,
        manor=owner,
        lineup=active.lineup,
        started_at=now - timedelta(hours=hours_ago + 1),
        ends_at=now - timedelta(hours=hours_ago),
        ended_at=now - timedelta(hours=hours_ago),
        settled_at=now - timedelta(hours=hours_ago),
        lineup_updated_at=now - timedelta(hours=hours_ago + 1),
    )
    response = client.get(reverse("gameplay:arena_events"))
    groups = {group["ring"].key: group for group in response.context["ring_history_groups"]}
    assert set(groups) == {"qinglong", "baihu"}
    assert groups["qinglong"]["record_count"] == 1
    assert groups["baihu"]["record_count"] == int(visible)
    assert (reverse("gameplay:arena_ring_event_detail", args=[old.pk]) in response.content.decode()) is visible
    assert response.content.decode().count('<details class="ring-history-group ') == 2
    assert all(record["tenure"].ring_id == group["ring"].pk for group in groups.values() for record in group["records"])


def test_ring_group_pagination_keeps_other_ring_page_and_expands_selected_group(ring_view_world):
    _, _, _, _, _, _, client = ring_view_world
    response = client.get(reverse("gameplay:arena_events"), {"qinglong_page": "bad", "baihu_page": "1", "page": "2"})
    groups = response.context["ring_history_groups"]
    assert all(group["expanded"] for group in groups)
    assert all(group["page"].number == 1 for group in groups)
