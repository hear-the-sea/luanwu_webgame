from __future__ import annotations

from datetime import date

import pytest
from django.db import IntegrityError, transaction

from gameplay.models import JailInteractionLog, JailPrisoner, Manor
from guests.models import Guest, GuestTemplate


@pytest.fixture
def jail_entities(django_user_model):
    captor_user = django_user_model.objects.create_user(username="persuasion_captor")
    original_user = django_user_model.objects.create_user(username="persuasion_original")
    captor = Manor.objects.get(user=captor_user)
    original = Manor.objects.get(user=original_user)
    captor.name = "招降庄园"
    original.name = "原属庄园"
    captor.save(update_fields=["name"])
    original.save(update_fields=["name"])
    prisoner_template = GuestTemplate.objects.create(
        key="persuasion_prisoner_template",
        name="待招门客",
        rarity="green",
        archetype="civil",
        base_attack=80,
        base_intellect=120,
    )
    speaker_template = GuestTemplate.objects.create(
        key="persuasion_speaker_template",
        name="说客模板",
        rarity="gray",
        archetype="civil",
        base_attack=90,
        base_intellect=130,
    )
    prisoner = JailPrisoner.objects.create(
        captor=captor,
        original_manor=original,
        guest_template=prisoner_template,
        original_guest_name="阶下之客",
        original_level=20,
        loyalty=80,
        captured_loyalty=80,
    )
    speaker = Guest.objects.create(
        manor=captor,
        template=speaker_template,
        custom_name="纵横客",
        loyalty=70,
    )
    return captor, prisoner, speaker


def _log(prisoner, speaker, attempt_scope=None, **overrides):
    speaker_name = speaker.display_name if speaker is not None else ""
    speaker_template_key = speaker.template.key if speaker is not None else ""
    speaker_base_value = speaker.template.base_intellect if speaker is not None else None
    speaker_loyalty = speaker.loyalty if speaker is not None else None
    values = {
        "captor": prisoner.captor,
        "prisoner": prisoner,
        "method": "reason",
        "speaker": speaker,
        "speaker_name_snapshot": speaker_name,
        "speaker_template_key_snapshot": speaker_template_key,
        "speaker_base_value_snapshot": speaker_base_value,
        "speaker_loyalty_before": speaker_loyalty,
        "speaker_loyalty_after": speaker_loyalty,
        "usage_date": date(2026, 7, 20),
        "attempt_scope": attempt_scope,
        "heart_before": prisoner.loyalty,
        "heart_after": prisoner.loyalty - 6,
        "affinity_before": prisoner.affinity,
        "affinity_after": prisoner.affinity + 10,
        "outcome": "neutral",
        "copy_key": "feedback.reason.neutral.1",
        "copy_params": {"prisoner_name": prisoner.display_name, "speaker_name": speaker_name},
        "resource_cost": {},
    }
    values.update(overrides)
    return JailInteractionLog.objects.create(**values)


@pytest.mark.django_db
def test_prisoner_persuasion_state_defaults(jail_entities):
    _captor, prisoner, _speaker = jail_entities
    assert prisoner.captured_loyalty == 80
    assert prisoner.affinity == 0
    assert prisoner.stance_method == ""
    assert prisoner.taboo_method == ""
    assert prisoner.revealed_level == 0
    assert prisoner.milestone_stage == 0
    assert prisoner.interaction_date is None
    assert prisoner.interactions_today == 0
    assert prisoner.last_method == ""
    assert prisoner.same_method_streak == 0
    assert prisoner.observed_at is None


@pytest.mark.django_db
def test_speaker_can_only_be_used_once_per_usage_date(jail_entities):
    _captor, prisoner, speaker = jail_entities
    _log(prisoner, speaker)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _log(prisoner, speaker, method="might")


@pytest.mark.django_db
def test_speaker_can_be_reused_on_another_date(jail_entities):
    _captor, prisoner, speaker = jail_entities
    _log(prisoner, speaker)
    _log(prisoner, speaker, usage_date=date(2026, 7, 21))
    assert JailInteractionLog.objects.filter(speaker=speaker).count() == 2


@pytest.mark.django_db
def test_non_speaker_interactions_do_not_share_unique_limit(jail_entities):
    _captor, prisoner, _speaker = jail_entities
    _log(
        prisoner,
        None,
        method="kindness",
        speaker_name_snapshot="",
        speaker_template_key_snapshot="",
        speaker_base_value_snapshot=None,
        speaker_loyalty_before=None,
        speaker_loyalty_after=None,
    )
    _log(
        prisoner,
        None,
        method="bribe",
        speaker_name_snapshot="",
        speaker_template_key_snapshot="",
        speaker_base_value_snapshot=None,
        speaker_loyalty_before=None,
        speaker_loyalty_after=None,
    )
    assert JailInteractionLog.objects.filter(speaker__isnull=True).count() == 2


@pytest.mark.django_db
def test_null_attempt_scopes_do_not_share_unique_limit(jail_entities):
    _captor, prisoner, _speaker = jail_entities
    first = _log(prisoner, None, method="kindness")
    second = _log(prisoner, None, method="bribe")

    assert first.attempt_scope is None
    assert second.attempt_scope is None


@pytest.mark.django_db
def test_recruitment_attempt_scope_is_unique_per_prisoner_and_date(jail_entities):
    _captor, prisoner, _speaker = jail_entities
    _log(prisoner, None, attempt_scope="recruitment")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _log(prisoner, None, attempt_scope="recruitment", method="might")


@pytest.mark.django_db
def test_recruitment_attempt_scope_can_be_reused_on_another_date(jail_entities):
    _captor, prisoner, _speaker = jail_entities
    _log(prisoner, None, attempt_scope="recruitment")
    _log(
        prisoner,
        None,
        attempt_scope="recruitment",
        usage_date=date(2026, 7, 21),
    )

    assert JailInteractionLog.objects.filter(attempt_scope="recruitment").count() == 2


@pytest.mark.django_db
def test_recruitment_attempt_scope_can_be_reused_by_another_prisoner(jail_entities):
    captor, prisoner, _speaker = jail_entities
    second_prisoner = JailPrisoner.objects.create(
        captor=captor,
        original_manor=prisoner.original_manor,
        guest_template=prisoner.guest_template,
        original_guest_name="另一名阶下之客",
        original_level=18,
        loyalty=75,
        captured_loyalty=75,
    )
    _log(prisoner, None, attempt_scope="recruitment")
    _log(second_prisoner, None, attempt_scope="recruitment")

    assert JailInteractionLog.objects.filter(attempt_scope="recruitment").count() == 2


def test_speaker_daily_unique_constraint_is_portable_to_mysql():
    constraint = next(
        item for item in JailInteractionLog._meta.constraints if item.name == "uniq_jail_speaker_usage_date"
    )

    assert constraint.condition is None


def test_attempt_scope_daily_unique_constraint_is_portable_to_mysql():
    constraint = next(
        item for item in JailInteractionLog._meta.constraints if item.name == "uniq_jail_attempt_scope_date"
    )

    assert constraint.condition is None


def test_recruited_outcome_is_available():
    assert JailInteractionLog.Outcome.RECRUITED == "recruited"
    assert JailInteractionLog.Outcome.RECRUITED.label == "归附成功"


@pytest.mark.django_db
def test_deleting_speaker_preserves_log_snapshots(jail_entities):
    _captor, prisoner, speaker = jail_entities
    log = _log(prisoner, speaker)
    expected_name = speaker.display_name
    speaker.delete()

    log.refresh_from_db()
    assert log.speaker is None
    assert log.speaker_name_snapshot == expected_name
    assert log.speaker_template_key_snapshot == "persuasion_speaker_template"
    assert log.speaker_base_value_snapshot == 130


@pytest.mark.django_db
def test_prisoner_numeric_check_constraints_reject_out_of_range_values(jail_entities):
    _captor, prisoner, _speaker = jail_entities
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            JailPrisoner.objects.filter(pk=prisoner.pk).update(affinity=101)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            JailPrisoner.objects.filter(pk=prisoner.pk).update(revealed_level=4)
