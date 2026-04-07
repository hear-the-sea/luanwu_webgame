from __future__ import annotations

from itertools import count

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestTemplate,
    RecruitmentCandidate,
    RecruitmentPool,
    Skill,
)

_SEQ = count()


def unique(prefix: str) -> str:
    return f"{prefix}_{next(_SEQ)}"


def ajax_headers() -> dict[str, str]:
    return {
        "HTTP_X_REQUESTED_WITH": "XMLHttpRequest",
        "HTTP_ACCEPT": "application/json",
    }


def messages(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def login_client(django_user_model, *, prefix: str = "guest_view_boundary"):
    cache.clear()
    username = unique(prefix)
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    client = Client()
    assert client.login(username=username, password="pass123")
    return client, manor


def create_guest(manor, *, prefix: str = "guest") -> Guest:
    template = GuestTemplate.objects.create(
        key=unique(f"{prefix}_tpl"),
        name=f"{prefix}模板",
        archetype="military",
        rarity="green",
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=f"{prefix}门客",
        level=10,
        force=120,
        intellect=90,
        defense_stat=100,
        agility=95,
        attribute_points=5,
    )


def create_gear(manor, *, guest=None, slot: str = GearSlot.WEAPON) -> GearItem:
    template = GearTemplate.objects.create(
        key=unique("gear_tpl"),
        name="测试装备",
        slot=slot,
        rarity="green",
    )
    return GearItem.objects.create(manor=manor, template=template, guest=guest)


def create_item(manor, *, effect_type: str, effect_payload: dict, prefix: str) -> InventoryItem:
    template = ItemTemplate.objects.create(
        key=unique(prefix),
        name=f"{prefix}道具",
        effect_type=effect_type,
        effect_payload=effect_payload,
        is_usable=True,
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


def create_pool(prefix: str = "pool") -> RecruitmentPool:
    return RecruitmentPool.objects.create(
        key=unique(prefix),
        name=f"{prefix}卡池",
        cost={},
        cooldown_seconds=0,
        draw_count=1,
    )


def create_candidate(manor, *, prefix: str = "candidate") -> RecruitmentCandidate:
    pool = create_pool(f"{prefix}_pool")
    template = GuestTemplate.objects.create(
        key=unique(f"{prefix}_tpl"),
        name=f"{prefix}模板",
        archetype="military",
        rarity="green",
    )
    return RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name=f"{prefix}候选",
        rarity=template.rarity,
        archetype=template.archetype,
    )


def create_skill_book(manor, *, prefix: str = "skill_book") -> tuple[Skill, InventoryItem]:
    skill = Skill.objects.create(
        key=unique(f"{prefix}_skill"),
        name=f"{prefix}技能",
        rarity="green",
    )
    item = create_item(
        manor,
        effect_type=ItemTemplate.EffectType.SKILL_BOOK,
        effect_payload={"skill_key": skill.key},
        prefix=prefix,
    )
    return skill, item


def stub_recruit_lock(monkeypatch) -> None:
    monkeypatch.setattr("guests.views.recruit._acquire_recruit_action_lock", lambda *_a, **_k: (True, "lock", "token"))
    monkeypatch.setattr("guests.views.recruit._release_recruit_action_lock", lambda *_a, **_k: None)


def stub_equip_form(monkeypatch, guest: Guest, gear: GearItem) -> None:
    class DummyEquipForm:
        cleaned_data = {"guest": guest, "gear": gear}

        def __init__(self, *_args, **_kwargs):
            pass

        def is_valid(self) -> bool:
            return True

    monkeypatch.setattr("guests.views.equipment.EquipForm", DummyEquipForm)
    monkeypatch.setattr("guests.services.equipment.ensure_inventory_gears", lambda *_a, **_k: None)
