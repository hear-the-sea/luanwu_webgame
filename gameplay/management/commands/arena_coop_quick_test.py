from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Count, F, Q

import gameplay.services.arena.coop_core as arena_coop_core
from gameplay.models import ArenaCoopEntry, ArenaCoopEvent
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate

User = get_user_model()


@dataclass(frozen=True)
class _ParticipantSeedResult:
    username: str
    event_id: int
    entry_count: int
    moved_to_preparing: bool


def _build_or_get_test_template(template_key: str) -> GuestTemplate:
    template, _ = GuestTemplate.objects.get_or_create(
        key=template_key,
        defaults={
            "name": "共斗测试门客",
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
        custom_name=f"共斗测试-{suffix}",
        level=30,
        force=180,
        intellect=120,
        defense_stat=150,
        agility=130,
    )


class Command(BaseCommand):
    help = "一键创建围攻光明顶测试数据：自动造号报名，快速补齐共斗队友。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--players",
            type=int,
            default=None,
            help="本次新增报名玩家数。默认自动补齐当前报名池至满员。",
        )
        parser.add_argument(
            "--guests-per-player",
            type=int,
            default=arena_coop_core.ARENA_COOP_MAX_GUESTS_PER_ENTRY,
            help="每名测试玩家报名门客数量（默认按共斗上限）。",
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
            default="arena_coop_quick",
            help="测试账号前缀（默认 arena_coop_quick）。",
        )
        parser.add_argument(
            "--template-key",
            type=str,
            default="arena_coop_quick_test_tpl",
            help="测试门客模板 key（不存在会自动创建）。",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="在非 DEBUG 且非测试环境下强制执行（请谨慎）。",
        )

    def handle(self, *args: object, **options: Any) -> None:
        requested_players = options["players"]
        guests_per_player = int(options["guests_per_player"] or 0)
        seed_silver = int(options["seed_silver"] or 0)
        username_prefix = (options["username_prefix"] or "arena_coop_quick").strip() or "arena_coop_quick"
        template_key = (options["template_key"] or "arena_coop_quick_test_tpl").strip() or "arena_coop_quick_test_tpl"
        force = bool(options["force"])

        if not force and not settings.DEBUG and not getattr(settings, "RUNNING_TESTS", False):
            raise CommandError("arena_coop_quick_test 仅允许在 DEBUG/测试环境执行；如需继续请显式传入 --force")

        if requested_players is not None and int(requested_players) <= 0:
            raise CommandError("--players 必须为正整数")
        if not 1 <= guests_per_player <= arena_coop_core.ARENA_COOP_MAX_GUESTS_PER_ENTRY:
            raise CommandError(
                f"--guests-per-player 必须在 1 到 {arena_coop_core.ARENA_COOP_MAX_GUESTS_PER_ENTRY} 之间"
            )
        if seed_silver < arena_coop_core.ARENA_COOP_REGISTRATION_SILVER_COST:
            raise CommandError(
                f"--seed-silver 不能低于报名费 {arena_coop_core.ARENA_COOP_REGISTRATION_SILVER_COST}，否则无法报名"
            )

        recruiting = (
            ArenaCoopEvent.objects.filter(status=ArenaCoopEvent.Status.RECRUITING)
            .annotate(
                entry_count=Count(
                    "entries",
                    filter=Q(entries__status=ArenaCoopEntry.Status.REGISTERED),
                )
            )
            .filter(entry_count__lt=F("player_limit"))
            .order_by("created_at")
            .first()
        )

        if recruiting:
            remaining = recruiting.player_limit - int(getattr(recruiting, "entry_count", 0))
            players_to_seed = remaining if requested_players is None else int(requested_players)
            if players_to_seed > remaining:
                raise CommandError(f"当前共斗池只差 {remaining} 人满员，--players={players_to_seed} 过大。")
            self.stdout.write(
                f"检测到报名中的共斗 #{recruiting.id}，当前 {getattr(recruiting, 'entry_count', 0)}/{recruiting.player_limit}，"
                f"本次将补齐 {players_to_seed} 人。"
            )
        else:
            players_to_seed = int(requested_players or arena_coop_core.ARENA_COOP_PLAYER_LIMIT)
            self.stdout.write(
                f"未检测到可用共斗报名池，本次将新建并报名 {players_to_seed} 人"
                f"（默认满员 {arena_coop_core.ARENA_COOP_PLAYER_LIMIT}）。"
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

            result = arena_coop_core.register_arena_coop_entry(manor, selected_guest_ids)
            created_users.append(username)
            seed_results.append(
                _ParticipantSeedResult(
                    username=username,
                    event_id=result.event.id,
                    entry_count=result.entry_count,
                    moved_to_preparing=result.moved_to_preparing,
                )
            )

        if not seed_results:
            raise CommandError("未生成任何共斗报名数据")

        event = ArenaCoopEvent.objects.get(pk=seed_results[-1].event_id)
        entry_count = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"报名完成：共斗 #{event.id} 状态={event.status}，报名人数={entry_count}/{event.player_limit}"
            )
        )
        self.stdout.write(f"本次创建测试账号 {len(created_users)} 个。")
        self.stdout.write(self.style.SUCCESS("共斗快速测试完成。"))
