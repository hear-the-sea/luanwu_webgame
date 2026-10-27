"""赛季周结算与到期归档扫描。"""

from __future__ import annotations

from celery import shared_task


@shared_task(name="gameplay.settle_seasons")
def settle_seasons_task() -> dict[str, int]:
    from gameplay.services.season.expeditions import advance_due_expeditions
    from gameplay.services.season.lifecycle import advance_seasons

    processed = advance_due_expeditions()
    result = advance_seasons()
    result.update(processed)
    return result


@shared_task(name="gameplay.advance_season_expedition")
def advance_season_expedition_task(run_id: int) -> bool:
    from gameplay.services.season.expeditions import process_expedition

    return process_expedition(run_id)
