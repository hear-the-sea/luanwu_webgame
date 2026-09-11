from __future__ import annotations

import pytest

from battle.combatants_pkg.guest_builder import build_guest_combatants, serialize_guest_for_report
from core.exceptions import GuestItemConfigurationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.arena.snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot
from gameplay.services.battle_snapshots import build_guest_battle_snapshot, build_guest_snapshot_proxies
from gameplay.services.inventory.guest_items import use_super_guest_disguise_card
from gameplay.services.inventory.guest_reset_helpers import apply_guest_template_reset
from gameplay.services.manor.core import ensure_manor
from guests.constants import SUPER_GUEST_DISGUISE_AVATAR_KEY, SUPER_GUEST_DISGUISE_AVATAR_PATH
from guests.models import Guest, GuestStatus, GuestTemplate


def _prepare_disguise_case(django_user_model, *, quantity: int = 1):
    user = django_user_model.objects.create_user(username="super_disguise_user", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="super_disguise_guest_tpl",
        name="易容测试门客",
        archetype="military",
        rarity="purple",
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=75,
        base_luck=50,
        base_hp=1200,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        status=GuestStatus.IDLE,
        force=160,
        agility=140,
    )
    item_template = ItemTemplate.objects.create(
        key="super_guest_disguise_card_test",
        name="超级门客易容卡",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        effect_payload={
            "action": "disguise_guest",
            "avatar_override": SUPER_GUEST_DISGUISE_AVATAR_KEY,
            "hp": 800,
            "force": 20,
            "agility": 20,
        },
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    return manor, guest, item


@pytest.mark.django_db
def test_super_guest_disguise_applies_stats_avatar_and_consumes_card(django_user_model):
    manor, guest, item = _prepare_disguise_case(django_user_model)
    old_max_hp = guest.max_hp
    old_current_hp = guest.current_hp

    result = use_super_guest_disguise_card(manor, item, guest.id)
    guest.refresh_from_db()

    assert guest.avatar_override_key == SUPER_GUEST_DISGUISE_AVATAR_KEY
    assert guest.super_disguise_applied is True
    assert guest.force == 180
    assert guest.agility == 160
    assert guest.max_hp == old_max_hp + 800
    assert guest.current_hp == min(old_max_hp + 800, old_current_hp + 800)
    assert result["avatar_override_key"] == SUPER_GUEST_DISGUISE_AVATAR_KEY
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_super_guest_disguise_cannot_stack_on_one_guest(django_user_model):
    manor, guest, item = _prepare_disguise_case(django_user_model, quantity=2)

    use_super_guest_disguise_card(manor, item, guest.id)

    with pytest.raises(GuestItemConfigurationError, match="已经使用过"):
        use_super_guest_disguise_card(manor, item, guest.id)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.force == 180
    assert guest.agility == 160
    assert item.quantity == 1


@pytest.mark.django_db
def test_super_guest_disguise_can_target_any_owned_guest_status(django_user_model):
    manor, guest, item = _prepare_disguise_case(django_user_model)
    guest.status = GuestStatus.WORKING
    guest.save(update_fields=["status"])

    use_super_guest_disguise_card(manor, item, guest.id)

    guest.refresh_from_db()
    assert guest.status == GuestStatus.WORKING
    assert guest.super_disguise_applied is True


@pytest.mark.django_db
def test_super_guest_disguise_avatar_is_carried_by_battle_and_arena_snapshots(django_user_model):
    manor, guest, item = _prepare_disguise_case(django_user_model)
    use_super_guest_disguise_card(manor, item, guest.id)
    guest.refresh_from_db()

    battle_snapshot = build_guest_battle_snapshot(guest, include_identity=False)
    assert battle_snapshot["avatar_url"].endswith(SUPER_GUEST_DISGUISE_AVATAR_PATH)
    battle_proxy = build_guest_snapshot_proxies([battle_snapshot])[0]
    combatant = build_guest_combatants([battle_proxy], side="attacker")[0]
    assert serialize_guest_for_report(combatant)["avatar_url"] == battle_snapshot["avatar_url"]

    arena_snapshot = build_entry_guest_snapshot(guest)
    assert arena_snapshot["avatar_url"] == battle_snapshot["avatar_url"]
    assert ArenaGuestSnapshotProxy(arena_snapshot).avatar_url == battle_snapshot["avatar_url"]


@pytest.mark.django_db
def test_guest_reset_clears_super_guest_disguise_state(django_user_model):
    manor, guest, item = _prepare_disguise_case(django_user_model)
    use_super_guest_disguise_card(manor, item, guest.id)
    guest.refresh_from_db()
    guest.level = 20
    guest.save(update_fields=["level"])

    apply_guest_template_reset(
        guest,
        target_template=guest.template,
        varied_attrs={
            "force": guest.template.base_attack,
            "intellect": guest.template.base_intellect,
            "defense": guest.template.base_defense,
            "agility": guest.template.base_agility,
            "luck": guest.template.base_luck,
        },
    )
    guest.refresh_from_db()

    assert guest.avatar_override_key == ""
    assert guest.super_disguise_applied is False
    assert guest.hp_bonus == 0
