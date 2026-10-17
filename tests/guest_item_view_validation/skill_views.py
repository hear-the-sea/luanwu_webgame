from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from guests.models import GearItem, GearSlot, GearTemplate, GuestSkill, Skill
from guests.services.equipment import equip_guest
from tests.guest_item_view_validation.support import bootstrap_guest_client


@pytest.mark.django_db
def test_learn_skill_view_rejects_invalid_item_id_redirect(game_data, django_user_model):
    _manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_learn_skill_invalid")

    response = client.post(
        reverse("guests:learn_skill", args=[guest.pk]),
        {"item_id": "invalid"},
    )

    assert response.status_code == 302
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("请选择技能书" in m for m in messages)


@pytest.mark.django_db
def test_learn_skill_view_rejects_non_mapping_effect_payload_redirect(game_data, django_user_model):
    manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_learn_skill_bad_payload")
    template = ItemTemplate.objects.create(
        key=f"view_learn_skill_bad_payload_{manor.id}",
        name="坏结构技能书",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload=False,
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

    response = client.post(
        reverse("guests:learn_skill", args=[guest.pk]),
        {"item_id": str(item.pk)},
    )

    assert response.status_code == 302
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("技能书配置有误" in m for m in messages)


@pytest.mark.django_db
def test_learn_skill_view_rejects_non_string_skill_key_redirect(game_data, django_user_model):
    manor, guest, client = bootstrap_guest_client(
        game_data, django_user_model, username="view_learn_skill_bad_skill_key"
    )
    Skill.objects.create(key=f"view_learn_skill_unused_{manor.id}", name="测试技能")
    template = ItemTemplate.objects.create(
        key=f"view_learn_skill_bad_skill_key_{manor.id}",
        name="坏技能键技能书",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload={"skill_key": 123},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

    response = client.post(
        reverse("guests:learn_skill", args=[guest.pk]),
        {"item_id": str(item.pk)},
    )

    assert response.status_code == 302
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("技能书配置有误" in m for m in messages)


@pytest.mark.django_db
def test_learn_skill_view_rejects_unmet_requirements_redirect(game_data, django_user_model):
    manor, guest, client = bootstrap_guest_client(
        game_data, django_user_model, username="view_learn_skill_requirements"
    )
    skill = Skill.objects.create(
        key=f"view_learn_skill_requirements_{manor.id}",
        name="门槛技能",
        required_level=guest.level + 5,
        required_agility=guest.agility + 20,
    )
    template = ItemTemplate.objects.create(
        key=f"view_learn_skill_requirements_book_{manor.id}",
        name="高门槛技能书",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload={"skill_key": skill.key},
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

    response = client.post(
        reverse("guests:learn_skill", args=[guest.pk]),
        {"item_id": str(item.pk)},
    )

    assert response.status_code == 302
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("学习条件不足" in m for m in messages)
    assert any("等级需 ≥" in m for m in messages)
    assert any("敏捷需 ≥" in m for m in messages)


@pytest.mark.django_db
def test_skill_book_modal_shows_non_equipment_attribute_shortfall(game_data, django_user_model):
    manor, guest, client = bootstrap_guest_client(
        game_data, django_user_model, username="view_learn_skill_non_equipment"
    )
    GuestSkill.objects.filter(guest=guest).delete()
    base_force = int(guest.force)
    gear_template = GearTemplate.objects.create(
        key=f"view_skill_study_gear_{manor.id}",
        name="试学武器",
        slot=GearSlot.WEAPON,
        extra_stats={"force": 200},
    )
    equip_guest(GearItem.objects.create(manor=manor, template=gear_template), guest)
    skill = Skill.objects.create(
        key=f"view_skill_non_equipment_{manor.id}",
        name="试学技能",
        rarity="blue",
        required_force=base_force + 200,
    )
    item_template = ItemTemplate.objects.create(
        key=f"view_skill_non_equipment_book_{manor.id}",
        name="试学技能书",
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload={"skill_key": skill.key},
    )
    InventoryItem.objects.create(manor=manor, template=item_template, quantity=1)

    response = client.get(reverse("guests:detail", args=[guest.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    minimum = ((base_force + 200) * 65 + 99) // 100
    assert f"武力需 ≥ {base_force + 200}（当前 {base_force + 200}）" in content
    assert f"非装备武力需 ≥ {minimum}" in content
    assert f"非装备武力需 ≥ {minimum}（当前 {base_force}）" in content
    assert "当前不足：" not in content


@pytest.mark.django_db
def test_forget_skill_view_rejects_invalid_guest_skill_id_redirect(game_data, django_user_model):
    _manor, guest, client = bootstrap_guest_client(game_data, django_user_model, username="view_forget_skill_invalid")

    response = client.post(
        reverse("guests:forget_skill", args=[guest.pk]),
        {"guest_skill_id": "invalid"},
    )

    assert response.status_code == 302
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("未指定技能" in m for m in messages)
