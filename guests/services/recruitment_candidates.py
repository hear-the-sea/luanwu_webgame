from __future__ import annotations

import random
from typing import TYPE_CHECKING, Callable, TypedDict

from django.db import connections, transaction

from ..models import GuestRarity, GuestTemplate, RecruitmentCandidate
from .template_identity import get_non_repeatable_family_template_ids_by_id

if TYPE_CHECKING:
    from gameplay.models import Manor

    from ..models import RecruitmentPool, RecruitmentPoolEntry

ChooseTemplateFunc = Callable[..., GuestTemplate]
GenerateNameFunc = Callable[[random.Random], str]


class CandidateGenerationContext(TypedDict):
    pool_entries: list[RecruitmentPoolEntry]
    rng: random.Random
    templates_by_rarity: dict[str, list[GuestTemplate]]
    hermit_templates: list[GuestTemplate]
    resolved_draw_count: int
    excluded_ids: set[int]


def _normalize_rng_seed(seed: int | None) -> int | None:
    if seed is None:
        return None
    if isinstance(seed, bool):
        raise AssertionError(f"invalid recruitment seed: {seed!r}")
    try:
        normalized_seed = int(seed)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment seed: {seed!r}") from exc
    if normalized_seed <= 0:
        raise AssertionError(f"invalid recruitment seed: {seed!r}")
    return normalized_seed


def resolve_candidate_draw_count(*, pool: RecruitmentPool, manor: Manor, total_draw_count: int | None) -> int:
    resolved_draw_count = total_draw_count
    if resolved_draw_count is None:
        resolved_draw_count = pool.draw_count + manor.tavern_recruitment_bonus
    try:
        normalized_draw_count = int(resolved_draw_count)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment draw count: {resolved_draw_count!r}") from exc
    if normalized_draw_count <= 0:
        raise AssertionError(f"invalid recruitment draw count: {resolved_draw_count!r}")
    return normalized_draw_count


def build_candidate_display_name(
    template: GuestTemplate,
    rng: random.Random,
    *,
    generate_random_name: GenerateNameFunc,
) -> str:
    if template.rarity in (GuestRarity.BLACK, GuestRarity.GRAY) and not template.is_hermit:
        return generate_random_name(rng)
    return template.name


def should_reveal_candidate_rarity(template: GuestTemplate) -> bool:
    return template.rarity in (GuestRarity.RED, GuestRarity.GRAY)


def should_exclude_candidate_template(template: GuestTemplate, *, non_repeatable_rarities: frozenset[str]) -> bool:
    return template.rarity in non_repeatable_rarities or (template.rarity == GuestRarity.BLACK and template.is_hermit)


def build_candidate_batch(
    *,
    manor: Manor,
    pool: RecruitmentPool,
    pool_entries: list[RecruitmentPoolEntry],
    resolved_draw_count: int,
    excluded_ids: set[int],
    rng: random.Random,
    choose_template_from_entries: ChooseTemplateFunc,
    templates_by_rarity: dict[str, list[GuestTemplate]],
    hermit_templates: list[GuestTemplate],
    generate_random_name: GenerateNameFunc,
    non_repeatable_rarities: frozenset[str],
) -> list[RecruitmentCandidate]:
    candidates_to_create: list[RecruitmentCandidate] = []
    template_ids = {entry.template_id for entry in pool_entries if entry.template_id}
    template_ids.update(template.id for templates in templates_by_rarity.values() for template in templates)
    template_ids.update(template.id for template in hermit_templates)
    template_families = get_non_repeatable_family_template_ids_by_id(template_ids)
    for _ in range(resolved_draw_count):
        template = choose_template_from_entries(
            pool_entries,
            rng=rng,
            excluded_ids=excluded_ids,
            templates_by_rarity=templates_by_rarity,
            hermit_templates=hermit_templates,
        )
        candidates_to_create.append(
            RecruitmentCandidate(
                manor=manor,
                pool=pool,
                template=template,
                display_name=build_candidate_display_name(
                    template,
                    rng,
                    generate_random_name=generate_random_name,
                ),
                rarity=template.rarity,
                archetype=template.archetype,
                rarity_revealed=should_reveal_candidate_rarity(template),
            )
        )
        if should_exclude_candidate_template(template, non_repeatable_rarities=non_repeatable_rarities):
            excluded_ids.add(template.id)
            excluded_ids.update(template_families.get(template.id, set()))
    return candidates_to_create


def load_candidate_generation_context(
    *,
    manor: Manor,
    pool: RecruitmentPool,
    seed: int | None,
    total_draw_count: int | None,
    get_recruitable_templates_by_rarity: Callable[[], dict[str, list[GuestTemplate]]],
    get_hermit_templates: Callable[[], list[GuestTemplate]],
    get_excluded_template_ids: Callable[[Manor], set[int]],
) -> CandidateGenerationContext:
    return {
        "pool_entries": list(pool.entries.select_related("template")),
        "rng": random.Random(_normalize_rng_seed(seed)),
        "templates_by_rarity": get_recruitable_templates_by_rarity(),
        "hermit_templates": get_hermit_templates(),
        "resolved_draw_count": resolve_candidate_draw_count(pool=pool, manor=manor, total_draw_count=total_draw_count),
        "excluded_ids": get_excluded_template_ids(manor),
    }


@transaction.atomic
def persist_candidate_batch(
    *,
    recruitment_candidate_model: type[RecruitmentCandidate],
    manor: Manor,
    candidates_to_create: list[RecruitmentCandidate],
    invalidate_cache: Callable[[int | None], None],
) -> list[RecruitmentCandidate]:
    if not candidates_to_create:
        invalidate_cache(getattr(manor, "id", None))
        return []

    connection = connections[recruitment_candidate_model.objects.db]
    can_return_bulk_rows = bool(getattr(connection.features, "can_return_rows_from_bulk_insert", False))

    if can_return_bulk_rows:
        candidates = recruitment_candidate_model.objects.bulk_create(candidates_to_create)
    else:
        candidates = []
        for candidate in candidates_to_create:
            candidate.save(force_insert=True)
            candidates.append(candidate)
    invalidate_cache(getattr(manor, "id", None))
    return candidates
