from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from gameplay.services.global_announcement import broadcast_global_announcement, publish_global_announcement


class Command(BaseCommand):
    help = "发布一条全服顶部公告，并即时推送给在线真实玩家。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--title", default="全服公告", help="公告标题。")
        parser.add_argument("--message", required=True, help="公告正文。")
        parser.add_argument(
            "--duration-hours",
            type=float,
            default=6.0,
            help="公告持续时间，单位为小时，默认 6 小时。",
        )
        parser.add_argument("--announcement-id", default=None, help="可选的幂等公告标识。")

    def handle(self, *args: object, **options: Any) -> None:
        duration_hours = float(options["duration_hours"])
        if duration_hours <= 0:
            raise CommandError("--duration-hours 必须大于 0")

        try:
            announcement = publish_global_announcement(
                title=str(options["title"]),
                body=str(options["message"]),
                duration_seconds=int(round(duration_hours * 60 * 60)),
                announcement_id=options.get("announcement_id"),
            )
            attempted, delivered = broadcast_global_announcement(announcement)
        except (RuntimeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("全服顶部公告已发布"))
        self.stdout.write(f"公告标识: {announcement['id']}")
        self.stdout.write(f"有效期至: {announcement['expires_at']}")
        self.stdout.write(f"推送玩家组: {attempted}，推送成功: {delivered}")
