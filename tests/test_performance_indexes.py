from __future__ import annotations

from gameplay.models import Building, Manor, PlayerTechnology
from guests.models import Guest, RecruitmentCandidate
from guilds.models import GuildMissionRun


def _index_names(model) -> set[str]:
    return {index.name for index in model._meta.indexes}


def _index_fields(model, index_name: str) -> tuple[str, ...]:
    index = next(index for index in model._meta.indexes if index.name == index_name)
    return tuple(index.fields)


def test_periodic_scan_models_expose_matching_composite_indexes() -> None:
    expected_indexes = {
        (Guest, "guest_hp_recovery_scan_idx"): ("status", "last_hp_recovery_at", "id"),
        (Guest, "guest_injury_loyalty_idx"): ("status", "injury_loyalty_processed_at", "id"),
        (GuildMissionRun, "gmr_due_scan_idx"): ("status", "return_at", "id"),
        (Building, "building_due_scan_idx"): ("is_upgrading", "upgrade_complete_at", "id"),
        (PlayerTechnology, "tech_due_scan_idx"): ("is_upgrading", "upgrade_complete_at", "id"),
        (Manor, "manor_resource_updated_idx"): ("resource_updated_at", "id"),
        (RecruitmentCandidate, "guest_candidate_expiry_idx"): ("created_at", "id"),
    }

    for (model, index_name), fields in expected_indexes.items():
        assert index_name in _index_names(model)
        assert _index_fields(model, index_name) == fields
