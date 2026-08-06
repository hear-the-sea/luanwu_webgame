from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone


def finalize_mission_run(
    run: Any,
    *,
    now=None,
    load_locked_mission_run,
    build_defense_report_if_needed,
    extract_report_guest_state,
    select_guests_for_finalize,
    prepare_guest_updates_for_finalize,
    mark_run_completed,
    apply_city_defense_battle_damage,
    apply_defender_troop_losses,
    return_attacker_troops_after_mission,
    apply_mission_rewards_if_won,
    send_mission_report_message,
) -> bool:
    from guests.models import Guest

    now = now or timezone.now()

    with transaction.atomic():
        locked_run = load_locked_mission_run(run.pk)
        if not locked_run or locked_run.status == locked_run.Status.COMPLETED:
            return False

        report = build_defense_report_if_needed(locked_run)
        player_side = "defender" if locked_run.mission.is_defense else "attacker"

        hp_updates, defeated_guest_ids, participant_ids = extract_report_guest_state(report, player_side)
        guests = select_guests_for_finalize(locked_run, report, participant_ids)
        guests_to_update, update_fields = prepare_guest_updates_for_finalize(
            guests,
            is_retreating=locked_run.is_retreating,
            defeated_guest_ids=defeated_guest_ids,
            hp_updates=hp_updates,
            now=now,
        )

        if guests_to_update:
            Guest.objects.bulk_update(guests_to_update, update_fields)

        if report and locked_run.mission.is_defense and not locked_run.is_retreating:
            apply_city_defense_battle_damage(
                locked_run.manor,
                getattr(report, "defender_city_defenses", []) or [],
                now=now,
            )

        mark_run_completed(locked_run, now)

        if report and locked_run.mission.is_defense and not locked_run.is_retreating:
            apply_defender_troop_losses(locked_run.manor, report)

        return_attacker_troops_after_mission(locked_run, report)
        apply_mission_rewards_if_won(locked_run, report, player_side)

        def _send_report_message_after_commit() -> None:
            send_mission_report_message(locked_run, report)

        transaction.on_commit(_send_report_message_after_commit)
        return True
