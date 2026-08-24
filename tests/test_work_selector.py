from __future__ import annotations

import pytest

from gameplay.models import WorkTemplate
from gameplay.selectors.work import get_work_page_context
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate


def _create_guest(template, manor, name: str, **overrides) -> Guest:
    values = {
        "level": 10,
        "force": 100,
        "intellect": 100,
        "defense_stat": 100,
        "agility": 60,
        "status": GuestStatus.IDLE,
    }
    values.update(overrides)
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        **values,
    )


@pytest.mark.django_db
def test_work_selector_groups_guests_and_keeps_three_closest_ineligible(manor_with_user):
    manor, _client = manor_with_user
    work = WorkTemplate.objects.create(
        key="selector-grouping",
        name="选择器分组工作",
        tier=WorkTemplate.Tier.JUNIOR,
        required_level=10,
        required_intellect=100,
        required_agility=60,
        display_order=1,
    )
    template = GuestTemplate.objects.create(
        key="selector-grouping-guest",
        name="选择器门客",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    exact = _create_guest(template, manor, "恰好胜任")
    surplus = _create_guest(template, manor, "属性富余", intellect=105, agility=65)
    close = _create_guest(template, manor, "尚缺一", agility=59)
    medium = _create_guest(template, manor, "尚缺二", intellect=99, agility=59)
    far = _create_guest(template, manor, "尚缺五", intellect=97, agility=58)
    level_short = _create_guest(template, manor, "等级不足", level=9)
    working = _create_guest(template, manor, "正在忙碌", status=GuestStatus.WORKING)

    context = get_work_page_context(manor, current_tier="junior", page=1)
    selected_work = context["works"][0]

    assert selected_work.pk == work.pk
    assert selected_work.action_point_cost == 10
    assert [(item.key, item.label, item.required) for item in selected_work.requirements] == [
        ("level", "等级", 10),
        ("intellect", "智力", 100),
        ("agility", "敏捷", 60),
    ]
    assert [option.guest_id for option in selected_work.eligible_guest_options] == [exact.pk, surplus.pk]
    assert [option.guest_id for option in selected_work.ineligible_guest_options] == [
        close.pk,
        medium.pk,
        far.pk,
        level_short.pk,
    ]
    assert [option.guest_id for option in selected_work.closest_ineligible_guests] == [
        close.pk,
        medium.pk,
        far.pk,
    ]
    assert [(item.key, item.missing) for item in selected_work.ineligible_guest_options[1].missing_requirements] == [
        ("intellect", 1),
        ("agility", 1),
    ]
    all_option_ids = {
        option.guest_id for option in selected_work.eligible_guest_options + selected_work.ineligible_guest_options
    }
    assert working.pk not in all_option_ids


@pytest.mark.django_db
def test_work_selector_uses_stable_tiebreakers_for_equal_gaps(manor_with_user):
    manor, _client = manor_with_user
    WorkTemplate.objects.create(
        key="selector-stability",
        name="选择器稳定排序",
        tier=WorkTemplate.Tier.JUNIOR,
        required_level=10,
        required_force=100,
        display_order=1,
    )
    template = GuestTemplate.objects.create(
        key="selector-stability-guest",
        name="稳定排序门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
    )
    eligible_higher_level = _create_guest(template, manor, "合格B", level=11, force=100)
    eligible_name_second = _create_guest(template, manor, "合格B", level=10, force=100)
    eligible_name_first = _create_guest(template, manor, "合格A", level=10, force=100)
    ineligible_higher_level = _create_guest(template, manor, "不足B", level=11, force=98)
    ineligible_name_second = _create_guest(template, manor, "不足B", level=10, force=98)
    ineligible_name_first = _create_guest(template, manor, "不足A", level=10, force=98)

    context = get_work_page_context(manor, current_tier="junior", page=1)
    selected_work = context["works"][0]

    assert [option.guest_id for option in selected_work.eligible_guest_options] == [
        eligible_name_first.pk,
        eligible_name_second.pk,
        eligible_higher_level.pk,
    ]
    assert [option.guest_id for option in selected_work.ineligible_guest_options] == [
        ineligible_name_first.pk,
        ineligible_name_second.pk,
        ineligible_higher_level.pk,
    ]
