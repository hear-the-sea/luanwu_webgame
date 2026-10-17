import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse

from gameplay.services.inventory.guest_items import use_guest_disguise_card, use_super_guest_disguise_card
from guests.avatar import resolve_guest_avatar_url
from guests.constants import SUPER_GUEST_DISGUISE_AVATAR_PATH
from tests.inventory_guest_items.disguise import _prepare_avatar_disguise_case, _prepare_disguise_case


@pytest.mark.django_db
def test_normal_disguise_preserves_original_and_repeated_cards(client, django_user_model, monkeypatch):
    manor, guest, item = _prepare_avatar_disguise_case(django_user_model, quantity=2)
    guest.avatar_asset_key = default_storage.save("guests/original-switch.webp", ContentFile(b"original"))
    guest.save(update_fields=["avatar_asset_key"])
    original_key = guest.avatar_asset_key
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.resolve_guest_disguise_avatar_asset",
        lambda key: default_storage.save("guests/" + key, ContentFile(b"disguise")),
    )
    client.force_login(manor.user)
    use_guest_disguise_card(manor, item, guest.pk, "first.webp")
    guest.refresh_from_db()
    assert guest.original_avatar_asset_key == original_key
    assert guest.can_switch_avatar
    url = reverse("guests:switch_avatar", args=[guest.pk])
    assert client.post(url, {"appearance": "original"}).status_code == 302
    guest.refresh_from_db()
    assert resolve_guest_avatar_url(guest) == default_storage.url(original_key)
    use_guest_disguise_card(manor, item, guest.pk, "second.webp")
    guest.refresh_from_db()
    assert guest.original_avatar_asset_key == original_key
    assert not guest.use_original_avatar
    assert resolve_guest_avatar_url(guest) == default_storage.url(guest.avatar_asset_key)
    assert client.post(url, {"appearance": "original"}).status_code == 302
    assert client.post(url, {"appearance": "disguise"}).status_code == 302
    guest.refresh_from_db()
    assert not guest.use_original_avatar


@pytest.mark.django_db
def test_super_disguise_switch_keeps_stats_and_permissions(client, django_user_model):
    manor, guest, item = _prepare_disguise_case(django_user_model)
    guest.template.avatar = "guests/template-original.webp"
    guest.template.save(update_fields=["avatar"])
    use_super_guest_disguise_card(manor, item, guest.pk)
    guest.refresh_from_db()
    boosted = (guest.force, guest.agility, guest.hp_bonus, guest.current_hp)
    url = reverse("guests:switch_avatar", args=[guest.pk])
    assert client.post(url, {"appearance": "original"}).status_code == 302
    other = django_user_model.objects.create_user(username="avatar_other")
    client.force_login(other)
    assert client.post(url, {"appearance": "original"}).status_code == 404
    client.force_login(manor.user)
    assert client.get(url).status_code == 405
    client.post(url, {"appearance": "invalid"})
    guest.refresh_from_db()
    assert not guest.use_original_avatar
    client.post(url, {"appearance": "original"})
    guest.refresh_from_db()
    assert resolve_guest_avatar_url(guest) == guest.template.avatar.url
    from battle.combatants_pkg.guest_builder import _resolve_battle_avatar_url
    from gameplay.services.battle_snapshots import build_guest_battle_snapshot

    assert _resolve_battle_avatar_url(guest) == guest.template.avatar.url
    assert build_guest_battle_snapshot(guest)["avatar_url"] == guest.template.avatar.url
    assert (guest.force, guest.agility, guest.hp_bonus, guest.current_hp) == boosted
    assert guest.super_disguise_applied
    client.post(url, {"appearance": "disguise"})
    guest.refresh_from_db()
    assert resolve_guest_avatar_url(guest) == default_storage.url(SUPER_GUEST_DISGUISE_AVATAR_PATH)


@pytest.mark.django_db
def test_undisguised_guest_cannot_switch(client, django_user_model):
    manor, guest, _ = _prepare_disguise_case(django_user_model)
    client.force_login(manor.user)
    client.post(reverse("guests:switch_avatar", args=[guest.pk]), {"appearance": "original"})
    guest.refresh_from_db()
    assert not guest.use_original_avatar
    assert not guest.can_switch_avatar


@pytest.mark.django_db
def test_legacy_disguise_backfill_excludes_generated_base_portraits(django_user_model):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    from guests.models import Guest, GuestTemplate

    manor, guest, _ = _prepare_disguise_case(django_user_model)
    guest.avatar_asset_key = "guests/old-disguise.webp"
    guest.save(update_fields=["avatar_asset_key"])
    template = GuestTemplate.objects.create(key="base_gray_civil", name="基础文客")
    generated = Guest.objects.create(manor=manor, template=template, avatar_asset_key="guests/generated.webp")
    special = Guest.objects.create(
        manor=manor, template=template, avatar_asset_key="guests/special-original.webp", super_disguise_applied=True
    )
    migration = import_module("guests.migrations.0087_guest_avatar_switch")
    migration.mark_legacy_disguises(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    generated.refresh_from_db()
    special.refresh_from_db()
    assert guest.original_avatar_asset_key == ""
    assert guest.can_switch_avatar
    assert generated.original_avatar_asset_key is None
    assert not generated.can_switch_avatar
    assert special.original_avatar_asset_key == "guests/special-original.webp"


@pytest.mark.django_db
def test_template_avatar_refresh_keeps_original_portrait_until_no_guest_references_it(django_user_model):
    from guests.management.commands.load_guest_templates import Command

    _, guest, _ = _prepare_disguise_case(django_user_model)
    original = default_storage.save("guests/preserved-original.webp", ContentFile(b"original"))
    guest.original_avatar_asset_key = original
    guest.save(update_fields=["original_avatar_asset_key"])
    command = Command()

    command._safe_delete_old_avatar(guest.template, original, "guests/replacement.webp")
    assert default_storage.exists(original)

    guest.original_avatar_asset_key = None
    guest.save(update_fields=["original_avatar_asset_key"])
    command._safe_delete_old_avatar(guest.template, original, "guests/replacement.webp")
    assert not default_storage.exists(original)


@pytest.mark.django_db
def test_normal_then_super_disguise_retains_first_original(client, django_user_model, monkeypatch):
    from guests.constants import SUPER_GUEST_DISGUISE_AVATAR_KEY

    manor, guest, item = _prepare_avatar_disguise_case(django_user_model, quantity=2)
    original = default_storage.save("guests/before-any-disguise.webp", ContentFile(b"original"))
    guest.avatar_asset_key = original
    guest.save(update_fields=["avatar_asset_key"])
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items.resolve_guest_disguise_avatar_asset",
        lambda _key: default_storage.save("guests/ordinary-disguise.webp", ContentFile(b"disguise")),
    )
    use_guest_disguise_card(manor, item, guest.pk, "monkey_wen_male1.png")
    item.template.effect_payload = {"action": "disguise_guest", "avatar_override": SUPER_GUEST_DISGUISE_AVATAR_KEY}
    item.template.save(update_fields=["effect_payload"])
    use_super_guest_disguise_card(manor, item, guest.pk)
    guest.refresh_from_db()
    assert guest.original_avatar_asset_key == original
    assert resolve_guest_avatar_url(guest) == default_storage.url(SUPER_GUEST_DISGUISE_AVATAR_PATH)

    client.force_login(manor.user)
    client.post(reverse("guests:switch_avatar", args=[guest.pk]), {"appearance": "original"})
    guest.refresh_from_db()
    assert resolve_guest_avatar_url(guest) == default_storage.url(original)
    assert guest.super_disguise_applied
