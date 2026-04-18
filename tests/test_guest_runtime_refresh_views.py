from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gameplay.models import (
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    InventoryItem,
    ItemTemplate,
    MissionRun,
    MissionTemplate,
    RaidRun,
)
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestStatus, GuestTemplate


def _create_guest(manor, *, prefix: str) -> Guest:
    template = GuestTemplate.objects.create(
        key=f"{prefix}_tpl",
        name=f"{prefix}模板",
        archetype="military",
        rarity="green",
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=f"{prefix}门客",
        level=1,
        force=100,
        intellect=80,
        defense_stat=90,
        agility=85,
    )


def _create_mission_template(*, key: str, name: str) -> MissionTemplate:
    return MissionTemplate.objects.create(key=key, name=name)


@pytest.mark.django_db
def test_roster_view_does_not_start_training_from_get(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="roster_auto_train", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="roster_auto_train")
    assert guest.training_complete_at is None

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:roster"))

    assert response.status_code == 200
    guest.refresh_from_db()
    assert guest.training_complete_at is None
    assert guest.training_target_level == 0


@pytest.mark.django_db
def test_guest_detail_view_does_not_finalize_overdue_training_on_get(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="detail_auto_train", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="detail_auto_train")
    guest.training_target_level = 2
    overdue_at = timezone.now() - timedelta(seconds=1)
    guest.training_complete_at = overdue_at
    guest.save(update_fields=["training_target_level", "training_complete_at"])

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:detail", args=[guest.pk]))

    assert response.status_code == 200
    guest.refresh_from_db()
    assert guest.level == 1
    assert guest.training_target_level == 2
    assert guest.training_complete_at == overdue_at


@pytest.mark.django_db
def test_roster_view_uses_explicit_read_helper(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="roster_read_helper", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="roster_read_helper")

    calls = {"helper": 0}

    def _fake_helper(request, *, logger, source, available_guests_fn):
        calls["helper"] += 1
        assert source == "guest_roster_view"
        return manor, [guest]

    monkeypatch.setattr("guests.views.roster.get_prepared_guest_roster_for_read", _fake_helper)

    client = Client()
    client.force_login(user)
    response = client.get(reverse("guests:roster"))

    assert response.status_code == 200
    assert calls["helper"] == 1


@pytest.mark.django_db
def test_guest_detail_view_uses_explicit_read_helper(game_data, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="detail_read_helper", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="detail_read_helper")

    calls = {"helper": 0}

    def _fake_helper(request, guest_pk, *, logger, source, load_guest_detail_fn):
        calls["helper"] += 1
        assert guest_pk == guest.pk
        assert source == "guest_detail_view"
        return manor, guest

    monkeypatch.setattr("guests.views.roster.get_prepared_guest_detail_for_read", _fake_helper)

    client = Client()
    client.force_login(user)
    response = client.get(reverse("guests:detail", args=[guest.pk]))

    assert response.status_code == 200
    assert calls["helper"] == 1


@pytest.mark.django_db
def test_guest_detail_view_bubbles_up_invalid_skill_book_payload(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="detail_skill_book_bad_payload", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="detail_skill_book_bad_payload")
    template = ItemTemplate.objects.create(
        key="detail_skill_book_bad_payload_item",
        name="坏结构技能书",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload=False,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    client = Client()
    client.force_login(user)

    with pytest.raises(AssertionError, match="invalid guest roster skill_book effect_payload"):
        client.get(reverse("guests:detail", args=[guest.pk]))


@pytest.mark.django_db
def test_guest_detail_view_loads_external_page_script_without_inline_detail_logic(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="detail_external_page_script", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="detail_external_page_script")

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:detail", args=[guest.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "js/guest-detail.js" in content
    assert "const attributeResponseFieldMap" not in content


@pytest.mark.django_db
def test_guest_detail_view_loads_tooltip_script_for_equipped_gear(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="detail_tooltip_script", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="detail_tooltip_script")
    gear_template = GearTemplate.objects.create(
        key="detail_tooltip_script_gear",
        name="边界测试剑",
        slot=GearSlot.WEAPON,
        rarity="green",
    )
    GearItem.objects.create(manor=manor, template=gear_template, guest=guest)

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:detail", args=[guest.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "js/tooltip.js" in content
    assert "guest-equip-tooltip-trigger" in content
    assert "guest-equip-tooltip-bubble" in content


@pytest.mark.django_db
def test_roster_view_loads_external_page_script_without_inline_roster_logic(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="roster_external_page_script", password="pass123")
    manor = ensure_manor(user)
    _create_guest(manor, prefix="roster_external_page_script")

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:roster"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "js/guest-roster.js" in content
    assert "function openSalaryModal" not in content


@pytest.mark.django_db
def test_roster_view_renders_unified_modal_layout_classes(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="roster_modal_layout", password="pass123")
    manor = ensure_manor(user)
    _create_guest(manor, prefix="roster_modal_layout")
    exp_template = ItemTemplate.objects.create(
        key="roster_modal_layout_exp",
        name="名册经验书",
        effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
        effect_payload={"time": 3600},
    )
    medicine_template = ItemTemplate.objects.create(
        key="roster_modal_layout_medicine",
        name="名册金创药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 100},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=exp_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=medicine_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:roster"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert content.count("guest-roster-modal") >= 3
    assert "guest-roster-modal__body" in content
    assert "guest-roster-modal__actions" in content
    assert "guest-roster-modal__salary-card" in content
    assert 'style="max-width: 360px;"' not in content
    assert 'style="margin: 0 0 1rem;"' not in content


@pytest.mark.django_db
def test_roster_view_does_not_finalize_due_mission_on_get(game_data, django_user_model, settings):
    user = django_user_model.objects.create_user(username="roster_due_mission", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="roster_due_mission")
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])

    mission = _create_mission_template(key="roster_due_mission_tpl", name="名册刷新任务")
    run = MissionRun.objects.create(
        manor=manor,
        mission=mission,
        status=MissionRun.Status.ACTIVE,
        is_retreating=True,
        return_at=timezone.now() - timedelta(seconds=1),
    )
    run.guests.add(guest)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 0

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:roster"))

    assert response.status_code == 200
    guest.refresh_from_db(fields=["status"])
    run.refresh_from_db(fields=["status", "completed_at"])
    assert guest.status == GuestStatus.DEPLOYED
    assert run.status == MissionRun.Status.ACTIVE
    assert run.completed_at is None


@pytest.mark.django_db
def test_home_view_does_not_finalize_due_raid_on_get(game_data, django_user_model, settings):
    attacker = django_user_model.objects.create_user(username="home_due_raid", password="pass123")
    defender = django_user_model.objects.create_user(username="home_due_raid_target", password="pass123")
    manor = ensure_manor(attacker)
    target_manor = ensure_manor(defender)
    guest = _create_guest(manor, prefix="home_due_raid")
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])

    run = RaidRun.objects.create(
        attacker=manor,
        defender=target_manor,
        status=RaidRun.Status.RETREATED,
        is_retreating=True,
        return_at=timezone.now() - timedelta(seconds=1),
    )
    run.guests.add(guest)

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 0

    client = Client()
    client.force_login(attacker)

    response = client.get(reverse("home"))

    assert response.status_code == 200
    guest.refresh_from_db(fields=["status"])
    run.refresh_from_db(fields=["status", "completed_at"])
    assert guest.status == GuestStatus.DEPLOYED
    assert run.status == RaidRun.Status.RETREATED
    assert run.completed_at is None


@pytest.mark.django_db
def test_roster_view_does_not_finalize_due_arena_tournament_on_get(game_data, django_user_model, settings):
    user = django_user_model.objects.create_user(username="roster_due_arena", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest(manor, prefix="roster_due_arena")
    guest.status = GuestStatus.ARENA
    guest.save(update_fields=["status"])

    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=0,
        started_at=timezone.now() - timedelta(minutes=10),
        next_round_at=timezone.now() - timedelta(seconds=1),
    )
    entry = ArenaEntry.objects.create(tournament=tournament, manor=manor, status=ArenaEntry.Status.REGISTERED)
    ArenaEntryGuest.objects.create(entry=entry, guest=guest, snapshot={"guest_id": guest.id})

    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 0

    client = Client()
    client.force_login(user)

    response = client.get(reverse("guests:roster"))

    assert response.status_code == 200
    guest.refresh_from_db(fields=["status"])
    tournament.refresh_from_db(fields=["status", "ended_at", "winner_entry_id"])
    entry.refresh_from_db(fields=["status", "final_rank"])
    assert guest.status == GuestStatus.ARENA
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert tournament.ended_at is None
    assert entry.status == ArenaEntry.Status.REGISTERED
