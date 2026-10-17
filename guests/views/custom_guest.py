"""专属门客养成页面与二次确认。"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import unicodedata
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.exceptions import GameError
from core.utils import sanitize_error_message
from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS
from core.utils.rate_limit import rate_limit_redirect
from core.utils.responses import json_error, json_success
from gameplay.models import InventoryItem, Manor
from gameplay.services.manor.core import get_manor
from guests.avatar import get_guest_disguise_avatar_options
from guests.models import CustomGuestProgress, Guest
from guests.services.custom_guest_biography import build_custom_guest_biography

logger = logging.getLogger(__name__)
WEIGHT_LABELS = {"force": "武力", "intellect": "智力", "defense": "防御", "agility": "敏捷"}
SALT = "guests.custom-guest-confirm.v1"


def _service():
    from guests.services import custom_guest

    return custom_guest


def _equipment_rows(manor):
    rows = []
    for item in manor.inventory_items.select_related("template").filter(
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity__gt=0,
        template__effect_type__startswith="equip_",
        template__rarity__in=["blue", "purple", "orange"],
    ):
        try:
            stat, gain = _service().custom_equipment_weight_gain(item.template)
        except GameError:
            continue
        for mode, quantity in (("bound", item.bound_quantity), ("unbound", item.unbound_quantity)):
            if quantity:
                rows.append(
                    {
                        "item": item,
                        "mode": mode,
                        "quantity": quantity,
                        "stat": stat,
                        "label": WEIGHT_LABELS[stat],
                        "gain": gain,
                        "value": f"{item.pk}:{mode}",
                    }
                )
    return rows


def get_custom_guest_panel_context(manor, guest):
    service = _service()
    if not service.is_custom_guest(guest):
        return {}
    context = service.get_custom_guest_context(guest)
    total = context["total_weight"]
    context["weight_rows"] = [
        {"label": label, "value": context["weights"][stat], "percent": round(context["weights"][stat] * 100 / total, 1)}
        for stat, label in WEIGHT_LABELS.items()
    ]
    context["equipment_rows"] = _equipment_rows(manor)
    context["has_resettable_weights"] = any(value != 500 for value in context["weights"].values())
    context["material_quantity"] = sum(
        manor.inventory_items.filter(template__key="yijinjing", storage_location="warehouse").values_list(
            "quantity", flat=True
        )
    )
    context["fruit_quantity"] = sum(
        manor.inventory_items.filter(template__key="millennium_zhuguo", storage_location="warehouse").values_list(
            "quantity", flat=True
        )
    )
    return {
        "custom_guest": context,
        "custom_guest_biography": build_custom_guest_biography(manor, timezone.localdate(manor.created_at)),
    }


def _state(manor, guest, inventory_id):
    """签名包含养成与库存状态，旧页面不能再次扣除资源。"""
    progress = CustomGuestProgress.objects.filter(manor=manor).first()
    inventory = list(
        manor.inventory_items.filter(template__key__in=["custom_guest_card", "yijinjing", "millennium_zhuguo"])
        .order_by("pk")
        .values_list("pk", "quantity", "bound_quantity", "updated_at")
    )
    if inventory_id:
        inventory.extend(
            manor.inventory_items.filter(pk=inventory_id).values_list("pk", "quantity", "bound_quantity", "updated_at")
        )
    data = (
        [
            progress.guest_id,
            progress.weights,
            progress.enhancement_level,
            progress.failed_attempts,
            progress.feeding_date,
            progress.feeding_count,
            str(timezone.localdate()),
            inventory,
        ]
        if progress
        else [None, inventory]
    )
    if guest:
        data.extend(
            [
                guest.pk,
                guest.level,
                guest.template_id,
                guest.status,
                guest.custom_name,
                guest.avatar_asset_key,
                guest.avatar_override_key,
                list(guest.guest_skills.values_list("pk", flat=True)),
                list(guest.gear_items.values_list("pk", flat=True)),
            ]
        )
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _parameters(request, action):
    params = {"action": action}
    if action in {"create", "appearance"}:
        params.update(name=(request.POST.get("name") or "").strip(), avatar_key=request.POST.get("avatar_key") or "")
        if not 2 <= len(params["name"]) <= 12 or any(
            unicodedata.category(char).startswith("C") for char in params["name"]
        ):
            raise GameError("姓名须为2～12个字符")
        options = get_guest_disguise_avatar_options()
        if params["avatar_key"] not in {option["key"] for option in options}:
            raise GameError("请选择门客形象")
    elif action == "consume":
        try:
            inventory_id, mode = request.POST.get("equipment", "").split(":")
            params.update(
                inventory_id=int(inventory_id), binding_mode=mode, quantity=int(request.POST.get("quantity", "1"))
            )
        except (ValueError, TypeError):
            raise GameError("请选择装备和吞噬数量") from None
        if params["quantity"] <= 0 or mode not in {"bound", "unbound"}:
            raise GameError("请选择装备和吞噬数量")
    elif action not in {"advance", "reset"}:
        raise GameError("不支持此操作")
    return params


def _preview(manor, guest, params, card_id):
    action = params["action"]
    if action in {"create", "appearance"}:
        avatar = next(option for option in get_guest_disguise_avatar_options() if option["key"] == params["avatar_key"])
        return {
            "title": "创建门客" if action == "create" else "更改姓名与形象",
            "summary": params["name"],
            "avatar": avatar,
            "detail": (
                "消耗1张自定义门客卡，获得1级绿色专属门客。" if action == "create" else "免费更改，不影响养成进度。"
            ),
        }
    context = _service().get_custom_guest_context(guest)
    if action == "consume":
        if params["quantity"] > context["feeding_remaining"]:
            raise GameError(f"今日还可吞噬{context['feeding_remaining']}件装备")
        row = next(
            (
                row
                for row in _equipment_rows(manor)
                if row["item"].pk == params["inventory_id"] and row["mode"] == params["binding_mode"]
            ),
            None,
        )
        if not row or params["quantity"] > row["quantity"]:
            raise GameError("可用装备数量不足")
        gain = min(params["quantity"] * row["gain"], 25000 - context["total_weight"])
        if gain <= 0:
            raise GameError("成长权重已达上限")
        if params["quantity"] > (25000 - context["total_weight"] + row["gain"] - 1) // row["gain"]:
            raise GameError("吞噬数量超过剩余权重所需数量")
        old = context["weights"][row["stat"]]
        total = context["total_weight"]
        return {
            "title": "吞噬装备",
            "summary": f"{row['item'].template.name} × {params['quantity']}",
            "detail": (
                f"{row['label']}权重 +{gain}，成长分配占比 {old / total:.1%} → {(old + gain) / (total + gain):.1%}。"
                f"装备将被消耗，完成后今日还可吞噬{context['feeding_remaining'] - params['quantity']}件。"
            ),
        }
    if action == "reset":
        if all(value == 500 for value in context["weights"].values()):
            raise GameError("四项成长权重已为初始值")
        return {
            "title": "重置成长权重",
            "summary": "消耗1个千年朱果",
            "detail": "四项权重重置为各500；保留品质、强化、等级和现有属性，不返还已吞噬装备。",
        }
    stage = context["next_stage"]
    if not stage:
        raise GameError("已完成全部强化")
    if context["total_weight"] < stage["required_weight"]:
        raise GameError("成长权重尚未达到升阶要求")
    return {
        "title": stage["label"],
        "summary": f"消耗{stage['cost']}本易筋经，本次成功率{stage['chance']}%",
        "detail": (
            "成功后回到1级，清空升级属性、自由加点和技能，装备退回仓库。保留姓名、形象和成长权重。"
            if guest.rarity != "orange"
            else "成功后成长上限提高1，等级与现有属性保留。"
        )
        + "失败保留现有养成，下次成功率增加10个百分点。",
    }


def _execute(manor, guest, card_id, params):
    service = _service()
    action = params["action"]
    if action == "create":
        return service.create_custom_guest(manor, card_id, params["name"], params["avatar_key"])
    if action == "appearance":
        return service.update_custom_guest_appearance(manor, guest.pk, params["name"], params["avatar_key"])
    if action == "consume":
        return service.consume_custom_guest_equipment(
            manor, guest.pk, params["inventory_id"], params["quantity"], binding_mode=params["binding_mode"]
        )
    if action == "advance":
        return service.advance_custom_guest(manor, guest.pk)
    return service.reset_custom_guest_weights(manor, guest.pk)


@login_required
@require_http_methods(["GET", "POST"])
@rate_limit_redirect("custom_guest_action", limit=30, window_seconds=60)
def custom_guest_action_view(request, pk=None, inventory_id=None, action="create"):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    manor = get_manor(request.user)
    guest = get_object_or_404(Guest, manor=manor, pk=pk) if pk else None
    if guest and not _service().is_custom_guest(guest):
        return redirect("guests:detail", pk=guest.pk)
    if inventory_id:
        get_object_or_404(
            InventoryItem,
            manor=manor,
            pk=inventory_id,
            template__key="custom_guest_card",
            storage_location="warehouse",
            quantity__gt=0,
        )
    back = ("guests:detail", {"pk": guest.pk}) if guest else ("gameplay:warehouse", {})
    if request.method == "GET":
        if action not in {"create", "appearance"}:
            return redirect(back[0], **back[1])
        avatars = get_guest_disguise_avatar_options()
        selected_avatar = next(
            (
                avatar["key"]
                for avatar in avatars
                if guest and Path(avatar["key"]).stem == Path(guest.avatar_asset_key).stem
            ),
            "",
        )
        return render(
            request,
            "guests/custom_guest_appearance.html",
            {
                "guest": guest,
                "creating": action == "create",
                "avatars": avatars,
                "selected_avatar": selected_avatar,
            },
        )
    try:
        token = request.POST.get("confirmation")
        if not token:
            params = _parameters(request, action)
            preview = _preview(manor, guest, params, inventory_id)
            signed = signing.dumps(
                {
                    "manor": manor.pk,
                    "guest": pk,
                    "card": inventory_id,
                    "params": params,
                    "state": _state(manor, guest, params.get("inventory_id") or inventory_id),
                    "nonce": secrets.token_hex(24),
                },
                salt=SALT,
            )
            if is_ajax:
                return json_success(preview=preview, confirmation=signed)
            return render(
                request,
                "guests/custom_guest_confirm.html",
                {
                    "guest": guest,
                    "preview": preview,
                    "confirmation": signed,
                },
            )
        payload = signing.loads(token, salt=SALT, max_age=300)
        if (payload["manor"], payload["guest"], payload["card"], payload["params"]["action"]) != (
            manor.pk,
            pk,
            inventory_id,
            action,
        ):
            raise GameError("确认信息已失效，请重新操作")
        with transaction.atomic():
            manor = Manor.objects.select_for_update().get(pk=manor.pk)
            guest = Guest.objects.get(pk=pk, manor=manor) if pk else None
            params = payload["params"]
            if payload["state"] != _state(manor, guest, params.get("inventory_id") or inventory_id):
                raise GameError("养成或库存已变化，请重新确认")
            if not cache.add(f"custom-guest-confirm:{payload['nonce']}", True, timeout=600):
                raise GameError("此操作已处理，请刷新页面")
            result = _execute(manor, guest, inventory_id, params)
        messages.success(request, result.get("message", "操作完成"))
        if action == "create":
            return redirect("guests:detail", pk=result["guest_id"])
    except signing.BadSignature:
        if is_ajax:
            return json_error("确认信息已过期，请重新操作")
        messages.error(request, "确认信息已过期，请重新操作")
    except GameError as exc:
        if is_ajax:
            return json_error(sanitize_error_message(exc))
        messages.error(request, sanitize_error_message(exc))
    except DatabaseError:
        logger.exception("Custom guest action failed: manor=%s action=%s", manor.pk, action)
        if is_ajax:
            return json_error("操作暂未完成，请稍后重试", status=503)
        messages.error(request, "操作暂未完成，请稍后重试")
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.exception("Custom guest confirmation unavailable: manor=%s action=%s", manor.pk, action)
        if is_ajax:
            return json_error("操作暂未完成，请稍后重试", status=503)
        messages.error(request, "操作暂未完成，请稍后重试")
    return redirect(back[0], **back[1])
