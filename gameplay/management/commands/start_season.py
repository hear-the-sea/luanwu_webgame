from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.exceptions import GameError
from gameplay.services.season.availability import season_gameplay_enabled
from gameplay.services.season.lifecycle import create_season


class Command(BaseCommand):
    help = "开启一个90天赛季，并固定当季规则快照。请使用单个运营进程执行。"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--name", default="山海争鸣·第一季", help="赛季名称")
        parser.add_argument("--starts-at", help="ISO开始时间；省略时立即开启，不含时区时按服务器时区解析")

    def handle(self, *args: Any, **options: Any) -> None:
        if not season_gameplay_enabled():
            raise CommandError("赛季玩法已关闭（DJANGO_SEASON_GAMEPLAY_ENABLED=0），不能创建新赛季")
        starts_at = timezone.now()
        if options["starts_at"]:
            try:
                parsed = parse_datetime(options["starts_at"])
            except ValueError as exc:
                raise CommandError("--starts-at 必须为有效的 ISO 日期时间") from exc
            if parsed is None:
                raise CommandError("--starts-at 必须为有效的 ISO 日期时间")
            starts_at = timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
        try:
            season = create_season(options["name"], starts_at=starts_at)
        except GameError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"赛季 #{season.pk}「{season.name}」已创建："
                f"{timezone.localtime(season.starts_at).isoformat()} 至 {timezone.localtime(season.ends_at).isoformat()}"
            )
        )
