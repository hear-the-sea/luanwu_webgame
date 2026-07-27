from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from battle.random_context import RNG_STREAM_CAPTURE, BattleRandomContext
from gameplay.models import RaidRun
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import capture as raid_capture
from guests.models import Guest, GuestTemplate


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
