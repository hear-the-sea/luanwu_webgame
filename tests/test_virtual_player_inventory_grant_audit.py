from __future__ import annotations

import pytest
from django.utils import timezone

from gameplay.models import (
    BotInventoryDailyCounter,
    BotMaintenanceAttempt,
    BotMaintenanceCycle,
    InventoryItem,
    ItemTemplate,
)
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.contracts import MaintenanceOutcome, MaintenanceTrigger
from gameplay.services.virtual_player_core.maintenance_action_specs import InventoryAcquisitionActionSpec
from gameplay.services.virtual_player_core.projection import DevelopmentIntent
from tests import test_virtual_player_maintenance_v2 as v2_fixtures

FIXED_NOW = v2_fixtures.FIXED_NOW
active_v2_profile = v2_fixtures.active_v2_profile
permissive_reference = v2_fixtures.permissive_reference
released_v2_policy = v2_fixtures.released_v2_policy


@pytest.fixture
def inventory_batch(active_v2_profile, permissive_reference, monkeypatch, request):
    v2_fixtures._isolate_upgrade_candidates(monkeypatch, keep="inventory")
    for builder in (
        "build_virtual_equipment_candidates",
        "build_virtual_skill_learning_candidates",
        "build_virtual_troop_candidates",
    ):
        monkeypatch.setattr(maintenance, builder, lambda **_kwargs: ((), {}))
    templates = tuple(
        ItemTemplate.objects.create(
            key=f"grant_audit_{index}",
            name=f"Grant audit {index}",
            rarity=rarity,
            tradeable=True,
            effect_type=ItemTemplate.EffectType.TOOL,
        )
        for index, rarity in enumerate(("gray", "red"))
    )
    spec = InventoryAcquisitionActionSpec(
        item_template_id=templates[0].id,
        item_key=templates[0].key,
        daily_caps=(),
        batch_id="grant-audit",
        batch_items=(
            (templates[0].id, templates[0].key, (), 1),
            (templates[1].id, templates[1].key, (("rare", 8),), getattr(request, "param", 1)),
        ),
        batch_draws=((1, "gray", templates[0].key, 1.0), (2, "red", templates[1].key, 1.0)),
    )

    def build_batch(**kwargs):
        strength = kwargs["strength_before"]
        return (
            DevelopmentIntent(
                business_key=spec.business_key,
                action_kind=spec.action_kind,
                source_prestige_band=kwargs["prestige_band"],
                target_prestige_band=kwargs["prestige_band"],
                strength_before=strength,
                strength_after=strength,
                utility_score=1.0,
            ),
            spec,
        )

    monkeypatch.setattr(maintenance, "build_virtual_inventory_batch_candidate", build_batch)
    return active_v2_profile, templates


@pytest.mark.django_db
@pytest.mark.parametrize("blocked_by", [None, "daily_cap", "already_stocked"])
def test_inventory_draw_audit_matches_committed_grants(inventory_batch, monkeypatch, blocked_by):
    profile, templates = inventory_batch
    apply_batch = maintenance._apply_inventory_acquisition_locked

    def apply_after_competing_write(manor, spec, *, now):
        # Another writer can consume the global quota after planning. Existing
        # stock is also rechecked by the locked materializer.
        if blocked_by == "daily_cap":
            BotInventoryDailyCounter.objects.update_or_create(
                category="rare",
                counter_date=timezone.localdate(now),
                defaults={"quantity": 8},
            )
        elif blocked_by == "already_stocked":
            InventoryItem.objects.create(manor=manor, template=templates[1], quantity=4)
        return apply_batch(manor, spec, now=now)

    monkeypatch.setattr(maintenance, "_apply_inventory_acquisition_locked", apply_after_competing_write)
    result = maintenance.maintain_virtual_player_v2(profile.id, trigger=MaintenanceTrigger.SCHEDULED, now=FIXED_NOW)

    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.action_kind == "inventory_acquisition"
    assert result.shadow_cost[f"inventory_granted:{templates[0].key}"] == 1
    assert result.shadow_cost.get(f"inventory_granted:{templates[1].key}", 0) == int(blocked_by is None)
    rows = BotMaintenanceAttempt.objects.filter(profile=profile, shadow_cost__kind="inventory_draw")
    assert rows.count() == 2
    ordinary = rows.get(shadow_cost__item_key=templates[0].key)
    rare = rows.get(shadow_cost__item_key=templates[1].key)
    assert ordinary.outcome == "applied"
    assert ordinary.shadow_cost["granted_quantity"] == 1
    assert rare.outcome == ("applied" if blocked_by is None else "no_action")
    assert rare.reason == ("" if blocked_by is None else "inventory_item_not_granted")
    assert rare.shadow_cost["granted_quantity"] == int(blocked_by is None)
    assert InventoryItem.objects.get(manor=profile.manor, template=templates[0]).quantity == 1
    rare_quantity = (
        InventoryItem.objects.filter(manor=profile.manor, template=templates[1])
        .values_list("quantity", flat=True)
        .first()
    )
    assert rare_quantity == {None: 1, "daily_cap": None, "already_stocked": 4}[blocked_by]
    cycle = BotMaintenanceCycle.objects.get(profile=profile, trigger="scheduled")
    assert cycle.action_ordinal == 1
    assert cycle.payload["action_kind_counts"]["inventory_acquisition"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("inventory_batch", [3], indirect=True)
def test_inventory_audit_records_clipped_quantity(inventory_batch, monkeypatch):
    profile, templates = inventory_batch
    apply_batch = maintenance._apply_inventory_acquisition_locked

    def apply_with_one_rare_unit_left(manor, spec, *, now):
        BotInventoryDailyCounter.objects.update_or_create(
            category="rare", counter_date=timezone.localdate(now), defaults={"quantity": 7}
        )
        return apply_batch(manor, spec, now=now)

    monkeypatch.setattr(maintenance, "_apply_inventory_acquisition_locked", apply_with_one_rare_unit_left)
    result = maintenance.maintain_virtual_player_v2(profile.id, trigger=MaintenanceTrigger.SCHEDULED, now=FIXED_NOW)

    assert result.outcome is MaintenanceOutcome.APPLIED
    assert result.shadow_cost[f"inventory_granted:{templates[1].key}"] == 1
    draw = BotMaintenanceAttempt.objects.get(profile=profile, shadow_cost__item_key=templates[1].key)
    assert draw.outcome == "applied"
    assert draw.shadow_cost["granted_quantity"] == 1
    assert InventoryItem.objects.get(manor=profile.manor, template=templates[1]).quantity == 1
    assert (
        BotInventoryDailyCounter.objects.get(category="rare", counter_date=timezone.localdate(FIXED_NOW)).quantity == 8
    )


@pytest.mark.django_db
def test_inventory_and_quota_roll_back_when_audit_commit_fails(inventory_batch, monkeypatch):
    profile, templates = inventory_batch
    before_sequence = profile.maintenance_sequence

    def reject_audit(*_args, **_kwargs):
        raise RuntimeError("inventory audit failed")

    monkeypatch.setattr(maintenance, "_record_policy2_scheduled_cycle_result", reject_audit)
    with pytest.raises(RuntimeError, match="inventory audit failed"):
        maintenance.maintain_virtual_player_v2(profile.id, trigger=MaintenanceTrigger.SCHEDULED, now=FIXED_NOW)

    profile.refresh_from_db()
    assert profile.maintenance_sequence == before_sequence
    assert not InventoryItem.objects.filter(manor=profile.manor, template__in=templates).exists()
    assert not BotInventoryDailyCounter.objects.filter(category="rare", quantity__gt=0).exists()
    assert not BotMaintenanceAttempt.objects.filter(profile=profile, shadow_cost__kind="inventory_draw").exists()
