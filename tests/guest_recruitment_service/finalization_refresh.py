from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

import guests.services.recruitment as recruitment_command_service
import guests.services.recruitment_guests as recruitment_guest_service
from core.exceptions import GuestAlreadyOwnedError, RecruitmentItemOwnershipError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.action_points import ACTION_POINT_EXPEDITION_COST
from gameplay.services.manor.core import ensure_manor
from guests.models import (
    Guest,
    GuestRecruitment,
    GuestTemplate,
    RecruitmentCandidate,
    RecruitmentPool,
    RecruitmentRecord,
    Skill,
)


@pytest.mark.django_db(transaction=True)
def test_start_guest_recruitment_consumes_action_points(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(
        username="guest_recruit_action_points",
        password="pass123",
        email="guest_recruit_action_points@test.local",
    )
    manor = ensure_manor(user)
    pool = RecruitmentPool.objects.create(
        key="guest_recruit_action_points_pool",
        name="行动力招募卡池",
        cost={},
        cooldown_seconds=60,
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )
    monkeypatch.setattr(recruitment_command_service, "_schedule_guest_recruitment_completion", lambda *_args: None)

    recruitment = recruitment_command_service.start_guest_recruitment(manor, pool, seed=123)

    manor.refresh_from_db()
    assert recruitment.status == GuestRecruitment.Status.PENDING
    assert manor.action_points == 1000 - ACTION_POINT_EXPEDITION_COST


@pytest.mark.django_db(transaction=True)
def test_start_guest_recruitment_rejects_when_action_points_insufficient(django_user_model, monkeypatch):
    from core.exceptions import ActionPointsInsufficientError

    user = django_user_model.objects.create_user(
        username="guest_recruit_no_action_points",
        password="pass123",
        email="guest_recruit_no_action_points@test.local",
    )
    manor = ensure_manor(user)
    manor.action_points = ACTION_POINT_EXPEDITION_COST - 1
    manor.save(update_fields=["action_points"])
    pool = RecruitmentPool.objects.create(
        key="guest_recruit_no_action_points_pool",
        name="行动力不足招募卡池",
        cost={},
        cooldown_seconds=60,
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )
    monkeypatch.setattr(recruitment_command_service, "_schedule_guest_recruitment_completion", lambda *_args: None)

    with pytest.raises(ActionPointsInsufficientError, match="行动力不足"):
        recruitment_command_service.start_guest_recruitment(manor, pool, seed=123)

    manor.refresh_from_db()
    assert manor.action_points == ACTION_POINT_EXPEDITION_COST - 1
    assert GuestRecruitment.objects.filter(manor=manor, pool=pool).exists() is False


@pytest.mark.django_db
def test_use_magnifying_glass_for_candidates_rejects_item_not_owned(django_user_model):
    user = django_user_model.objects.create_user(
        username="recruitment_magnifier_missing_user",
        password="pass123",
        email="recruitment_magnifier_missing_user@test.local",
    )
    manor = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="recruitment_magnifier_missing",
        name="放大镜",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(RecruitmentItemOwnershipError, match="道具不存在或不属于您的庄园"):
        recruitment_command_service.use_magnifying_glass_for_candidates(manor, item_id=999999)


@pytest.mark.django_db
def test_bulk_finalize_candidates_respects_capacity_and_grants_template_skills(django_user_model):
    user = django_user_model.objects.create_user(
        username="bulk_finalize_user",
        password="pass123",
        email="bulk_finalize_user@test.local",
    )
    manor = ensure_manor(user)

    skill_a = Skill.objects.create(key="bulk_finalize_skill_a", name="技能A")
    skill_b = Skill.objects.create(key="bulk_finalize_skill_b", name="技能B")

    template = GuestTemplate.objects.create(
        key="bulk_finalize_tpl",
        name="批量门客模板",
        archetype="civil",
        rarity="gray",
        base_attack=60,
        base_intellect=80,
        base_defense=50,
        base_agility=40,
        base_luck=30,
        base_hp=500,
    )
    template.initial_skills.add(skill_a, skill_b)

    pool = RecruitmentPool.objects.create(
        key="bulk_finalize_pool",
        name="批量测试卡池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )

    for idx in range(3):
        Guest.objects.create(manor=manor, template=template, custom_name=f"已有门客{idx}")

    candidate_1 = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="候选一",
        rarity="gray",
        archetype="civil",
    )
    candidate_2 = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="候选二",
        rarity="gray",
        archetype="civil",
    )

    created, failed = recruitment_guest_service.bulk_finalize_candidates([candidate_1, candidate_2])

    assert len(created) == 1
    assert len(failed) == 1
    assert failed[0].id == candidate_2.id
    created_guest = created[0]
    assert created_guest.custom_name == "候选一"
    assert RecruitmentRecord.objects.filter(manor=manor, guest=created_guest).count() == 1
    assert set(created_guest.guest_skills.values_list("skill__key", flat=True)) == {
        "bulk_finalize_skill_a",
        "bulk_finalize_skill_b",
    }
    assert created_guest.training_complete_at is not None
    assert created_guest.training_target_level == 2
    assert RecruitmentCandidate.objects.filter(id=candidate_1.id).exists() is False
    assert RecruitmentCandidate.objects.filter(id=candidate_2.id).exists() is True


@pytest.mark.django_db
def test_bulk_finalize_candidates_marks_missing_candidates_as_failed(django_user_model):
    user = django_user_model.objects.create_user(
        username="bulk_finalize_missing_user",
        password="pass123",
        email="bulk_finalize_missing_user@test.local",
    )
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="bulk_finalize_missing_tpl",
        name="批量缺失模板",
        archetype="civil",
        rarity="gray",
        base_attack=60,
        base_intellect=80,
        base_defense=50,
        base_agility=40,
        base_luck=30,
        base_hp=500,
    )
    pool = RecruitmentPool.objects.create(
        key="bulk_finalize_missing_pool",
        name="批量缺失卡池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )

    candidate_1 = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="缺失候选一",
        rarity="gray",
        archetype="civil",
    )
    candidate_2 = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="缺失候选二",
        rarity="gray",
        archetype="civil",
    )
    stale_candidate = RecruitmentCandidate.objects.get(pk=candidate_1.pk)
    RecruitmentCandidate.objects.filter(pk=candidate_1.pk).delete()

    created, failed = recruitment_guest_service.bulk_finalize_candidates([stale_candidate, candidate_2])

    assert len(created) == 1
    assert created[0].custom_name == "缺失候选二"
    assert [candidate.id for candidate in failed] == [candidate_1.id]


@pytest.mark.django_db
def test_bulk_finalize_candidates_deduplicates_repeated_candidate_input(django_user_model):
    user = django_user_model.objects.create_user(
        username="bulk_finalize_duplicate_user",
        password="pass123",
        email="bulk_finalize_duplicate_user@test.local",
    )
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="bulk_finalize_duplicate_tpl",
        name="批量重复模板",
        archetype="civil",
        rarity="gray",
        base_attack=60,
        base_intellect=80,
        base_defense=50,
        base_agility=40,
        base_luck=30,
        base_hp=500,
    )
    pool = RecruitmentPool.objects.create(
        key="bulk_finalize_duplicate_pool",
        name="批量重复卡池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="重复候选",
        rarity="gray",
        archetype="civil",
    )

    created, failed = recruitment_guest_service.bulk_finalize_candidates([candidate, candidate])

    assert len(created) == 1
    assert failed == []
    assert RecruitmentRecord.objects.filter(manor=manor).count() == 1
    assert Guest.objects.filter(manor=manor).count() == 1
    assert RecruitmentCandidate.objects.filter(pk=candidate.pk).exists() is False


@pytest.mark.django_db
def test_bulk_finalize_candidates_deduplicates_repeated_stale_candidate_input(django_user_model):
    user = django_user_model.objects.create_user(
        username="bulk_finalize_duplicate_stale_user",
        password="pass123",
        email="bulk_finalize_duplicate_stale_user@test.local",
    )
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key="bulk_finalize_duplicate_stale_tpl",
        name="批量重复失效模板",
        archetype="civil",
        rarity="gray",
        base_attack=60,
        base_intellect=80,
        base_defense=50,
        base_agility=40,
        base_luck=30,
        base_hp=500,
    )
    pool = RecruitmentPool.objects.create(
        key="bulk_finalize_duplicate_stale_pool",
        name="批量重复失效卡池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name="重复失效候选",
        rarity="gray",
        archetype="civil",
    )
    stale_candidate = RecruitmentCandidate.objects.get(pk=candidate.pk)
    RecruitmentCandidate.objects.filter(pk=candidate.pk).delete()

    created, failed = recruitment_guest_service.bulk_finalize_candidates([stale_candidate, stale_candidate])

    assert created == []
    assert [failed_candidate.id for failed_candidate in failed] == [candidate.id]


@pytest.mark.django_db
def test_bulk_finalize_candidates_rejects_unpersisted_candidate():
    with pytest.raises(AssertionError, match="invalid recruitment candidate id"):
        recruitment_guest_service.bulk_finalize_candidates([SimpleNamespace(id=None, manor_id=1)])


@pytest.mark.django_db
def test_bulk_finalize_candidates_rejects_mixed_manor_candidates(django_user_model):
    user_a = django_user_model.objects.create_user(
        username="bulk_finalize_mixed_a",
        password="pass123",
        email="bulk_finalize_mixed_a@test.local",
    )
    user_b = django_user_model.objects.create_user(
        username="bulk_finalize_mixed_b",
        password="pass123",
        email="bulk_finalize_mixed_b@test.local",
    )
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)

    template = GuestTemplate.objects.create(
        key="bulk_finalize_mixed_tpl",
        name="批量混庄园模板",
        archetype="civil",
        rarity="gray",
        base_attack=60,
        base_intellect=80,
        base_defense=50,
        base_agility=40,
        base_luck=30,
        base_hp=500,
    )
    pool = RecruitmentPool.objects.create(
        key="bulk_finalize_mixed_pool",
        name="批量混庄园卡池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )

    candidate_a = RecruitmentCandidate.objects.create(
        manor=manor_a,
        pool=pool,
        template=template,
        display_name="混庄园候选甲",
        rarity="gray",
        archetype="civil",
    )
    candidate_b = RecruitmentCandidate.objects.create(
        manor=manor_b,
        pool=pool,
        template=template,
        display_name="混庄园候选乙",
        rarity="gray",
        archetype="civil",
    )

    with pytest.raises(AssertionError, match="mixed recruitment candidate manor ids"):
        recruitment_guest_service.bulk_finalize_candidates([candidate_a, candidate_b])


def test_finalize_guest_recruitment_rejects_unpersisted_recruitment():
    with pytest.raises(AssertionError, match="requires a persisted recruitment"):
        recruitment_command_service.finalize_guest_recruitment(SimpleNamespace(pk=None))


def test_refresh_guest_recruitments_rejects_non_positive_limit():
    manor = SimpleNamespace()

    with pytest.raises(AssertionError, match="invalid guest recruitment refresh limit"):
        recruitment_command_service.refresh_guest_recruitments(manor, limit=0)


@pytest.mark.django_db
def test_refresh_guest_recruitments_only_processes_due_pending_records(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(
        username="refresh_guest_recruitments_due_pending",
        password="pass123",
        email="refresh_guest_recruitments_due_pending@test.local",
    )
    manor = ensure_manor(user)
    future_user = django_user_model.objects.create_user(
        username="refresh_guest_recruitments_future_pending",
        password="pass123",
        email="refresh_guest_recruitments_future_pending@test.local",
    )
    future_manor = ensure_manor(future_user)
    pool = RecruitmentPool.objects.create(
        key="refresh_guest_recruitments_pool",
        name="刷新招募测试卡池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )

    now = recruitment_command_service.timezone.now()
    due_pending = GuestRecruitment.objects.create(
        manor=manor,
        pool=pool,
        cost={},
        draw_count=1,
        duration_seconds=30,
        seed=11,
        status=GuestRecruitment.Status.PENDING,
        complete_at=now,
    )
    future_pending = GuestRecruitment.objects.create(
        manor=future_manor,
        pool=pool,
        cost={},
        draw_count=1,
        duration_seconds=30,
        seed=22,
        status=GuestRecruitment.Status.PENDING,
        complete_at=now + timedelta(minutes=5),
    )
    completed = GuestRecruitment.objects.create(
        manor=manor,
        pool=pool,
        cost={},
        draw_count=1,
        duration_seconds=30,
        seed=33,
        status=GuestRecruitment.Status.COMPLETED,
        complete_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=4),
        result_count=1,
    )

    finalized_ids: list[int] = []

    def _fake_finalize(recruitment, *, now=None, send_notification=False):
        assert now is not None
        assert send_notification is True
        finalized_ids.append(recruitment.pk)
        return recruitment.pk == due_pending.pk

    monkeypatch.setattr(recruitment_command_service, "finalize_guest_recruitment", _fake_finalize)
    completed_count = recruitment_command_service.refresh_guest_recruitments(manor)

    assert completed_count == 1
    assert finalized_ids == [due_pending.pk]
    assert future_pending.pk not in finalized_ids
    assert completed.pk not in finalized_ids


@pytest.mark.django_db
def test_finalize_candidate_rejects_owned_non_repeatable_template(django_user_model):
    user = django_user_model.objects.create_user(
        username="finalize_owned_unique_guest",
        password="pass123",
    )
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key="finalize_owned_unique_template",
        name="已拥有唯一门客",
        archetype="civil",
        rarity="green",
        base_attack=60,
        base_intellect=80,
        base_defense=50,
        base_agility=40,
        base_luck=30,
        base_hp=500,
    )
    pool = RecruitmentPool.objects.create(
        key="finalize_owned_unique_pool",
        name="唯一门客录用测试池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=1,
    )
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name=template.name,
        rarity=template.rarity,
        archetype=template.archetype,
    )
    Guest.objects.create(manor=manor, template=template)

    with pytest.raises(GuestAlreadyOwnedError, match="不可重复获得"):
        recruitment_guest_service.finalize_candidate(candidate)

    assert manor.guests.filter(template=template).count() == 1
    assert RecruitmentCandidate.objects.filter(pk=candidate.pk).exists()


@pytest.mark.django_db
def test_bulk_finalize_candidates_skips_owned_and_batch_duplicate_non_repeatable_templates(django_user_model):
    user = django_user_model.objects.create_user(
        username="bulk_finalize_unique_guests",
        password="pass123",
    )
    manor = ensure_manor(user)
    owned_template = GuestTemplate.objects.create(
        key="bulk_finalize_owned_unique_template",
        name="庄园已有门客",
        archetype="civil",
        rarity="green",
        base_attack=60,
        base_intellect=80,
        base_defense=50,
        base_agility=40,
        base_luck=30,
        base_hp=500,
    )
    new_template = GuestTemplate.objects.create(
        key="bulk_finalize_new_unique_template",
        name="批次唯一门客",
        archetype="military",
        rarity="blue",
        base_attack=80,
        base_intellect=60,
        base_defense=60,
        base_agility=50,
        base_luck=30,
        base_hp=600,
    )
    pool = RecruitmentPool.objects.create(
        key="bulk_finalize_unique_pool",
        name="批量唯一门客录用测试池",
        cost={},
        tier=RecruitmentPool.Tier.CUNMU,
        draw_count=3,
    )
    Guest.objects.create(manor=manor, template=owned_template)
    owned_candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=owned_template,
        display_name=owned_template.name,
        rarity=owned_template.rarity,
        archetype=owned_template.archetype,
    )
    new_candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=new_template,
        display_name=new_template.name,
        rarity=new_template.rarity,
        archetype=new_template.archetype,
    )
    duplicate_candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=new_template,
        display_name=new_template.name,
        rarity=new_template.rarity,
        archetype=new_template.archetype,
    )

    created, failed = recruitment_guest_service.bulk_finalize_candidates(
        [owned_candidate, new_candidate, duplicate_candidate]
    )

    assert [guest.template_id for guest in created] == [new_template.id]
    assert [candidate.id for candidate in failed] == [owned_candidate.id, duplicate_candidate.id]
    assert manor.guests.filter(template=owned_template).count() == 1
    assert manor.guests.filter(template=new_template).count() == 1
    assert RecruitmentCandidate.objects.filter(pk=owned_candidate.pk).exists()
    assert RecruitmentCandidate.objects.filter(pk=duplicate_candidate.pk).exists()
