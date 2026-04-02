from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Count, F
from django.utils import timezone

import gameplay.services.arena.core as arena_core
from gameplay.models import ArenaEntry, ArenaTournament
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate

User = get_user_model()


@dataclass(frozen=True)
class _ParticipantSeedResult:
    username: str
    tournament_id: int
    entry_count: int
    auto_started: bool


def _build_or_get_test_template(template_key: str) -> GuestTemplate:
    template, _ = GuestTemplate.objects.get_or_create(
        key=template_key,
        defaults={
            "name": "竞技场测试门客",
            "archetype": "military",
            "rarity": "green",
            "base_attack": 120,
            "base_intellect": 90,
            "base_defense": 100,
            "base_agility": 90,
            "base_luck": 50,
            "base_hp": 1500,
            "recruitable": False,
        },
    )
    return template


def _create_guest(manor, template: GuestTemplate, suffix: str) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=f"竞技测试-{suffix}",
        level=30,
        force=180,
        intellect=120,
        defense_stat=150,
        agility=130,
    )


class Command(BaseCommand):
    help = "一键创建竞技场测试数据：自动造号报名，可选快速跑完整场赛事。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--players",
            type=int,
            default=None,
            help="本次新增报名玩家数。默认自动补齐当前报名池至满员开赛。",
        )
        parser.add_argument(
            "--guests-per-player",
            type=int,
            default=1,
            help="每名测试玩家报名门客数量（1-10，默认1）。",
        )
        parser.add_argument(
            "--seed-silver",
            type=int,
            default=100000,
            help="给每个测试庄园设置的银两（默认100000）。",
        )
        parser.add_argument(
            "--username-prefix",
            type=str,
            default="arena_quick",
            help="测试账号前缀（默认 arena_quick）。",
        )
        parser.add_argument(
            "--template-key",
            type=str,
            default="arena_quick_test_tpl",
            help="测试门客模板 key（不存在会自动创建）。",
        )
        parser.add_argument(
            "--finish",
            action="store_true",
            help="报名满员后立即按时间推进，自动跑完整场竞技场。",
        )
        parser.add_argument(
            "--max-steps",
            type=int,
            default=20,
            help="--finish 模式下最多推进轮次次数（默认20）。",
        )
        parser.add_argument(
            "--step-seconds",
            type=int,
            default=None,
            help="--finish 模式下每步推进秒数（默认按赛事轮次间隔推进）。",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="在非 DEBUG 且非测试环境下强制执行（请谨慎）。",
        )

    def handle(self, *args: object, **options: Any) -> None:
        requested_players = options["players"]
        guests_per_player = options["guests_per_player"]
        seed_silver = options["seed_silver"]
        username_prefix = (options["username_prefix"] or "arena_quick").strip() or "arena_quick"
        template_key = (options["template_key"] or "arena_quick_test_tpl").strip() or "arena_quick_test_tpl"
        finish = bool(options["finish"])
        max_steps = int(options["max_steps"] or 0)
        step_seconds_option = options["step_seconds"]
        force = bool(options["force"])

        if not force and not settings.DEBUG and not getattr(settings, "RUNNING_TESTS", False):
            raise CommandError("arena_quick_test 仅允许在 DEBUG/测试环境执行；如需继续请显式传入 --force")

        if requested_players is not None and requested_players <= 0:
            raise CommandError("--players 必须为正整数")
        if not 1 <= guests_per_player <= 10:
            raise CommandError("--guests-per-player 必须在 1 到 10 之间")
        if seed_silver < arena_core.ARENA_REGISTRATION_SILVER_COST:
            raise CommandError(
                f"--seed-silver 不能低于报名费 {arena_core.ARENA_REGISTRATION_SILVER_COST}，否则无法报名"
            )
        if finish and max_steps <= 0:
            raise CommandError("--finish 模式下 --max-steps 必须为正整数")
        if step_seconds_option is not None and int(step_seconds_option) <= 0:
            raise CommandError("--step-seconds 必须为正整数")

        recruiting = (
            ArenaTournament.objects.filter(status=ArenaTournament.Status.RECRUITING)
            .annotate(entry_count=Count("entries"))
            .filter(entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )

        if recruiting:
            remaining = recruiting.player_limit - int(getattr(recruiting, "entry_count", 0))
            players_to_seed = remaining if requested_players is None else int(requested_players)
            if players_to_seed > remaining:
                raise CommandError(
                    f"当前报名池只差 {remaining} 人满员，--players={players_to_seed} 过大。"
                    "可改小，或等待开赛后再执行下一轮。"
                )
            self.stdout.write(
                f"检测到报名中的赛事 #{recruiting.id}，当前 {recruiting.entry_count}/{recruiting.player_limit}，本次将补齐 {players_to_seed} 人。"
            )
        else:
            players_to_seed = int(requested_players or arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT)
            self.stdout.write(
                f"未检测到可用报名池，本次将新建并报名 {players_to_seed} 人（默认满员 {arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT}）。"
            )

        if players_to_seed <= 0:
            raise CommandError("本次无需新增报名玩家")

        template = _build_or_get_test_template(template_key)
        created_users: list[str] = []
        seed_results: list[_ParticipantSeedResult] = []

        for idx in range(players_to_seed):
            token = uuid.uuid4().hex[:8]
            username = f"{username_prefix}_{token}"
            email = f"{username}@test.local"

            user = User.objects.create_user(
                username=username,
                password=None,
                email=email,
            )
            manor = ensure_manor(user)
            manor.silver = max(manor.silver, seed_silver)
            manor.save(update_fields=["silver"])

            selected_guest_ids: list[int] = []
            for guest_idx in range(guests_per_player):
                guest = _create_guest(manor, template, f"{idx + 1}-{guest_idx + 1}")
                selected_guest_ids.append(guest.id)

            result = arena_core.register_arena_entry(manor, selected_guest_ids)
            created_users.append(username)
            seed_results.append(
                _ParticipantSeedResult(
                    username=username,
                    tournament_id=result.tournament.id,
                    entry_count=result.entry_count,
                    auto_started=result.auto_started,
                )
            )

        if not seed_results:
            raise CommandError("未生成任何报名数据")

        primary_tournament_id = seed_results[-1].tournament_id
        tournament = ArenaTournament.objects.get(pk=primary_tournament_id)
        self.stdout.write(
            self.style.SUCCESS(
                f"报名完成：赛事 #{tournament.id} 状态={tournament.status}，报名人数={tournament.entries.count()}/{tournament.player_limit}"
            )
        )
        self.stdout.write(f"本次创建测试账号 {len(created_users)} 个。")

        if finish:
            self._finish_tournament(
                tournament=tournament,
                max_steps=max_steps,
                step_seconds=step_seconds_option,
            )

        self._print_summary(tournament_id=tournament.id)
        self.stdout.write(self.style.SUCCESS("竞技场快速测试完成。"))

    def _finish_tournament(self, *, tournament: ArenaTournament, max_steps: int, step_seconds: int | None) -> None:
        tournament.refresh_from_db()
        if tournament.status != ArenaTournament.Status.RUNNING:
            self.stdout.write(
                self.style.WARNING(f"赛事 #{tournament.id} 当前状态={tournament.status}，尚未开赛，跳过自动跑完。")
            )
            return

        stride_seconds = int(step_seconds or tournament.round_interval_seconds or 600)
        simulated_now = timezone.now()
        processed_rounds = 0

        for _ in range(max_steps):
            processed = arena_core.run_due_arena_rounds(now=simulated_now, limit=50)
            processed_rounds += processed
            tournament.refresh_from_db(fields=["status", "current_round", "next_round_at", "ended_at"])
            if tournament.status != ArenaTournament.Status.RUNNING:
                break
            simulated_now += timedelta(seconds=stride_seconds)

        tournament.refresh_from_db(fields=["status", "current_round", "next_round_at", "ended_at"])
        if tournament.status == ArenaTournament.Status.RUNNING:
            self.stdout.write(
                self.style.WARNING(
                    f"赛事 #{tournament.id} 仍在进行中（已推进 {max_steps} 步，处理轮次 {processed_rounds}）。"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"赛事 #{tournament.id} 已结束，状态={tournament.status}，总轮次={tournament.current_round}。"
                )
            )

    def _print_summary(self, *, tournament_id: int) -> None:
        tournament = ArenaTournament.objects.filter(pk=tournament_id).first()
        if not tournament:
            self.stdout.write(self.style.WARNING("赛事已不存在，无法输出汇总。"))
            return

        entries = list(
            ArenaEntry.objects.filter(tournament=tournament)
            .select_related("manor", "manor__user")
            .order_by("final_rank", "id")
        )
        self.stdout.write(f"赛事 #{tournament.id} 汇总：")
        self.stdout.write(
            f"- 状态: {tournament.status}"
            f" | 当前轮次: {tournament.current_round}"
            f" | 报名数: {len(entries)}/{tournament.player_limit}"
        )
        for entry in entries:
            username = entry.manor.user.username
            rank_text = str(entry.final_rank) if entry.final_rank else "-"
            self.stdout.write(
                f"  - {username}: status={entry.status}, rank={rank_text}, wins={entry.matches_won}, coins={entry.coin_reward}"
            )
