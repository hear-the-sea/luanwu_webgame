"""联表权限校验不得把出征锁扩散到庄园、用户或模板。"""

import threading
from types import SimpleNamespace

import pytest
from django.db import close_old_connections, connection, transaction
from django.db.models import QuerySet

from battle.models import TroopTemplate
from core.exceptions import GuildValidationError
from gameplay.models import Manor, PlayerTroop
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildBattleLineupEntry, GuildHeroPoolEntry, GuildMember, GuildTroopStorage
from guilds.services import guild_dispatch, guild_troops, hero_pool


@pytest.fixture
def dispatch_lock_world(django_user_model):
    leader = django_user_model.objects.create_user(username="dispatch_lock_leader")
    manor = ensure_manor(leader)
    guild = Guild.objects.create(name="出征锁范围帮会", founder=leader)
    member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    template = GuestTemplate.objects.create(
        key="dispatch_lock_guest",
        name="出征门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )
    guest = Guest.objects.create(manor=manor, template=template)
    pool_entry = GuildHeroPoolEntry.objects.create(guild=guild, owner_member=member, source_guest=guest, slot_index=1)
    GuildBattleLineupEntry.objects.create(guild=guild, pool_entry=pool_entry, slot_index=1, selected_by=leader)
    return guild, member, manor, guest, pool_entry


@pytest.mark.django_db
def test_joined_guild_queries_lock_only_their_write_rows(dispatch_lock_world, monkeypatch):
    guild, member, manor, guest, pool_entry = dispatch_lock_world
    troop_template, _ = TroopTemplate.objects.get_or_create(key="dao_ke", defaults={"name": "刀客"})
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=10)
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=10)
    locks = []
    original_fetch_all = QuerySet._fetch_all

    def capture(queryset):
        if queryset.query.select_for_update and queryset.query.select_related and queryset._result_cache is None:
            locks.append((queryset.model, queryset.query.select_for_update_of))
        return original_fetch_all(queryset)

    monkeypatch.setattr(QuerySet, "_fetch_all", capture)
    with transaction.atomic():
        locked_guild = Guild.objects.select_for_update().get(pk=guild.pk)
        guild_dispatch.lock_manage_member(guild=locked_guild, operator=member.user, permission_label="出征")
        rows = guild_dispatch.load_dispatch_lineup_rows(guild=locked_guild, pool_entry_ids=[pool_entry.pk])
        assert [row.pool_entry.source_guest_id for row in rows] == [guest.pk]
        hero_pool.lock_guild_lineup_for_dispatch(locked_guild)
        replacement_guest = Guest.objects.create(manor=manor, template=guest.template)
        hero_pool.submit_hero_pool_entry(member, guest_id=replacement_guest.pk, slot_index=2)
        guild_troops.donate_troops(member=member, troop_key=troop_template.key, quantity=1)
        guild_troops.deduct_guild_troops(guild=locked_guild, loadout={troop_template.key: 1})
        guild_troops.apply_guild_troop_losses(
            guild=locked_guild,
            loadout={troop_template.key: 1},
            report=SimpleNamespace(losses={"attacker": {"casualties": [{"key": troop_template.key, "lost": 1}]}}),
            side="attacker",
        )

    assert (GuildBattleLineupEntry, ("self", "pool_entry", "pool_entry__source_guest")) in locks
    assert (GuildBattleLineupEntry, ("self", "pool_entry")) in locks
    for model in (GuildMember, Guest, GuildHeroPoolEntry, PlayerTroop, GuildTroopStorage):
        model_locks = [scope for locked_model, scope in locks if locked_model is model]
        assert model_locks, model
        assert all(scope == ("self",) for scope in model_locks), (model, model_locks)


@pytest.mark.django_db
@pytest.mark.parametrize("invalid_state", ["inactive_member", "foreign_manor"])
def test_dispatch_still_checks_membership_and_guest_ownership(dispatch_lock_world, django_user_model, invalid_state):
    guild, member, _manor, guest, pool_entry = dispatch_lock_world
    if invalid_state == "inactive_member":
        GuildMember.objects.filter(pk=member.pk).update(is_active=False)
    else:
        outsider = django_user_model.objects.create_user(username="dispatch_lock_outsider")
        Guest.objects.filter(pk=guest.pk).update(manor=ensure_manor(outsider))
    with transaction.atomic(), pytest.raises(GuildValidationError, match="出征只能从上阵门客"):
        locked_guild = Guild.objects.select_for_update().get(pk=guild.pk)
        guild_dispatch.load_dispatch_lineup_rows(guild=locked_guild, pool_entry_ids=[pool_entry.pk])


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("held_row", ["manor", "user", "template"])
def test_dispatch_does_not_wait_for_unrelated_rows_held_by_a_guild_waiter(
    dispatch_lock_world, django_user_model, held_row
):
    if connection.vendor != "mysql" or not connection.features.has_select_for_update_of:
        pytest.skip("requires MySQL 8 row locking with FOR UPDATE OF")
    guild, member, manor, guest, pool_entry = dispatch_lock_world
    blocker_model, blocker_pk = {
        "manor": (Manor, manor.pk),
        "user": (django_user_model, member.user_id),
        "template": (GuestTemplate, guest.template_id),
    }[held_row]
    blocker_has_row = threading.Event()
    dispatch_has_guild = threading.Event()
    blocker_attempting_guild = threading.Event()
    errors = []
    dispatched_guest_ids = []

    def worker(dispatching):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")
            with transaction.atomic():
                if dispatching:
                    assert blocker_has_row.wait(5), "blocker did not lock its row"
                    locked_guild = Guild.objects.select_for_update().get(pk=guild.pk)
                    dispatch_has_guild.set()
                    assert blocker_attempting_guild.wait(5), "blocker did not attempt its guild lock"
                    guild_dispatch.lock_manage_member(
                        guild=locked_guild, operator=member.user_id, permission_label="出征"
                    )
                    rows = guild_dispatch.load_dispatch_lineup_rows(guild=locked_guild, pool_entry_ids=[pool_entry.pk])
                    dispatched_guest_ids.extend(row.pool_entry.source_guest_id for row in rows)
                else:
                    blocker_model.objects.select_for_update().get(pk=blocker_pk)
                    blocker_has_row.set()
                    assert dispatch_has_guild.wait(5), "dispatch did not lock its guild"
                    blocker_attempting_guild.set()
                    Guild.objects.select_for_update().get(pk=guild.pk)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(dispatching,), daemon=True) for dispatching in (False, True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert dispatched_guest_ids == [guest.pk]
