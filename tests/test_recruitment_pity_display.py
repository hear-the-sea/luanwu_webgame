import pytest
from django.template.loader import render_to_string

from core.config import RECRUITMENT
from gameplay.selectors.recruitment import get_recruitment_hall_context
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestTemplate, RecruitmentCandidate, RecruitmentPity, RecruitmentPool


@pytest.mark.django_db
def test_hall_pity_read_starts_at_zero_without_creating_state(django_user_model):
    user = django_user_model.objects.create_user(username="pity_read_only")
    manor = ensure_manor(user)

    context = get_recruitment_hall_context(manor, records_limit=5, use_cache=False)

    assert "recruitment_pity" not in context
    assert not RecruitmentPity.objects.filter(manor=manor).exists()


@pytest.mark.django_db
def test_hall_hides_pity_progress_without_changing_counters(django_user_model):
    user = django_user_model.objects.create_user(username="pity_live_progress")
    manor = ensure_manor(user)
    pity = RecruitmentPity.objects.create(manor=manor, purple_misses=12, orange_misses=34)
    before = get_recruitment_hall_context(manor, records_limit=5)
    assert "recruitment_pity" not in before

    pity.purple_misses = RECRUITMENT.PURPLE_PITY_LIMIT
    pity.orange_misses = RECRUITMENT.ORANGE_PITY_LIMIT
    pity.save()
    after = get_recruitment_hall_context(manor, records_limit=5)

    assert "recruitment_pity" not in after
    html = render_to_string("gameplay/partials/recruitment_pools_section.html", after)
    assert "保底" not in html
    assert "待兑现" not in html
    pity.refresh_from_db()
    assert pity.purple_misses == RECRUITMENT.PURPLE_PITY_LIMIT
    assert pity.orange_misses == RECRUITMENT.ORANGE_PITY_LIMIT


@pytest.mark.django_db
def test_hall_does_not_expose_pity_badge_or_reveal_candidate(game_data, django_user_model):
    user = django_user_model.objects.create_user(username="pity_candidate_badge")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.filter(rarity="orange", recruitable=True).first()
    pool = RecruitmentPool.objects.get(key="cunmu")
    candidate = RecruitmentCandidate.objects.create(
        manor=manor,
        pool=pool,
        template=template,
        display_name=template.name,
        rarity=template.rarity,
        archetype=template.archetype,
        pity_rarity="orange",
    )

    context = get_recruitment_hall_context(manor, records_limit=5, use_cache=False)

    payload = next(row for row in context["candidates_payload"] if row["id"] == candidate.id)
    assert "pity_rarity" not in payload
    assert payload["rarity_revealed"] is False
    candidate.refresh_from_db()
    assert candidate.rarity_revealed is False
