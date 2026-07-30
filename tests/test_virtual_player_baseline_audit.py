from __future__ import annotations

import json
import threading
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.management.commands import audit_virtual_player_baseline as baseline_audit
from gameplay.management.commands.audit_virtual_player_baseline import (
    _write_report_exclusive,
    build_virtual_player_baseline,
)
from gameplay.models import BotProfile, Building, BuildingType, Manor, PlayerTroop
from gameplay.services.manor.core import ensure_manor
from guests.models import (
    GearItem,
    GearSlot,
    GearTemplate,
    Guest,
    GuestArchetype,
    GuestRarity,
    GuestSkill,
    GuestTemplate,
    Skill,
    SkillKind,
)


class _SqlRecorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, execute, sql, params, many, context):
        self.statements.append(str(sql).lstrip().split(None, 1)[0].upper())
        return execute(sql, params, many, context)


@pytest.fixture
def virtual_player_baseline_sample(django_user_model, monkeypatch):
    monkeypatch.setattr(
        "gameplay.management.commands.audit_virtual_player_baseline.virtual_player_prestige_bands",
        lambda: {"junior": (0, None)},
    )
    current_time = timezone.now()
    real_user = django_user_model.objects.create_user(
        username="baseline_real_private_user",
        email="baseline-real-private@example.invalid",
    )
    bot_user = django_user_model.objects.create_user(
        username="baseline_bot_private_user",
        email="baseline-bot-private@example.invalid",
    )
    real_manor = ensure_manor(real_user, region="north")
    bot_manor = ensure_manor(bot_user, region="south")
    Manor.objects.filter(pk=real_manor.pk).update(
        name="baseline-real-manor",
        prestige=1200,
    )
    Manor.objects.filter(pk=bot_manor.pk).update(
        name="baseline-bot-manor",
        prestige=1600,
    )

    civil_template = GuestTemplate.objects.create(
        key="baseline_civil",
        name="Baseline Civil",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GREEN,
    )
    military_template = GuestTemplate.objects.create(
        key="baseline_military",
        name="Baseline Military",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.BLUE,
    )
    real_guest = Guest.objects.create(
        manor=real_manor,
        template=civil_template,
        level=8,
        custom_name="baseline-real-private-guest",
    )
    bot_guest = Guest.objects.create(
        manor=bot_manor,
        template=military_template,
        level=10,
        custom_name="baseline-bot-private-guest",
    )

    gear_template = GearTemplate.objects.create(
        key="baseline_weapon",
        name="Baseline Weapon",
        slot=GearSlot.WEAPON,
        rarity=GuestRarity.GREEN,
    )
    GearItem.objects.create(manor=real_manor, guest=real_guest, template=gear_template, level=2)
    GearItem.objects.create(manor=bot_manor, guest=bot_guest, template=gear_template, level=3)

    skill = Skill.objects.create(
        key="baseline_skill",
        name="Baseline Skill",
        kind=SkillKind.ACTIVE,
        rarity=GuestRarity.GREEN,
    )
    GuestSkill.objects.create(guest=real_guest, skill=skill)
    GuestSkill.objects.create(guest=bot_guest, skill=skill)

    troop_template = TroopTemplate.objects.create(key="baseline_guard", name="Baseline Guard")
    PlayerTroop.objects.create(manor=real_manor, troop_template=troop_template, count=10)
    PlayerTroop.objects.create(manor=bot_manor, troop_template=troop_template, count=20)

    building_type = BuildingType.objects.create(key="baseline_hall", name="Baseline Hall")
    Building.objects.create(manor=real_manor, building_type=building_type, level=2)
    Building.objects.create(manor=bot_manor, building_type=building_type, level=3)

    profile = BotProfile.objects.create(
        manor=bot_manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="junior",
        target_prestige_band="junior",
        current_prestige_band="junior",
        growth_seed=123,
        next_growth_at=current_time + timedelta(hours=2),
        abandon_at=current_time + timedelta(days=30),
        retire_at=current_time + timedelta(days=60),
    )
    return {"real_manor": real_manor, "bot_profile": profile}


@pytest.mark.django_db(transaction=True)
def test_baseline_audit_is_read_only_and_snapshot_checksum_is_reproducible(virtual_player_baseline_sample) -> None:
    del virtual_player_baseline_sample
    counts_before = {
        "profiles": BotProfile.objects.count(),
        "guests": Guest.objects.count(),
        "gear": GearItem.objects.count(),
        "skills": GuestSkill.objects.count(),
        "troops": PlayerTroop.objects.count(),
        "buildings": Building.objects.count(),
    }
    recorder = _SqlRecorder()
    with connection.execute_wrapper(recorder):
        first = build_virtual_player_baseline(
            sample_limit=10,
            minimum_real_profiles=1,
            minimum_bot_profiles=1,
            minimum_profiles_per_band=1,
        )
        second = build_virtual_player_baseline(
            sample_limit=10,
            minimum_real_profiles=1,
            minimum_bot_profiles=1,
            minimum_profiles_per_band=1,
        )

    assert first["status"] == "ready_for_threshold_review"
    assert first["snapshot_checksum"] == second["snapshot_checksum"]
    assert first["source_fingerprints"] == second["source_fingerprints"]
    assert first["cohorts"] == second["cohorts"]
    assert first["cohorts"]["real"]["profile_count"] == 1
    assert first["cohorts"]["v1_bot"]["profile_count"] == 1
    assert first["cohorts"]["real"]["guest_level"]["p50"] == 8
    assert first["cohorts"]["v1_bot"]["guest_level"]["p50"] == 10
    assert first["sampling"]["profile_counts_by_prestige_band"] == {
        "real": {"junior": 1},
        "v1_bot": {"junior": 1},
    }
    expected_isolation = "sqlite_transaction_snapshot" if connection.vendor == "sqlite" else "repeatable_read"
    assert first["audit_runtime"]["snapshot_contract"]["isolation"] == expected_isolation
    assert first["maintenance_runtime"]["status"] == "not_measured_by_read_only_audit"
    assert not {"INSERT", "UPDATE", "DELETE"} & set(recorder.statements)
    assert counts_before == {
        "profiles": BotProfile.objects.count(),
        "guests": Guest.objects.count(),
        "gear": GearItem.objects.count(),
        "skills": GuestSkill.objects.count(),
        "troops": PlayerTroop.objects.count(),
        "buildings": Building.objects.count(),
    }


@pytest.mark.django_db(transaction=True)
def test_baseline_command_emits_machine_readable_report(virtual_player_baseline_sample) -> None:
    del virtual_player_baseline_sample
    stdout = StringIO()

    call_command(
        "audit_virtual_player_baseline",
        sample_limit=10,
        minimum_real_profiles=1,
        minimum_bot_profiles=1,
        minimum_profiles_per_band=1,
        stdout=stdout,
    )

    report = json.loads(stdout.getvalue())
    assert report["schema_version"] == 2
    assert report["status"] == "ready_for_threshold_review"
    assert len(report["snapshot_checksum"]) == 64
    assert report["audit_runtime"]["query_count"] > 0


@pytest.mark.django_db(transaction=True)
def test_baseline_command_refuses_to_replace_an_existing_report(tmp_path, virtual_player_baseline_sample) -> None:
    del virtual_player_baseline_sample
    output_path = tmp_path / "baseline.json"
    call_command(
        "audit_virtual_player_baseline",
        sample_limit=10,
        minimum_real_profiles=1,
        minimum_bot_profiles=1,
        minimum_profiles_per_band=1,
        output=str(output_path),
        stdout=StringIO(),
    )

    original = output_path.read_text(encoding="utf-8")
    with pytest.raises(CommandError, match="refusing to overwrite"):
        call_command(
            "audit_virtual_player_baseline",
            sample_limit=10,
            minimum_real_profiles=1,
            minimum_bot_profiles=1,
            minimum_profiles_per_band=1,
            output=str(output_path),
            stdout=StringIO(),
        )
    assert output_path.read_text(encoding="utf-8") == original


@pytest.mark.django_db(transaction=True)
def test_baseline_command_fails_closed_when_review_samples_are_insufficient(
    virtual_player_baseline_sample,
) -> None:
    del virtual_player_baseline_sample

    with pytest.raises(CommandError, match="insufficient samples"):
        call_command(
            "audit_virtual_player_baseline",
            sample_limit=10,
            minimum_real_profiles=30,
            minimum_bot_profiles=30,
            minimum_profiles_per_band=30,
            fail_on_insufficient=True,
            stdout=StringIO(),
        )


@pytest.mark.django_db(transaction=True)
def test_baseline_audit_fails_closed_when_a_configured_prestige_band_is_unsampled(
    virtual_player_baseline_sample,
    monkeypatch,
) -> None:
    del virtual_player_baseline_sample
    monkeypatch.setattr(
        "gameplay.management.commands.audit_virtual_player_baseline.virtual_player_prestige_bands",
        lambda: {"junior": (0, 2000), "senior": (2000, None)},
    )

    report = build_virtual_player_baseline(
        sample_limit=10,
        minimum_real_profiles=1,
        minimum_bot_profiles=1,
        minimum_profiles_per_band=1,
    )

    assert report["status"] == "insufficient_samples"
    assert report["sampling"]["profile_counts_by_prestige_band"] == {
        "real": {"junior": 1, "senior": 0},
        "v1_bot": {"junior": 1, "senior": 0},
    }
    assert "real profile prestige band 'senior' sample 0 is below required minimum 1" in report["blocking_reasons"]
    assert "V1 BotProfile prestige band 'senior' sample 0 is below required minimum 1" in report["blocking_reasons"]


@pytest.mark.django_db(transaction=True)
def test_baseline_report_excludes_account_and_display_identity_fields(
    virtual_player_baseline_sample,
) -> None:
    del virtual_player_baseline_sample

    report = build_virtual_player_baseline(
        sample_limit=10,
        minimum_real_profiles=1,
        minimum_bot_profiles=1,
        minimum_profiles_per_band=1,
    )
    payload = json.dumps(report, ensure_ascii=True, sort_keys=True)

    for private_value in (
        "baseline_real_private_user",
        "baseline_bot_private_user",
        "baseline-real-private@example.invalid",
        "baseline-bot-private@example.invalid",
        "baseline-real-manor",
        "baseline-bot-manor",
        "baseline-real-private-guest",
        "baseline-bot-private-guest",
    ):
        assert private_value not in payload
    for private_key in ('"username"', '"email"', '"manor_id"', '"guest_id"', '"custom_name"'):
        assert private_key not in payload


@pytest.mark.django_db(transaction=True)
def test_baseline_audit_rejects_an_existing_caller_transaction(virtual_player_baseline_sample) -> None:
    del virtual_player_baseline_sample

    with transaction.atomic():
        with pytest.raises(ValueError, match="outside an existing transaction"):
            build_virtual_player_baseline(
                sample_limit=10,
                minimum_real_profiles=1,
                minimum_bot_profiles=1,
                minimum_profiles_per_band=1,
            )


def test_report_file_creation_is_exclusive_under_concurrent_writers(tmp_path) -> None:
    output_path = tmp_path / "baseline.json"
    barrier = threading.Barrier(3)
    payloads = ('{"writer":1}\n', '{"writer":2}\n')
    completed: list[str] = []
    errors: list[BaseException] = []

    def _write(payload: str) -> None:
        barrier.wait(timeout=5)
        try:
            _write_report_exclusive(output_path, payload)
            completed.append(payload)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_write, args=(payload,)) for payload in payloads]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(completed) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], FileExistsError)
    assert output_path.read_text(encoding="utf-8") == completed[0]


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_baseline_audit_holds_a_repeatable_read_snapshot_during_concurrent_updates(
    virtual_player_baseline_sample,
    monkeypatch,
) -> None:
    if connection.vendor == "sqlite":
        pytest.skip("SQLite serializes this write instead of exercising repeatable-read MVCC")

    real_manor = virtual_player_baseline_sample["real_manor"]
    real_guest_id = Guest.objects.get(manor=real_manor).pk
    sample_selected = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []
    original_sample_manors = baseline_audit._sample_manors

    def _sample_then_pause(*, sample_limit: int):
        rows = original_sample_manors(sample_limit=sample_limit)
        sample_selected.set()
        if not writer_finished.wait(timeout=10):
            raise AssertionError("concurrent baseline writer did not finish")
        return rows

    monkeypatch.setattr(baseline_audit, "_sample_manors", _sample_then_pause)

    def _update_guest() -> None:
        close_old_connections()
        try:
            if not sample_selected.wait(timeout=10):
                raise AssertionError("baseline sampler did not establish its snapshot")
            Guest.objects.filter(pk=real_guest_id).update(level=99)
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            close_old_connections()
            writer_finished.set()

    writer = threading.Thread(target=_update_guest)
    writer.start()
    try:
        report = build_virtual_player_baseline(
            sample_limit=10,
            minimum_real_profiles=1,
            minimum_bot_profiles=1,
            minimum_profiles_per_band=1,
        )
    finally:
        writer.join(timeout=10)

    assert not writer.is_alive()
    assert writer_errors == []
    assert report["audit_runtime"]["snapshot_contract"]["isolation"] == "repeatable_read"
    assert report["cohorts"]["real"]["guest_level"]["p50"] == 8
    assert Guest.objects.get(pk=real_guest_id).level == 99
