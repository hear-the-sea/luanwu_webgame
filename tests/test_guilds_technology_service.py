from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from django.db.utils import DatabaseError


def _seed_basic_tech_upgrade_warehouse_costs(guild) -> None:
    from guilds.models import GuildWarehouse

    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=999999, contribution_cost=2)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=999999, contribution_cost=50)


@pytest.mark.django_db
def test_calculate_tech_upgrade_cost_uses_staged_curves_without_exponential_fallback():
    from core.exceptions import GuildTechnologyError
    from guilds.services.technology import calculate_tech_upgrade_cost

    cost0 = calculate_tech_upgrade_cost("unknown", 0)
    assert cost0 == {"silver": 5000, "grain": 2000, "gold_bar": 1}

    with pytest.raises(GuildTechnologyError, match="缺少升至3级的费用配置"):
        calculate_tech_upgrade_cost("unknown", 2)

    assert calculate_tech_upgrade_cost("equipment_forge", 1) == {
        "silver": 10000,
        "grain": 4000,
        "gold_bar": 2,
    }
    assert calculate_tech_upgrade_cost("equipment_forge", 8) == {
        "silver": 750000,
        "grain": 300000,
        "gold_bar": 150,
    }
    assert calculate_tech_upgrade_cost("equipment_forge", 9) == {
        "silver": 1750000,
        "grain": 700000,
        "gold_bar": 350,
    }
    assert calculate_tech_upgrade_cost("resource_boost", 4) == {
        "silver": 600000,
        "grain": 300000,
        "gold_bar": 180,
    }
    assert calculate_tech_upgrade_cost("guild_lineup_capacity", 19) == {"red_ruby": 100}


def test_all_supported_guild_technology_levels_have_explicit_positive_costs():
    from guilds import constants as guild_constants
    from guilds.services.technology import calculate_tech_upgrade_cost

    for tech_key, _category, max_level in guild_constants.get_supported_guild_technology_configs():
        for current_level in range(max_level):
            cost = calculate_tech_upgrade_cost(tech_key, current_level)
            assert cost
            assert all(int(amount) > 0 for amount in cost.values())


def test_guild_technology_cost_curves_are_strictly_increasing_and_keep_expensive_endgame():
    from guilds import constants as guild_constants

    minimum_final_multipliers = {
        "standard_10": 350,
        "welfare_5": 60,
        "capacity_20": 20,
    }
    for curve_key, minimum_final_multiplier in minimum_final_multipliers.items():
        curve = guild_constants.TECH_UPGRADE_COST_CURVES[curve_key]
        levels = sorted(int(level) for level in curve)
        multipliers = [1, *(int(curve[str(level)]) for level in levels)]

        assert levels == list(range(2, levels[-1] + 1))
        assert all(previous < current for previous, current in zip(multipliers, multipliers[1:]))
        assert multipliers[-1] >= minimum_final_multiplier


@pytest.mark.django_db
def test_get_guild_tech_level_returns_zero_when_missing(django_user_model):
    from guilds.models import Guild
    from guilds.services.technology import get_guild_tech_level

    founder = django_user_model.objects.create_user(username="tech_founder", password="pass")
    guild = Guild.objects.create(name="TechGuild", founder=founder)

    assert get_guild_tech_level(guild, "military_study") == 0


@pytest.mark.django_db
def test_get_tech_bonus_branches(django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import get_tech_bonus

    founder = django_user_model.objects.create_user(username="tech_founder2", password="pass")
    guild = Guild.objects.create(name="TechGuild2", founder=founder)

    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", level=4)
    GuildTechnology.objects.create(guild=guild, tech_key="resource_boost", level=2)
    GuildTechnology.objects.create(guild=guild, tech_key="march_speed", level=3)

    assert get_tech_bonus(guild, "guest_force") == pytest.approx(0.0)
    assert get_tech_bonus(guild, "guest_intellect") == pytest.approx(0.0)
    assert get_tech_bonus(guild, "guest_defense") == pytest.approx(0.0)

    assert get_tech_bonus(guild, "troop_attack") == pytest.approx(0.0)
    assert get_tech_bonus(guild, "troop_defense") == pytest.approx(0.0)
    assert get_tech_bonus(guild, "troop_hp") == pytest.approx(0.0)

    assert get_tech_bonus(guild, "resource_production") == pytest.approx(0.10)
    assert get_tech_bonus(guild, "march_speed") == pytest.approx(0.15)
    assert get_tech_bonus(guild, "unknown") == pytest.approx(0.0)


@pytest.mark.django_db
def test_get_effective_guild_tech_max_level_projects_troop_tactics_to_runtime_cap():
    from guilds.services.technology import get_effective_guild_tech_max_level

    assert get_effective_guild_tech_max_level("troop_tactics", 5) == 10
    assert get_effective_guild_tech_max_level("troop_tactics", 10) == 10
    assert get_effective_guild_tech_max_level("march_speed", 5) == 5


@pytest.mark.django_db
def test_build_guild_troop_tech_levels_maps_troop_tactics_linearly(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import build_guild_troop_tech_levels

    founder = django_user_model.objects.create_user(username="tech_founder_troop_mapping", password="pass")
    guild = Guild.objects.create(name="TechTroopMappingGuild", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", level=7, max_level=5)

    monkeypatch.setattr(
        "guilds.services.technology.player_technology_service.load_technology_templates",
        lambda: {
            "technologies": [
                {"key": "dao_attack", "troop_class": "dao", "max_level": 5},
                {"key": "dao_hp", "troop_class": "dao", "max_level": 8},
                {"key": "resource_supply", "max_level": 5},
            ]
        },
    )

    assert build_guild_troop_tech_levels(guild) == {
        "dao_attack": 3,
        "dao_hp": 5,
    }


@pytest.mark.django_db
def test_build_guild_troop_tech_levels_defaults_missing_troop_tactics_to_zero(monkeypatch, django_user_model):
    from guilds.models import Guild
    from guilds.services.technology import build_guild_troop_tech_levels

    founder = django_user_model.objects.create_user(username="tech_founder_troop_mapping_zero", password="pass")
    guild = Guild.objects.create(name="TechTroopMappingZeroGuild", founder=founder)

    monkeypatch.setattr(
        "guilds.services.technology.player_technology_service.load_technology_templates",
        lambda: {
            "technologies": [
                {"key": "dao_attack", "troop_class": "dao", "max_level": 5},
                {"key": "dao_hp", "troop_class": "dao", "max_level": 8},
            ]
        },
    )

    assert build_guild_troop_tech_levels(guild) == {
        "dao_attack": 0,
        "dao_hp": 0,
    }


@pytest.mark.django_db
def test_legacy_military_study_row_no_longer_grants_guest_bonuses(django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import get_tech_bonus

    founder = django_user_model.objects.create_user(username="tech_founder_legacy_military", password="pass")
    guild = Guild.objects.create(name="TechGuildLegacyMilitary", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="military_study", level=5)

    assert get_tech_bonus(guild, "guest_force") == pytest.approx(0.0)
    assert get_tech_bonus(guild, "guest_intellect") == pytest.approx(0.0)
    assert get_tech_bonus(guild, "guest_defense") == pytest.approx(0.0)


@pytest.mark.django_db
def test_apply_guild_bonus_to_guest_and_troop(django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import apply_guild_bonus_to_guest, apply_guild_bonus_to_troop

    founder = django_user_model.objects.create_user(username="tech_founder3", password="pass")
    guild = Guild.objects.create(name="TechGuild3", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", level=5)

    user_no_guild = SimpleNamespace()
    guest_no_guild = SimpleNamespace(
        force=100,
        intellect=100,
        defense=100,
        manor=SimpleNamespace(user=user_no_guild),
    )
    assert apply_guild_bonus_to_guest(guest_no_guild) == {"force": 100, "intellect": 100, "defense": 100}

    user_in_guild = SimpleNamespace(guild_membership=SimpleNamespace(is_active=True, guild=guild))
    guest_in_guild = SimpleNamespace(
        force=100,
        intellect=100,
        defense=100,
        manor=SimpleNamespace(user=user_in_guild),
    )
    assert apply_guild_bonus_to_guest(guest_in_guild) == {"force": 100, "intellect": 100, "defense": 100}

    troop_stats = {"attack": 100, "defense": 100, "hp": 100}
    assert apply_guild_bonus_to_troop(troop_stats, user_no_guild) == troop_stats

    assert apply_guild_bonus_to_troop(troop_stats, user_in_guild) == troop_stats


@pytest.mark.django_db
def test_apply_guild_bonus_to_guest_supports_defense_stat_field(django_user_model):
    from guilds.models import Guild
    from guilds.services.technology import apply_guild_bonus_to_guest

    founder = django_user_model.objects.create_user(username="tech_founder_defense_stat", password="pass")
    guild = Guild.objects.create(name="TechGuildDefenseStat", founder=founder)

    user_in_guild = SimpleNamespace(guild_membership=SimpleNamespace(is_active=True, guild=guild))
    guest_in_guild = SimpleNamespace(
        force=100,
        intellect=100,
        defense_stat=100,
        manor=SimpleNamespace(user=user_in_guild),
    )

    assert apply_guild_bonus_to_guest(guest_in_guild) == {"force": 100, "intellect": 100, "defense": 100}


@pytest.mark.django_db
def test_load_ordered_technologies_projects_legacy_troop_tactics_runtime_max_level(django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.views.helpers import load_ordered_technologies

    founder = django_user_model.objects.create_user(username="tech_founder_view_projection", password="pass")
    guild = Guild.objects.create(name="TechViewProjectionGuild", founder=founder)
    tech = GuildTechnology.objects.create(
        guild=guild, tech_key="troop_tactics", category="combat", level=5, max_level=5
    )

    technologies = load_ordered_technologies(guild)
    projected = next(item for item in technologies if item.tech_key == "troop_tactics")

    assert projected.effective_max_level == 10
    tech.refresh_from_db()
    assert tech.max_level == 5


@pytest.mark.django_db
def test_guild_technology_can_upgrade_uses_effective_runtime_max_level(django_user_model):
    from guilds.models import Guild, GuildTechnology

    founder = django_user_model.objects.create_user(username="tech_founder_runtime_property", password="pass")
    guild = Guild.objects.create(name="TechRuntimePropertyGuild", founder=founder)
    tech = GuildTechnology.objects.create(
        guild=guild, tech_key="troop_tactics", category="combat", level=5, max_level=5
    )

    assert tech.effective_max_level == 10
    assert tech.can_upgrade is True


@pytest.mark.django_db
def test_apply_guild_bonus_to_troop_uses_mapped_personal_tech_levels(monkeypatch, django_user_model):
    from guilds.models import Guild
    from guilds.services.technology import apply_guild_bonus_to_troop

    founder = django_user_model.objects.create_user(username="tech_founder_troop_stats_mapping", password="pass")
    guild = Guild.objects.create(name="TechTroopStatsMappingGuild", founder=founder)
    user_in_guild = SimpleNamespace(guild_membership=SimpleNamespace(is_active=True, guild=guild))

    monkeypatch.setattr(
        "guilds.services.technology.build_guild_troop_tech_levels",
        lambda _guild: {"gong_attack": 2, "gong_hp": 1},
    )

    troop_stats = {"troop_key": "archer", "attack": 100, "defense": 100, "hp": 100}
    assert apply_guild_bonus_to_troop(troop_stats, user_in_guild) == {
        "attack": 120,
        "defense": 100,
        "hp": 110,
    }


@pytest.mark.django_db
def test_normalize_guild_technology_rows_migration_backfills_troop_tactics_and_removes_military_study(
    django_user_model,
):
    from django.apps import apps

    from guilds.models import Guild, GuildTechnology

    migration_module = importlib.import_module("guilds.migrations.0017_normalize_guild_technology_rows")

    founder = django_user_model.objects.create_user(username="tech_founder_migration_normalize", password="pass")
    guild = Guild.objects.create(name="TechMigrationNormalizeGuild", founder=founder)
    troop_tactics = GuildTechnology.objects.create(
        guild=guild,
        tech_key="troop_tactics",
        category="combat",
        level=5,
        max_level=5,
    )
    GuildTechnology.objects.create(
        guild=guild,
        tech_key="military_study",
        category="combat",
        level=3,
        max_level=5,
    )

    migration_module.normalize_guild_technology_rows(apps, None)

    troop_tactics.refresh_from_db()
    assert troop_tactics.max_level == 10
    assert not GuildTechnology.objects.filter(guild=guild, tech_key="military_study").exists()


@pytest.mark.django_db
def test_troop_tactics_display_meta_uses_mapping_copy():
    from guilds.views.technology import _build_tech_display_meta

    display_meta = _build_tech_display_meta(SimpleNamespace(tech_key="troop_tactics", level=0, max_level=10))

    assert display_meta["description"] == "可以增强帮会战斗中护院的能力"


@pytest.mark.django_db
def test_upgrade_technology_happy_path(monkeypatch, django_user_model):
    from gameplay.services.manor.core import ensure_manor
    from guilds.models import Guild, GuildResourceLog, GuildTechnology
    from guilds.services.technology import upgrade_technology

    # Make permission check deterministic.
    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )

    announcements: list[str] = []
    monkeypatch.setattr(
        "guilds.services.technology.create_announcement",
        lambda _guild, _type, content: announcements.append(content),
    )

    operator = django_user_model.objects.create_user(username="tech_operator", password="pass")
    ensure_manor(operator)

    founder = django_user_model.objects.create_user(username="tech_founder4", password="pass")
    guild = Guild.objects.create(name="TechGuild4", founder=founder, silver=999999, grain=0, gold_bar=0)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=0, max_level=5)
    _seed_basic_tech_upgrade_warehouse_costs(guild)

    upgrade_technology(guild, "equipment_forge", operator)

    tech.refresh_from_db()
    guild.refresh_from_db()
    assert tech.level == 1
    assert guild.silver < 999999
    assert GuildResourceLog.objects.filter(guild=guild, action="tech_upgrade").exists()
    assert announcements


@pytest.mark.django_db
def test_upgrade_technology_spends_grain_and_gold_bar_from_warehouse(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology, GuildWarehouse
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *_a, **_k: None)

    operator = django_user_model.objects.create_user(username="tech_operator_warehouse_cost", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder_warehouse_cost", password="pass")
    guild = Guild.objects.create(name="TechWarehouseCost", founder=founder, silver=10000, grain=0, gold_bar=0)
    GuildTechnology.objects.create(guild=guild, tech_key="march_speed", level=0, max_level=5)
    grain_row = GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=6000, contribution_cost=2)
    gold_bar_row = GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=4, contribution_cost=50)

    upgrade_technology(guild, "march_speed", operator)

    guild.refresh_from_db()
    grain_row.refresh_from_db()
    gold_bar_row.refresh_from_db()

    assert guild.silver == 0
    assert guild.grain == 0
    assert guild.gold_bar == 0
    assert grain_row.quantity == 1000
    assert grain_row.total_exchanged == 5000
    assert gold_bar_row.quantity == 1
    assert gold_bar_row.total_exchanged == 3


@pytest.mark.django_db
def test_upgrade_technology_uses_runtime_troop_tactics_cap_for_legacy_rows(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *_a, **_k: None)

    operator = django_user_model.objects.create_user(username="tech_operator_runtime_troop_cap", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder_runtime_troop_cap", password="pass")
    guild = Guild.objects.create(name="TechRuntimeTroopCapGuild", founder=founder, silver=999999, grain=0, gold_bar=0)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", level=5, max_level=5)
    _seed_basic_tech_upgrade_warehouse_costs(guild)

    upgrade_technology(guild, "troop_tactics", operator)

    tech.refresh_from_db()
    assert tech.level == 6


@pytest.mark.django_db
def test_upgrade_technology_keeps_success_when_announcement_fails(monkeypatch, django_user_model):
    from gameplay.services.manor.core import ensure_manor
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )

    operator = django_user_model.objects.create_user(username="tech_operator_announce_fail", password="pass")
    ensure_manor(operator)

    founder = django_user_model.objects.create_user(username="tech_founder_announce_fail", password="pass")
    guild = Guild.objects.create(
        name="TechGuildAnnounceFail",
        founder=founder,
        silver=999999,
        grain=0,
        gold_bar=0,
    )
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=0, max_level=5)
    _seed_basic_tech_upgrade_warehouse_costs(guild)

    monkeypatch.setattr(
        "guilds.services.technology.create_announcement",
        lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("announcement down")),
    )
    monkeypatch.setattr(
        "guilds.services.technology.Manor.objects.filter", lambda *_a, **_k: SimpleNamespace(first=lambda: None)
    )

    upgrade_technology(guild, "equipment_forge", operator)

    tech.refresh_from_db()
    assert tech.level == 1


@pytest.mark.django_db
def test_upgrade_technology_programming_error_in_announcement_bubbles_up(monkeypatch, django_user_model):
    from gameplay.services.manor.core import ensure_manor
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )

    operator = django_user_model.objects.create_user(username="tech_operator_announce_bug", password="pass")
    ensure_manor(operator)

    founder = django_user_model.objects.create_user(username="tech_founder_announce_bug", password="pass")
    guild = Guild.objects.create(
        name="TechGuildAnnounceBug",
        founder=founder,
        silver=999999,
        grain=0,
        gold_bar=0,
    )
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=0, max_level=5)
    _seed_basic_tech_upgrade_warehouse_costs(guild)

    monkeypatch.setattr(
        "guilds.services.technology.create_announcement",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("broken guild tech announcement contract")),
    )
    monkeypatch.setattr(
        "guilds.services.technology.Manor.objects.filter", lambda *_a, **_k: SimpleNamespace(first=lambda: None)
    )

    with pytest.raises(AssertionError, match="broken guild tech announcement contract"):
        upgrade_technology(guild, "equipment_forge", operator)

    tech.refresh_from_db()
    assert tech.level == 1


@pytest.mark.django_db
def test_upgrade_technology_permission_denied(monkeypatch, django_user_model):
    from core.exceptions import GuildTechnologyError
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=False),
    )

    operator = django_user_model.objects.create_user(username="tech_operator2", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder5", password="pass")
    guild = Guild.objects.create(name="TechGuild5", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=0, max_level=5)

    with pytest.raises(GuildTechnologyError, match="只有帮主和管理员"):
        upgrade_technology(guild, "equipment_forge", operator)


@pytest.mark.django_db
def test_upgrade_removed_military_study_is_rejected(monkeypatch, django_user_model):
    from core.exceptions import GuildTechnologyError
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )

    operator = django_user_model.objects.create_user(username="tech_operator_removed_military", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder_removed_military", password="pass")
    guild = Guild.objects.create(name="TechGuildRemovedMilitary", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="military_study", level=0, max_level=5)

    with pytest.raises(GuildTechnologyError, match="科技不存在"):
        upgrade_technology(guild, "military_study", operator)


@pytest.mark.django_db
def test_upgrade_technology_missing_membership_is_wrapped_as_guild_technology_error(monkeypatch, django_user_model):
    from core.exceptions import GuildMembershipError, GuildTechnologyError
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: (_ for _ in ()).throw(GuildMembershipError("只有帮主和管理员可以升级科技")),
    )

    operator = django_user_model.objects.create_user(username="tech_operator_missing_membership", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder_missing_membership", password="pass")
    guild = Guild.objects.create(name="TechGuildMissingMembership", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=0, max_level=5)

    with pytest.raises(GuildTechnologyError, match="只有帮主和管理员可以升级科技"):
        upgrade_technology(guild, "equipment_forge", operator)


@pytest.mark.django_db
def test_upgrade_technology_insufficient_resources(monkeypatch, django_user_model):
    from core.exceptions import GuildTechnologyError
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "guilds.services.technology.Manor.objects.get", lambda *_a, **_k: SimpleNamespace(display_name="X")
    )

    operator = django_user_model.objects.create_user(username="tech_operator3", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder6", password="pass")
    guild = Guild.objects.create(name="TechGuild6", founder=founder, silver=0, grain=0, gold_bar=0)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="equipment_forge", level=0, max_level=5)
    _seed_basic_tech_upgrade_warehouse_costs(guild)

    with pytest.raises(GuildTechnologyError, match="银两不足"):
        upgrade_technology(guild, "equipment_forge", operator)

    tech.refresh_from_db()
    assert tech.level == 0


@pytest.mark.django_db
def test_upgrade_new_guild_capacity_tech_consumes_red_ruby(monkeypatch, django_user_model):
    from guilds.models import Guild, GuildResourceLog, GuildTechnology, GuildWarehouse
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *_a, **_k: None)

    operator = django_user_model.objects.create_user(username="tech_operator_red_ruby", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder_red_ruby", password="pass")
    guild = Guild.objects.create(name="TechGuildRedRuby", founder=founder, silver=0, grain=0, gold_bar=0)
    tech = GuildTechnology.objects.create(guild=guild, tech_key="guild_lineup_capacity", level=0, max_level=5)
    ruby = GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=6, contribution_cost=0)

    upgrade_technology(guild, "guild_lineup_capacity", operator)

    tech.refresh_from_db()
    guild.refresh_from_db()
    ruby.refresh_from_db()

    assert tech.level == 1
    assert ruby.quantity == 1
    assert guild.silver == 0
    assert guild.grain == 0
    assert guild.gold_bar == 0
    assert GuildResourceLog.objects.filter(
        guild=guild,
        action="tech_upgrade",
        note__contains="红宝石×5",
    ).exists()


@pytest.mark.django_db
def test_upgrade_mysticism_uses_explicit_costs_and_stops_at_level_three(monkeypatch, django_user_model):
    from core.exceptions import GuildTechnologyError
    from guilds.models import Guild, GuildResourceLog, GuildTechnology, GuildWarehouse
    from guilds.services.technology import calculate_tech_upgrade_cost, upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *_a, **_k: None)

    operator = django_user_model.objects.create_user(username="tech_operator_mysticism", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder_mysticism", password="pass")
    guild = Guild.objects.create(name="TechGuildMysticism", founder=founder, silver=0, grain=0, gold_bar=0)
    tech = GuildTechnology.objects.create(
        guild=guild,
        tech_key="mysticism",
        category="production",
        level=0,
        max_level=3,
    )
    GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=800, contribution_cost=0)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=350, contribution_cost=0)

    assert calculate_tech_upgrade_cost("mysticism", 0) == {"red_ruby": 200}
    assert calculate_tech_upgrade_cost("mysticism", 1) == {"red_ruby": 300, "gold_bar": 150}
    assert calculate_tech_upgrade_cost("mysticism", 2) == {"red_ruby": 300, "gold_bar": 200}

    upgrade_technology(guild, "mysticism", operator)

    tech.refresh_from_db()
    assert tech.level == 1
    assert GuildWarehouse.objects.get(guild=guild, item_key="red_ruby").quantity == 600
    assert GuildWarehouse.objects.get(guild=guild, item_key="gold_bar").quantity == 350
    assert GuildResourceLog.objects.filter(
        guild=guild,
        action="tech_upgrade",
        note__contains="神秘学至1级（消耗红宝石×200）",
    ).exists()

    upgrade_technology(guild, "mysticism", operator)

    tech.refresh_from_db()
    assert tech.level == 2
    assert GuildWarehouse.objects.get(guild=guild, item_key="red_ruby").quantity == 300
    assert GuildWarehouse.objects.get(guild=guild, item_key="gold_bar").quantity == 200
    assert GuildResourceLog.objects.filter(
        guild=guild,
        action="tech_upgrade",
        gold_bar_change=-150,
        note__contains="神秘学至2级（消耗红宝石×300、金条×150）",
    ).exists()

    upgrade_technology(guild, "mysticism", operator)

    tech.refresh_from_db()
    assert tech.level == 3
    assert not GuildWarehouse.objects.filter(guild=guild, item_key__in=("red_ruby", "gold_bar")).exists()
    assert GuildResourceLog.objects.filter(
        guild=guild,
        action="tech_upgrade",
        gold_bar_change=-200,
        note__contains="神秘学至3级（消耗红宝石×300、金条×200）",
    ).exists()

    with pytest.raises(GuildTechnologyError, match="科技已达最高等级"):
        upgrade_technology(guild, "mysticism", operator)


@pytest.mark.django_db
def test_upgrade_mysticism_does_not_partially_spend_when_gold_bars_are_insufficient(
    monkeypatch,
    django_user_model,
):
    from core.exceptions import GuildTechnologyError
    from guilds.models import Guild, GuildTechnology, GuildWarehouse
    from guilds.services.technology import upgrade_technology

    monkeypatch.setattr(
        "guilds.services.technology.get_active_membership",
        lambda *_a, **_k: SimpleNamespace(can_manage=True),
    )
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *_a, **_k: None)

    operator = django_user_model.objects.create_user(username="tech_operator_mysticism_short", password="pass")
    founder = django_user_model.objects.create_user(username="tech_founder_mysticism_short", password="pass")
    guild = Guild.objects.create(name="TechGuildMysticismShort", founder=founder)
    tech = GuildTechnology.objects.create(
        guild=guild,
        tech_key="mysticism",
        category="production",
        level=1,
        max_level=3,
    )
    ruby = GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=300, contribution_cost=0)
    gold = GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=149, contribution_cost=0)

    with pytest.raises(GuildTechnologyError, match="帮会仓库金条不足，需要150"):
        upgrade_technology(guild, "mysticism", operator)

    tech.refresh_from_db()
    ruby.refresh_from_db()
    gold.refresh_from_db()
    assert tech.level == 1
    assert ruby.quantity == 300
    assert gold.quantity == 149


@pytest.mark.django_db
def test_mysticism_migration_backfills_existing_guilds_idempotently(django_user_model):
    from django.apps import apps

    from guilds.models import Guild, GuildTechnology

    migration_module = importlib.import_module("guilds.migrations.0023_add_mysticism_technology")
    founder = django_user_model.objects.create_user(username="tech_mysticism_migration", password="pass")
    guild = Guild.objects.create(name="MysticismMigrationGuild", founder=founder)
    legacy_guild = Guild.objects.create(name="MysticismLegacyGuild", founder=founder)
    GuildTechnology.objects.create(
        guild=legacy_guild,
        tech_key="mysticism",
        category="welfare",
        level=3,
        max_level=5,
    )

    migration_module.add_mysticism_technology(apps, None)
    migration_module.add_mysticism_technology(apps, None)

    tech = GuildTechnology.objects.get(guild=guild, tech_key="mysticism")
    assert tech.category == "production"
    assert tech.level == 0
    assert tech.max_level == 1
    assert GuildTechnology.objects.filter(guild=guild, tech_key="mysticism").count() == 1
    legacy_tech = GuildTechnology.objects.get(guild=legacy_guild, tech_key="mysticism")
    assert legacy_tech.category == "production"
    assert legacy_tech.level == 1
    assert legacy_tech.max_level == 1


@pytest.mark.django_db
def test_expand_mysticism_migration_updates_existing_level_cap_idempotently(django_user_model):
    from django.apps import apps

    from guilds.models import Guild, GuildTechnology

    migration_module = importlib.import_module("guilds.migrations.0024_expand_mysticism_technology")
    founder = django_user_model.objects.create_user(username="tech_mysticism_expand_migration", password="pass")
    guild = Guild.objects.create(name="MysticismExpandGuild", founder=founder)
    tech = GuildTechnology.objects.create(
        guild=guild,
        tech_key="mysticism",
        category="welfare",
        level=1,
        max_level=1,
    )

    migration_module.expand_mysticism_technology(apps, None)
    migration_module.expand_mysticism_technology(apps, None)

    tech.refresh_from_db()
    assert tech.category == "production"
    assert tech.level == 1
    assert tech.max_level == 3


@pytest.mark.django_db
def test_capacity_helpers_clamp_to_spec_maximums(django_user_model, monkeypatch):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import get_guild_dispatch_capacity, get_guild_lineup_capacity

    monkeypatch.setattr("guilds.constants.GUILD_BATTLE_LINEUP_LIMIT", 30)
    monkeypatch.setattr("guilds.constants.GUILD_DISPATCH_GUEST_BASE_LIMIT", 10)

    founder = django_user_model.objects.create_user(username="tech_founder_capacity_clamp", password="pass")
    guild = Guild.objects.create(name="TechCapacityClamp", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="guild_lineup_capacity", level=20, max_level=20)
    GuildTechnology.objects.create(guild=guild, tech_key="guild_dispatch_capacity", level=20, max_level=20)

    assert get_guild_lineup_capacity(guild) == 40
    assert get_guild_dispatch_capacity(guild) == 25
