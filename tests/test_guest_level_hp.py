from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone

from battle.combatants_pkg.guest_builder import build_guest_combatants
from guests.growth_engine import apply_training_completion
from guests.guest_combat_stats import resolve_guest_combat_stats
from guests.management.commands.load_guest_templates import Command
from guests.models import Guest, GuestRarity, GuestStatus, GuestTemplate
from guests.query_utils import guest_max_hp_expression
from guests.services.health import heal_all_guests_with_medicine
from guests.tasks import scan_passive_hp_recovery
from tests.test_bulk_guest_healing import _medicine


@pytest.mark.parametrize("rarity", GuestRarity.values)
@pytest.mark.parametrize(
    "archetype,level,bonus",
    [
        ("military", 1, 0),
        ("military", 2, 70),
        ("military", 50, 3430),
        ("military", 100, 6930),
        ("civil", 1, 0),
        ("civil", 2, 50),
        ("civil", 50, 2450),
        ("civil", 100, 4950),
    ],
)
def test_every_rarity_receives_the_same_fixed_level_hp(rarity, archetype, level, bonus):
    guest = Guest(
        template=GuestTemplate(rarity=rarity, archetype=archetype, base_hp=1000),
        level=level,
        defense_stat=100,
        hp_bonus=125,
    )
    assert guest.max_hp == 5125 + bonus
    assert guest.stat_block()["hp"] == guest.max_hp
    assert resolve_guest_combat_stats(guest).max_hp == guest.max_hp


@pytest.mark.parametrize("level", [1, 2, 100])
def test_level_hp_is_added_on_top_of_the_minimum_hp_floor(level):
    guest = Guest(template=GuestTemplate(base_hp=100, archetype="military"), level=level, defense_stat=0, hp_bonus=-500)
    assert guest.max_hp == 200 + (level - 1) * 70


@pytest.mark.parametrize("levels", [1, 5, 99])
def test_training_adds_fixed_hp_in_addition_to_existing_defense_growth(levels):
    guest = Guest(
        template=GuestTemplate(rarity="blue", archetype="military", base_hp=1000),
        level=1,
        defense_stat=100,
        hp_bonus=125,
    )
    old_max_hp = guest.max_hp
    apply_training_completion(
        guest,
        levels_gained=levels,
        allocate_level_up_attributes_func=lambda *_: {"force": 0, "intellect": 0, "defense": levels, "agility": 0},
    )
    assert guest.level == 1 + levels
    assert guest.max_hp == old_max_hp + levels * (70 + 40)
    assert guest.hp_bonus == 125
    assert guest.current_hp == guest.max_hp


@pytest.mark.django_db
def test_sql_hp_expression_matches_current_guests_including_floor_and_signed_bonuses(manor_with_user):
    manor, _ = manor_with_user
    for archetype in ("military", "civil"):
        template = GuestTemplate.objects.create(
            key=f"level_hp_sql_{archetype}",
            name=f"等级生命_{archetype}",
            archetype=archetype,
            base_hp=1000,
        )
        for level in (0, 1, 2, 50, 100):
            for hp_bonus in (-6000, 125):
                Guest.objects.create(manor=manor, template=template, level=level, hp_bonus=hp_bonus)
    for guest in (
        Guest.objects.filter(template__key__startswith="level_hp_sql_")
        .select_related("template")
        .annotate(sql_hp=guest_max_hp_expression())
    ):
        assert guest.sql_hp == guest.max_hp


@pytest.mark.django_db
def test_existing_level_hp_is_used_by_passive_recovery_and_batch_medicine(manor_with_user):
    manor, _ = manor_with_user
    template = GuestTemplate.objects.create(key="level_hp_healing", name="等级回血", base_hp=1000)
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        level=50,
        defense_stat=0,
        current_hp=1000,
        last_hp_recovery_at=timezone.now() - timezone.timedelta(days=2),
    )
    assert scan_passive_hp_recovery(limit=10) == 1
    guest.refresh_from_db()
    assert guest.current_hp == guest.max_hp == 3940

    Guest.objects.filter(pk=guest.pk).update(current_hp=1000)
    item = _medicine(manor, heal_amount=2940, quantity=1)
    result = heal_all_guests_with_medicine(manor)
    guest.refresh_from_db()
    assert result["healed_hp"] == 2940
    assert guest.current_hp == guest.max_hp
    assert not type(item).objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_existing_guest_level_hp_reaches_the_real_battle_builder(manor_with_user):
    manor, _ = manor_with_user
    template = GuestTemplate.objects.create(key="level_hp_battle", name="等级战斗", base_hp=1000)
    guest = Guest.objects.create(manor=manor, template=template, level=100, defense_stat=100)
    fighter = build_guest_combatants([guest], "attacker")[0]
    assert fighter.max_hp == fighter.hp == 10940


@pytest.mark.django_db
def test_template_base_hp_sync_keeps_level_hp_and_existing_health_ratio(manor_with_user):
    manor, _ = manor_with_user
    template = GuestTemplate.objects.create(key="level_hp_template_sync", name="等级模板同步", base_hp=1200)
    full = Guest.objects.create(manor=manor, template=template, level=50, defense_stat=80)
    half = Guest.objects.create(manor=manor, template=template, level=50, defense_stat=80, current_hp=full.max_hp // 2)
    template.base_hp = 1000
    template.save(update_fields=["base_hp"])
    Command()._sync_existing_guest_current_hp_for_template_base_hp(template=template, previous_base_hp=1200)
    full.refresh_from_db()
    half.refresh_from_db()
    assert full.current_hp == full.max_hp == 7140
    assert half.current_hp == 3570


@pytest.mark.django_db
def test_level_hp_migration_preserves_full_wounded_and_zero_hp(manor_with_user, guest_hp_before_level_growth):
    manor, _ = manor_with_user
    template = GuestTemplate.objects.create(key="level_hp_migration", name="等级生命迁移", base_hp=1000)
    guests = [
        Guest.objects.create(manor=manor, template=template, level=50, defense_stat=80, current_hp=hp)
        for hp in (5000, 2500, 1)
    ]
    Guest.objects.filter(pk=guests[-1].pk).update(current_hp=0, status=GuestStatus.INJURED)
    level_one = Guest.objects.create(manor=manor, template=template, level=1, defense_stat=80)
    migration = importlib.import_module("guests.migrations.0093_guest_level_hp_gain")
    editor = SimpleNamespace(connection=connection)
    migration.add_level_hp(apps, editor)
    for guest, hp in zip(guests, (7940, 3970, 0)):
        guest.refresh_from_db()
        assert guest.current_hp == hp
        assert guest.hp_bonus == 0
    level_one.refresh_from_db()
    assert level_one.current_hp == 5000
    assert guests[-1].status == GuestStatus.INJURED

    migration.remove_level_hp(apps, editor)
    for guest, hp in zip(guests, (5000, 2500, 0)):
        guest.refresh_from_db()
        assert guest.current_hp == hp


@pytest.mark.django_db
def test_defense_hp_multiplier_migration_preserves_health_ratio(manor_with_user):
    manor, _ = manor_with_user
    template = GuestTemplate.objects.create(key="defense_hp_migration", name="防御生命迁移", base_hp=1000)
    guests = [Guest.objects.create(manor=manor, template=template, level=50, defense_stat=80) for _ in range(3)]
    Guest.objects.filter(pk=guests[0].pk).update(current_hp=7940)
    Guest.objects.filter(pk=guests[1].pk).update(current_hp=3970)
    Guest.objects.filter(pk=guests[2].pk).update(current_hp=0, status=GuestStatus.INJURED)
    migration = importlib.import_module("guests.migrations.0098_reduce_defense_hp_multiplier")
    editor = SimpleNamespace(connection=connection)

    migration.reduce_defense_hp(apps, editor)
    for guest, hp in zip(guests, (6340, 3170, 0)):
        guest.refresh_from_db()
        assert guest.current_hp == hp
    assert guests[-1].status == GuestStatus.INJURED

    migration.restore_defense_hp(apps, editor)
    for guest, hp in zip(guests, (7940, 3970, 0)):
        guest.refresh_from_db()
        assert guest.current_hp == hp


@pytest.mark.django_db
def test_archetype_level_hp_gain_migration_preserves_health_ratio(manor_with_user):
    manor, _ = manor_with_user
    mil_template = GuestTemplate.objects.create(
        key="archetype_hp_mil",
        name="武门客等级生命迁移",
        archetype="military",
        base_hp=1000,
    )
    civ_template = GuestTemplate.objects.create(
        key="archetype_hp_civ",
        name="文门客等级生命迁移",
        archetype="civil",
        base_hp=1000,
    )
    mil_full = Guest.objects.create(manor=manor, template=mil_template, level=50, defense_stat=80)
    mil_half = Guest.objects.create(manor=manor, template=mil_template, level=50, defense_stat=80)
    civ_full = Guest.objects.create(manor=manor, template=civ_template, level=50, defense_stat=80)
    civ_zero = Guest.objects.create(manor=manor, template=civ_template, level=50, defense_stat=80)
    # 迁移前 50 级（+49*60=2940）且 80 防（+2400）+ 1000 = 6340
    Guest.objects.filter(pk=mil_full.pk).update(current_hp=6340)
    Guest.objects.filter(pk=mil_half.pk).update(current_hp=3170)
    Guest.objects.filter(pk=civ_full.pk).update(current_hp=6340)
    Guest.objects.filter(pk=civ_zero.pk).update(current_hp=0, status=GuestStatus.INJURED)

    migration = importlib.import_module("guests.migrations.0101_archetype_level_hp_gain")
    editor = SimpleNamespace(connection=connection)

    migration.apply_archetype_level_hp_gain(apps, editor)
    mil_full.refresh_from_db()
    mil_half.refresh_from_db()
    civ_full.refresh_from_db()
    civ_zero.refresh_from_db()
    # 武门客 50 级：1000 + 80*40 + 49*70 = 7630；文门客 50 级：1000 + 80*40 + 49*50 = 6650
    assert mil_full.current_hp == mil_full.max_hp == 7630
    assert mil_half.current_hp == 3815
    assert civ_full.current_hp == civ_full.max_hp == 6650
    assert civ_zero.current_hp == 0

    migration.revert_archetype_level_hp_gain(apps, editor)
    mil_full.refresh_from_db()
    mil_half.refresh_from_db()
    civ_full.refresh_from_db()
    assert mil_full.current_hp == 6340
    assert mil_half.current_hp == 3170
    assert civ_full.current_hp == 6340
