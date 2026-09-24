from __future__ import annotations

import pytest

from gameplay.models import BotProfile, Building, InventoryItem, ItemTemplate, Manor
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.contracts import MaintenanceTrigger
from tests import test_virtual_player_maintenance_v2 as v2_fixtures

FIXED_NOW = v2_fixtures.FIXED_NOW
active_v2_profile = v2_fixtures.active_v2_profile
permissive_reference = v2_fixtures.permissive_reference
released_v2_policy = v2_fixtures.released_v2_policy


@pytest.mark.django_db
def test_old_stage_can_draw_rare_inventory_without_redrawing_stocked_items(
    active_v2_profile, permissive_reference, monkeypatch
) -> None:
    # A middle-band manor may stock red items after stage repair.
    Manor.objects.filter(pk=active_v2_profile.manor_id).update(prestige=3_000)
    BotProfile.objects.filter(pk=active_v2_profile.pk).update(
        prestige_band="middle",
        current_prestige_band="middle",
    )
    Building.objects.filter(manor_id=active_v2_profile.manor_id, building_type__key="silver_vault").update(level=11)
    templates = tuple(
        ItemTemplate.objects.create(
            key=f"stage_sync_inventory_{index}",
            name=f"Stage sync inventory {index}",
            effect_type=ItemTemplate.EffectType.TOOL,
            rarity="red",
            tradeable=True,
        )
        for index in range(2)
    )
    stocked = InventoryItem.objects.create(
        manor_id=active_v2_profile.manor_id,
        template=templates[0],
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    monkeypatch.setattr(
        maintenance,
        "_load_virtual_projection_pools",
        lambda **_kwargs: maintenance._VirtualProjectionPools(
            skill_books=(), skills=(), gear_templates=(), inventory_templates=templates, rare_inventory_quantity_today=0
        ),
    )
    build_batch = maintenance.build_virtual_inventory_batch_candidate
    generated_specs = []

    def capture_batch(**kwargs):
        intent, spec = build_batch(**kwargs)
        generated_specs.append(spec)
        return intent, spec

    monkeypatch.setattr(maintenance, "build_virtual_inventory_batch_candidate", capture_batch)
    maintenance.build_virtual_player_v2_maintenance_plan(
        active_v2_profile.id,
        trigger=MaintenanceTrigger.SCHEDULED,
        now=FIXED_NOW,
        _candidate_exclusions=tuple(f"unrelated-retry-{index}" for index in range(5)),
    )

    assert len(generated_specs) == 1
    assert generated_specs[0] is not None
    assert {entry[1] for entry in generated_specs[0].batch_items} == {templates[1].key}
    # Planning repairs the effective stage without writing the profile or inventory.
    active_v2_profile.refresh_from_db()
    stocked.refresh_from_db()
    assert active_v2_profile.growth_stage == 1
    assert stocked.quantity == 1
    assert not InventoryItem.objects.filter(manor_id=active_v2_profile.manor_id, template=templates[1]).exists()
