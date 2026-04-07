from __future__ import annotations

from functools import partial
from typing import Any, Callable


def build_finalize_mission_dependencies(
    *,
    load_locked_mission_run: Callable[..., Any],
    build_defense_report_if_needed: Callable[..., Any],
    guest_template_rarity_rank_case: Any,
    generate_sync_battle_report: Callable[..., Any],
    extract_report_guest_state: Callable[..., Any],
    select_guests_for_finalize: Callable[..., Any],
    prepare_guest_updates_for_finalize: Callable[..., Any],
    mark_run_completed: Callable[..., Any],
    apply_defender_troop_losses: Callable[..., Any],
    return_attacker_troops_after_mission: Callable[..., Any],
    logger: Any,
    apply_mission_rewards_if_won: Callable[..., Any],
    resolve_defense_drops_if_missing: Callable[..., Any],
    award_mission_drops_locked: Callable[..., Any],
    send_mission_report_message: Callable[..., Any],
    create_message: Callable[..., Any],
    notify_user: Callable[..., Any],
    notification_infrastructure_exceptions: tuple[type[Exception], ...],
) -> dict[str, Any]:
    return {
        "load_locked_mission_run": load_locked_mission_run,
        "build_defense_report_if_needed": partial(
            build_defense_report_if_needed,
            guest_template_rarity_rank_case=guest_template_rarity_rank_case,
            generate_sync_battle_report=generate_sync_battle_report,
        ),
        "extract_report_guest_state": extract_report_guest_state,
        "select_guests_for_finalize": select_guests_for_finalize,
        "prepare_guest_updates_for_finalize": prepare_guest_updates_for_finalize,
        "mark_run_completed": mark_run_completed,
        "apply_defender_troop_losses": apply_defender_troop_losses,
        "return_attacker_troops_after_mission": partial(return_attacker_troops_after_mission, logger=logger),
        "apply_mission_rewards_if_won": partial(
            apply_mission_rewards_if_won,
            logger=logger,
            resolve_defense_drops_if_missing=resolve_defense_drops_if_missing,
            award_mission_drops_locked=award_mission_drops_locked,
        ),
        "send_mission_report_message": partial(
            send_mission_report_message,
            logger=logger,
            create_message=create_message,
            notify_user=notify_user,
            notification_infrastructure_exceptions=notification_infrastructure_exceptions,
        ),
    }


def build_launch_mission_dependencies(
    *,
    scale_duration: Callable[..., Any],
    prevalidate_launch_report: Callable[..., Any],
    import_launch_post_action_tasks: Callable[..., Any],
    try_prepare_launch_report: Callable[..., Any],
    dispatch_complete_mission_task: Callable[..., Any],
) -> dict[str, Any]:
    return {
        "scale_duration": scale_duration,
        "prevalidate_launch_report": prevalidate_launch_report,
        "import_launch_post_action_tasks": import_launch_post_action_tasks,
        "try_prepare_launch_report": try_prepare_launch_report,
        "dispatch_complete_mission_task": dispatch_complete_mission_task,
    }
