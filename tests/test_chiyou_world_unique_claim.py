"""蚩尤兑换的两位门客各自独立全服唯一，沿用全局归属锁。"""

import random

import pytest

from core.exceptions import GuestAlreadyOwnedError, WorldUniqueGuestError
from gameplay.models import ItemTemplate
from guests.models import Guest, GuestTemplate, WorldUniqueGuest
from guests.services.world_unique import (
    WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY,
    WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
    claim_world_unique_guest_from_scroll,
    get_world_unique_guest_status,
    is_world_unique_summon_source,
)


@pytest.fixture
def unique_templates(db):
    templates = []
    for key, name in [("hero_yang_zheng_purple", "杨铮"), ("hero_di_qinglin", "狄青麟")]:
        template, _ = GuestTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": name,
                "archetype": "military",
                "rarity": "purple",
                "is_world_unique": True,
                "recruitable": False,
                "base_attack": 100,
                "base_intellect": 80,
                "base_defense": 80,
                "base_agility": 100,
                "base_luck": 80,
                "base_hp": 1500,
            },
        )
        templates.append(template)
    ItemTemplate.objects.update_or_create(key="chiyou_card", defaults={"name": "蚩尤卡", "effect_type": "tool"})
    # 同时覆盖无状态时的只读回退；迁移预建状态的路径由同步集成测试覆盖。
    WorldUniqueGuest.objects.filter(template__in=templates, status="wild").delete()
    return templates


@pytest.mark.parametrize(
    "source,target,expected",
    [
        ("chiyou_card", "hero_yang_zheng_purple", True),
        ("chiyou_card", "hero_di_qinglin", True),
        (WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY, WORLD_UNIQUE_LUBU_TEMPLATE_KEY, True),
        ("chiyou_card", WORLD_UNIQUE_LUBU_TEMPLATE_KEY, False),
        (WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY, "hero_yang_zheng_purple", False),
        (WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY, "hero_di_qinglin", False),
        ("random_card", "hero_di_qinglin", False),
        ("chiyou_card", "random_template", False),
    ],
)
def test_unique_summon_source_authorizes_only_explicit_pairs(source, target, expected):
    assert is_world_unique_summon_source(source, target) is expected


def claim(manor, template):
    return claim_world_unique_guest_from_scroll(manor, "chiyou_card", template, rng=random.Random(7))


def test_status_reads_real_name_without_creating_a_state(unique_templates):
    for template in unique_templates:
        assert not WorldUniqueGuest.objects.filter(template=template).exists()
        status = get_world_unique_guest_status(template_key=template.key)
        assert status["guest_name"] == template.name
        assert status["is_available"] is True
        assert status["summary"] == "在野"
        assert not WorldUniqueGuest.objects.filter(template=template).exists()


def test_two_templates_can_be_claimed_by_different_manors_independently(unique_templates, manor_factory):
    first, second = unique_templates
    owner, _ = manor_factory(username="chiyou_unique_first")
    other, _ = manor_factory(username="chiyou_unique_second")
    first_guest = claim(owner, first)
    assert get_world_unique_guest_status(template_key=second.key)["is_available"] is True
    second_guest = claim(other, second)
    assert first_guest.manor_id == owner.pk
    assert second_guest.manor_id == other.pk
    for template, guest, manor in [(first, first_guest, owner), (second, second_guest, other)]:
        state = WorldUniqueGuest.objects.get(template=template)
        assert (state.owner_guest_id, state.owner_manor_id, state.status, state.version) == (
            guest.pk,
            manor.pk,
            WorldUniqueGuest.Status.SERVING,
            1,
        )
        assert guest.training_target_level == guest.level + 1
    with pytest.raises(GuestAlreadyOwnedError, match=first.name):
        claim(other, first)
    assert Guest.objects.filter(template=first).count() == 1
    assert Guest.objects.filter(template=second).count() == 1


def test_same_manor_may_own_both_individually_unique_templates(unique_templates, manor_factory):
    manor, _ = manor_factory(username="chiyou_unique_both")
    for template in unique_templates:
        claim(manor, template)
    assert WorldUniqueGuest.objects.filter(owner_manor=manor, template__in=unique_templates).count() == 2
    assert Guest.objects.filter(manor=manor, template__in=unique_templates).count() == 2


@pytest.mark.parametrize("source", [WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY, "forged_chiyou_card"])
def test_wrong_source_cannot_claim_chiyou_unique_guest(unique_templates, manor_factory, source):
    manor, _ = manor_factory(username="chiyou_wrong_source")
    template = unique_templates[0]
    with pytest.raises(WorldUniqueGuestError, match=template.name):
        claim_world_unique_guest_from_scroll(manor, source, template)
    assert not Guest.objects.filter(template=template).exists()
    assert not WorldUniqueGuest.objects.filter(template=template).exists()


def test_configuration_error_and_already_serving_message_use_actual_names(unique_templates, manor_factory):
    manor, _ = manor_factory(username="chiyou_unique_error_owner")
    other, _ = manor_factory(username="chiyou_unique_error_other")
    template = unique_templates[1]
    template.is_world_unique = False
    with pytest.raises(WorldUniqueGuestError, match="狄青麟模板未配置"):
        claim(manor, template)
    template.is_world_unique = True
    claim(manor, template)
    with pytest.raises(GuestAlreadyOwnedError) as error:
        claim(other, template)
    assert "狄青麟" in str(error.value)
    assert "蚩尤卡" in str(error.value)
    assert "吕布" not in str(error.value)
