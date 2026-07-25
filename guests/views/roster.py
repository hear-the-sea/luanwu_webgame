"""
门客名册视图：列表、详情、辞退
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.config import GUEST
from core.exceptions import GameError
from core.utils import sanitize_error_message

from ..forms import AllocateSkillPointsForm
from ..models import GearItem, GearSlot, GearTemplate, GuestSkill, Skill
from ..services.recruitment_queries import available_guests
from ..services.roster import dismiss_guest
from ..services.skills import collect_skill_requirements, collect_unmet_skill_requirements
from .read_helpers import get_prepared_guest_detail_for_read, get_prepared_guest_roster_for_read

logger = logging.getLogger(__name__)
MAX_GUEST_SKILL_SLOTS = int(GUEST.MAX_SKILL_SLOTS)


def _resolve_skill_book_payload(entry) -> dict[str, str | None]:
    payload = entry.template.effect_payload
    if payload is None:
        return {"skill_key": None, "skill_name": None, "skill_description": None}
    if not isinstance(payload, dict):
        raise AssertionError(f"invalid guest roster skill_book effect_payload: {payload!r}")

    resolved: dict[str, str | None] = {}
    for field_name in ("skill_key", "skill_name", "skill_description"):
        raw_value = payload.get(field_name)
        if raw_value is None:
            resolved[field_name] = None
            continue
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise AssertionError(f"invalid guest roster skill_book {field_name}: {raw_value!r}")
        resolved[field_name] = raw_value.strip()
    return resolved


def _skill_book_display_meta(entry, skill, payload: dict[str, str | None]) -> dict[str, str]:
    display_name = getattr(skill, "name", "") or payload.get("skill_name") or payload.get("skill_key") or "未知技能"
    display_description = (
        getattr(skill, "description", "")
        or payload.get("skill_description")
        or getattr(entry.template, "description", "")
        or ""
    )
    return {
        "skill_name": display_name,
        "skill_description": display_description,
    }


def _load_guest_detail(manor, guest_pk: int):
    return get_object_or_404(
        manor.guests.select_related("template").prefetch_related(
            Prefetch(
                "gear_items",
                queryset=GearItem.objects.select_related("template").only(
                    "id",
                    "guest_id",
                    "template_id",
                    "template__slot",
                    "template__name",
                    "template__rarity",
                    "template__set_key",
                    "template__set_description",
                    "template__set_bonus",
                    "template__attack_bonus",
                    "template__defense_bonus",
                    "template__extra_stats",
                ),
            ),
            Prefetch(
                "guest_skills",
                queryset=GuestSkill.objects.select_related("skill").only(
                    "id",
                    "guest_id",
                    "skill_id",
                    "learned_at",
                    "skill__key",
                    "skill__name",
                    "skill__description",
                ),
            ),
        ),
        pk=guest_pk,
    )


def _build_gear_set_context(gear_items):
    equipped_templates = [item.template for item in gear_items]
    set_keys = {tpl.set_key for tpl in equipped_templates if getattr(tpl, "set_key", "")}
    gear_sets = []
    gear_set_map = {}
    if not set_keys:
        return gear_sets, gear_set_map

    templates = list(
        GearTemplate.objects.filter(set_key__in=set_keys)
        .only("id", "name", "set_key", "set_description", "set_bonus", "rarity", "slot")
        .order_by("set_key", "slot", "id")
    )
    templates_by_set = {}
    for tpl in templates:
        templates_by_set.setdefault(tpl.set_key, []).append(tpl)
    equipped_ids = {tpl.id for tpl in equipped_templates}
    for set_key in sorted(templates_by_set):
        members = templates_by_set.get(set_key, [])
        if not members:
            continue
        bonus = members[0].set_bonus or {}
        pieces = None
        if isinstance(bonus, dict):
            pieces = bonus.get("pieces")
        elif isinstance(bonus, list) and bonus and isinstance(bonus[0], dict):
            pieces = bonus[0].get("pieces")
        members_payload = [
            {
                "id": tpl.id,
                "name": tpl.name,
                "slot": tpl.get_slot_display() if hasattr(tpl, "get_slot_display") else tpl.slot,
                "rarity": tpl.rarity,
                "equipped": tpl.id in equipped_ids,
            }
            for tpl in members
        ]
        set_desc = members[0].set_description if hasattr(members[0], "set_description") else ""
        payload = {
            "description": set_desc,
            "pieces": pieces,
            "bonus": bonus,
            "members": members_payload,
        }
        gear_sets.append({"key": set_key, **payload})
        gear_set_map[set_key] = payload
    return gear_sets, gear_set_map


def _build_skill_books_context(manor, guest, guest_skill_records):
    from gameplay.models import InventoryItem, ItemTemplate

    skill_books = []
    if len(guest_skill_records) >= MAX_GUEST_SKILL_SLOTS:
        return skill_books

    skill_book_items = (
        manor.inventory_items.filter(
            template__effect_type=ItemTemplate.EffectType.SKILL_BOOK,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .only("id", "quantity", "template__name", "template__description", "template__effect_payload")
        .order_by("template__name")
    )
    skill_key_entries = []
    for entry in skill_book_items:
        payload = _resolve_skill_book_payload(entry)
        skill_key_entries.append((entry, payload["skill_key"], payload))
    skill_keys = {skill_key for _entry, skill_key, _payload in skill_key_entries if skill_key}
    learned_skill_keys = {record.skill.key for record in guest_skill_records}
    skills = {
        skill.key: skill
        for skill in Skill.objects.filter(key__in=skill_keys).only(
            "key",
            "name",
            "description",
            "rarity",
            "required_level",
            "required_force",
            "required_intellect",
            "required_defense",
            "required_agility",
        )
    }
    for entry, key, payload in skill_key_entries:
        skill = skills.get(key)
        requirements = collect_skill_requirements(skill)
        unmet_requirements = collect_unmet_skill_requirements(guest, skill)
        already_learned = bool(skill and skill.key in learned_skill_keys)
        display_meta = _skill_book_display_meta(entry, skill, payload)
        skill_books.append(
            {
                "inventory": entry,
                "skill": skill,
                "skill_key": key,
                **display_meta,
                "requirements": requirements,
                "requirements_text": "，".join(requirements),
                "unmet_requirements": unmet_requirements,
                "already_learned": already_learned,
                "can_learn": bool(skill) and not already_learned and not unmet_requirements and entry.quantity > 0,
            }
        )
    return skill_books


def _build_slot_panels(slots, equipped, slot_capacity):
    slot_panels = []
    for slot, label in slots:
        equipped_list = equipped.get(slot) or []
        capacity = slot_capacity.get(slot, 1)
        positions = [equipped_list[idx] if idx < len(equipped_list) else None for idx in range(capacity)]
        slot_panels.append(
            {
                "value": slot,
                "label": label,
                "equipped": equipped_list,
                "positions": positions,
                "options": [],
                "capacity": capacity,
            }
        )
    return slot_panels


class RosterView(LoginRequiredMixin, TemplateView):
    template_name = "guests/roster.html"

    def get_context_data(self, **kwargs):
        from gameplay.models import InventoryItem, ItemTemplate
        from guests.services.salary import bulk_check_salary_paid, get_guest_salary, get_unpaid_guests

        context = super().get_context_data(**kwargs)
        manor, guests = get_prepared_guest_roster_for_read(
            self.request,
            logger=logger,
            source="guest_roster_view",
            available_guests_fn=available_guests,
        )
        exp_items = list(
            manor.inventory_items.select_related("template").filter(
                template__effect_type=ItemTemplate.EffectType.EXPERIENCE_ITEM,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
        )
        medicine_items = list(
            manor.inventory_items.select_related("template").filter(
                template__effect_type=ItemTemplate.EffectType.MEDICINE,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
        )

        # 添加工资相关信息（优化 N+1：批量查询已支付状态）
        today = timezone.localdate()
        guest_ids = [g.id for g in guests]
        paid_ids = bulk_check_salary_paid(guest_ids, today)

        guests_with_salary = []
        for guest in guests:
            guest_salary = get_guest_salary(guest)
            guest_paid = guest.id in paid_ids
            guests_with_salary.append(
                {
                    "guest": guest,
                    "salary": guest_salary,
                    "paid_today": guest_paid,
                }
            )

        unpaid_guests = get_unpaid_guests(manor, today)
        total_unpaid_salary = sum(get_guest_salary(g) for g in unpaid_guests)

        context["manor"] = manor
        context["guests"] = guests
        context["guests_with_salary"] = guests_with_salary
        context["unpaid_count"] = len(unpaid_guests)
        context["total_unpaid_salary"] = total_unpaid_salary
        context["exp_items"] = exp_items
        context["medicine_items"] = medicine_items
        return context


class GuestDetailView(LoginRequiredMixin, TemplateView):
    template_name = "guests/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        manor, guest = get_prepared_guest_detail_for_read(
            self.request,
            self.kwargs["pk"],
            logger=logger,
            source="guest_detail_view",
            load_guest_detail_fn=_load_guest_detail,
        )
        slots = [(choice.value, choice.label) for choice in GearSlot]
        slot_capacity = {
            GearSlot.DEVICE.value: 3,
            GearSlot.ORNAMENT.value: 3,
        }
        gear_items = list(guest.gear_items.all())
        equipped = {slot: [] for slot, _ in slots}
        for item in gear_items:
            equipped[item.template.slot].append(item)

        guest_skill_records = sorted(
            guest.guest_skills.all(),
            key=lambda record: (record.learned_at, record.id),
        )
        gear_sets, gear_set_map = _build_gear_set_context(gear_items)
        skill_slots = []
        for idx in range(MAX_GUEST_SKILL_SLOTS):
            record = guest_skill_records[idx] if idx < len(guest_skill_records) else None
            skill_slots.append(
                {
                    "index": idx + 1,
                    "record": record,
                    "skill": record.skill if record else None,
                }
            )
        skill_books = _build_skill_books_context(manor, guest, guest_skill_records)
        slot_panels = _build_slot_panels(slots, equipped, slot_capacity)
        context.update(
            {
                "guest": guest,
                "slot_panels": slot_panels,
                "slot_panels_map": {panel["value"]: panel for panel in slot_panels},
                "skill_slots": skill_slots,
                "skill_capacity": MAX_GUEST_SKILL_SLOTS,
                "skill_count": len(guest_skill_records),
                "skill_books": skill_books,
                "skill_point_form": AllocateSkillPointsForm(manor=manor, initial={"guest": guest}),
                "gear_sets": gear_sets,
                "gear_set_map": gear_set_map,
            }
        )
        context["skill_point_form"].fields["guest"].queryset = manor.guests.filter(pk=guest.pk)
        return context


@login_required
@require_POST
def dismiss_guest_view(request, pk: int):
    from gameplay.services.manor.core import get_manor

    manor = get_manor(request.user)
    guest = get_object_or_404(manor.guests, pk=pk)

    try:
        result = dismiss_guest(guest)
    except GameError as exc:
        messages.error(request, f"辞退失败：{sanitize_error_message(exc)}")
        return redirect("guests:detail", pk=pk)
    except DatabaseError as exc:
        logger.exception(
            "Unexpected dismiss guest view database error: manor_id=%s user_id=%s guest_id=%s",
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            pk,
        )
        messages.error(request, f"辞退失败：{sanitize_error_message(exc)}")
        return redirect("guests:detail", pk=pk)

    if result.gear_summary:
        readable = "、".join(f"{name} ×{count}" if count > 1 else name for name, count in result.gear_summary.items())
        messages.success(request, f"已辞退 {result.guest_name}，装备已归还仓库（{readable}）")
    else:
        messages.info(request, f"已辞退 {result.guest_name}。")
    return redirect("guests:roster")
