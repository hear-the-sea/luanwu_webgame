"""
监牢与结义林服务

功能：
- 踢馆俘虏列表查询
- 招募俘虏（消耗金条，等级/装备重置）
- 结义关系管理（结义门客不可被俘获）
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, List

from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.config import GUEST
from core.exceptions import (
    GuestCapacityFullError,
    GuestNotIdleError,
    ItemInsufficientError,
    ItemNotFoundError,
    JailError,
    OathBondAlreadyExistsError,
    OathCapacityFullError,
    OathGuestNotFoundError,
    PrisonerAlreadyHandledError,
    PrisonerNotFoundError,
    PrisonerUnavailableError,
    WorldUniqueGuestError,
)
from guests.models import Guest, GuestStatus, GuestTemplate
from guests.services.recruitment_guests import grant_template_skills
from guests.services.training import ensure_auto_training
from guests.utils.recruitment_variance import apply_recruitment_variance
from trade.services.auction.gold_bars import consume_available_gold_bars_locked

from ..models import BotProfile, JailInteractionLog, JailPrisoner, Manor, OathBond
from .inventory.core import get_item_quantity
from .jail_expiration import (
    JAIL_MAX_HOLD_DURATION,
    release_expired_prisoner_if_needed,
    release_expired_prisoners_for_captor,
)
from .jail_persuasion.eligibility import (
    RECRUIT_NEGOTIATED,
    RECRUIT_STANDARD,
    recruitment_offer,
    recruitment_success_percent,
)
from .jail_persuasion.interactions import interact_prisoner
from .jail_persuasion.milestones import pending_milestone_stage
from .jail_persuasion.profiles import METHOD_BRIBE, load_jail_persuasion_profiles, render_copy, stable_seed

GOLD_BAR_ITEM_KEY = "gold_bar"
VIRTUAL_JAIL_CLEANUP_DEFAULT_BATCH_SIZE = 100
VIRTUAL_JAIL_CLEANUP_MAX_BATCH_SIZE = 1_000
VIRTUAL_JAIL_CLEANUP_DEFAULT_MAX_BATCHES = 100
VIRTUAL_JAIL_CLEANUP_MAX_BATCHES = 1_000
JAIL_CLEANUP_DEFAULT_BATCH_SIZE = VIRTUAL_JAIL_CLEANUP_DEFAULT_BATCH_SIZE
JAIL_CLEANUP_MAX_BATCH_SIZE = VIRTUAL_JAIL_CLEANUP_MAX_BATCH_SIZE
JAIL_CLEANUP_DEFAULT_MAX_BATCHES = VIRTUAL_JAIL_CLEANUP_DEFAULT_MAX_BATCHES
JAIL_CLEANUP_MAX_BATCHES = VIRTUAL_JAIL_CLEANUP_MAX_BATCHES


class JailCleanupError(ValueError):
    pass


VirtualJailCleanupError = JailCleanupError


@dataclass(frozen=True, slots=True)
class JailCleanupResult:
    cutoff: datetime
    batch_size: int
    batch_count: int
    scanned: int
    locked: int
    released: int
    skipped: int
    failed: int
    oldest_remaining_age_seconds: int | None
    batch_limit_reached: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "cutoff": self.cutoff.isoformat(),
            "batch_size": self.batch_size,
            "batch_count": self.batch_count,
            "scanned": self.scanned,
            "locked": self.locked,
            "released": self.released,
            "skipped": self.skipped,
            "failed": self.failed,
            "oldest_remaining_age_seconds": self.oldest_remaining_age_seconds,
            "batch_limit_reached": self.batch_limit_reached,
        }


@dataclass(frozen=True, slots=True)
class _JailCleanupBatchResult:
    scanned: int
    locked: int
    released: int
    skipped: int
    failed: int = 0


@dataclass(frozen=True)
class RecruitmentResult:
    recruited: bool
    mode: str
    prisoner: JailPrisoner
    guest: Guest | None
    gold_cost: int
    initial_loyalty: int | None
    copy_key: str
    copy_params: dict[str, object]
    copy_text: str


PRISONER_RECRUIT_DUPLICATE_TEMPLATE_GROUPS = {
    "hist_sljnbc_0589": ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
    "hist_sljnbc_0589_blue": ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
    "hist_sljnbc_0589_purple": ("hist_sljnbc_0589", "hist_sljnbc_0589_blue", "hist_sljnbc_0589_purple"),
    "hist_sljnbc_0590": ("hist_sljnbc_0590", "hist_sljnbc_0590_blue", "hist_sljnbc_0590_purple"),
    "hist_sljnbc_0590_blue": ("hist_sljnbc_0590", "hist_sljnbc_0590_blue", "hist_sljnbc_0590_purple"),
    "hist_sljnbc_0590_purple": ("hist_sljnbc_0590", "hist_sljnbc_0590_blue", "hist_sljnbc_0590_purple"),
    "orig_ma_wencai": ("orig_ma_wencai",),
    "orig_liang_shanbo": ("orig_liang_shanbo",),
    "orig_zhu_yingtai": ("orig_zhu_yingtai",),
}


PRISONER_RECRUIT_REPEATABLE_TEMPLATE_GROUPS: dict[str, tuple[str, ...]] = {
    "pubayi_green": ("pubayi_green", "pubayi_blue"),
    "pubayi_blue": ("pubayi_green", "pubayi_blue"),
    "orig_edward_blue": ("orig_edward_blue", "orig_edward_purple"),
    "orig_edward_purple": ("orig_edward_blue", "orig_edward_purple"),
}


def _get_prisoner_recruit_duplicate_keys(template_key: str) -> tuple[str, ...]:
    normalized_key = str(template_key or "").strip()
    if not normalized_key:
        return ()
    return PRISONER_RECRUIT_DUPLICATE_TEMPLATE_GROUPS.get(normalized_key, (normalized_key,))


def _get_prisoner_recruit_repeatable_keys(template_key: str) -> tuple[str, ...]:
    return PRISONER_RECRUIT_REPEATABLE_TEMPLATE_GROUPS.get(str(template_key or "").strip(), ())


def list_held_prisoners(manor: Manor) -> List[JailPrisoner]:
    release_expired_prisoners_for_captor(manor)
    return list(
        JailPrisoner.objects.filter(captor=manor, status=JailPrisoner.Status.HELD)
        .select_related("guest_template", "original_manor")
        .order_by("-captured_at")
    )


def list_oath_bonds(manor: Manor) -> List[OathBond]:
    return list(OathBond.objects.filter(manor=manor).select_related("guest", "guest__template").order_by("-created_at"))


def _normalize_jail_cleanup_cutoff(cutoff: datetime) -> datetime:
    if not isinstance(cutoff, datetime) or timezone.is_naive(cutoff):
        raise JailCleanupError("cutoff must be a timezone-aware datetime")
    return cutoff.astimezone(UTC)


def _normalize_jail_cleanup_limit(
    value: int,
    *,
    field: str,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise JailCleanupError(f"{field} must be between 1 and {maximum}")
    return value


def _eligible_jail_prisoners(*, cutoff: datetime, captor_ids: Any = None) -> QuerySet[JailPrisoner]:
    filters: dict[str, Any] = {
        "status": JailPrisoner.Status.HELD,
        "captured_at__lte": cutoff,
    }
    if captor_ids is not None:
        filters["captor_id__in"] = captor_ids
    return JailPrisoner.objects.filter(**filters)


def _virtual_player_manor_ids() -> Any:
    return BotProfile.objects.values_list("manor_id", flat=True)


@transaction.atomic
def _release_jail_prisoners_batch(
    *,
    cutoff: datetime,
    batch_size: int,
    captor_ids: Any = None,
) -> _JailCleanupBatchResult:
    candidates = _eligible_jail_prisoners(cutoff=cutoff, captor_ids=captor_ids)
    if connection.features.has_select_for_update_skip_locked:
        candidates = candidates.select_for_update(skip_locked=True)
    else:
        candidates = candidates.select_for_update()

    prisoner_ids = list(candidates.order_by("captured_at", "id").values_list("id", flat=True)[:batch_size])
    locked = len(prisoner_ids)
    if not prisoner_ids:
        return _JailCleanupBatchResult(
            scanned=0,
            locked=0,
            released=0,
            skipped=0,
        )

    release_queryset = JailPrisoner.objects.filter(
        id__in=prisoner_ids,
        status=JailPrisoner.Status.HELD,
        captured_at__lte=cutoff,
    )
    if captor_ids is not None:
        release_queryset = release_queryset.filter(captor_id__in=captor_ids)
    released = release_queryset.update(status=JailPrisoner.Status.RELEASED)
    return _JailCleanupBatchResult(
        scanned=locked,
        locked=locked,
        released=released,
        skipped=locked - released,
    )


def _cleanup_jail_prisoners(
    *,
    cutoff: datetime,
    batch_size: int,
    max_batches: int,
    captor_ids: Any = None,
) -> JailCleanupResult:
    normalized_cutoff = _normalize_jail_cleanup_cutoff(cutoff)
    normalized_batch_size = _normalize_jail_cleanup_limit(
        batch_size,
        field="batch_size",
        maximum=JAIL_CLEANUP_MAX_BATCH_SIZE,
    )
    normalized_max_batches = _normalize_jail_cleanup_limit(
        max_batches,
        field="max_batches",
        maximum=JAIL_CLEANUP_MAX_BATCHES,
    )

    batch_count = 0
    scanned = 0
    locked = 0
    released = 0
    skipped = 0
    failed = 0
    for _batch_index in range(normalized_max_batches):
        batch = _release_jail_prisoners_batch(
            cutoff=normalized_cutoff,
            batch_size=normalized_batch_size,
            captor_ids=captor_ids,
        )
        scanned += batch.scanned
        locked += batch.locked
        released += batch.released
        skipped += batch.skipped
        failed += batch.failed
        if batch.locked == 0:
            break
        batch_count += 1

    oldest_remaining_at = (
        _eligible_jail_prisoners(cutoff=normalized_cutoff, captor_ids=captor_ids)
        .order_by("captured_at", "id")
        .values_list("captured_at", flat=True)
        .first()
    )
    oldest_remaining_age_seconds = None
    if oldest_remaining_at is not None:
        oldest_remaining_age_seconds = max(
            0,
            int((normalized_cutoff - oldest_remaining_at.astimezone(UTC)).total_seconds()),
        )

    return JailCleanupResult(
        cutoff=normalized_cutoff,
        batch_size=normalized_batch_size,
        batch_count=batch_count,
        scanned=scanned,
        locked=locked,
        released=released,
        skipped=skipped,
        failed=failed,
        oldest_remaining_age_seconds=oldest_remaining_age_seconds,
        batch_limit_reached=(oldest_remaining_at is not None and batch_count >= normalized_max_batches),
    )


def cleanup_virtual_player_jail(
    *,
    cutoff: datetime,
    batch_size: int = VIRTUAL_JAIL_CLEANUP_DEFAULT_BATCH_SIZE,
    max_batches: int = VIRTUAL_JAIL_CLEANUP_DEFAULT_MAX_BATCHES,
) -> JailCleanupResult:
    """Release a bounded daily slice of prisoners held by virtual-player captors.

    Database failures roll back their current batch and propagate to the caller, so
    every successfully returned summary has ``failed=0``.
    """
    return _cleanup_jail_prisoners(
        cutoff=cutoff,
        batch_size=batch_size,
        max_batches=max_batches,
        captor_ids=_virtual_player_manor_ids(),
    )


def cleanup_expired_jail_prisoners(
    *,
    as_of: datetime | None = None,
    batch_size: int = JAIL_CLEANUP_DEFAULT_BATCH_SIZE,
    max_batches: int = JAIL_CLEANUP_DEFAULT_MAX_BATCHES,
) -> JailCleanupResult:
    """Release expired prisoners for both real and virtual-player captors."""
    normalized_as_of = _normalize_jail_cleanup_cutoff(as_of or timezone.now())
    return _cleanup_jail_prisoners(
        cutoff=normalized_as_of - JAIL_MAX_HOLD_DURATION,
        batch_size=batch_size,
        max_batches=max_batches,
    )


VirtualJailCleanupResult = JailCleanupResult


@transaction.atomic
def add_oath_bond(manor: Manor, guest_id: int) -> OathBond:
    # Lock manor to serialize oath bond additions and prevent capacity bypass
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)

    guest = (
        Guest.objects.select_for_update()
        .select_related("template")
        .filter(pk=guest_id, manor_id=getattr(locked_manor, "pk", None))
        .first()
    )
    if not guest:
        raise OathGuestNotFoundError()
    if getattr(getattr(guest, "template", None), "is_world_unique", False):
        raise WorldUniqueGuestError(message="全服唯一门客不可结义")
    if guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)

    # 容量校验：使用锁定后的对象读取容量
    capacity = int(getattr(locked_manor, "oath_capacity", 0) or 0)
    current = OathBond.objects.filter(manor=manor).count()
    if current >= capacity:
        raise OathCapacityFullError()

    bond, created = OathBond.objects.get_or_create(manor=manor, guest=guest)
    if not created:
        raise OathBondAlreadyExistsError()
    return bond


@transaction.atomic
def remove_oath_bond(manor: Manor, guest_id: int) -> int:
    guest = Guest.objects.select_for_update().filter(pk=guest_id, manor_id=getattr(manor, "pk", None)).first()
    if guest and guest.status != GuestStatus.IDLE:
        raise GuestNotIdleError(guest)
    deleted, _ = OathBond.objects.filter(manor=manor, guest_id=guest_id).delete()
    return int(deleted)


def release_prisoner(manor: Manor, prisoner_id: int) -> JailPrisoner:
    """
    释放囚徒：将囚徒状态设置为已释放
    """
    prisoner = _release_prisoner(manor, prisoner_id)
    if prisoner is None:
        raise PrisonerUnavailableError()
    return prisoner


@transaction.atomic
def _release_prisoner(manor: Manor, prisoner_id: int) -> JailPrisoner | None:
    prisoner = (
        JailPrisoner.objects.select_for_update()
        .filter(pk=prisoner_id, captor=manor, status=JailPrisoner.Status.HELD)
        .first()
    )
    if not prisoner:
        raise PrisonerUnavailableError()

    if release_expired_prisoner_if_needed(prisoner):
        return None

    prisoner.status = JailPrisoner.Status.RELEASED
    prisoner.save(update_fields=["status"])

    return prisoner


def draw_pie(manor: Manor, prisoner_id: int) -> JailPrisoner:
    """兼容旧“画饼”入口，按新的许以重利规则结算。"""
    result = interact_prisoner(
        manor,
        prisoner_id,
        method=METHOD_BRIBE,
        lazy_observe=True,
    )
    prisoner = result.prisoner
    setattr(prisoner, "_reduction", max(0, -int(result.heart_delta)))
    setattr(prisoner, "_persuasion_result", result)
    return prisoner


def recruit_prisoner(
    manor: Manor,
    prisoner_id: int,
    *,
    mode: str = RECRUIT_STANDARD,
    rng: Any = None,
) -> RecruitmentResult:
    result = _recruit_prisoner(manor, prisoner_id, mode=mode, rng=rng)
    if result is None:
        raise JailError("囚徒已关押满30天，已自动释放")
    return result


@transaction.atomic
def _recruit_prisoner(
    manor: Manor,
    prisoner_id: int,
    *,
    mode: str = RECRUIT_STANDARD,
    rng: Any = None,
) -> RecruitmentResult | None:
    # 死锁/并发预防：先锁定 Manor，确保容量检查原子化
    # 必须使用锁定后的对象来检查容量，防止陈旧读
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)

    prisoner = (
        JailPrisoner.objects.select_for_update()
        .select_related("guest_template")
        .filter(pk=prisoner_id, captor=locked_manor)
        .first()
    )
    if not prisoner:
        raise PrisonerNotFoundError()
    if prisoner.status != JailPrisoner.Status.HELD:
        raise PrisonerAlreadyHandledError()
    if release_expired_prisoner_if_needed(prisoner):
        return None

    if pending_milestone_stage(prisoner):
        raise JailError("请先处理当前归心事件")
    offer = recruitment_offer(prisoner, mode)
    if not offer.eligible:
        if offer.mode == RECRUIT_STANDARD:
            raise JailError("忠诚度过高，无法招募")
        if offer.mode == RECRUIT_NEGOTIATED:
            raise JailError("尚未满足权宜归附条件")
        raise JailError("尚未满足心悦诚服条件")

    # 使用锁定后的 manor 对象检查容量
    capacity = locked_manor.guest_capacity
    current = locked_manor.guests.count()
    if current >= capacity:
        raise GuestCapacityFullError()

    template: GuestTemplate = prisoner.guest_template
    if getattr(template, "is_world_unique", False):
        raise WorldUniqueGuestError(message="全服唯一门客不可通过监牢收编")
    repeatable_template_keys = _get_prisoner_recruit_repeatable_keys(template.key)
    duplicate_template_keys = _get_prisoner_recruit_duplicate_keys(template.key)
    if (
        duplicate_template_keys
        and not repeatable_template_keys
        and locked_manor.guests.filter(template__key__in=duplicate_template_keys).exists()
    ):
        raise JailError(f"庄园已拥有门客「{template.name}」，不可重复招募")

    usage_date = timezone.localdate()
    if JailInteractionLog.objects.filter(
        prisoner=prisoner,
        usage_date=usage_date,
        attempt_scope="recruitment",
    ).exists():
        raise JailError("该囚徒今日已尝试归附")

    cost = int(offer.gold_cost)
    if cost > 0:
        try:
            consume_available_gold_bars_locked(locked_manor, cost)
        except (ItemInsufficientError, ItemNotFoundError) as exc:
            have = exc.context.get("available", get_item_quantity(locked_manor, GOLD_BAR_ITEM_KEY))
            raise JailError(f"金条不足，需要 {cost} 个（当前 {have} 个）")

    if rng is None:
        rng = random.Random()
    success_percent = recruitment_success_percent(prisoner, offer.mode)
    roll = int(rng.randint(1, 100))
    recruited = roll <= success_percent
    heart_before = int(prisoner.loyalty)
    affinity_before = int(prisoner.affinity)
    guest: Guest | None = None

    if recruited:
        gender_choice = template.default_gender
        if not gender_choice or gender_choice == "unknown":
            gender_choice = rng.choice(["male", "female"])
        morality_value = template.default_morality or rng.randint(30, 100)

        template_attrs = {
            "force": template.base_attack,
            "intellect": template.base_intellect,
            "defense": template.base_defense,
            "agility": template.base_agility,
            "luck": template.base_luck,
        }
        varied_attrs = apply_recruitment_variance(
            template_attrs,
            rarity=template.rarity,
            archetype=template.archetype,
            rng=rng,
        )

        initial_hp = max(
            int(GUEST.MIN_HP_FLOOR),
            template.base_hp + varied_attrs["defense"] * int(GUEST.DEFENSE_TO_HP_MULTIPLIER),
        )

        custom_name = ""
        prisoner_name = (prisoner.original_guest_name or "").strip()
        if prisoner_name and prisoner_name != template.name:
            custom_name = prisoner_name

        guest = Guest.objects.create(
            manor=locked_manor,
            template=template,
            level=1,
            experience=0,
            custom_name=custom_name,
            force=varied_attrs["force"],
            intellect=varied_attrs["intellect"],
            defense_stat=varied_attrs["defense"],
            agility=varied_attrs["agility"],
            luck=varied_attrs["luck"],
            initial_force=varied_attrs["force"],
            initial_intellect=varied_attrs["intellect"],
            initial_defense=varied_attrs["defense"],
            initial_agility=varied_attrs["agility"],
            loyalty=int(offer.initial_loyalty),
            gender=gender_choice,
            morality=morality_value,
            current_hp=initial_hp,
        )
        grant_template_skills(guest)
        ensure_auto_training(guest)

        prisoner.status = JailPrisoner.Status.RECRUITED
        prisoner.save(update_fields=["status"])

    copy_pool_name = "recruitment_copy" if recruited else "recruitment_failure_copy"
    copy_pool = load_jail_persuasion_profiles()[copy_pool_name][offer.mode]
    if recruited:
        copy_seed = stable_seed(prisoner.id, offer.mode, "recruitment-copy")
    else:
        copy_seed = stable_seed(
            prisoner.id,
            offer.mode,
            usage_date.isoformat(),
            "recruitment-failure-copy",
        )
    copy_entry = copy_pool[copy_seed % len(copy_pool)]
    public_copy_params: dict[str, object] = {
        "prisoner_name": prisoner.display_name,
        "new_loyalty": int(offer.initial_loyalty) if recruited else 0,
    }
    audit_copy_params: dict[str, object] = {
        **public_copy_params,
        "mode": offer.mode,
        "success_percent": success_percent,
        "roll": roll,
    }
    JailInteractionLog.objects.create(
        prisoner=prisoner,
        captor=locked_manor,
        method="recruitment",
        speaker=None,
        speaker_name_snapshot="",
        speaker_template_key_snapshot="",
        speaker_base_value_snapshot=None,
        speaker_loyalty_before=None,
        speaker_loyalty_after=None,
        usage_date=usage_date,
        attempt_scope="recruitment",
        heart_before=heart_before,
        heart_after=heart_before,
        affinity_before=affinity_before,
        affinity_after=affinity_before,
        outcome=(JailInteractionLog.Outcome.RECRUITED if recruited else JailInteractionLog.Outcome.FAILED),
        copy_key=copy_entry["key"],
        copy_params=audit_copy_params,
        resource_cost={GOLD_BAR_ITEM_KEY: cost},
    )
    return RecruitmentResult(
        recruited=recruited,
        mode=offer.mode,
        prisoner=prisoner,
        guest=guest,
        gold_cost=cost,
        initial_loyalty=int(offer.initial_loyalty) if recruited else None,
        copy_key=copy_entry["key"],
        copy_params=public_copy_params,
        copy_text=render_copy(copy_entry["key"], public_copy_params),
    )
