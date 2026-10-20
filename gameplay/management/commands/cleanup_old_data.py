"""
清理过期数据的管理命令

用于定期清理旧的流水记录，节省数据库空间。
建议通过 Celery Beat 每小时执行一次。

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
from gameplay.record_retention import (
    MAX_RECORD_RETENTION_DAYS,
    TERMINAL_RECORD_TIME_FIELDS,
    delete_eligible_record_batch,
    eligible_record_queryset,
)
from gameplay.services.arena.fill_service_retention import (
    FillServiceCleanupResult,
    cleanup_fill_service_rows,
    eligible_fill_service_episodes,
    fill_service_outcomes_for_retention,
)
from gameplay.tasks.maintenance import ARENA_FILL_SERVICE_RETENTION_DAYS, VIRTUAL_PLAYER_SAFETY_AUDIT_RETENTION_DAYS

# 清理配置：模型 -> (默认保留天数, 时间字段名)
CLEANUP_CONFIG = {
    "gameplay.ResourceEvent": (MAX_RECORD_RETENTION_DAYS, "created_at"),
    "gameplay.ArenaExchangeRecord": (MAX_RECORD_RETENTION_DAYS, "created_at"),
    "battle.BattleReport": (MAX_RECORD_RETENTION_DAYS, "created_at"),
    "gameplay.Message": (min(MAX_RECORD_RETENTION_DAYS, MESSAGE.RETENTION_DAYS), "created_at"),
    "guilds.GuildResourceLog": (MAX_RECORD_RETENTION_DAYS, "created_at"),
    "guilds.GuildDonationLog": (MAX_RECORD_RETENTION_DAYS, "donated_at"),
    "guilds.GuildExchangeLog": (MAX_RECORD_RETENTION_DAYS, "exchanged_at"),
    "gameplay.BotSafetyDecisionAudit": (VIRTUAL_PLAYER_SAFETY_AUDIT_RETENTION_DAYS, "created_at"),
}

TERMINAL_CLEANUP_CONFIG = {
    model_path: (MAX_RECORD_RETENTION_DAYS, time_field)
    for model_path, time_field in TERMINAL_RECORD_TIME_FIELDS.items()
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
            help=("设置保留天数；会限制在15天以内，须非负（0 表示立即清理符合条件的历史记录；" "默认使用各模型配置）"),
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
        if override_days is not None:
            override_days = min(MAX_RECORD_RETENTION_DAYS, int(override_days))
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

        for model_path, (default_days, time_field) in TERMINAL_CLEANUP_CONFIG.items():
            if target_model and model_path != target_model:
                continue

            days = override_days if override_days is not None else default_days
            deleted = self._cleanup_model(
                model_path,
                days,
                time_field,
                dry_run,
                batch_size,
                terminal_only=True,
            )
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

    def _cleanup_model(
        self,
        model_path: str,
        days: int,
        time_field: str,
        dry_run: bool,
        batch_size: int,
        *,
        terminal_only: bool = False,
    ) -> int:
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

        queryset = (
            eligible_record_queryset(model_path, cutoff=cutoff)
            if terminal_only
            else model.objects.filter(**{f"{time_field}__lt": cutoff})
        )
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
            if terminal_only:
                eligible = eligible_record_queryset(model_path, cutoff=cutoff)
            else:
                eligible = model.objects.filter(**{f"{time_field}__lt": cutoff})
            ids_to_delete = list(eligible.order_by(time_field, "pk").values_list("pk", flat=True)[:batch_size])
            if not ids_to_delete:
                break

            if terminal_only:
                deleted = delete_eligible_record_batch(model_path, cutoff=cutoff, record_ids=ids_to_delete)
            else:
                deleted, _ = eligible.filter(pk__in=ids_to_delete).delete()
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
