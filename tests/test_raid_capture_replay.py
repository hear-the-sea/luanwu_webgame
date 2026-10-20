from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from battle.random_context import RNG_STREAM_CAPTURE, BattleRandomContext
from gameplay.models import JailPrisoner, RaidRun
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import capture as raid_capture
from guests.constants import RARITY_CONVERSION_TEMPLATE_KEY_PREFIX
from guests.models import Guest, GuestStatus, GuestTemplate


@pytest.mark.parametrize(
    ("is_attacker_victory", "expected_losing_side"),
    [(True, "defender"), (False, "attacker")],
)
def test_capture_uses_rate_for_losing_side(monkeypatch, is_attacker_victory, expected_losing_side):
    captured: dict[str, str] = {}

    def _capture_gate(_winner, *, losing_side, rng):
        captured["losing_side"] = losing_side
        return False

    monkeypatch.setattr(raid_capture, "_can_attempt_capture", _capture_gate)

    payload = raid_capture._try_capture_guest(
        SimpleNamespace(attacker=object(), defender=object()),
        SimpleNamespace(),
        is_attacker_victory,
        rng=BattleRandomContext.create(1234).rng(RNG_STREAM_CAPTURE),
    )

    assert payload is None
    assert captured == {"losing_side": expected_losing_side}


def _create_template(index: int) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=f"capture_replay_guest_{index}",
        name=f"俘获重放门客{index}",
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1200,
    )


@pytest.mark.django_db
def test_capture_target_replays_identically_from_capture_substream(monkeypatch):
    User = get_user_model()
    templates = [_create_template(index) for index in range(3)]
    captured_template_keys: list[str] = []
    monkeypatch.setattr(raid_capture, "_can_attempt_capture", lambda *_args, **_kwargs: True)

    for fixture_index in range(2):
        attacker = ensure_manor(
            User.objects.create_user(username=f"capture_replay_attacker_{fixture_index}", password="pass123")
        )
        defender = ensure_manor(
            User.objects.create_user(username=f"capture_replay_defender_{fixture_index}", password="pass123")
        )
        guests = [
            Guest.objects.create(
                manor=defender,
                template=template,
                custom_name=f"镜像门客{index}",
                level=10,
            )
            for index, template in enumerate(templates)
        ]
        run = RaidRun.objects.create(attacker=attacker, defender=defender)
        report = SimpleNamespace(defender_team=[{"guest_id": guest.pk} for guest in guests])
        random_context = BattleRandomContext.create(271828)

        payload = raid_capture._try_capture_guest(
            run,
            report,
            True,
            rng=random_context.rng(RNG_STREAM_CAPTURE),
        )

        assert payload is not None
        captured_template_keys.append(payload["template_key"])

    assert captured_template_keys[0] == captured_template_keys[1]


@pytest.mark.django_db
@pytest.mark.parametrize("rarity_pill_used", [False, True])
def test_capture_converted_blue_guest_restores_original_green_template(monkeypatch, rarity_pill_used):
    User = get_user_model()
    attacker = ensure_manor(User.objects.create_user(username="capture_converted_attacker", password="pass123"))
    defender = ensure_manor(User.objects.create_user(username="capture_converted_defender", password="pass123"))
    source_template = _create_template(99)
    converted_template = GuestTemplate.objects.create(
        key=f"{RARITY_CONVERSION_TEMPLATE_KEY_PREFIX}blue_{source_template.pk}",
        name=source_template.name,
        archetype=source_template.archetype,
        rarity="blue",
        base_attack=source_template.base_attack,
        base_intellect=source_template.base_intellect,
        base_defense=source_template.base_defense,
        base_agility=source_template.base_agility,
        base_luck=source_template.base_luck,
        base_hp=source_template.base_hp,
    )
    guest = Guest.objects.create(
        manor=defender,
        template=converted_template,
        custom_name="已转蓝门客",
        level=10,
        loyalty=25,
        rarity_pill_used=rarity_pill_used,
    )
    run = RaidRun.objects.create(attacker=attacker, defender=defender)
    report = SimpleNamespace(defender_team=[{"guest_id": guest.pk}])
    monkeypatch.setattr(raid_capture, "_can_attempt_capture", lambda *_args, **_kwargs: True)

    payload = raid_capture._try_capture_guest(
        run,
        report,
        True,
        rng=BattleRandomContext.create(1234).rng(RNG_STREAM_CAPTURE),
    )

    prisoner = JailPrisoner.objects.get(captor=attacker, original_manor=defender)
    assert prisoner.guest_template_id == source_template.id
    assert prisoner.guest_template.rarity == "green"
    assert prisoner.rarity_pill_used is True
    assert payload == {
        "guest_name": "已转蓝门客",
        "rarity": "green",
        "template_key": source_template.key,
        "from": "defender",
        "into": "jail",
    }


@pytest.mark.django_db
@pytest.mark.parametrize("rarity_pill_used", [False, True])
def test_capture_preserves_rarity_pill_record_after_template_was_restored(monkeypatch, rarity_pill_used):
    User = get_user_model()
    attacker = ensure_manor(User.objects.create_user(username="capture_record_attacker", password="pass123"))
    defender = ensure_manor(User.objects.create_user(username="capture_record_defender", password="pass123"))
    source_template = _create_template(100)
    guest = Guest.objects.create(
        manor=defender,
        template=source_template,
        loyalty=25,
        rarity_pill_used=rarity_pill_used,
    )
    run = RaidRun.objects.create(attacker=attacker, defender=defender)
    report = SimpleNamespace(defender_team=[{"guest_id": guest.pk}])
    monkeypatch.setattr(raid_capture, "_can_attempt_capture", lambda *_args, **_kwargs: True)

    payload = raid_capture._try_capture_guest(
        run,
        report,
        True,
        rng=BattleRandomContext.create(1234).rng(RNG_STREAM_CAPTURE),
    )

    prisoner = JailPrisoner.objects.get(captor=attacker, original_manor=defender)
    assert payload is not None
    assert prisoner.guest_template_id == source_template.pk
    assert prisoner.rarity_pill_used is rarity_pill_used


@pytest.mark.django_db
def test_defender_loss_can_capture_vacation_guest_with_half_weight(monkeypatch):
    User = get_user_model()
    attacker = ensure_manor(User.objects.create_user(username="capture_vacation_attacker", password="pass123"))
    defender = ensure_manor(User.objects.create_user(username="capture_vacation_defender", password="pass123"))
    active_template = _create_template(200)
    vacation_template = _create_template(201)
    active_guest = Guest.objects.create(
        manor=defender,
        template=active_template,
        custom_name="出战门客",
        level=10,
    )
    vacation_guest = Guest.objects.create(
        manor=defender,
        template=vacation_template,
        custom_name="归闲门客",
        level=10,
        status=GuestStatus.VACATION,
    )
    run = RaidRun.objects.create(attacker=attacker, defender=defender)
    report = SimpleNamespace(defender_team=[{"guest_id": active_guest.pk}])
    monkeypatch.setattr(raid_capture, "_can_attempt_capture", lambda *_args, **_kwargs: True)

    class _ChooseVacation:
        def randrange(self, stop: int) -> int:
            assert stop == raid_capture.ACTIVE_CAPTURE_CANDIDATE_WEIGHT + raid_capture.VACATION_CAPTURE_CANDIDATE_WEIGHT
            return raid_capture.ACTIVE_CAPTURE_CANDIDATE_WEIGHT

    payload = raid_capture._try_capture_guest(run, report, True, rng=_ChooseVacation())

    assert payload is not None
    assert payload["guest_name"] == "归闲门客"
    assert not Guest.objects.filter(pk=vacation_guest.pk).exists()
    assert Guest.objects.filter(pk=active_guest.pk).exists()
    assert JailPrisoner.objects.filter(captor=attacker, original_manor=defender).count() == 1


@pytest.mark.django_db
def test_attacker_loss_does_not_include_attacker_vacation_guests(monkeypatch):
    User = get_user_model()
    attacker = ensure_manor(User.objects.create_user(username="capture_vacation_attacker_loss", password="pass123"))
    defender = ensure_manor(User.objects.create_user(username="capture_vacation_defender_loss", password="pass123"))
    vacation_template = _create_template(202)
    vacation_guest = Guest.objects.create(
        manor=attacker,
        template=vacation_template,
        custom_name="攻击方归闲门客",
        status=GuestStatus.VACATION,
    )
    run = RaidRun.objects.create(attacker=attacker, defender=defender)
    report = SimpleNamespace(attacker_team=[])
    monkeypatch.setattr(raid_capture, "_can_attempt_capture", lambda *_args, **_kwargs: True)

    payload = raid_capture._try_capture_guest(
        run,
        report,
        False,
        rng=BattleRandomContext.create(1234).rng(RNG_STREAM_CAPTURE),
    )

    assert payload is None
    assert Guest.objects.filter(pk=vacation_guest.pk).exists()
    assert not JailPrisoner.objects.filter(captor=defender, original_manor=attacker).exists()
