from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, QuerySet
from django.views.generic import DetailView

from .models import BattleReport
from .troops import load_troop_templates
from .view_helpers import (
    attach_avatar_urls,
    build_report_title,
    build_reward_context,
    build_rounds_display,
    build_side_labels,
    collect_template_keys,
    load_avatar_map,
    resolve_capture_loss_label,
    resolve_city_defense_perspective,
    resolve_display_drops,
    resolve_display_losses,
    resolve_equipment_bonus_perspective,
    resolve_perspective,
    resolve_report_runtime_context,
    serialize_city_defense_rows,
    serialize_troops,
)

if TYPE_CHECKING:
    from gameplay.models import Manor


class BattleReportDetailView(LoginRequiredMixin, DetailView):
    template_name = "battle/report_detail.html"
    model = BattleReport
    context_object_name = "report"
    _request_manor: Manor | None = None
    _request_manor_loaded = False

    def _get_request_manor(self) -> Manor | None:
        from gameplay.services.manor.core import ManorNotFoundError, get_manor

        if not self._request_manor_loaded:
            try:
                self._request_manor = get_manor(self.request.user)
            except ManorNotFoundError:
                self._request_manor = None
            self._request_manor_loaded = True
        return self._request_manor

    def get_queryset(self) -> QuerySet[BattleReport]:
        manor = self._get_request_manor()
        if manor is None:
            return BattleReport.objects.none()
        # 允许查看：
        # 1) 自己作为战报归属方（report.manor）
        # 2) 通过站内信收到的战报（Message.battle_report）
        # 3) 竞技场公开战报（通过 ArenaMatch 关联）
        return (
            BattleReport.objects.select_related("manor")
            .filter(
                Q(manor=manor)
                | Q(messages__manor=manor)
                | Q(arena_matches__isnull=False)
                | Q(arena_coop_events__entries__manor=manor)
            )
            .distinct()
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        report: BattleReport = context["report"]
        manor = self._get_request_manor()
        viewer_manor_id = manor.id if manor is not None else 0
        troop_definitions = load_troop_templates()

        report_runtime = resolve_report_runtime_context(report, manor_id=viewer_manor_id)
        player_side = str(report_runtime["player_side"])

        attacker_team = report.attacker_team or []
        defender_team = report.defender_team or []
        template_keys = collect_template_keys(attacker_team, defender_team, report.rounds or [])
        avatar_map = load_avatar_map(template_keys)
        attach_avatar_urls(attacker_team, avatar_map)
        attach_avatar_urls(defender_team, avatar_map)
        context["battle_rounds"] = build_rounds_display(report.rounds or [], avatar_map)

        my_team, enemy_team, my_troops_raw, enemy_troops_raw, my_loss, enemy_loss = resolve_perspective(
            report,
            player_side,
        )
        my_city_defenses_raw, enemy_city_defenses_raw = resolve_city_defense_perspective(report, player_side)
        my_equipment_bonuses, enemy_equipment_bonuses = resolve_equipment_bonus_perspective(report, player_side)
        if player_side == "defender":
            equipment_left_bonuses = enemy_equipment_bonuses
            equipment_right_bonuses = my_equipment_bonuses
            loss_left = enemy_loss
            loss_right = my_loss
        else:
            equipment_left_bonuses = my_equipment_bonuses
            equipment_right_bonuses = enemy_equipment_bonuses
            loss_left = my_loss
            loss_right = enemy_loss

        context["attacker_team_display"] = my_team
        context["defender_team_display"] = enemy_team
        context["attacker_troops"] = serialize_troops(my_troops_raw, troop_definitions)
        context["defender_troops"] = serialize_troops(enemy_troops_raw, troop_definitions)
        context["attacker_city_defenses"] = serialize_city_defense_rows(my_city_defenses_raw)
        context["defender_city_defenses"] = serialize_city_defense_rows(enemy_city_defenses_raw)
        context["attacker_equipment_bonuses"] = my_equipment_bonuses
        context["defender_equipment_bonuses"] = enemy_equipment_bonuses
        context["equipment_left_bonuses"] = equipment_left_bonuses
        context["equipment_right_bonuses"] = equipment_right_bonuses
        context["attacker_loss"] = my_loss
        context["defender_loss"] = enemy_loss
        context["loss_left"] = loss_left
        context["loss_right"] = loss_right

        side_context = build_side_labels(player_side=player_side, winner=report.winner)
        is_spectator = bool(side_context["is_spectator"])
        player_won = bool(side_context["player_won"])
        context["report_title"] = build_report_title(report, player_side=player_side, viewer_manor_id=viewer_manor_id)
        context.update(side_context)

        raid_run = None if is_spectator else report_runtime.get("raid_run")
        drops = (
            report.drops or {}
            if is_spectator
            else resolve_display_drops(report, player_won=player_won, player_side=player_side, raid_run=raid_run)
        )
        loss_map: dict[str, int] = {}
        if not is_spectator:
            loss_map = resolve_display_losses(player_won=player_won, player_side=player_side, raid_run=raid_run)

        capture_loss_label = (
            "" if is_spectator else resolve_capture_loss_label(player_side=player_side, raid_run=raid_run)
        )
        context.update(
            build_reward_context(
                drops=drops,
                loss_map=loss_map,
                capture_loss_label=capture_loss_label,
            )
        )
        return context
