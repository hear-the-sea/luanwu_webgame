from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from django.db.models import F

from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

if TYPE_CHECKING:
    from ..models import PlayerTechnology


class TechnologyUpgradeQuoteStaleError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TechnologyUpgradeQuote:
    manor_id: int
    technology_key: str
    technology_name: str
    current_level: int
    target_level: int
    max_level: int
    silver_cost: int
    active_upgrade_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "active_upgrade_count": self.active_upgrade_count,
            "current_level": self.current_level,
            "manor_id": self.manor_id,
            "max_level": self.max_level,
            "silver_cost": self.silver_cost,
            "target_level": self.target_level,
            "technology_key": self.technology_key,
            "technology_name": self.technology_name,
        }


def _validate_technology_upgrade_manor(manor: Any) -> int:
    manor_id = getattr(manor, "pk", None)
    if isinstance(manor_id, bool) or not isinstance(manor_id, int) or manor_id < 1:
        raise ValueError("technology upgrade requires a persisted Manor row")
    return manor_id


def _resolve_technology_template(
    tech_key: str,
    *,
    get_technology_template_func: Callable[[str], dict[str, Any] | None],
    technology_not_found_error_cls: type[Exception],
) -> dict[str, Any]:
    template = get_technology_template_func(tech_key)
    if not template:
        raise technology_not_found_error_cls(tech_key)
    return template


def _build_technology_upgrade_quote(
    manor: Any,
    tech_key: str,
    technology: Any | None,
    template: dict[str, Any],
    *,
    calculate_upgrade_cost_func: Callable[[str, int], int],
    max_concurrent_tech_upgrades: int,
    technology_upgrade_in_progress_error_cls: type[Exception],
    technology_max_level_error_cls: type[Exception],
    technology_concurrent_upgrade_limit_error_cls: type[Exception],
    active_upgrade_count: int | None = None,
) -> TechnologyUpgradeQuote:
    from ..models import PlayerTechnology

    manor_id = _validate_technology_upgrade_manor(manor)
    technology_name = str(template["name"])
    if technology is None:
        current_level = 0
    else:
        if int(technology.manor_id) != manor_id or str(technology.tech_key) != tech_key:
            raise ValueError("technology does not belong to the supplied Manor")
        if technology.is_upgrading:
            raise technology_upgrade_in_progress_error_cls(
                tech_key,
                technology_name,
            )
        current_level = int(technology.level)

    max_level = int(template.get("max_level", 10))
    if current_level >= max_level:
        raise technology_max_level_error_cls(
            tech_key,
            technology_name,
            max_level,
        )

    if active_upgrade_count is None:
        active_upgrade_count = PlayerTechnology.objects.filter(
            manor_id=manor_id,
            is_upgrading=True,
        ).count()
    if active_upgrade_count >= max_concurrent_tech_upgrades:
        raise technology_concurrent_upgrade_limit_error_cls(max_concurrent_tech_upgrades)

    return TechnologyUpgradeQuote(
        manor_id=manor_id,
        technology_key=tech_key,
        technology_name=technology_name,
        current_level=current_level,
        target_level=current_level + 1,
        max_level=max_level,
        silver_cost=int(calculate_upgrade_cost_func(tech_key, current_level)),
        active_upgrade_count=active_upgrade_count,
    )


def quote_technology_upgrade(
    manor: Any,
    tech_key: str,
    *,
    get_technology_template_func: Callable[[str], dict[str, Any] | None],
    calculate_upgrade_cost_func: Callable[[str, int], int],
    max_concurrent_tech_upgrades: int,
    technology_not_found_error_cls: type[Exception],
    technology_upgrade_in_progress_error_cls: type[Exception],
    technology_max_level_error_cls: type[Exception],
    technology_concurrent_upgrade_limit_error_cls: type[Exception],
    technologies: Sequence[Any] | None = None,
) -> TechnologyUpgradeQuote:
    """Validate and freeze the current one-level technology upgrade inputs."""

    from ..models import PlayerTechnology

    manor_id = _validate_technology_upgrade_manor(manor)
    template = _resolve_technology_template(
        tech_key,
        get_technology_template_func=get_technology_template_func,
        technology_not_found_error_cls=technology_not_found_error_cls,
    )
    active_upgrade_count = None
    if technologies is None:
        technology = PlayerTechnology.objects.filter(
            manor_id=manor_id,
            tech_key=tech_key,
        ).first()
    else:
        technology_snapshot = tuple(technologies)
        if any(int(candidate.manor_id) != manor_id for candidate in technology_snapshot):
            raise ValueError("technology snapshot contains a row from another Manor")
        technology = next(
            (candidate for candidate in technology_snapshot if str(candidate.tech_key) == tech_key),
            None,
        )
        active_upgrade_count = sum(bool(candidate.is_upgrading) for candidate in technology_snapshot)
    return _build_technology_upgrade_quote(
        manor,
        tech_key,
        technology,
        template,
        calculate_upgrade_cost_func=calculate_upgrade_cost_func,
        max_concurrent_tech_upgrades=max_concurrent_tech_upgrades,
        technology_upgrade_in_progress_error_cls=technology_upgrade_in_progress_error_cls,
        technology_max_level_error_cls=technology_max_level_error_cls,
        technology_concurrent_upgrade_limit_error_cls=technology_concurrent_upgrade_limit_error_cls,
        active_upgrade_count=active_upgrade_count,
    )


def _require_technology_upgrade_atomic(transaction_module: Any) -> None:
    if not transaction_module.get_connection().in_atomic_block:
        raise RuntimeError("apply_technology_upgrade_locked must be called inside transaction.atomic()")


def _assert_current_technology_upgrade_quote_locked(
    manor: Any,
    quote: TechnologyUpgradeQuote,
    *,
    get_technology_template_func: Callable[[str], dict[str, Any] | None],
    calculate_upgrade_cost_func: Callable[[str, int], int],
    max_concurrent_tech_upgrades: int,
    transaction_module: Any,
    technology_not_found_error_cls: type[Exception],
    technology_upgrade_in_progress_error_cls: type[Exception],
    technology_max_level_error_cls: type[Exception],
    technology_concurrent_upgrade_limit_error_cls: type[Exception],
    technologies: Sequence[Any] | None = None,
    technologies_locked: bool = False,
) -> tuple[TechnologyUpgradeQuote, PlayerTechnology | None]:
    from ..models import PlayerTechnology

    _require_technology_upgrade_atomic(transaction_module)
    if not isinstance(quote, TechnologyUpgradeQuote):
        raise TypeError("quote must be TechnologyUpgradeQuote")
    manor_id = _validate_technology_upgrade_manor(manor)
    if quote.manor_id != manor_id:
        raise TechnologyUpgradeQuoteStaleError("technology upgrade quote does not match the locked Manor")

    technology_snapshot = None if technologies is None else tuple(technologies)
    if technologies_locked:
        if technology_snapshot is None:
            raise ValueError("locked technology snapshot is required")
        technology = next(
            (candidate for candidate in technology_snapshot if str(candidate.tech_key) == quote.technology_key),
            None,
        )
    else:
        technology = (
            PlayerTechnology.objects.select_for_update()
            .filter(
                manor_id=manor_id,
                tech_key=quote.technology_key,
            )
            .first()
        )
    template = _resolve_technology_template(
        quote.technology_key,
        get_technology_template_func=get_technology_template_func,
        technology_not_found_error_cls=technology_not_found_error_cls,
    )
    active_upgrade_count = (
        None if technology_snapshot is None else sum(bool(candidate.is_upgrading) for candidate in technology_snapshot)
    )
    if technology_snapshot is not None and any(
        int(candidate.manor_id) != manor_id for candidate in technology_snapshot
    ):
        raise ValueError("technology snapshot contains a row from another Manor")
    current_quote = _build_technology_upgrade_quote(
        manor,
        quote.technology_key,
        technology,
        template,
        calculate_upgrade_cost_func=calculate_upgrade_cost_func,
        max_concurrent_tech_upgrades=max_concurrent_tech_upgrades,
        technology_upgrade_in_progress_error_cls=technology_upgrade_in_progress_error_cls,
        technology_max_level_error_cls=technology_max_level_error_cls,
        technology_concurrent_upgrade_limit_error_cls=technology_concurrent_upgrade_limit_error_cls,
        active_upgrade_count=active_upgrade_count,
    )
    if current_quote != quote:
        raise TechnologyUpgradeQuoteStaleError("technology upgrade quote is stale; retry with current state")
    return current_quote, technology


def _consume_technology_upgrade_quote_locked(
    manor: Any,
    quote: TechnologyUpgradeQuote,
    *,
    sync_production: bool,
    insufficient_resource_error_cls: type[Exception],
) -> None:
    from core.exceptions import InsufficientResourceError

    from ..models import ResourceEvent
    from .manor.prestige import add_prestige_silver_locked
    from .resources import spend_resources_locked

    try:
        spend_resources_locked(
            manor,
            {"silver": quote.silver_cost},
            reason=ResourceEvent.Reason.TECH_UPGRADE,
            note=f"升级{quote.technology_name}",
            sync_production=sync_production,
        )
    except InsufficientResourceError as exc:
        raise insufficient_resource_error_cls(
            "silver",
            quote.silver_cost,
            manor.silver,
        ) from exc
    add_prestige_silver_locked(manor, quote.silver_cost)


def _schedule_technology_cache_invalidation(
    manor_id: int,
    *,
    transaction_module: Any,
    invalidate_home_stats_cache_func: Callable[[int], None],
) -> None:
    def _invalidate_after_commit() -> None:
        invalidate_home_stats_cache_func(manor_id)

    transaction_module.on_commit(_invalidate_after_commit, robust=True)


def _save_technology_state(technology: PlayerTechnology) -> None:
    if technology.pk is None:
        technology.save(force_insert=True)
        return
    technology.save(update_fields=["level", "is_upgrading", "upgrade_complete_at"])


def apply_technology_upgrade_locked(
    manor: Any,
    quote: TechnologyUpgradeQuote,
    *,
    get_technology_template_func: Callable[[str], dict[str, Any] | None],
    calculate_upgrade_cost_func: Callable[[str, int], int],
    max_concurrent_tech_upgrades: int,
    transaction_module: Any,
    invalidate_home_stats_cache_func: Callable[[int], None],
    technology_not_found_error_cls: type[Exception],
    technology_upgrade_in_progress_error_cls: type[Exception],
    technology_max_level_error_cls: type[Exception],
    technology_concurrent_upgrade_limit_error_cls: type[Exception],
    insufficient_resource_error_cls: type[Exception],
    sync_production: bool = True,
    technologies: Sequence[Any] | None = None,
    technologies_locked: bool = False,
) -> PlayerTechnology:
    """Synchronously complete one level with a caller-held Manor lock."""

    from ..models import PlayerTechnology

    current_quote, technology = _assert_current_technology_upgrade_quote_locked(
        manor,
        quote,
        get_technology_template_func=get_technology_template_func,
        calculate_upgrade_cost_func=calculate_upgrade_cost_func,
        max_concurrent_tech_upgrades=max_concurrent_tech_upgrades,
        transaction_module=transaction_module,
        technology_not_found_error_cls=technology_not_found_error_cls,
        technology_upgrade_in_progress_error_cls=technology_upgrade_in_progress_error_cls,
        technology_max_level_error_cls=technology_max_level_error_cls,
        technology_concurrent_upgrade_limit_error_cls=technology_concurrent_upgrade_limit_error_cls,
        technologies=technologies,
        technologies_locked=technologies_locked,
    )
    if technology is None:
        technology = PlayerTechnology(
            manor=manor,
            tech_key=current_quote.technology_key,
            level=current_quote.current_level,
        )

    _consume_technology_upgrade_quote_locked(
        manor,
        current_quote,
        sync_production=sync_production,
        insufficient_resource_error_cls=insufficient_resource_error_cls,
    )
    technology.level = current_quote.target_level
    technology.is_upgrading = False
    technology.upgrade_complete_at = None
    _save_technology_state(technology)
    technology.manor = manor
    _schedule_technology_cache_invalidation(
        int(manor.pk),
        transaction_module=transaction_module,
        invalidate_home_stats_cache_func=invalidate_home_stats_cache_func,
    )
    return technology


def start_technology_upgrade_locked(
    manor: Any,
    quote: TechnologyUpgradeQuote,
    *,
    get_technology_template_func: Callable[[str], dict[str, Any] | None],
    calculate_upgrade_cost_func: Callable[[str, int], int],
    max_concurrent_tech_upgrades: int,
    transaction_module: Any,
    invalidate_home_stats_cache_func: Callable[[int], None],
    technology_not_found_error_cls: type[Exception],
    technology_upgrade_in_progress_error_cls: type[Exception],
    technology_max_level_error_cls: type[Exception],
    technology_concurrent_upgrade_limit_error_cls: type[Exception],
    insufficient_resource_error_cls: type[Exception],
    schedule_technology_completion_func: Callable[[Any, int], None],
    sync_production: bool = True,
    technologies: Sequence[Any] | None = None,
    technologies_locked: bool = False,
    now: Any | None = None,
) -> Any:
    """Charge and start a technology timer without incrementing its level.

    ``apply_technology_upgrade_locked`` intentionally remains the synchronous
    primitive used by the legacy user flow.  V2 maintenance calls this start
    primitive so the existing completion task is the sole level-increment
    owner.
    """

    from datetime import timedelta

    from django.utils import timezone

    from ..models import PlayerTechnology

    current_quote, technology = _assert_current_technology_upgrade_quote_locked(
        manor,
        quote,
        get_technology_template_func=get_technology_template_func,
        calculate_upgrade_cost_func=calculate_upgrade_cost_func,
        max_concurrent_tech_upgrades=max_concurrent_tech_upgrades,
        transaction_module=transaction_module,
        technology_not_found_error_cls=technology_not_found_error_cls,
        technology_upgrade_in_progress_error_cls=technology_upgrade_in_progress_error_cls,
        technology_max_level_error_cls=technology_max_level_error_cls,
        technology_concurrent_upgrade_limit_error_cls=technology_concurrent_upgrade_limit_error_cls,
        technologies=technologies,
        technologies_locked=technologies_locked,
    )
    if technology is None:
        technology = PlayerTechnology(
            manor=manor,
            tech_key=current_quote.technology_key,
            level=current_quote.current_level,
        )
    _consume_technology_upgrade_quote_locked(
        manor,
        current_quote,
        sync_production=sync_production,
        insufficient_resource_error_cls=insufficient_resource_error_cls,
    )
    duration = technology.upgrade_duration()
    technology.is_upgrading = True
    technology.upgrade_complete_at = (now or timezone.now()) + timedelta(seconds=duration)
    _save_technology_state(technology)
    technology.manor = manor
    schedule_technology_completion_func(technology, duration)
    return technology


def should_skip_tech_refresh_by_local_fallback(
    local_refresh_state: dict[int, float],
    *,
    state_lock: Any,
    max_size: int,
    manor_id: int,
    min_interval: int,
    monotonic_func: Callable[[], float],
) -> bool:
    if manor_id <= 0 or min_interval <= 0:
        return False

    now_monotonic = monotonic_func()
    stale_before = now_monotonic - max(min_interval * 2, 60)

    with state_lock:
        last_refresh = local_refresh_state.get(manor_id)
        if last_refresh is not None and now_monotonic - last_refresh < min_interval:
            return True

        local_refresh_state[manor_id] = now_monotonic
        if len(local_refresh_state) > max_size:
            stale_keys = [key for key, ts in local_refresh_state.items() if ts < stale_before]
            for key in stale_keys[:1000]:
                local_refresh_state.pop(key, None)
            if len(local_refresh_state) > max_size:
                for key, _ in sorted(local_refresh_state.items(), key=lambda item: item[1])[:500]:
                    local_refresh_state.pop(key, None)
    return False


def upgrade_technology(
    manor: Any,
    tech_key: str,
    *,
    get_technology_template_func: Callable[[str], dict[str, Any] | None],
    calculate_upgrade_cost_func: Callable[[str, int], int],
    max_concurrent_tech_upgrades: int,
    schedule_technology_completion_func: Callable[[Any, int], None],
    build_technology_upgrade_response_func: Callable[..., dict[str, Any]],
    transaction_module: Any,
    technology_not_found_error_cls: type[Exception],
    technology_upgrade_in_progress_error_cls: type[Exception],
    technology_max_level_error_cls: type[Exception],
    technology_concurrent_upgrade_limit_error_cls: type[Exception],
    insufficient_resource_error_cls: type[Exception],
) -> dict[str, Any]:
    from datetime import timedelta

    from django.utils import timezone

    from ..models import Manor, PlayerTechnology

    template = _resolve_technology_template(
        tech_key,
        get_technology_template_func=get_technology_template_func,
        technology_not_found_error_cls=technology_not_found_error_cls,
    )

    with transaction_module.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        tech = PlayerTechnology.objects.select_for_update().filter(manor=locked_manor, tech_key=tech_key).first()
        quote = _build_technology_upgrade_quote(
            locked_manor,
            tech_key,
            tech,
            template,
            calculate_upgrade_cost_func=calculate_upgrade_cost_func,
            max_concurrent_tech_upgrades=max_concurrent_tech_upgrades,
            technology_upgrade_in_progress_error_cls=technology_upgrade_in_progress_error_cls,
            technology_max_level_error_cls=technology_max_level_error_cls,
            technology_concurrent_upgrade_limit_error_cls=technology_concurrent_upgrade_limit_error_cls,
        )
        if tech is None:
            tech = PlayerTechnology(
                manor=locked_manor,
                tech_key=tech_key,
                level=quote.current_level,
            )

        _consume_technology_upgrade_quote_locked(
            locked_manor,
            quote,
            sync_production=True,
            insufficient_resource_error_cls=insufficient_resource_error_cls,
        )

        duration = tech.upgrade_duration()
        tech.is_upgrading = True
        tech.upgrade_complete_at = timezone.now() + timedelta(seconds=duration)
        _save_technology_state(tech)

        schedule_technology_completion_func(tech, duration)

    return build_technology_upgrade_response_func(template_name=template["name"], duration=duration)


def finalize_technology_upgrade(
    tech: Any,
    *,
    get_technology_template_func: Callable[[str], dict[str, Any] | None],
    resolve_technology_name_func: Callable[[dict[str, Any] | None, str], str],
    send_technology_completion_notification_func: Callable[..., None],
    notify_user_func: Callable[..., Any],
    invalidate_home_stats_cache_func: Callable[[int], None],
    logger: Any,
    send_notification: bool = False,
) -> bool:
    from django.utils import timezone

    if not getattr(tech, "pk", None):
        return False
    now = timezone.now()
    updated = tech.__class__.objects.filter(
        pk=tech.pk,
        is_upgrading=True,
        upgrade_complete_at__isnull=False,
        upgrade_complete_at__lte=now,
    ).update(
        level=F("level") + 1,
        is_upgrading=False,
        upgrade_complete_at=None,
        updated_at=now,
    )
    if updated != 1:
        return False

    tech = tech.__class__.objects.select_related("manor").get(pk=tech.pk)
    template = get_technology_template_func(tech.tech_key)
    tech_name = resolve_technology_name_func(template, tech.tech_key)

    if send_notification:
        send_technology_completion_notification_func(
            tech=tech,
            tech_name=tech_name,
            logger=logger,
            notify_user_func=notify_user_func,
        )

    invalidate_home_stats_cache_func(tech.manor_id)
    return True


def refresh_technology_upgrades(
    manor: Any,
    *,
    settings_obj: Any,
    cache_backend: Any,
    logger: Any,
    should_skip_tech_refresh_by_local_fallback_func: Callable[[int, int], bool],
    finalize_technology_upgrade_func: Callable[..., bool],
) -> int:
    from django.utils import timezone

    min_interval = getattr(settings_obj, "MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS", 0)
    if min_interval > 0 and getattr(manor, "pk", None):
        cache_key = f"tech:refresh:{manor.pk}"
        try:
            if not cache_backend.add(cache_key, "1", timeout=min_interval):
                return 0
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "Technology refresh cache unavailable, fallback to local throttle: %s",
                exc,
                exc_info=True,
            )
            if should_skip_tech_refresh_by_local_fallback_func(int(manor.pk), min_interval):
                return 0

    completed = 0
    upgrading_techs = list(manor.technologies.filter(is_upgrading=True, upgrade_complete_at__lte=timezone.now()))
    for tech in upgrading_techs:
        if finalize_technology_upgrade_func(tech, send_notification=True):
            completed += 1

    return completed


__all__ = [
    "TechnologyUpgradeQuote",
    "TechnologyUpgradeQuoteStaleError",
    "apply_technology_upgrade_locked",
    "finalize_technology_upgrade",
    "quote_technology_upgrade",
    "refresh_technology_upgrades",
    "should_skip_tech_refresh_by_local_fallback",
    "upgrade_technology",
]
