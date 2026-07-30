from __future__ import annotations

from django.db import DatabaseError, IntegrityError, transaction


def ensure_manor_provisioning(
    manor,
    *,
    created: bool,
    bootstrap_buildings_func,
    ensure_buildings_exist_func,
    grant_initial_peace_shield_func,
    deliver_active_global_mail_campaigns_func,
    provision_rewards: bool = True,
) -> None:
    if created:
        bootstrap_buildings_func(manor)
    else:
        ensure_buildings_exist_func(manor)

    if provision_rewards:
        grant_initial_peace_shield_func(manor)
        deliver_active_global_mail_campaigns_func(manor)


def assign_manor_location_and_name(
    manor,
    *,
    region: str,
    normalized_name: str | None,
    generate_unique_coordinate_func,
    is_manor_name_available_func,
    is_occupied_manor_location_conflict_func,
    manor_model,
    conflict_error_cls,
) -> bool:
    for _ in range(5):
        x, y = generate_unique_coordinate_func(region)
        try:
            with transaction.atomic():
                locked = manor_model.objects.select_for_update().get(pk=manor.pk)
                if locked.coordinate_x > 0 and locked.coordinate_y > 0:
                    if normalized_name and not locked.name:
                        locked.name = normalized_name
                        locked.save(update_fields=["name"])
                    return True
                locked.region = region
                locked.coordinate_x = x
                locked.coordinate_y = y
                update_fields = ["region", "coordinate_x", "coordinate_y"]
                if normalized_name and not locked.name:
                    locked.name = normalized_name
                    update_fields.append("name")
                locked.save(update_fields=update_fields)
            return True
        except IntegrityError as exc:
            if normalized_name and not is_manor_name_available_func(normalized_name, exclude_manor_id=manor.id):
                raise conflict_error_cls("该庄园名称已被使用") from exc
            if is_occupied_manor_location_conflict_func(exc):
                continue
            raise
    return False


def grant_initial_peace_shield(
    manor,
    *,
    initial_peace_shield_keys: tuple[str, ...],
    item_template_model,
    manor_model,
    timezone_module,
    logger,
) -> None:
    available_keys = set(
        item_template_model.objects.filter(key__in=initial_peace_shield_keys).values_list("key", flat=True)
    )
    shield_key = next((key for key in initial_peace_shield_keys if key in available_keys), None)
    if not shield_key:
        logger.warning("Initial peace shield template not found, skipped: manor_id=%s", manor.pk)
        return

    try:
        from ..inventory.core import add_item_to_inventory_locked

        granted_at = timezone_module.now()
        with transaction.atomic():
            locked_manor = manor_model.objects.select_for_update().get(pk=manor.pk)
            if locked_manor.initial_peace_shield_granted_at:
                manor.initial_peace_shield_granted_at = locked_manor.initial_peace_shield_granted_at
                return

            add_item_to_inventory_locked(locked_manor, shield_key, 1)
            locked_manor.initial_peace_shield_granted_at = granted_at
            locked_manor.save(update_fields=["initial_peace_shield_granted_at"])
        manor.initial_peace_shield_granted_at = granted_at
    except DatabaseError:
        logger.exception("Failed to grant initial peace shield: manor_id=%s item_key=%s", manor.pk, shield_key)


def deliver_active_global_mail_campaigns(
    manor,
    *,
    is_missing_global_mail_schema_error_func,
    logger,
) -> None:
    try:
        from ..global_mail import deliver_active_global_mail_campaigns as deliver_func

        deliver_func(manor)
    except DatabaseError as exc:
        if is_missing_global_mail_schema_error_func(exc):
            logger.warning(
                "Skipped active global mail delivery because schema is unavailable (run migrations): manor_id=%s error=%s",
                manor.pk,
                exc,
            )
            return
        logger.exception("Failed to deliver active global mail campaigns: manor_id=%s", manor.pk)


def is_missing_global_mail_schema_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    if ("globalmailcampaign" not in message) and ("globalmaildelivery" not in message):
        return False
    return ("doesn't exist" in message) or ("no such table" in message) or ("undefined table" in message)


def generate_unique_coordinate(region: str, *, manor_model, logger) -> tuple[int, int]:
    import random

    from ...constants import PVPConstants

    for _ in range(10):
        candidates: list[tuple[int, int]] = []
        for __ in range(20):
            candidates.append(
                (
                    random.randint(PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX),
                    random.randint(PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX),
                )
            )
        occupied = set(
            manor_model.objects.filter(
                region=region,
                coordinate_x__in=[x for x, _ in candidates],
                coordinate_y__in=[y for _, y in candidates],
            ).values_list("coordinate_x", "coordinate_y")
        )
        for x, y in candidates:
            if (x, y) not in occupied:
                return x, y

    for _ in range(200):
        x = random.randint(PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX)
        y = random.randint(PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX)
        if not manor_model.objects.filter(region=region, coordinate_x=x, coordinate_y=y).exists():
            return x, y

    logger.warning("Failed to find a unique coordinate for region=%s; using random fallback", region)
    return random.randint(PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX), random.randint(
        PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX
    )


def bootstrap_buildings(manor, *, building_type_model, building_model) -> None:
    building_types = list(building_type_model.objects.all())
    buildings_to_create = [building_model(manor=manor, building_type=bt) for bt in building_types]
    building_model.objects.bulk_create(buildings_to_create, ignore_conflicts=True)


def ensure_buildings_exist(manor, *, building_type_model, building_model) -> None:
    existing = set(manor.buildings.values_list("building_type_id", flat=True))
    missing = list(building_type_model.objects.exclude(id__in=existing))
    if missing:
        buildings_to_create = [building_model(manor=manor, building_type=bt) for bt in missing]
        building_model.objects.bulk_create(buildings_to_create)


__all__ = [
    "assign_manor_location_and_name",
    "bootstrap_buildings",
    "deliver_active_global_mail_campaigns",
    "ensure_buildings_exist",
    "ensure_manor_provisioning",
    "generate_unique_coordinate",
    "grant_initial_peace_shield",
    "is_missing_global_mail_schema_error",
]
