from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import MissionGuestSelectionError, RaidStartError
from gameplay.models import InventoryItem, ItemTemplate, MissionRun, MissionTemplate, RaidRun
from gameplay.services.missions_impl.launch_command import prepare_offense_launch_inputs
from gameplay.services.raid.combat import runs as combat_runs
from gameplay.services.raid.combat.raid_inputs import _load_and_validate_attacker_guests
from guests.models import Guest, GuestStatus, GuestTemplate
from guests.services.health import (
    _heal_locked_guests_one_dose_at_a_time,
    _MedicineStock,
    heal_all_guests_with_medicine,
    heal_guest,
    recover_guest_hp,
    use_medicine_item_for_guest,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def returning_guest(manor_factory):
    attacker, _ = manor_factory()
    defender, _ = manor_factory()
    template = GuestTemplate.objects.create(key="return_occupancy_guest", name="返程门客", rarity="green", base_hp=1000)
    guest = Guest.objects.create(
        manor=attacker, template=template, status=GuestStatus.INJURED, current_hp=1, training_remaining_seconds=90
    )
    now = timezone.now()
    run = RaidRun.objects.create(
        attacker=attacker, defender=defender, status=RaidRun.Status.RETURNING, return_at=now + timedelta(minutes=10)
    )
    run.guests.add(guest)
    return attacker, defender, guest, run, now


def medicine(manor):
    template = ItemTemplate.objects.create(
        key="return_occupancy_medicine",
        name="疗伤药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": 10000},
    )
    return InventoryItem.objects.create(manor=manor, template=template, quantity=1)


@pytest.mark.parametrize("method", ["medicine", "batch", "natural", "full_restore"])
def test_healed_guest_stays_deployed_until_raid_returns(returning_guest, method):
    attacker, _defender, guest, run, now = returning_guest
    if method == "medicine":
        item = medicine(attacker)
        result = use_medicine_item_for_guest(attacker, guest, item.pk, 10000)
        assert result["injury_cured"] is True
    elif method == "batch":
        medicine(attacker)
        heal_all_guests_with_medicine(attacker)
    elif method == "natural":
        guest.current_hp = guest.max_hp
        guest.save(update_fields=["current_hp"])
        recover_guest_hp(guest, now=now)
    else:
        guest.restore_full_hp()

    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED
    assert guest.training_remaining_seconds == 90
    with pytest.raises(RaidStartError, match="不可出征"):
        _load_and_validate_attacker_guests(attacker, [guest.pk])
    assert combat_runs.finalize_raid(run, now=now) is False
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED
    assert combat_runs.finalize_raid(run, now=run.return_at) is True
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


def test_legacy_idle_guest_cannot_be_sent_on_another_raid(returning_guest):
    attacker, _defender, guest, _run, _now = returning_guest
    guest.status = GuestStatus.IDLE
    guest.save(update_fields=["status"])
    with pytest.raises(RaidStartError, match="仍在踢馆队伍"):
        _load_and_validate_attacker_guests(attacker, [guest.pk])
    mission = MissionTemplate(key="return_occupancy_mission", name="测试任务", guest_only=True)
    with pytest.raises(MissionGuestSelectionError, match="仍在踢馆队伍"):
        prepare_offense_launch_inputs(attacker, mission, [guest.pk], {})


def test_finishing_old_raid_does_not_release_guest_in_new_raid(returning_guest):
    attacker, defender, guest, old_run, now = returning_guest
    heal_guest(guest, guest.max_hp, now=now)
    newer = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        return_at=old_run.return_at + timedelta(minutes=10),
    )
    newer.guests.add(guest)

    assert combat_runs.finalize_raid(old_run, now=old_run.return_at) is True
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED
    newer.status = RaidRun.Status.RETURNING
    newer.save(update_fields=["status"])
    assert combat_runs.finalize_raid(newer, now=newer.return_at) is True
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


def test_finishing_raid_does_not_release_guest_in_an_existing_mission(returning_guest):
    attacker, _defender, guest, old_run, now = returning_guest
    heal_guest(guest, guest.max_hp, now=now)
    mission_template = MissionTemplate.objects.create(key="return_overlap_mission", name="重叠任务")
    mission = MissionRun.objects.create(manor=attacker, mission=mission_template, status=MissionRun.Status.ACTIVE)
    mission.guests.add(guest)

    assert combat_runs.finalize_raid(old_run, now=old_run.return_at) is True
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED


def test_healing_after_raid_return_keeps_guest_occupied_by_existing_mission(returning_guest):
    attacker, _defender, guest, old_run, now = returning_guest
    mission_template = MissionTemplate.objects.create(key="return_overlap_injured", name="重叠任务")
    mission = MissionRun.objects.create(manor=attacker, mission=mission_template, status=MissionRun.Status.ACTIVE)
    mission.guests.add(guest)

    assert combat_runs.finalize_raid(old_run, now=old_run.return_at) is True
    guest.refresh_from_db()
    assert guest.status == GuestStatus.INJURED
    heal_guest(guest, guest.max_hp, now=now)
    guest.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED


def test_batch_fallback_keeps_remaining_medicine_available_for_other_guests(returning_guest):
    attacker, _defender, guest, _run, now = returning_guest
    other = Guest.objects.create(
        manor=attacker, template=guest.template, status=GuestStatus.IDLE, current_hp=guest.max_hp // 2
    )
    item = medicine(attacker)
    item.quantity = 2
    item.save(update_fields=["quantity"])
    dose = guest.max_hp // 2
    item.template.effect_payload = {"hp": dose}
    item.template.save(update_fields=["effect_payload"])

    with transaction.atomic():
        result = _heal_locked_guests_one_dose_at_a_time(attacker, [guest, other], [_MedicineStock(item, dose)], now=now)

    guest.refresh_from_db()
    other.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED
    assert other.current_hp == other.max_hp
    assert result["consumed_item_count"] == 2
