from __future__ import annotations

import pytest
from django.db import connection

from battle.combatants_pkg.guest_builder import build_guest_combatants, serialize_guest_for_report
from core.exceptions import GuestItemConfigurationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.arena.snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot
from gameplay.services.battle_snapshots import build_guest_battle_snapshot, build_guest_snapshot_proxies
from gameplay.services.inventory.guest_items import use_guest_disguise_card, use_super_guest_disguise_card
from gameplay.services.inventory.guest_reset_helpers import apply_guest_template_reset
from gameplay.services.manor.core import ensure_manor
from guests.avatar import _guest_disguise_avatar_label, get_guest_disguise_avatar_filenames
from guests.constants import (
    GUEST_DISGUISE_AVATAR_ACTION,
    SUPER_GUEST_DISGUISE_AVATAR_KEY,
    SUPER_GUEST_DISGUISE_AVATAR_PATH,
)
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


def _prepare_avatar_disguise_case(django_user_model, *, quantity: int = 1):
    manor, guest, item = _prepare_disguise_case(django_user_model, quantity=quantity)
    item.template.key = "guest_disguise_card_test"
    item.template.name = "门客易容卡"
    item.template.effect_payload = {"action": GUEST_DISGUISE_AVATAR_ACTION}
    item.template.save(update_fields=["key", "name", "effect_payload"])
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
def test_guest_disguise_card_applies_selected_avatar_without_stat_changes(django_user_model, monkeypatch):
    manor, guest, item = _prepare_avatar_disguise_case(django_user_model)
    old_stats = (guest.force, guest.intellect, guest.defense_stat, guest.agility, guest.luck, guest.max_hp)
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.resolve_guest_disguise_avatar_asset",
        lambda avatar_key: "guests/monkey_wen_male1.webp" if avatar_key == "monkey_wen_male1.png" else "",
    )

    result = use_guest_disguise_card(manor, item, guest.id, "monkey_wen_male1.png")
    guest.refresh_from_db()

    assert guest.avatar_asset_key == "guests/monkey_wen_male1.webp"
    assert guest.avatar_override_key == ""
    assert guest.super_disguise_applied is False
    assert (guest.force, guest.intellect, guest.defense_stat, guest.agility, guest.luck, guest.max_hp) == old_stats
    assert result["avatar_key"] == "monkey_wen_male1.png"
    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_guest_disguise_card_opens_transaction_before_locking_manor(django_user_model, monkeypatch):
    manor, guest, item = _prepare_avatar_disguise_case(django_user_model)
    transaction_states = []
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.resolve_guest_disguise_avatar_asset",
        lambda _avatar_key: transaction_states.append(connection.in_atomic_block) or "guests/monkey_wen_male1.webp",
    )

    use_guest_disguise_card(manor, item, guest.id, "monkey_wen_male1.png")

    assert transaction_states == [True]


@pytest.mark.django_db
def test_guest_disguise_card_rejects_avatar_outside_controlled_catalog(django_user_model, monkeypatch):
    manor, guest, item = _prepare_avatar_disguise_case(django_user_model, quantity=2)
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.resolve_guest_disguise_avatar_asset",
        lambda _avatar_key: "",
    )

    with pytest.raises(GuestItemConfigurationError, match="形象不存在"):
        use_guest_disguise_card(manor, item, guest.id, "../media/evil.webp")

    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.avatar_asset_key == ""
    assert item.quantity == 2


def test_guest_disguise_catalog_only_contains_requested_prefixes():
    filenames = get_guest_disguise_avatar_filenames()

    assert filenames
    assert all(filename.lower().startswith(("monkey", "pig", "duck")) for filename in filenames)


@pytest.mark.parametrize(
    ("filename", "expected_label"),
    [
        ("monkey_wen_female1.png", "猴·女1"),
        ("pig_wu_male2.png", "猪·男2"),
        ("duck_wen_male1.png", "鸭·男1"),
        ("monkey_mistry.png", "猴·神秘"),
    ],
)
def test_guest_disguise_avatar_labels_are_player_facing(filename, expected_label):
    assert _guest_disguise_avatar_label(filename) == expected_label


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


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("template_key", "template_avatar", "expected_asset"),
    [
        ("base_gray_civil", "guests/legacy.webp", "guests/regenerated.webp"),
        ("unpictured_reset_guest", "", "guests/regenerated.webp"),
        ("pictured_reset_guest", "guests/fixed.webp", ""),
    ],
)
def test_guest_reset_restores_appropriate_avatar(
    django_user_model, monkeypatch, template_key, template_avatar, expected_asset
):
    _manor, guest, _item = _prepare_disguise_case(django_user_model)
    template = guest.template
    template.key = template_key
    template.avatar = template_avatar
    template.save(update_fields=["key", "avatar"])
    guest.avatar_asset_key = "guests/original.webp"
    guest.avatar_override_key = SUPER_GUEST_DISGUISE_AVATAR_KEY
    guest.save(update_fields=["avatar_asset_key", "avatar_override_key"])
    prepared: list[tuple[str, str]] = []

    def prepare_avatar(*, archetype, gender, rng):
        prepared.append((archetype, gender))
        return "guests/regenerated.webp"

    monkeypatch.setattr("gameplay.services.inventory.guest_reset_helpers.prepare_guest_avatar_asset", prepare_avatar)

    apply_guest_template_reset(
        guest,
        target_template=template,
        varied_attrs={"force": 100, "intellect": 80, "defense": 90, "agility": 75, "luck": 50},
    )
    guest.refresh_from_db()

    assert guest.avatar_override_key == ""
    assert guest.avatar_asset_key == expected_asset
    assert prepared == ([(template.archetype, guest.gender)] if expected_asset else [])
