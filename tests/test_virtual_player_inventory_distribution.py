from gameplay.services.virtual_player_core.bootstrap_assets import _inventory_targets
from gameplay.services.virtual_player_core.bootstrap_catalog import BootstrapCatalog, InventoryCatalogEntry
from gameplay.services.virtual_player_core.random_context import RandomContext
from gameplay.services.virtual_player_core.virtual_assets import (
    VirtualAssetCandidate,
    draw_inventory_batch,
    inventory_rarity_cap_for_band,
)


def _candidate(rarity: str) -> VirtualAssetCandidate:
    return VirtualAssetCandidate(key=f"equip_{rarity}", kind="equip_weapon", rarity=rarity, weight=1)


def test_inventory_rarity_respects_prestige_band_when_building_stage_is_higher():
    stage_caps = {1: "green", 7: "blue", 11: "purple", 16: "orange"}
    band_stage_caps = {"junior": 6, "middle": 10, "senior": 15, "veteran": 20}

    for band, expected in (("junior", "green"), ("middle", "blue"), ("senior", "purple"), ("veteran", "orange")):
        assert (
            inventory_rarity_cap_for_band(
                prestige_band=band,
                growth_stage=30,
                stage_caps=stage_caps,
                band_stage_caps=band_stage_caps,
            )
            == expected
        )

    batch = draw_inventory_batch(
        [_candidate(color) for color in ("green", "blue", "purple", "orange")],
        archetype="balanced",
        prestige_band="senior",
        growth_stage=30,
        seed=11,
        stage_caps=stage_caps,
        band_stage_caps=band_stage_caps,
        color_weights_by_band={"senior": {"green": 1, "blue": 1, "purple": 1, "orange": 100}},
    )
    assert "orange" not in {candidate.rarity for candidate in batch.candidates}


def test_stocked_common_color_does_not_make_orange_certain():
    candidates = [_candidate("green"), _candidate("orange")]
    orange_batches = sum(
        bool(
            draw_inventory_batch(
                candidates,
                archetype="balanced",
                prestige_band="veteran",
                growth_stage=20,
                seed=seed,
                used_keys={"equip_green"},
                color_weights_by_band={"veteran": {"green": 99, "orange": 1}},
            ).candidates
        )
        for seed in range(100)
    )
    assert orange_batches < 10


def test_bootstrap_inventory_uses_band_weights_and_keeps_rare_items_for_maintenance():
    catalog = BootstrapCatalog(
        buildings=(),
        technologies=(),
        guests=(),
        gear=(),
        skills=(),
        troops=(),
        inventory=tuple(
            InventoryCatalogEntry(
                key=f"equip_{color}",
                effect_type="equip_weapon",
                rarity=color,
                tradeable=True,
                price=1,
                storage_space=1,
                effect_payload_digest="0" * 64,
            )
            for color in ("black", "blue", "green", "orange", "purple", "red")
        ),
        digest="0" * 64,
    )
    config = {
        "growth": {"stage_caps": {"newbie": 3, "middle": 10, "veteran": 20}},
        "projection": {
            "inventory_max_rarity_by_stage": {1: "green", 7: "blue", 11: "purple", 16: "orange"},
            "inventory_template_slots_by_archetype": {"balanced": 4},
            "inventory_color_weights_by_prestige_band": {
                "newbie": {"black": 0, "green": 1, "blue": 100},
                "middle": {"black": 0, "green": 0, "blue": 1, "red": 100},
                "veteran": {"black": 0, "green": 0, "blue": 1, "red": 100, "purple": 100, "orange": 100},
            },
        },
    }
    context = RandomContext(
        rng_version=1,
        growth_seed=123,
        engine_version=2,
        plan_schema_version=1,
        policy_version=2,
        maintenance_sequence=0,
    )

    def selected_colors(band: str) -> set[str]:
        targets = _inventory_targets(
            context=context,
            archetype="balanced",
            prestige_band=band,
            growth_stage=20,
            config=config,
            catalog=catalog,
            historical_age_days=0,
        )
        return {target.template_key.removeprefix("equip_") for target in targets}

    assert selected_colors("newbie") == {"green"}
    assert selected_colors("middle") == {"blue"}
    assert selected_colors("veteran") == {"blue"}
