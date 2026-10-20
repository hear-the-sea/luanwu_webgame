"""全服唯一门客的状态、招募和踢馆流转服务。"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from core.exceptions import GuestAlreadyOwnedError, GuestCapacityFullError, WorldUniqueGuestError
from gameplay.models import ItemTemplate, Manor, OathBond, RaidRun

from ..models import Guest, GuestTemplate, WorldUniqueGuest
from . import equipment as equipment_service

WORLD_UNIQUE_LUBU_TEMPLATE_KEY = "hist_sljnbc_0013"
WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY = "lvbu_guest_scroll"
WORLD_UNIQUE_LUBU_NAME = "吕布"
WORLD_UNIQUE_SUMMON_SOURCES = {
    WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY: frozenset({WORLD_UNIQUE_LUBU_TEMPLATE_KEY}),
    "chiyou_card": frozenset({"hero_yang_zheng_purple", "hero_di_qinglin"}),
}


def is_world_unique_summon_source(item_template_key: str, template_key: str) -> bool:
    """唯一门客只接受显式授权的道具与角色配对。"""
    return template_key in WORLD_UNIQUE_SUMMON_SOURCES.get(item_template_key, frozenset())


def is_world_unique_template(template: Any) -> bool:
    return bool(getattr(template, "is_world_unique", False))


def is_world_unique_guest(guest: Any) -> bool:
    template = getattr(guest, "template", None)
    return is_world_unique_template(template) or getattr(template, "key", None) == WORLD_UNIQUE_LUBU_TEMPLATE_KEY


def ensure_guest_not_world_unique(guest: Any, *, action: str) -> None:
    """阻止会改变唯一门客身份的操作；吕布辞退由专用释放服务处理。"""
    template = getattr(guest, "template", None)
    if action == "辞退" and getattr(template, "key", None) == WORLD_UNIQUE_LUBU_TEMPLATE_KEY:
        return
    if not is_world_unique_guest(guest):
        return
    guest_name = getattr(guest, "display_name", WORLD_UNIQUE_LUBU_NAME)
    action_message = {
        "辞退": "不可辞退",
        "升阶": "不可升阶",
        "灵魂融合": "不可进行灵魂融合",
    }.get(action, "不可进行此操作")
    raise WorldUniqueGuestError(message=f"{guest_name}为全服唯一门客，{action_message}")


def _build_wild_status(*, template_key: str, guest_name: str = WORLD_UNIQUE_LUBU_NAME) -> dict[str, Any]:
    return {
        "template_key": template_key,
        "guest_name": guest_name,
        "status": WorldUniqueGuest.Status.WILD,
        "status_display": WorldUniqueGuest.Status.WILD.label,
        "summary": "在野",
        "owner_manor_id": None,
        "owner_name": "",
        "owner_location": "",
        "is_available": True,
    }


def _build_serving_anomaly_status(state: WorldUniqueGuest) -> dict[str, Any]:
    """返回不可操作的异常状态，避免脏数据被误报为可领取。"""
    return {
        "template_key": state.template.key,
        "guest_name": state.template.name,
        "status": WorldUniqueGuest.Status.SERVING,
        "status_display": WorldUniqueGuest.Status.SERVING.label,
        "summary": "仕官（状态异常）",
        "owner_manor_id": None,
        "owner_name": "",
        "owner_location": "",
        "is_available": False,
    }


def _status_payload_from_state(state: WorldUniqueGuest) -> dict[str, Any]:
    if state.status != WorldUniqueGuest.Status.SERVING:
        return _build_wild_status(
            template_key=state.template.key,
            guest_name=state.template.name,
        )
    if state.owner_manor_id is None or state.owner_guest_id is None:
        return _build_serving_anomaly_status(state)

    owner_manor = state.owner_manor
    if owner_manor is None:
        # PROTECT 应保证正常数据不会走到这里；出现历史脏数据时也不能误报为在野。
        return _build_serving_anomaly_status(state)
    owner_name = owner_manor.display_name
    owner_location = owner_manor.location_display
    return {
        "template_key": state.template.key,
        "guest_name": state.template.name,
        "status": WorldUniqueGuest.Status.SERVING,
        "status_display": WorldUniqueGuest.Status.SERVING.label,
        "summary": f"仕官（{owner_name}，坐标{owner_location}）",
        "owner_manor_id": int(owner_manor.pk),
        "owner_name": owner_name,
        "owner_location": owner_location,
        "is_available": False,
    }


def get_world_unique_guest_status(
    *,
    template_key: str = WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
) -> dict[str, Any]:
    """读取唯一门客公开状态；该读取不会创建或修复任何状态。"""
    state = (
        WorldUniqueGuest.objects.filter(template__key=template_key)
        .select_related("template", "owner_manor__user")
        .first()
    )
    if state is None:
        guest_name = GuestTemplate.objects.filter(key=template_key).values_list("name", flat=True).first()
        return _build_wild_status(
            template_key=template_key,
            guest_name=guest_name
            or (WORLD_UNIQUE_LUBU_NAME if template_key == WORLD_UNIQUE_LUBU_TEMPLATE_KEY else template_key),
        )
    return _status_payload_from_state(state)


def _get_locked_state(template: GuestTemplate) -> WorldUniqueGuest:
    state, _created = WorldUniqueGuest.objects.get_or_create(
        template=template,
        defaults={"status": WorldUniqueGuest.Status.WILD},
    )
    return WorldUniqueGuest.objects.select_for_update().select_related("template", "owner_manor__user").get(pk=state.pk)


def _validate_state_consistency(state: WorldUniqueGuest) -> None:
    has_owner = state.owner_manor_id is not None or state.owner_guest_id is not None
    if state.status == WorldUniqueGuest.Status.WILD and has_owner:
        raise WorldUniqueGuestError(message=f"{state.template.name}的唯一状态异常，请联系管理员")
    if state.status == WorldUniqueGuest.Status.SERVING and (
        state.owner_manor_id is None or state.owner_guest_id is None
    ):
        raise WorldUniqueGuestError(message=f"{state.template.name}的唯一状态异常，请联系管理员")


@transaction.atomic
def claim_world_unique_guest_from_scroll(
    manor: Manor,
    item_template_key: str,
    template: GuestTemplate,
    *,
    custom_name: str = "",
    rng: Any = None,
) -> Guest:
    """用已授权的专属道具兑换唯一门客，并原子写入全局归属。"""
    source_name = ItemTemplate.objects.filter(key=item_template_key).values_list("name", flat=True).first()
    source_name = source_name or {
        WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY: "吕布召唤卷轴",
        "chiyou_card": "蚩尤卡",
    }.get(item_template_key, item_template_key)
    if not is_world_unique_summon_source(item_template_key, template.key):
        raise WorldUniqueGuestError(message=f"{source_name}无法兑换全服唯一门客{template.name}")
    if not is_world_unique_template(template):
        raise WorldUniqueGuestError(message=f"{template.name}模板未配置为全服唯一门客")

    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    state = _get_locked_state(template)
    _validate_state_consistency(state)
    if state.status != WorldUniqueGuest.Status.WILD:
        owner_summary = _status_payload_from_state(state)["summary"]
        raise GuestAlreadyOwnedError(
            template,
            message=f"{template.name}{owner_summary}，当前无法使用{source_name}兑换",
        )

    if locked_manor.active_guest_count >= locked_manor.guest_capacity:
        raise GuestCapacityFullError()

    from .recruitment_guests import create_guest_from_template

    guest = create_guest_from_template(
        manor=locked_manor,
        template=template,
        custom_name=custom_name,
        rng=rng,
        allow_world_unique=True,
    )
    state.status = WorldUniqueGuest.Status.SERVING
    state.owner_manor_id = locked_manor.pk
    state.owner_guest_id = guest.pk
    state.version = int(state.version or 0) + 1
    state.save(update_fields=["status", "owner_manor", "owner_guest", "version", "updated_at"])

    from .training import ensure_auto_training

    ensure_auto_training(guest)
    return guest


@transaction.atomic
def _release_world_unique_guest(
    loser: Manor,
    *,
    guest_id: int,
    template_key: str = WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
    source: str,
) -> dict[str, Any] | None:
    """释放唯一门客并归还装备；调用方需先确认触发场景。"""
    locked_loser = Manor.objects.select_for_update().get(pk=loser.pk)
    state = (
        WorldUniqueGuest.objects.select_for_update()
        .select_related("template")
        .filter(template__key=template_key)
        .first()
    )
    if state is None or state.status != WorldUniqueGuest.Status.SERVING:
        return None
    if state.owner_manor_id != locked_loser.pk or state.owner_guest_id != int(guest_id):
        return None

    guest = (
        Guest.objects.select_for_update()
        .select_related("template")
        .filter(pk=guest_id, manor_id=locked_loser.pk)
        .first()
    )
    if guest is None or guest.template_id != state.template_id:
        raise WorldUniqueGuestError(message="唯一门客归属状态异常，无法结算战败")

    guest_name = guest.display_name
    returned_gear_count = equipment_service.return_guest_gear_to_inventory_locked(locked_loser, guest)
    # 测试环境可能存在历史脏数据，清理旧结义关系，避免删除时残留业务关系。
    OathBond.objects.filter(guest_id=guest.pk).delete()

    # 出征快照独立于门客实例；移除未开战队伍中的吕布，避免战败释放后仍由快照参战。
    for run in (
        RaidRun.objects.select_for_update()
        .filter(attacker_id=locked_loser.pk, status=RaidRun.Status.MARCHING)
        .order_by("pk")
    ):
        snapshots = run.guest_snapshots
        if not isinstance(snapshots, list):
            continue
        remaining = [
            snapshot
            for snapshot in snapshots
            if not (isinstance(snapshot, dict) and str(snapshot.get("guest_id")) == str(guest.pk))
        ]
        if len(remaining) != len(snapshots):
            run.guest_snapshots = remaining
            run.save(update_fields=["guest_snapshots"])

    state.status = WorldUniqueGuest.Status.WILD
    state.owner_manor_id = None
    state.owner_guest_id = None
    state.version = int(state.version or 0) + 1
    state.save(update_fields=["status", "owner_manor", "owner_guest", "version", "updated_at"])
    guest.delete()

    return {
        "guest_name": guest_name,
        "template_key": template_key,
        "from": source,
        "returned_gear_count": returned_gear_count,
        "into": WorldUniqueGuest.Status.WILD,
    }


def release_world_unique_guest_after_raid(
    loser: Manor,
    *,
    guest_id: int,
    template_key: str = WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
    losing_side: str,
) -> dict[str, Any] | None:
    """踢馆战败时释放吕布；防守方失去归属不要求其参战。"""
    return _release_world_unique_guest(
        loser,
        guest_id=guest_id,
        template_key=template_key,
        source=losing_side,
    )


def release_world_unique_guest_after_dismissal(
    manor: Manor,
    *,
    guest_id: int,
    template_key: str = WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
) -> dict[str, Any] | None:
    """辞退唯一门客时，归还装备并将其状态恢复为在野。"""
    return _release_world_unique_guest(
        manor,
        guest_id=guest_id,
        template_key=template_key,
        source="dismissal",
    )
