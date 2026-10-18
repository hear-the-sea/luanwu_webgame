import random
from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.guest_items import use_guest_rebirth_card, use_xisuidan
from gameplay.services.manor.core import ensure_manor
from guests.avatar import get_guest_disguise_avatar_filenames
from guests.growth_engine import allocate_level_up_attributes
from guests.models import GuestTemplate
from guests.services.custom_guest import (
    advance_custom_guest,
    consume_custom_guest_equipment,
    create_custom_guest,
    ensure_custom_guest_card,
    get_custom_guest_context,
    reset_custom_guest_weights,
    resolve_custom_growth_range,
    update_custom_guest_appearance,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def manor(django_user_model):
    return ensure_manor(django_user_model.objects.create_user(username="custom_owner"))


@pytest.fixture
def custom(manor):
    ensure_custom_guest_card(manor)
    item = manor.inventory_items.get(template__key="custom_guest_card")
    avatar = get_guest_disguise_avatar_filenames()[0]
    result = create_custom_guest(manor, item.pk, "穿越之人", avatar)
    return manor.guests.get(pk=result["guest_id"])


def stock(manor, key, quantity):
    template = ItemTemplate.objects.get(key=key)
    return InventoryItem.objects.create(manor=manor, template=template, quantity=quantity)


def weight(custom, **weights):
    progress = custom.custom_progress
    progress.weights = {**progress.weights, **weights}
    progress.save()
    return progress


def test_card_once_and_create(manor, custom):
    assert not ensure_custom_guest_card(manor)
    assert (custom.force, custom.intellect, custom.defense_stat, custom.agility, custom.luck) == (45, 50, 24, 52, 165)
    assert custom.custom_progress.card_issued
    card = stock(manor, "custom_guest_card", 1)
    with pytest.raises(GameError, match="已有"):
        create_custom_guest(manor, card.pk, "第二肉身", get_guest_disguise_avatar_filenames()[0])
    assert InventoryItem.objects.get(pk=card.pk).quantity == 1


def test_missing_template_does_not_mark_issued(manor):
    ItemTemplate.objects.filter(key="custom_guest_card").delete()
    assert not ensure_custom_guest_card(manor)
    assert not hasattr(manor, "custom_guest_progress")


def test_bad_appearance_preserves_card(manor):
    ensure_custom_guest_card(manor)
    card = manor.inventory_items.get(template__key="custom_guest_card")
    with pytest.raises(GameError):
        create_custom_guest(manor, card.pk, "坏", "invalid.png")
    assert InventoryItem.objects.get(pk=card.pk).quantity == 1


@pytest.mark.parametrize("rarity,gain", [("blue", 15), ("purple", 50), ("orange", 100)])
def test_swallow_exact_weight_and_binding(manor, custom, rarity, gain):
    template = ItemTemplate.objects.create(
        key=f"test_affix_{rarity}",
        name="力量之剑",
        rarity=rarity,
        effect_type="equip_weapon",
        effect_payload={"equipment_affix": "force", "equipment_base_key": "test_sword"},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=3, bound_quantity=1)
    consume_custom_guest_equipment(manor, custom.pk, item.pk, 2, binding_mode="unbound")
    item.refresh_from_db()
    custom.refresh_from_db()
    assert item.quantity == item.bound_quantity == 1
    assert custom.custom_progress.weights["force"] == 500 + gain * 2
    assert custom.force == 45


def test_swallow_cap_and_invalid_preserve(manor, custom):
    weight(custom, force=23490)
    template = ItemTemplate.objects.create(
        key="test_affix_cap",
        name="力量之剑",
        rarity="orange",
        effect_type="equip_weapon",
        effect_payload={"equipment_affix": "force"},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=3)
    with pytest.raises(GameError):
        consume_custom_guest_equipment(manor, custom.pk, item.pk, 2)
    result = consume_custom_guest_equipment(manor, custom.pk, item.pk, 1)
    assert result["gain"] == 10
    with pytest.raises(GameError):
        consume_custom_guest_equipment(manor, custom.pk, item.pk, 1)
    item.refresh_from_db()
    assert item.quantity == 2


def test_daily_feed_limit_counts_pieces_and_resets_next_day(manor, custom, monkeypatch):
    template = ItemTemplate.objects.create(
        key="daily_custom_feed",
        rarity="blue",
        effect_type="equip_weapon",
        effect_payload={"equipment_affix": "force"},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=53)
    today = timezone.localdate()
    consume_custom_guest_equipment(manor, custom.pk, item.pk, 30)
    consume_custom_guest_equipment(manor, custom.pk, item.pk, 20)
    with pytest.raises(GameError, match="今日还可吞噬0件"):
        consume_custom_guest_equipment(manor, custom.pk, item.pk, 1)
    custom.refresh_from_db()
    item.refresh_from_db()
    assert item.quantity == 3
    assert custom.custom_progress.feeding_count == 50
    assert custom.custom_progress.weights["force"] == 1250
    monkeypatch.setattr("guests.services.custom_guest.timezone.localdate", lambda: today + timedelta(days=1))
    assert get_custom_guest_context(custom)["feeding_remaining"] == 50
    consume_custom_guest_equipment(manor, custom.pk, item.pk, 1)
    custom.refresh_from_db()
    assert custom.custom_progress.feeding_count == 1
    assert custom.custom_progress.feeding_date == today + timedelta(days=1)


def test_failed_feed_and_weight_reset_do_not_restore_daily_allowance(manor, custom):
    template = ItemTemplate.objects.create(
        key="daily_custom_feed_failure",
        rarity="blue",
        effect_type="equip_weapon",
        effect_payload={"equipment_affix": "force"},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=2)
    with pytest.raises(GameError):
        consume_custom_guest_equipment(manor, custom.pk, item.pk, 3)
    custom.refresh_from_db()
    assert get_custom_guest_context(custom)["feeding_used"] == 0
    consume_custom_guest_equipment(manor, custom.pk, item.pk, 2)
    stock(manor, "millennium_zhuguo", 1)
    reset_custom_guest_weights(manor, custom.pk)
    custom.refresh_from_db()
    assert get_custom_guest_context(custom)["feeding_remaining"] == 48
    assert sum(custom.custom_progress.weights.values()) == 2000


def test_reduced_daily_limit_preserves_usage_already_above_50(manor, custom):
    progress = custom.custom_progress
    progress.feeding_count = 75
    progress.feeding_date = timezone.localdate()
    progress.save(update_fields=["feeding_count", "feeding_date"])
    template = ItemTemplate.objects.create(
        key="over_limit_custom_feed",
        rarity="blue",
        effect_type="equip_weapon",
        effect_payload={"equipment_affix": "force"},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    assert get_custom_guest_context(custom)["feeding_remaining"] == 0
    with pytest.raises(GameError, match="今日还可吞噬0件"):
        consume_custom_guest_equipment(manor, custom.pk, item.pk, 1)
    item.refresh_from_db()
    progress.refresh_from_db()
    assert item.quantity == 1 and progress.feeding_count == 75


class FixedRandom:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def test_upgrade_failure_then_success_reset(manor, custom):
    weight(custom, force=3500)
    item = stock(manor, "yijinjing", 30)
    custom.level = 37
    custom.force = 400
    custom.allocated_force = 20
    custom.attribute_points = 4
    custom.save()
    avatar = custom.avatar_asset_key
    assert not advance_custom_guest(manor, custom.pk, FixedRandom(0.9))["success"]
    custom.refresh_from_db()
    assert custom.level == 37
    assert get_custom_guest_context(custom)["next_stage"]["chance"] == 60
    assert advance_custom_guest(manor, custom.pk, FixedRandom(0.59))["success"]
    custom.refresh_from_db()
    item.refresh_from_db()
    assert custom.rarity == "blue"
    assert custom.level == 1 and custom.force == 45
    assert custom.allocated_force == custom.attribute_points == 0
    assert custom.avatar_asset_key == avatar
    assert custom.custom_progress.weights["force"] == 3500
    assert custom.custom_progress.failed_attempts == 0
    assert item.quantity == 10


def test_insufficient_threshold_does_not_spend_or_pity(manor, custom):
    item = stock(manor, "yijinjing", 10)
    with pytest.raises(GameError):
        advance_custom_guest(manor, custom.pk, FixedRandom(0))
    item.refresh_from_db()
    assert item.quantity == 10
    assert custom.custom_progress.failed_attempts == 0


def test_orange_enhancement_independent_pity(manor, custom):
    custom.template = GuestTemplate.objects.get(key="custom_guest_orange")
    custom.level = 60
    custom.save()
    progress = custom.custom_progress
    progress.failed_attempts = 9
    progress.save()
    stock(manor, "yijinjing", 150)
    assert advance_custom_guest(manor, custom.pk, FixedRandom(0.99))["success"]
    custom.refresh_from_db()
    assert custom.level == 60
    assert resolve_custom_growth_range(custom) == (6, 15)
    assert get_custom_guest_context(custom)["next_stage"]["chance"] == 10
    assert advance_custom_guest(manor, custom.pk, FixedRandom(0))["success"]
    custom.refresh_from_db()
    assert resolve_custom_growth_range(custom) == (6, 16)
    with pytest.raises(GameError):
        advance_custom_guest(manor, custom.pk, FixedRandom(0))


def test_zhuguo_keeps_rarity_pity_and_level(manor, custom):
    custom.template = GuestTemplate.objects.get(key="custom_guest_orange")
    custom.level = 80
    custom.save()
    progress = weight(custom, agility=1500)
    progress.enhancement_level = 1
    progress.failed_attempts = 3
    progress.save()
    item = stock(manor, "millennium_zhuguo", 2)
    reset_custom_guest_weights(manor, custom.pk)
    custom.refresh_from_db()
    assert custom.rarity == "orange" and custom.level == 80
    assert custom.custom_progress.enhancement_level == 1
    assert custom.custom_progress.failed_attempts == 3
    assert sum(custom.custom_progress.weights.values()) == 2000
    with pytest.raises(GameError):
        reset_custom_guest_weights(manor, custom.pk)
    item.refresh_from_db()
    assert item.quantity == 1


def test_growth_reads_instance_weights_and_enhancement(manor, custom):
    custom.template = GuestTemplate.objects.get(key="custom_guest_orange")
    custom.save()
    progress = weight(custom, force=23500)
    progress.enhancement_level = 2
    progress.save()
    growth = allocate_level_up_attributes(custom, 200, random.Random(1))
    assert sum(growth.values()) > 200 * 10
    assert growth["force"] / sum(growth.values()) > 0.90
    assert custom.template.attribute_weights["force"] == 500


def test_rebirth_retains_identity_progress(manor, custom):
    custom.template = GuestTemplate.objects.get(key="custom_guest_orange")
    custom.level = 70
    custom.save()
    progress = weight(custom, intellect=1500)
    progress.enhancement_level = 2
    progress.failed_attempts = 4
    progress.save()
    template = ItemTemplate.objects.create(
        key="test_rebirth_custom", name="重生卡", effect_type="tool", effect_payload={"action": "rebirth_guest"}
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    avatar = custom.avatar_asset_key
    use_guest_rebirth_card(manor, item, custom.pk)
    custom.refresh_from_db()
    assert custom.level == 1 and custom.rarity == "orange"
    assert custom.avatar_asset_key == avatar
    assert custom.custom_progress.enhancement_level == 2
    assert custom.custom_progress.failed_attempts == 4
    assert custom.custom_progress.weights["intellect"] == 1500


def test_xisuidan_unlimited_at_100(manor, custom):
    custom.level = 100
    custom.xisuidan_used = 10
    custom.save()
    template = ItemTemplate.objects.create(
        key="test_xisuidan_custom", name="洗髓丹", effect_type="tool", effect_payload={"action": "reroll_growth"}
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    use_xisuidan(manor, item, custom.pk)
    custom.refresh_from_db()
    assert custom.xisuidan_used == 11


def test_appearance_and_non_idle_guard(manor, custom):
    update_custom_guest_appearance(manor, custom.pk, "新生之人", get_guest_disguise_avatar_filenames()[0])
    custom.refresh_from_db()
    assert custom.custom_name == "新生之人"
    custom.status = "working"
    custom.save()
    with pytest.raises(GameError, match="空闲"):
        advance_custom_guest(manor, custom.pk)


def test_upgrade_clears_skills_and_returns_bound_gear(manor, custom):
    from guests.models import GearItem, GearTemplate, GuestSkill, Skill

    weight(custom, force=3500)
    stock(manor, "yijinjing", 10)
    template = ItemTemplate.objects.create(key="custom_test_gear", name="旧剑", effect_type="equip_weapon")
    gear_template = GearTemplate.objects.create(key=template.key, name="旧剑", slot="weapon")
    gear = GearItem.objects.create(manor=manor, guest=custom, template=gear_template, is_bound=True)
    skill = Skill.objects.create(key="custom_test_skill", name="旧招式")
    GuestSkill.objects.create(guest=custom, skill=skill)
    assert advance_custom_guest(manor, custom.pk, FixedRandom(0))["success"]
    gear.refresh_from_db()
    assert gear.guest_id is None
    assert not custom.guest_skills.exists()
    returned = manor.inventory_items.get(template=template)
    assert returned.quantity == returned.bound_quantity == 1


@pytest.mark.parametrize(
    "effect_type,rarity,payload",
    [
        ("equip_weapon", "green", {"equipment_affix": "force"}),
        ("equip_device", "orange", {"equipment_affix": "force"}),
        ("equip_ornament", "orange", {"equipment_affix": "force", "equipment_base_key": "soulorn_test"}),
        ("equip_weapon", "orange", {}),
    ],
)
def test_invalid_equipment_never_consumed(manor, custom, effect_type, rarity, payload):
    template = ItemTemplate.objects.create(
        key="custom_test_invalid", name="不可吞噬", effect_type=effect_type, rarity=rarity, effect_payload=payload
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    with pytest.raises(GameError):
        consume_custom_guest_equipment(manor, custom.pk, item.pk, 1)
    assert InventoryItem.objects.get(pk=item.pk).quantity == 1


def test_foreign_manor_inventory_rejected(manor, custom, django_user_model):
    foreign = ensure_manor(django_user_model.objects.create_user(username="foreign_owner"))
    template = ItemTemplate.objects.create(
        key="custom_test_foreign",
        name="力量之剑",
        effect_type="equip_weapon",
        rarity="blue",
        effect_payload={"equipment_affix": "force"},
    )
    item = InventoryItem.objects.create(manor=foreign, template=template, quantity=1)
    with pytest.raises(GameError):
        consume_custom_guest_equipment(manor, custom.pk, item.pk, 1)
    assert InventoryItem.objects.filter(pk=item.pk).exists()


def test_unknown_avatar_cannot_create_or_change(manor, custom):
    with pytest.raises(GameError, match="有效"):
        update_custom_guest_appearance(manor, custom.pk, "正常姓名", "forged.png")
    custom.refresh_from_db()
    assert custom.custom_name == "穿越之人"


def test_free_appearance_keeps_super_bonus_flag(manor, custom):
    from gameplay.services.inventory.guest_items import use_super_guest_disguise_card
    from guests.constants import SUPER_GUEST_DISGUISE_ACTION

    template = ItemTemplate.objects.create(
        key="custom_test_super",
        name="超级易容卡",
        effect_type="tool",
        effect_payload={"action": SUPER_GUEST_DISGUISE_ACTION},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=2)
    use_super_guest_disguise_card(manor, item, custom.pk)
    custom.refresh_from_db()
    force = custom.force
    update_custom_guest_appearance(manor, custom.pk, "换个模样", get_guest_disguise_avatar_filenames()[0])
    custom.refresh_from_db()
    assert custom.super_disguise_applied
    assert custom.force == force
    with pytest.raises(GameError):
        use_super_guest_disguise_card(manor, item, custom.pk)


def test_rebirth_clears_super_but_keeps_normal_avatar(manor, custom):
    from gameplay.services.inventory.guest_items import use_super_guest_disguise_card
    from guests.constants import SUPER_GUEST_DISGUISE_ACTION

    avatar = custom.avatar_asset_key
    template = ItemTemplate.objects.create(
        key="custom_test_super_rebirth",
        name="超级易容卡",
        effect_type="tool",
        effect_payload={"action": SUPER_GUEST_DISGUISE_ACTION},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    use_super_guest_disguise_card(manor, item, custom.pk)
    template = ItemTemplate.objects.create(
        key="custom_test_rebirth_super", name="重生卡", effect_type="tool", effect_payload={"action": "rebirth_guest"}
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    use_guest_rebirth_card(manor, item, custom.pk)
    custom.refresh_from_db()
    assert custom.avatar_asset_key == avatar
    assert custom.avatar_override_key == ""
    assert not custom.super_disguise_applied
    assert custom.force == 45


def test_warehouse_hides_custom_from_normal_upgrade_and_fusion(manor, custom):
    from gameplay.services.inventory.guest_item_selector import build_guest_item_selection_context

    custom.level = 100
    custom.save()
    template = ItemTemplate.objects.create(
        key="custom_test_generic_upgrade",
        name="普通升阶",
        effect_type="tool",
        is_usable=True,
        effect_payload={"action": "upgrade_guest_rarity", "source_rarity": "green", "target_rarity": "blue"},
    )
    InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    context = build_guest_item_selection_context(manor, eligible_guests=[custom])
    assert context.guests_for_rarity_upgrade == []
    assert context.guests_for_soul_fusion == []
    assert context.guests_for_xisuidan == [custom]


def test_debugger_choices_exclude_custom_guests(manor, custom, settings, monkeypatch):
    import json

    from django.http import HttpResponse
    from django.test import RequestFactory

    from battle_debugger.views import api_guests, custom_config

    settings.DEBUG = True
    normal = GuestTemplate.objects.create(
        key="debugger_normal_guest", name="寻常门客", archetype="military", rarity="green"
    )
    manor.user.is_staff = True
    request = RequestFactory().get("/debugger/api/guests/")
    request.user = manor.user
    response = api_guests(request)
    choices = json.loads(response.content)
    assert any(entry["key"] == normal.key for entry in choices)
    assert not any(entry["key"].startswith("custom_guest_") for entry in choices)
    captured = {}

    def capture_render(request, template, context):
        captured.update(context)
        # 实际求值，确保预加载关联也可正常执行。
        captured["guests"] = list(context["guests"])
        return HttpResponse("ok")

    monkeypatch.setattr("battle_debugger.views.render", capture_render)
    response = custom_config(request)
    assert response.status_code == 200
    assert any(guest.key == normal.key for guest in captured["guests"])
    assert all(not guest.key.startswith("custom_guest_") for guest in captured["guests"])
