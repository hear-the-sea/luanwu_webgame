from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from battle.combatants_pkg.guest_builder import build_guest_combatants
from core.config import GUEST
from gameplay.services.arena.snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot
from gameplay.services.arena.virtual_lineups import snapshot_power
from gameplay.services.battle_snapshots import BattleGuestSnapshotProxy, build_guest_battle_snapshot
from gameplay.services.season.coalition_combat import choose_commander
from gameplay.services.season.combat import snapshot_guest_strength
from gameplay.services.season.fatigue import fatigue_snapshot
from gameplay.services.season.rallies import alive_guests
from guests.guest_rules import compute_guest_attack_attribute_component
from guests.models import Guest, GuestTemplate
from guests.tasks import process_daily_loyalty

pytestmark = pytest.mark.django_db


@pytest.fixture
def guest(manor_factory):
    manor, _ = manor_factory()
    template, _ = GuestTemplate.objects.get_or_create(
        key="custom_guest_green",
        defaults={"name": "专属门客", "rarity": "green", "archetype": "civil"},
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        loyalty=59,
        force=101,
        intellect=123,
        defense_stat=103,
        agility=107,
        attack_bonus=37,
        defense_bonus=29,
        current_hp=100,
    )


def combatant(guest):
    return build_guest_combatants([guest], "attacker")[0]


def test_low_loyalty_reduces_only_combat_attributes_and_preserves_bonuses(guest):
    guest.loyalty = 60
    normal = combatant(guest)
    guest.loyalty = 59
    weakened = combatant(guest)

    assert (weakened.force_attr, weakened.intellect_attr, weakened.defense_attr, weakened.agility) == (70, 86, 72, 74)
    assert weakened.defense == normal.defense - (103 - 72)
    reduced_component = compute_guest_attack_attribute_component(
        SimpleNamespace(force=70, intellect=86, archetype=guest.archetype)
    )
    assert weakened.attack == normal.attack - compute_guest_attack_attribute_component(guest) + reduced_component
    assert weakened.max_hp == normal.max_hp
    assert weakened.luck == normal.luck
    guest.refresh_from_db()
    assert (guest.force, guest.intellect, guest.defense_stat, guest.agility) == (101, 123, 103, 107)


@pytest.mark.parametrize("snapshot_kind", ["battle", "arena"])
def test_low_loyalty_penalty_is_frozen_in_snapshots_and_applied_once(guest, snapshot_kind):
    direct = combatant(guest)
    if snapshot_kind == "battle":
        snapshot = build_guest_battle_snapshot(guest)
        proxy = BattleGuestSnapshotProxy(snapshot, include_guest_identity=True)
    else:
        snapshot = build_entry_guest_snapshot(guest)
        proxy = ArenaGuestSnapshotProxy(snapshot)
    assert snapshot["low_loyalty_penalty"] is True
    guest.loyalty = 100
    frozen = combatant(proxy)
    for attr in ("force_attr", "intellect_attr", "defense_attr", "agility", "attack", "defense", "max_hp"):
        assert getattr(frozen, attr) == getattr(direct, attr)


def test_normal_guests_and_custom_guests_at_threshold_have_no_penalty(guest):
    guest.loyalty = 60
    assert combatant(guest).force_attr == 101
    assert "low_loyalty_penalty" not in build_guest_battle_snapshot(guest)
    guest.loyalty = 0
    guest.template = GuestTemplate.objects.create(key="loyalty_normal_guest", name="普通门客", rarity="green")
    assert combatant(guest).force_attr == 101


@pytest.mark.parametrize("rarity", ["green", "blue", "purple", "orange"])
def test_all_custom_guest_rarities_keep_the_low_loyalty_rule(guest, rarity):
    guest.template.key = f"custom_guest_{rarity}"
    guest.loyalty = 0
    assert combatant(guest).force_attr == 70


def test_daily_loyalty_keeps_custom_guest_when_ordinary_guests_defect(guest, monkeypatch):
    ordinary_template = GuestTemplate.objects.create(key="daily_loyalty_ordinary", name="普通门客", rarity="green")
    ordinary = Guest.objects.create(manor=guest.manor, template=ordinary_template, loyalty=0)
    Guest.objects.filter(pk__in=[guest.pk, ordinary.pk]).update(created_at=timezone.now() - timedelta(days=3))
    monkeypatch.setattr("guests.tasks._should_defect", lambda *args, **kwargs: True)

    process_daily_loyalty.run()

    assert Guest.objects.filter(pk=guest.pk).exists()
    assert not Guest.objects.filter(pk=ordinary.pk).exists()


def test_frozen_high_loyalty_and_season_fatigue_keep_their_own_rules(guest):
    guest.loyalty = 60
    snapshot = build_guest_battle_snapshot(guest)
    guest.loyalty = 0
    assert combatant(BattleGuestSnapshotProxy(snapshot)).force_attr == 101

    snapshot = build_guest_battle_snapshot(guest)
    snapshot["attack_attribute_component"] = compute_guest_attack_attribute_component(guest)
    run = SimpleNamespace(
        member=SimpleNamespace(
            season=SimpleNamespace(
                rules={
                    "campaign": {
                        "fortifications": {
                            "enabled": True,
                            "fatigue": {"max_stacks": 3, "per_stack_percent": 10},
                        }
                    }
                }
            )
        ),
        result={"fatigue_stacks": 1},
    )
    fatigued = fatigue_snapshot(run, snapshot)
    unit = combatant(BattleGuestSnapshotProxy(fatigued))
    assert unit.force_attr == (101 * 90 // 100) * 7 // 10
    assert unit.max_hp == snapshot["max_hp"]


def test_matchmaking_and_coalition_selection_use_effective_stats(guest):
    snapshot = build_guest_battle_snapshot(guest)
    unit = combatant(guest)
    assert snapshot_power(snapshot) == (
        unit.attack + unit.defense + unit.max_hp // 10 + int(unit.agility * GUEST.AGILITY_TO_ARENA_POWER_WEIGHT)
    )
    assert snapshot_guest_strength(snapshot) == snapshot_power(snapshot, formula_version=2, hp=snapshot["current_hp"])

    normal = {**snapshot, "guest_id": guest.pk + 1}
    normal.pop("low_loyalty_penalty")
    run = SimpleNamespace(pk=1, result={}, lineup={"guests": [snapshot, normal]})
    other_run = SimpleNamespace(pk=2, result={}, lineup={"guests": [normal]})
    assert alive_guests(run, limit=1) == [normal]
    assert (
        choose_commander([SimpleNamespace(pk=1, result={}, lineup={"guests": [snapshot]}), other_run], 1) is other_run
    )
