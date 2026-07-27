from __future__ import annotations

import logging
import random
from contextlib import contextmanager
from typing import Any, Callable, Dict, Generator, List

from django.db import transaction

from core.utils.task_monitoring import increment_degraded_counter
from guests.guest_combat_stats import is_live_guest_model
from guests.models import Guest, GuestStatus

from . import execution as _execution
from .combatants_pkg import (
    build_ai_guests,
    build_guest_combatants,
    build_named_ai_guests,
    generate_ai_loadout,
    normalize_troop_loadout,
)
from .constants import DEFAULT_BATTLE_TYPE, MAX_SQUAD
from .defender_setup import build_defender_guest_and_loadout as _build_defender_guest_and_loadout_from_sources
from .deployment import BATTLE_ORPHANED_DEPLOYED_RECOVERY_COUNTER as _BATTLE_ORPHANED_DEPLOYED_RECOVERY_COUNTER
from .deployment import collect_active_deployment_guest_ids as _collect_active_deployment_guest_ids_from_deployment
from .deployment import find_orphaned_deployed_guest_ids as _find_orphaned_deployed_guest_ids_from_deployment
from .deployment import record_orphaned_guest_recovery as _record_orphaned_guest_recovery_from_deployment
from .deployment import recover_orphaned_deployed_guests as _recover_orphaned_deployed_guests_from_deployment
from .deployment import (
    recover_orphaned_locked_guest_statuses as _recover_orphaned_locked_guest_statuses_from_deployment,
)
from .execution import BattleOptions
from .execution import execute_battle as _execute_battle
from .locking import collect_guest_ids as _collect_guest_ids
from .locking import collect_manor_ids as _collect_manor_ids
from .locking import load_locked_battle_participants as _load_locked_battle_participants_from_locking
from .locking import lock_guest_rows as _lock_guest_rows
from .locking import lock_manor_rows as _lock_manor_rows
from .locking import mark_locked_guests_deployed as _mark_locked_guests_deployed
from .locking import refresh_guest_instances as _refresh_guest_instances
from .locking import release_deployed_guests as _release_deployed_guests
from .locking import validate_locked_guest_statuses as _validate_locked_guest_statuses
from .models import BattleReport
from .setup import build_battle_options as _build_battle_options
from .setup import resolve_attacker_guests_for_battle as _resolve_attacker_guests

logger = logging.getLogger(__name__)
BATTLE_ORPHANED_DEPLOYED_RECOVERY_COUNTER = _BATTLE_ORPHANED_DEPLOYED_RECOVERY_COUNTER

_extract_defender_tech_profile = _execution._extract_defender_tech_profile
validate_troop_capacity = _execution.validate_troop_capacity


def _load_locked_battle_participants(
    guest_ids: list[int],
    *,
    primary_guest_ids: list[int],
    secondary_guest_ids: list[int],
) -> tuple[list[Guest], list[Guest], list[Guest]]:
    return _load_locked_battle_participants_from_locking(
        guest_ids,
        primary_guest_ids=primary_guest_ids,
        secondary_guest_ids=secondary_guest_ids,
        lock_guest_rows_fn=_lock_guest_rows,
    )


def _collect_active_deployment_guest_ids(candidate_ids: list[int]) -> set[int]:
    return _collect_active_deployment_guest_ids_from_deployment(candidate_ids)


def _find_orphaned_deployed_guest_ids(candidate_ids: list[int]) -> list[int]:
    return _find_orphaned_deployed_guest_ids_from_deployment(candidate_ids)


def _record_orphaned_guest_recovery(orphaned_ids: list[int], recovered_count: int) -> None:
    _record_orphaned_guest_recovery_from_deployment(
        orphaned_ids,
        recovered_count,
        logger_override=logger,
        increment_counter_fn=increment_degraded_counter,
    )


def recover_orphaned_deployed_guests(*, guest_ids: list[int] | None = None) -> int:
    return _recover_orphaned_deployed_guests_from_deployment(
        guest_model=Guest,
        deployed_status=GuestStatus.DEPLOYED,
        idle_status=GuestStatus.IDLE,
        guest_ids=guest_ids,
        find_orphaned_deployed_guest_ids_fn=_find_orphaned_deployed_guest_ids,
        record_orphaned_guest_recovery_fn=_record_orphaned_guest_recovery,
    )


def _recover_orphaned_locked_guest_statuses(locked_guests: list[Guest]) -> int:
    return _recover_orphaned_locked_guest_statuses_from_deployment(
        locked_guests,
        guest_model=Guest,
        deployed_status=GuestStatus.DEPLOYED,
        idle_status=GuestStatus.IDLE,
        find_orphaned_deployed_guest_ids_fn=_find_orphaned_deployed_guest_ids,
        record_orphaned_guest_recovery_fn=_record_orphaned_guest_recovery,
    )


def _execute_battle_with_optional_lock(
    *,
    manor,
    guests: list[Guest],
    active_guests: list[Guest],
    options: BattleOptions,
    use_lock: bool,
) -> BattleReport:
    if use_lock:
        with lock_guests_for_battle(active_guests, manor=manor, other_guests=options.defender_guests):
            return _execute_battle(manor, guests, active_guests, options)
    return _execute_battle(manor, guests, active_guests, options)


def _build_defender_guest_and_loadout(
    defender_guests: List[Guest] | None,
    defender_setup: Dict[str, Any] | None,
    defender_limit: int,
    fill_default_troops: bool,
    rng,
    now,
    defender_guest_level: int,
    defender_guest_bonuses: Dict[str, float],
    defender_guest_skills: List[str] | None,
):
    from guests.services.health import recover_guest_hp

    return _build_defender_guest_and_loadout_from_sources(
        defender_guests=defender_guests,
        defender_setup=defender_setup,
        defender_limit=defender_limit,
        fill_default_troops=fill_default_troops,
        rng=rng,
        now=now,
        defender_guest_level=defender_guest_level,
        defender_guest_bonuses=defender_guest_bonuses,
        defender_guest_skills=defender_guest_skills,
        is_live_guest_model_fn=is_live_guest_model,
        recover_guest_hp_fn=recover_guest_hp,
        build_guest_combatants_fn=build_guest_combatants,
        build_named_ai_guests_fn=build_named_ai_guests,
        generate_ai_loadout_fn=generate_ai_loadout,
        normalize_troop_loadout_fn=normalize_troop_loadout,
        build_ai_guests_fn=build_ai_guests,
    )


@contextmanager
def lock_guests_for_battle(
    guests: List[Guest],
    manor=None,
    *,
    other_guests: List[Guest] | None = None,
) -> Generator[List[Guest], None, None]:
    """
    获取门客的战斗锁，防止并发战斗。

    使用 select_for_update 实现行级锁，确保同一门客不会同时参与多场战斗。
    战斗期间门客状态设为 DEPLOYED，战斗结束后恢复。

    Args:
        guests: 要锁定的门客列表
        manor: 可选庄园对象。提供时会先锁定庄园行，统一锁顺序为 Manor -> Guest

    Yields:
        锁定后的门客列表

    Raises:
        BattlePreparationError: 门客处于不可出征状态（战斗中/打工/重伤等）或锁定数据缺失
    """
    other_guests = other_guests or []
    if not guests and not other_guests:
        yield []
        return

    primary_guest_ids = _collect_guest_ids(guests)
    secondary_guest_ids = _collect_guest_ids(other_guests)
    guest_ids = sorted(set(primary_guest_ids + secondary_guest_ids))
    if not guest_ids:
        yield guests
        return

    locked = False
    try:
        with transaction.atomic():
            # Keep the DB transaction limited to lock acquisition + state transition.
            _lock_manor_rows(_collect_manor_ids(manor, guests, other_guests))
            _locked_primary, _locked_secondary, locked_participants = _load_locked_battle_participants(
                guest_ids,
                primary_guest_ids=primary_guest_ids,
                secondary_guest_ids=secondary_guest_ids,
            )

            _recover_orphaned_locked_guest_statuses(locked_participants)
            _validate_locked_guest_statuses(locked_participants)
            _mark_locked_guests_deployed(locked_participants)
            locked = True

        _refresh_guest_instances(guests)
        _refresh_guest_instances(other_guests)
        yield guests
    finally:
        if locked:
            with transaction.atomic():
                _release_deployed_guests(guest_ids)


def simulate_report(
    manor,
    battle_type: str = DEFAULT_BATTLE_TYPE,
    seed: int | None = None,
    rng_version: int = 1,
    battle_engine_version: str = "2",
    troop_loadout: Dict[str, int] | None = None,
    fill_default_troops: bool = True,
    attacker_guests: List[Guest] | None = None,
    defender_setup: Dict[str, Any] | None = None,
    defender_guests: List[Guest] | None = None,
    defender_max_squad: int | None = None,
    drop_table: Dict[str, Any] | None = None,
    opponent_name: str | None = None,
    travel_seconds: int | None = None,
    auto_reward: bool = True,
    drop_handler: Callable[[Dict[str, int]], None] | None = None,
    rng_source: random.Random | None = None,
    send_message: bool = True,
    max_squad: int | None = None,
    apply_damage: bool = True,
    use_lock: bool = True,
    attacker_tech_levels: Dict[str, int] | None = None,
    attacker_guest_bonuses: Dict[str, float] | None = None,
    attacker_guest_skills: List[str] | None = None,
    attacker_manor=None,
    defender_manor=None,
    validate_attacker_troop_capacity: bool = True,
) -> BattleReport:
    """
    Top-level entry point for simulating a battle and producing reports/messages.

    Args:
        manor: 庄园实例
        battle_type: 战斗类型
        seed: 基础随机种子
        rng_version: 随机派生算法版本
        battle_engine_version: 战斗引擎版本
        troop_loadout: 兵种配置
        fill_default_troops: troop_loadout 为空/全0时是否填充默认兵力
        attacker_guests: 攻击方门客列表
        defender_setup: 防守方配置
        defender_guests: 防守方门客列表（真实门客，用于PVP等场景）
        defender_max_squad: 防守方最大出战人数（如PVP防守方的游侠宝塔上限）
        drop_table: 掉落表
        opponent_name: 对手名称
        travel_seconds: 行军时间（秒）
        auto_reward: 是否自动发放奖励
        drop_handler: 自定义掉落处理函数
        rng_source: 兼容种子来源；仅在 seed 为空时抽取一次基础种子，显式 seed 优先
        send_message: 是否发送战报消息
        max_squad: 最大出战人数
        apply_damage: 是否应用伤害到门客
        use_lock: 是否使用并发锁（防止门客同时参与多场战斗）
        attacker_tech_levels: 攻击方科技等级字典（可选，供敌方使用）
        attacker_guest_bonuses: 攻击方门客属性加成（可选）
        attacker_guest_skills: 攻击方门客临时技能覆盖（可选）
        attacker_manor: 攻击方科技来源庄园（可选，默认为 manor）
        defender_manor: 防守方庄园（可选，用于城防建筑）
        validate_attacker_troop_capacity: 是否校验攻击方带兵上限（默认开启）

    Returns:
        BattleReport: 战斗报告实例
    """
    # 确定本场上阵人数（默认遵循庄园可上阵上限）
    limit = max_squad or (getattr(manor, "max_squad_size", None) or MAX_SQUAD)
    guests, active_guests = _resolve_attacker_guests(manor, attacker_guests, limit)
    defender_limit = defender_max_squad or limit
    options = _build_battle_options(
        battle_type=battle_type,
        seed=seed,
        rng_version=rng_version,
        battle_engine_version=battle_engine_version,
        troop_loadout=troop_loadout,
        fill_default_troops=fill_default_troops,
        defender_setup=defender_setup,
        defender_guests=defender_guests,
        defender_limit=defender_limit,
        drop_table=drop_table,
        opponent_name=opponent_name,
        travel_seconds=travel_seconds,
        auto_reward=auto_reward,
        drop_handler=drop_handler,
        rng_source=rng_source,
        send_message=send_message,
        limit=limit,
        apply_damage=apply_damage,
        attacker_tech_levels=attacker_tech_levels,
        attacker_guest_bonuses=attacker_guest_bonuses,
        attacker_guest_skills=attacker_guest_skills,
        attacker_manor=attacker_manor,
        defender_manor=defender_manor,
        validate_attacker_troop_capacity=validate_attacker_troop_capacity,
    )
    return _execute_battle_with_optional_lock(
        manor=manor,
        guests=guests,
        active_guests=active_guests,
        options=options,
        use_lock=use_lock,
    )
