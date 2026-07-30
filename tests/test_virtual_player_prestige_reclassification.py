from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import BotProfile
from gameplay.services.virtual_player_core.profile_management import reclassify_virtual_player_prestige_bands_batch

pytestmark = pytest.mark.django_db


def _create_profile(
    django_user_model,
    *,
    username: str,
    prestige: int,
    current_band: str,
    growth_seed: int,
) -> BotProfile:
    user = django_user_model.objects.create_user(
        username=username,
        password="pass123",
    )
    manor = user.manor
    manor.region = "north"
    manor.prestige = prestige
    manor.silver = 4321
    manor.save(update_fields=["region", "prestige", "silver"])
    now = timezone.now()
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.GUARD,
        state=BotProfile.State.ABANDONED,
        prestige_band="legacy_target",
        target_prestige_band="legacy_target",
        current_prestige_band=current_band,
        growth_seed=growth_seed,
        growth_stage=7,
        next_growth_at=now + timedelta(hours=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


def test_reclassification_defaults_to_read_only_and_resumes_by_profile_id(
    django_user_model,
) -> None:
    profiles = [
        _create_profile(
            django_user_model,
            username="reclassify_newbie",
            prestige=499,
            current_band="wrong",
            growth_seed=82_001,
        ),
        _create_profile(
            django_user_model,
            username="reclassify_junior",
            prestige=500,
            current_band="wrong",
            growth_seed=82_002,
        ),
        _create_profile(
            django_user_model,
            username="reclassify_mythic",
            prestige=240_000,
            current_band="wrong",
            growth_seed=82_003,
        ),
    ]
    snapshots = {
        profile.id: {
            "prestige_band": profile.prestige_band,
            "target_prestige_band": profile.target_prestige_band,
            "engine_version": profile.engine_version,
            "state": profile.state,
            "growth_stage": profile.growth_stage,
            "prestige": profile.manor.prestige,
            "silver": profile.manor.silver,
        }
        for profile in profiles
    }

    dry_run = reclassify_virtual_player_prestige_bands_batch(batch_size=2)

    assert (dry_run.scanned, dry_run.changed, dry_run.skipped) == (2, 2, 0)
    assert dry_run.last_profile_id == profiles[1].id
    for profile in profiles:
        profile.refresh_from_db()
        assert profile.current_prestige_band == "wrong"

    first = reclassify_virtual_player_prestige_bands_batch(
        batch_size=2,
        apply=True,
    )
    second = reclassify_virtual_player_prestige_bands_batch(
        after_id=first.last_profile_id or 0,
        batch_size=2,
        apply=True,
    )

    assert (first.scanned, first.changed, first.locked, first.failed) == (2, 2, 0, 0)
    assert (second.scanned, second.changed, second.locked, second.failed) == (
        1,
        1,
        0,
        0,
    )
    expected_bands = ("newbie", "junior", "mythic")
    for profile, expected_band in zip(profiles, expected_bands, strict=True):
        profile.refresh_from_db()
        profile.manor.refresh_from_db()
        assert profile.current_prestige_band == expected_band
        snapshot = snapshots[profile.id]
        assert profile.prestige_band == snapshot["prestige_band"]
        assert profile.target_prestige_band == snapshot["target_prestige_band"]
        assert profile.engine_version == snapshot["engine_version"]
        assert profile.state == snapshot["state"]
        assert profile.growth_stage == snapshot["growth_stage"]
        assert profile.manor.prestige == snapshot["prestige"]
        assert profile.manor.silver == snapshot["silver"]


def test_reclassification_is_idempotent_at_all_v2_boundaries(
    django_user_model,
) -> None:
    boundaries = (
        (0, "newbie"),
        (500, "junior"),
        (2000, "middle"),
        (8000, "senior"),
        (30_000, "veteran"),
        (60_000, "elite"),
        (120_000, "legend"),
        (240_000, "mythic"),
    )
    profiles = [
        _create_profile(
            django_user_model,
            username=f"reclassify_boundary_{index}",
            prestige=prestige,
            current_band="wrong",
            growth_seed=83_000 + index,
        )
        for index, (prestige, _band) in enumerate(boundaries)
    ]

    first = reclassify_virtual_player_prestige_bands_batch(
        batch_size=len(profiles),
        apply=True,
    )
    second = reclassify_virtual_player_prestige_bands_batch(
        batch_size=len(profiles),
        apply=True,
    )

    assert (first.changed, first.skipped, first.failed) == (len(profiles), 0, 0)
    assert (second.changed, second.skipped, second.failed) == (0, len(profiles), 0)
    assert [BotProfile.objects.get(pk=profile.pk).current_prestige_band for profile in profiles] == [
        band for _prestige, band in boundaries
    ]
