"""
清理过期数据的管理命令

用于定期清理旧的流水记录，节省数据库空间。
建议通过 cron 每天执行一次。

使用方式：
    python manage.py cleanup_old_data                    # 使用默认配置清理
    python manage.py cleanup_old_data --days 7           # 清理7天前的数据
    python manage.py cleanup_old_data --dry-run          # 模拟运行，不实际删除
    python manage.py cleanup_old_data --batch-size 5000  # 设置批量删除大小

竞技场补位期次及结果成对清理：期次最后观测和所有结果的发布时间均须超过保留期。
指定任一补位模型都会清理整个期次及其结果；未关闭或待发布证据不会删除。
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.config import MESSAGE
from gameplay.services.arena.fill_service_retention import (
    FillServiceCleanupResult,
    cleanup_fill_service_rows,
    eligible_fill_service_episodes,
    fill_service_outcomes_for_retention,
)
from gameplay.tasks.maintenance import ARENA_FILL_SERVICE_RETENTION_DAYS, VIRTUAL_PLAYER_SAFETY_AUDIT_RETENTION_DAYS

# 清理配置：模型 -> (默认保留天数, 时间字段名)
CLEANUP_CONFIG = {
    "gameplay.ResourceEvent": (30, "created_at"),  # 资源流水保留30天
    "gameplay.ArenaExchangeRecord": (30, "created_at"),  # 竞技场兑换记录保留30天
    "battle.BattleReport": (30, "created_at"),  # 战报保留30天
    "gameplay.Message": (MESSAGE.RETENTION_DAYS, "created_at"),  # 消息保留配置天数
    "guilds.GuildResourceLog": (30, "created_at"),  # 帮会资源流水保留30天
    "guilds.GuildDonationLog": (60, "donated_at"),  # 帮会捐献记录保留60天
    "guilds.GuildExchangeLog": (60, "exchanged_at"),  # 帮会兑换记录保留60天
    "gameplay.BotSafetyDecisionAudit": (VIRTUAL_PLAYER_SAFETY_AUDIT_RETENTION_DAYS, "created_at"),
}

FILL_SERVICE_MODEL_PATHS = frozenset(
    {
        "gameplay.ArenaFillServiceEpisode",
        "gameplay.ArenaFillServiceOutcome",
    }
)


class Command(BaseCommand):
    help = "清理过期的流水记录数据，节省数据库空间"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="覆盖所有模型的保留天数，须非负（0 表示立即清理符合条件的历史记录；默认使用各模型配置）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="模拟运行，只显示将要删除的数量，不实际删除",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10000,
            help="批量删除的大小，避免锁表过久（默认10000）",
        )
        parser.add_argument(
            "--model",
            type=str,
            default=None,
            help="只清理指定模型（如 gameplay.ResourceEvent）；任一 ArenaFillService 模型均按期次和结果成对清理",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = int(options["batch_size"])
        if batch_size < 1:
            raise CommandError("batch-size must be at least 1")
        override_days = options["days"]
        if override_days is not None and override_days < 0:
            raise CommandError("days must be non-negative")
        target_model = options["model"]

        if dry_run:
            self.stdout.write(self.style.WARNING("=== 模拟运行模式，不会实际删除数据 ===\n"))

        total_deleted = 0

        if target_model is None or target_model in FILL_SERVICE_MODEL_PATHS:
            days = override_days if override_days is not None else ARENA_FILL_SERVICE_RETENTION_DAYS
            total_deleted += self._cleanup_fill_service(days, dry_run, batch_size)

        for model_path, (default_days, time_field) in CLEANUP_CONFIG.items():
            # 如果指定了模型，只处理该模型
            if target_model and model_path != target_model:
                continue

            days = override_days if override_days is not None else default_days
            deleted = self._cleanup_model(model_path, days, time_field, dry_run, batch_size)
            total_deleted += deleted

        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"模拟完成：共将删除 {total_deleted} 条记录"))
        else:
            self.stdout.write(self.style.SUCCESS(f"清理完成：共删除 {total_deleted} 条记录"))

    def _cleanup_fill_service(self, days: int, dry_run: bool, batch_size: int) -> int:
        """Clean terminal episodes and their protected outcomes in FK-safe order."""
        cutoff = timezone.now() - timedelta(days=days)
        if dry_run:
            result = FillServiceCleanupResult(
                episodes=int(eligible_fill_service_episodes(cutoff=cutoff).count()),
                outcomes=int(fill_service_outcomes_for_retention(cutoff=cutoff).count()),
            )
        else:
            deleted_episodes = 0
            deleted_outcomes = 0
            while True:
                batch = cleanup_fill_service_rows(
                    cutoff=cutoff,
                    batch_size=batch_size,
                )
                deleted_episodes += batch.episodes
                deleted_outcomes += batch.outcomes
                if batch.episodes < batch_size:
                    break
            result = FillServiceCleanupResult(episodes=deleted_episodes, outcomes=deleted_outcomes)
        if result.deleted == 0:
            self.stdout.write(f"[gameplay.ArenaFillService] 无需清理（保留 {days} 天）")
            return 0
        verb = "将删除" if dry_run else "已删除"
        style = self.style.WARNING if dry_run else self.style.SUCCESS
        self.stdout.write(
            style(
                f"[gameplay.ArenaFillService] {verb}期次 {result.episodes} 条、结果 {result.outcomes} 条 "
                f"（保留 {days} 天；仅处理已关闭且所有结果发布均超过保留期的证据）"
            )
        )
        return result.deleted

    def _cleanup_model(self, model_path: str, days: int, time_field: str, dry_run: bool, batch_size: int) -> int:
        """清理单个模型的过期数据"""
        try:
            model = self._get_model(model_path)
        except (LookupError, ModuleNotFoundError) as e:
            self.stdout.write(self.style.WARNING(f"[跳过] {model_path}: {e}"))
            return 0

        cutoff = timezone.now() - timedelta(days=days)

        if model_path == "gameplay.Message":
            from gameplay.services.utils.messages import delete_expired_messages

            result = delete_expired_messages(cutoff, batch_size=batch_size, dry_run=dry_run)
            deleted_count = result.deleted_count
            protected_count = result.protected_count
            if deleted_count == 0 and protected_count == 0:
                self.stdout.write(f"[{model_path}] 无需清理（保留 {days} 天）")
                return 0

            if dry_run:
                message = (
                    f"[{model_path}] 将删除 {deleted_count} 条 {days} 天前的记录，"
                    f"保留 {protected_count} 条未领取附件消息"
                )
                self.stdout.write(self.style.WARNING(message))
            else:
                message = (
                    f"[{model_path}] 已删除 {deleted_count} 条 {days} 天前的记录，"
                    f"保留 {protected_count} 条未领取附件消息"
                )
                self.stdout.write(self.style.SUCCESS(message))
            return deleted_count

        # 构建过滤条件：{time_field}__lt=cutoff
        filter_kwargs = {f"{time_field}__lt": cutoff}

        # 查找过期记录
        queryset = model.objects.filter(**filter_kwargs)
        count = queryset.count()

        if count == 0:
            self.stdout.write(f"[{model_path}] 无需清理（保留 {days} 天）")
            return 0

        if dry_run:
            self.stdout.write(self.style.WARNING(f"[{model_path}] 将删除 {count} 条 {days} 天前的记录"))
            return count

        # 批量删除，避免锁表
        deleted_total = 0
        while True:
            # 获取一批要删除的 ID
            ids_to_delete = list(model.objects.filter(**filter_kwargs).values_list("id", flat=True)[:batch_size])
            if not ids_to_delete:
                break

            deleted, _ = model.objects.filter(id__in=ids_to_delete).delete()
            deleted_total += deleted

            if deleted < batch_size:
                break

        self.stdout.write(self.style.SUCCESS(f"[{model_path}] 已删除 {deleted_total} 条 {days} 天前的记录"))
        return deleted_total

    def _get_model(self, model_path: str):
        """根据路径获取模型类"""
        from django.apps import apps

        app_label, model_name = model_path.rsplit(".", 1)
        return apps.get_model(app_label, model_name)
