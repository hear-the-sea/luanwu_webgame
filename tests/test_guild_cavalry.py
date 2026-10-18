from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import GuildWarehouseError
from gameplay.models import PlayerTroop, TroopRecruitment
from gameplay.services.manor.core import ensure_manor
from gameplay.services.manor.troop_capacity import MANOR_TROOP_CAPACITY
from guilds.models import (
    Guild,
    GuildCavalryTraining,
    GuildExchangeLog,
    GuildMember,
    GuildResourceLog,
    GuildTechnology,
    GuildWarehouse,
)
from guilds.services.cavalry import (
    CAVALRY_ITEM_KEY,
    finalize_guild_cavalry_training,
    get_guild_cavalry_recruitment_state,
    recruit_guild_cavalry,
)
from guilds.services.warehouse import exchange_item, get_exchange_logs, get_warehouse_items

pytestmark = pytest.mark.django_db


@pytest.fixture
def cavalry_world(django_user_model):
    user = django_user_model.objects.create_user(username="cavalry_leader")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="铁骑帮", founder=user, silver=500000, grain=999999, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", current_contribution=10000)
    GuildTechnology.objects.create(guild=guild, tech_key="wolf_bloodthirsty_cavalry", level=1, max_level=1)
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=1000000, contribution_cost=1)
    template, _ = TroopTemplate.objects.get_or_create(key=CAVALRY_ITEM_KEY, defaults={"name": "嗜血铁骑"})
    return guild, member, manor, template


def finish_training(guild):
    GuildCavalryTraining.objects.filter(guild=guild, completed_at__isnull=True).update(complete_at=timezone.now())
    return finalize_guild_cavalry_training(guild.pk)


def test_manager_recruits_with_only_guild_silver_and_warehouse_grain(cavalry_world):
    guild, member, manor, _template = cavalry_world
    personal = (manor.silver, manor.grain, manor.retainer_count)
    result = recruit_guild_cavalry(guild, member.user, 10)
    guild.refresh_from_db()
    manor.refresh_from_db()
    assert {key: result[key] for key in ("quantity", "silver_cost", "grain_cost", "duration")} == {
        "quantity": 10,
        "silver_cost": 15000,
        "grain_cost": 50000,
        "duration": 300,
    }
    assert guild.silver == 485000
    assert GuildWarehouse.objects.get(guild=guild, item_key="grain").quantity == 950000
    assert not GuildWarehouse.objects.filter(guild=guild, item_key=CAVALRY_ITEM_KEY).exists()
    training = GuildCavalryTraining.objects.get(guild=guild)
    assert training.quantity == 10 and training.completed_at is None
    assert (training.complete_at - training.started_at).total_seconds() == 300
    assert result["complete_at"] == training.complete_at
    assert not PlayerTroop.objects.filter(manor=manor).exists()
    assert not TroopRecruitment.objects.filter(manor=manor).exists()
    assert (manor.silver, manor.grain, manor.retainer_count) == personal
    log = GuildResourceLog.objects.get(guild=guild, action="troop_recruit")
    assert (log.silver_change, log.grain_change, log.related_user_id) == (-15000, -50000, member.user_id)


@pytest.mark.parametrize("position", ["leader", "admin"])
def test_only_managers_can_recruit(cavalry_world, position):
    guild, member, _manor, _template = cavalry_world
    member.position = position
    member.save(update_fields=["position"])
    recruit_guild_cavalry(guild, member.user, 1)
    assert finish_training(guild) == 1
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == 1


@pytest.mark.parametrize("failure", ["member", "inactive_member", "inactive_guild", "locked_tech", "silver", "grain"])
def test_invalid_recruitment_does_not_charge_or_create_stock(cavalry_world, failure):
    guild, member, _manor, _template = cavalry_world
    if failure == "member":
        member.position = "member"
        member.save(update_fields=["position"])
    elif failure == "inactive_member":
        member.is_active = False
        member.save(update_fields=["is_active"])
    elif failure == "inactive_guild":
        guild.is_active = False
        guild.save(update_fields=["is_active"])
    elif failure == "locked_tech":
        GuildTechnology.objects.filter(guild=guild).update(level=0)
    elif failure == "silver":
        Guild.objects.filter(pk=guild.pk).update(silver=0)
    elif failure == "grain":
        GuildWarehouse.objects.filter(guild=guild, item_key="grain").update(quantity=0)
    silver_before = Guild.objects.get(pk=guild.pk).silver
    grain_before = GuildWarehouse.objects.get(guild=guild, item_key="grain").quantity
    with pytest.raises(GuildWarehouseError):
        recruit_guild_cavalry(guild, member.user, 1)
    assert Guild.objects.get(pk=guild.pk).silver == silver_before
    assert GuildWarehouse.objects.get(guild=guild, item_key="grain").quantity == grain_before
    assert not GuildWarehouse.objects.filter(guild=guild, item_key=CAVALRY_ITEM_KEY).exists()


def test_regular_member_exchanges_existing_stock_directly_for_troops(cavalry_world):
    guild, member, manor, template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 10)
    finish_training(guild)
    member.position = "member"
    member.save(update_fields=["position"])
    exchange_item(member, CAVALRY_ITEM_KEY, 4)
    member.refresh_from_db()
    assert PlayerTroop.objects.get(manor=manor, troop_template=template).count == 4
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == 6
    assert member.current_contribution == 9988
    assert member.daily_exchange_count == 1
    log = GuildExchangeLog.objects.get(member=member)
    assert (log.quantity, log.contribution_cost) == (4, 12)
    assert get_exchange_logs(guild)[0].item_display_name == "嗜血铁骑"


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "2", 2001])
def test_recruit_and_exchange_reject_invalid_quantities(cavalry_world, quantity):
    guild, member, _manor, _template = cavalry_world
    with pytest.raises(GuildWarehouseError):
        recruit_guild_cavalry(guild, member.user, quantity)
    with pytest.raises(GuildWarehouseError):
        exchange_item(member, CAVALRY_ITEM_KEY, quantity)


@pytest.mark.parametrize("failure", ["capacity", "reserved_capacity", "contribution", "stock", "daily", "inactive"])
def test_failed_exchange_is_atomic(cavalry_world, failure):
    guild, member, manor, template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 10)
    finish_training(guild)
    if failure == "capacity":
        PlayerTroop.objects.create(manor=manor, troop_template=template, count=MANOR_TROOP_CAPACITY)
    elif failure == "reserved_capacity":
        TroopRecruitment.objects.create(
            manor=manor,
            troop_key="dao_ke",
            troop_name="刀客",
            quantity=MANOR_TROOP_CAPACITY,
            status="recruiting",
            base_duration=60,
            actual_duration=60,
            complete_at=timezone.now(),
        )
    elif failure == "contribution":
        GuildMember.objects.filter(pk=member.pk).update(current_contribution=0)
    elif failure == "stock":
        GuildWarehouse.objects.filter(guild=guild, item_key=CAVALRY_ITEM_KEY).update(quantity=0)
    elif failure == "daily":
        GuildMember.objects.filter(pk=member.pk).update(daily_exchange_count=10)
    elif failure == "inactive":
        GuildMember.objects.filter(pk=member.pk).update(is_active=False)
    member.refresh_from_db()
    stock_before = GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity
    contribution_before = member.current_contribution
    with pytest.raises(GuildWarehouseError):
        exchange_item(member, CAVALRY_ITEM_KEY, 1)
    member.refresh_from_db()
    assert member.current_contribution == contribution_before
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == stock_before
    assert not GuildExchangeLog.objects.filter(member=member).exists()


@pytest.mark.parametrize("legacy_price", [0, 15, 150])
def test_legacy_price_stock_uses_runtime_contribution_price(cavalry_world, legacy_price):
    guild, member, _manor, _template = cavalry_world
    GuildWarehouse.objects.create(guild=guild, item_key=CAVALRY_ITEM_KEY, quantity=3, contribution_cost=legacy_price)
    items = get_warehouse_items(guild, member=member)["items"]
    cavalry = next(item for item in items if item.item_key == CAVALRY_ITEM_KEY)
    assert cavalry.display_name == "嗜血铁骑"
    assert cavalry.can_exchange and cavalry.exchange_unit_cost == 3
    exchange_item(member, CAVALRY_ITEM_KEY, 1)
    member.refresh_from_db()
    assert member.current_contribution == 9997


def test_full_manor_disables_exchange_on_warehouse_page(cavalry_world):
    guild, member, manor, template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 1)
    finish_training(guild)
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=MANOR_TROOP_CAPACITY)
    cavalry = next(
        item for item in get_warehouse_items(guild, member=member)["items"] if item.item_key == CAVALRY_ITEM_KEY
    )
    assert not cavalry.can_exchange and cavalry.exchange_max_quantity == 0
    assert "容量" in cavalry.exchange_disabled_reason


def test_recruitment_state_matches_resources_and_permissions(cavalry_world):
    guild, member, _manor, _template = cavalry_world
    state = get_guild_cavalry_recruitment_state(guild, member)
    assert state["can_recruit"] and state["max_recruitable_quantity"] == 200
    member.position = "member"
    assert not get_guild_cavalry_recruitment_state(guild, member)["can_recruit"]


def test_technology_popup_training_and_warehouse_exchange_flow(cavalry_world, client):
    guild, member, manor, template = cavalry_world
    client.force_login(member.user)
    response = client.get(reverse("guilds:warehouse"))
    assert response.status_code == 200 and "训练人数" not in response.content.decode()
    response = client.get(reverse("guilds:technology"))
    assert response.status_code == 200 and "训练人数" in response.content.decode()
    assert "data-cavalry-training-form" in response.content.decode()
    response = client.post(reverse("guilds:recruit_cavalry"), {"quantity": "2"})
    assert response.status_code == 302
    assert response.url == reverse("guilds:technology") + "?node=wolf_bloodthirsty_cavalry"
    assert GuildCavalryTraining.objects.get(guild=guild).quantity == 2
    member.position = "member"
    member.save(update_fields=["position"])
    response = client.post(reverse("guilds:recruit_cavalry"), {"quantity": "1"})
    assert response.status_code == 302
    assert GuildCavalryTraining.objects.filter(guild=guild).count() == 1
    finish_training(guild)
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == 2
    response = client.post(reverse("guilds:exchange_item", args=[CAVALRY_ITEM_KEY]), {"quantity": "2"})
    assert response.status_code == 302
    assert PlayerTroop.objects.get(manor=manor, troop_template=template).count == 2


def test_exchange_delivery_failure_rolls_back_both_contribution_and_stock(cavalry_world, monkeypatch):
    guild, member, manor, _template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 10)
    finish_training(guild)

    def fail_delivery(*_args, **_kwargs):
        raise GuildWarehouseError("发放失败")

    monkeypatch.setattr("guilds.services.cavalry._add_troops_batch", fail_delivery)
    with pytest.raises(GuildWarehouseError, match="发放失败"):
        exchange_item(member, CAVALRY_ITEM_KEY, 4)
    member.refresh_from_db()
    assert member.current_contribution == 10000 and member.daily_exchange_count == 0
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == 10
    assert not PlayerTroop.objects.filter(manor=manor).exists()
    assert not GuildExchangeLog.objects.filter(member=member).exists()


def test_training_creation_failure_rolls_back_resources(cavalry_world, monkeypatch):
    guild, member, _manor, _template = cavalry_world

    def fail_training(*_args, **_kwargs):
        raise GuildWarehouseError("训练失败")

    monkeypatch.setattr("guilds.services.cavalry.GuildCavalryTraining.objects.create", fail_training)
    with pytest.raises(GuildWarehouseError, match="训练失败"):
        recruit_guild_cavalry(guild, member.user, 10)
    guild.refresh_from_db()
    assert guild.silver == 500000
    assert GuildWarehouse.objects.get(guild=guild, item_key="grain").quantity == 1000000
    assert not GuildResourceLog.objects.filter(guild=guild).exists()


def test_training_finishes_only_at_deadline_and_only_once(cavalry_world, monkeypatch):
    guild, member, _manor, _template = cavalry_world
    started = timezone.now()
    monkeypatch.setattr("guilds.services.cavalry.timezone.now", lambda: started)
    recruit_guild_cavalry(guild, member.user, 2)
    monkeypatch.setattr("guilds.services.cavalry.timezone.now", lambda: started + timedelta(seconds=59))
    assert finalize_guild_cavalry_training(guild.pk) == 0
    assert not GuildWarehouse.objects.filter(guild=guild, item_key=CAVALRY_ITEM_KEY).exists()
    monkeypatch.setattr("guilds.services.cavalry.timezone.now", lambda: started + timedelta(seconds=60))
    assert finalize_guild_cavalry_training(guild.pk) == 2
    assert finalize_guild_cavalry_training(guild.pk) == 0
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == 2
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).total_produced == 2


def test_duplicate_batch_does_not_charge_twice(cavalry_world):
    guild, member, _manor, _template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 2)
    with pytest.raises(GuildWarehouseError, match="已有一批"):
        recruit_guild_cavalry(guild, member.user, 2)
    guild.refresh_from_db()
    assert guild.silver == 497000
    assert GuildWarehouse.objects.get(guild=guild, item_key="grain").quantity == 990000
    assert GuildCavalryTraining.objects.filter(guild=guild).count() == 1
    assert not get_guild_cavalry_recruitment_state(guild, member)["can_recruit"]


def test_maximum_training_batch_is_2000_with_60000_second_duration(cavalry_world):
    guild, member, _manor, _template = cavalry_world
    Guild.objects.filter(pk=guild.pk).update(silver=3000000)
    GuildWarehouse.objects.filter(guild=guild, item_key="grain").update(quantity=10000000)
    result = recruit_guild_cavalry(guild, member.user, 2000)
    assert result["duration"] == 60000
    guild.refresh_from_db()
    assert guild.silver == 0
    assert not GuildWarehouse.objects.filter(guild=guild, item_key="grain", quantity__gt=0).exists()
    assert GuildCavalryTraining.objects.get(guild=guild).quantity == 2000


def test_failed_completion_remains_pending_and_retries_without_double_credit(cavalry_world, monkeypatch):
    from guilds.services import warehouse as warehouse_service

    guild, member, _manor, _template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 2)
    GuildCavalryTraining.objects.filter(guild=guild).update(complete_at=timezone.now())
    original = warehouse_service.add_item_to_warehouse

    def fail_after_credit(*args, **kwargs):
        original(*args, **kwargs)
        raise GuildWarehouseError("入库失败")

    monkeypatch.setattr(warehouse_service, "add_item_to_warehouse", fail_after_credit)
    with pytest.raises(GuildWarehouseError, match="入库失败"):
        finalize_guild_cavalry_training(guild.pk)
    assert GuildCavalryTraining.objects.get(guild=guild).completed_at is None
    assert not GuildWarehouse.objects.filter(guild=guild, item_key=CAVALRY_ITEM_KEY).exists()
    monkeypatch.setattr(warehouse_service, "add_item_to_warehouse", original)
    assert finalize_guild_cavalry_training(guild.pk) == 2


def test_scanner_recovers_missed_completion_and_worker_is_idempotent(cavalry_world):
    from guilds.tasks import complete_guild_cavalry_training, scan_guild_cavalry_trainings

    guild, member, _manor, _template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 2)
    assert complete_guild_cavalry_training.run(guild.pk) == 0
    GuildCavalryTraining.objects.filter(guild=guild).update(complete_at=timezone.now())
    assert scan_guild_cavalry_trainings.run() == 1
    assert scan_guild_cavalry_trainings.run() == 0
    assert complete_guild_cavalry_training.run(guild.pk) == 0
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == 2


def test_training_dispatch_occurs_after_commit(cavalry_world, monkeypatch, django_capture_on_commit_callbacks):
    guild, member, _manor, _template = cavalry_world
    scheduled = []
    monkeypatch.setattr("guilds.services.cavalry._schedule_cavalry_training", lambda *args: scheduled.append(args))
    with django_capture_on_commit_callbacks(execute=True):
        recruit_guild_cavalry(guild, member.user, 2)
        assert scheduled == []
    assert scheduled == [(guild.pk, 60)]


def test_training_rechecks_manager_permission_after_initial_lookup(cavalry_world, monkeypatch):
    from guilds.services import cavalry as cavalry_service

    guild, member, _manor, _template = cavalry_world
    original = cavalry_service.get_active_membership

    def demote_after_lookup(*args, **kwargs):
        membership = original(*args, **kwargs)
        GuildMember.objects.filter(pk=member.pk).update(position="member")
        return membership

    monkeypatch.setattr(cavalry_service, "get_active_membership", demote_after_lookup)
    with pytest.raises(GuildWarehouseError, match="只有帮主和管理员"):
        recruit_guild_cavalry(guild, member.user, 2)
    guild.refresh_from_db()
    assert guild.silver == 500000
    assert not GuildCavalryTraining.objects.filter(guild=guild).exists()


def test_technology_popup_hides_training_form_from_regular_members(cavalry_world, client):
    guild, member, _manor, _template = cavalry_world
    member.position = "member"
    member.save(update_fields=["position"])
    client.force_login(member.user)
    response = client.get(reverse("guilds:technology"))
    assert response.status_code == 200
    assert "仅帮主和管理员可开始训练" in response.content.decode()
    assert "data-cavalry-training-form" not in response.content.decode()


def test_opening_warehouse_settles_overdue_training(cavalry_world, client):
    guild, member, _manor, _template = cavalry_world
    recruit_guild_cavalry(guild, member.user, 2)
    GuildCavalryTraining.objects.filter(guild=guild).update(complete_at=timezone.now())
    client.force_login(member.user)
    assert client.get(reverse("guilds:warehouse")).status_code == 200
    assert GuildWarehouse.objects.get(guild=guild, item_key=CAVALRY_ITEM_KEY).quantity == 2


def test_cavalry_migration_preserves_levels_and_reopens_completed_wolf_route(cavalry_world):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    guild, _member, _manor, _template = cavalry_world
    GuildTechnology.objects.create(guild=guild, tech_key="wolf_force", level=5, max_level=5)
    GuildTechnology.objects.create(guild=guild, tech_key="wolf_berserk", level=3, max_level=3)
    Guild.objects.filter(pk=guild.pk).update(
        completed_technology_routes=["wolf", "hero"], active_technology_route="sheep"
    )
    migration = import_module("guilds.migrations.0032_cavalry_recruitment_and_growth")
    migration.update_cavalry_technologies(apps, SimpleNamespace(connection=connection))
    force = GuildTechnology.objects.get(guild=guild, tech_key="wolf_force")
    berserk = GuildTechnology.objects.get(guild=guild, tech_key="wolf_berserk")
    guild.refresh_from_db()
    assert (force.level, force.max_level, berserk.level, berserk.max_level) == (5, 10, 3, 5)
    assert guild.completed_technology_routes == ["hero"] and guild.active_technology_route == "sheep"


def test_preemptive_migration_updates_existing_cavalry_template(cavalry_world):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    _guild, _member, _manor, template = cavalry_world
    TroopTemplate.objects.filter(pk=template.pk).update(priority=0)
    migration = import_module("battle.migrations.0010_cavalry_preemptive_attack")
    migration.enable_cavalry_preemptive_attack(apps, SimpleNamespace(connection=connection))
    template.refresh_from_db()
    assert template.priority == -1
    assert "先攻回合" in template.description and "固定概率" in template.description


def test_charge_rename_preserves_existing_template_settings(cavalry_world):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    _guild, _member, _manor, template = cavalry_world
    TroopTemplate.objects.filter(pk=template.pk).update(priority=-1, description="保留设定；浴血冲阵固定概率。")
    migration = import_module("battle.migrations.0011_rename_cavalry_charge")
    migration.rename_cavalry_charge(apps, SimpleNamespace(connection=connection))
    template.refresh_from_db()
    assert template.description == "保留设定；铁骑冲阵固定概率。"
    assert template.priority == -1


def test_mobility_migrations_match_yaml_without_changing_owned_troops(cavalry_world):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    from battle.troops import load_troop_templates_from_yaml

    _guild, _member, manor, template = cavalry_world
    TroopTemplate.objects.filter(pk=template.pk).update(
        base_attack=24, base_defense=11, base_hp=76, speed_bonus=12, priority=-1
    )
    owned = PlayerTroop.objects.create(manor=manor, troop_template=template, count=123)
    migration = import_module("battle.migrations.0012_cavalry_mobility_stats")
    migration.rebalance_cavalry_stats(apps, SimpleNamespace(connection=connection))
    template.refresh_from_db()
    assert template.base_attack == 15
    attack_migration = import_module("battle.migrations.0013_cavalry_attack_increase")
    attack_migration.increase_cavalry_attack(apps, SimpleNamespace(connection=connection))
    template.refresh_from_db()
    assert template.base_attack == 18
    base_attack_migration = import_module("battle.migrations.0014_cavalry_base_attack_17")
    base_attack_migration.set_cavalry_base_attack(apps, SimpleNamespace(connection=connection))
    training_migration = import_module("guilds.migrations.0033_cavalry_training")
    training_migration.update_cavalry_description(apps, SimpleNamespace(connection=connection))
    assets_migration = import_module("guilds.migrations.0036_guild_assets_copy")
    assets_migration.rename_guild_assets_copy(apps, SimpleNamespace(connection=connection))
    template.refresh_from_db()
    owned.refresh_from_db()
    yaml_template = load_troop_templates_from_yaml()[CAVALRY_ITEM_KEY]
    for field in ("base_attack", "base_defense", "base_hp", "speed_bonus", "description"):
        assert getattr(template, field) == yaml_template[field]
    assert template.priority == -1
    assert owned.count == 123 and owned.troop_template_id == template.pk
