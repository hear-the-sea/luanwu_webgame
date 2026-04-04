from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db.utils import DatabaseError


def _seed_basic_tech_upgrade_warehouse_costs(guild) -> None:
    from guilds.models import GuildWarehouse

    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=999999, contribution_cost=2)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=999999, contribution_cost=50)


@pytest.mark.django_db
def test_calculate_tech_upgrade_cost_defaults_and_scales():
    from guilds.services.technology import calculate_tech_upgrade_cost

    cost0 = calculate_tech_upgrade_cost("unknown", 0)
    assert cost0 == {"silver": 5000, "grain": 2000, "gold_bar": 1}

    cost2 = calculate_tech_upgrade_cost("unknown", 2)
    assert cost2 == {"silver": 20000, "grain": 8000, "gold_bar": 4}


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

    GuildTechnology.objects.create(guild=guild, tech_key="military_study", level=5)
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", level=4)
    GuildTechnology.objects.create(guild=guild, tech_key="resource_boost", level=2)
    GuildTechnology.objects.create(guild=guild, tech_key="march_speed", level=3)

    assert get_tech_bonus(guild, "guest_force") == pytest.approx(0.10)
    assert get_tech_bonus(guild, "guest_intellect") == pytest.approx(0.06)
    assert get_tech_bonus(guild, "guest_defense") == pytest.approx(0.02)

    assert get_tech_bonus(guild, "troop_attack") == pytest.approx(0.12)
    assert get_tech_bonus(guild, "troop_defense") == pytest.approx(0.06)
    assert get_tech_bonus(guild, "troop_hp") == pytest.approx(0.0)

    assert get_tech_bonus(guild, "resource_production") == pytest.approx(0.10)
    assert get_tech_bonus(guild, "march_speed") == pytest.approx(0.15)
    assert get_tech_bonus(guild, "unknown") == pytest.approx(0.0)


@pytest.mark.django_db
def test_apply_guild_bonus_to_guest_and_troop(django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import apply_guild_bonus_to_guest, apply_guild_bonus_to_troop

    founder = django_user_model.objects.create_user(username="tech_founder3", password="pass")
    guild = Guild.objects.create(name="TechGuild3", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="military_study", level=5)
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
    assert apply_guild_bonus_to_guest(guest_in_guild) == {"force": 110, "intellect": 106, "defense": 102}

    troop_stats = {"attack": 100, "defense": 100, "hp": 100}
    assert apply_guild_bonus_to_troop(troop_stats, user_no_guild) == troop_stats

    # troop_tactics level=5 -> attack +15%, defense +9%, hp +5%
    # Note: implementation truncates via int().
    assert apply_guild_bonus_to_troop(troop_stats, user_in_guild) == {"attack": 114, "defense": 109, "hp": 105}


@pytest.mark.django_db
def test_apply_guild_bonus_to_guest_supports_defense_stat_field(django_user_model):
    from guilds.models import Guild, GuildTechnology
    from guilds.services.technology import apply_guild_bonus_to_guest

    founder = django_user_model.objects.create_user(username="tech_founder_defense_stat", password="pass")
    guild = Guild.objects.create(name="TechGuildDefenseStat", founder=founder)
    GuildTechnology.objects.create(guild=guild, tech_key="military_study", level=5)

    user_in_guild = SimpleNamespace(guild_membership=SimpleNamespace(is_active=True, guild=guild))
    guest_in_guild = SimpleNamespace(
        force=100,
        intellect=100,
        defense_stat=100,
        manor=SimpleNamespace(user=user_in_guild),
    )

    assert apply_guild_bonus_to_guest(guest_in_guild) == {"force": 110, "intellect": 106, "defense": 102}


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
    ruby = GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=3, contribution_cost=0)

    upgrade_technology(guild, "guild_lineup_capacity", operator)

    tech.refresh_from_db()
    guild.refresh_from_db()
    ruby.refresh_from_db()

    assert tech.level == 1
    assert ruby.quantity == 2
    assert guild.silver == 0
    assert guild.grain == 0
    assert guild.gold_bar == 0
    assert GuildResourceLog.objects.filter(
        guild=guild,
        action="tech_upgrade",
        note__contains="红宝石x1",
    ).exists()


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
