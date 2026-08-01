from __future__ import annotations

import pytest

from battle.combatants_pkg.guest_builder import build_guest_combatants, serialize_guest_for_report
from guests.models import Guest, GuestTemplate


@pytest.mark.django_db
def test_build_guest_combatants_prefers_display_name_override_for_report_outputs():
    template = GuestTemplate.objects.create(
        key="battle_display_name_tpl",
        name="模板原名",
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=90,
        base_defense=80,
        base_agility=70,
        base_luck=60,
        base_hp=1200,
        default_gender="unknown",
        default_morality=50,
    )
    guest = Guest(
        template=template,
        level=10,
        force=120,
        intellect=80,
        defense_stat=90,
        agility=75,
        luck=60,
        attack_bonus=0,
        defense_bonus=0,
        hp_bonus=0,
        current_hp=1000,
    )
    setattr(guest, "_display_name_override", "任务别名")

    team = build_guest_combatants([guest], side="attacker", limit=1)

    assert team[0].name == "任务别名"
    assert serialize_guest_for_report(team[0])["name"] == "任务别名"


@pytest.mark.django_db
def test_serialize_guest_for_report_keeps_coop_owner_metadata():
    template = GuestTemplate.objects.create(
        key="battle_coop_meta_tpl",
        name="共斗模板",
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=90,
        base_defense=80,
        base_agility=70,
        base_luck=60,
        base_hp=1200,
        default_gender="unknown",
        default_morality=50,
    )
    guest = Guest(
        template=template,
        level=10,
        force=120,
        intellect=80,
        defense_stat=90,
        agility=75,
        luck=60,
        attack_bonus=0,
        defense_bonus=0,
        hp_bonus=0,
        current_hp=1000,
    )
    setattr(guest, "_owner_entry_id", 12)
    setattr(guest, "_combatant_slot", 2)
    setattr(guest, "_is_boss", True)

    team = build_guest_combatants([guest], side="attacker", limit=1)

    payload = serialize_guest_for_report(team[0])
    assert payload["owner_entry_id"] == 12
    assert payload["combatant_slot"] == 2
    assert payload["is_boss"] is True


@pytest.mark.django_db
def test_build_guest_combatants_keeps_fractional_stat_bonuses_until_combat():
    template = GuestTemplate.objects.create(
        key="battle_fractional_stats_tpl",
        name="小数属性门客",
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=90,
        base_defense=80,
        base_agility=70,
        base_luck=60,
        base_hp=1200,
        default_gender="unknown",
        default_morality=50,
    )
    guest = Guest(
        template=template,
        level=10,
        force=120,
        intellect=80,
        defense_stat=90,
        agility=75,
        luck=60,
        attack_bonus=0,
        defense_bonus=0,
        hp_bonus=0,
        current_hp=1000,
    )

    combatant = build_guest_combatants(
        [guest],
        side="attacker",
        limit=1,
        stat_bonuses={"attack": 0.05, "defense": 0.05, "hp": 0.05, "agility": 0.05},
    )[0]

    assert combatant.attack == pytest.approx(108 * 1.05)
    assert combatant.defense == pytest.approx(90 * 1.05)
    assert combatant.agility == pytest.approx((75 + 8) * 1.05)
    assert combatant.max_hp == 5985
    assert combatant.hp == 1050
    assert isinstance(combatant.hp, int)
    assert isinstance(combatant.max_hp, int)
