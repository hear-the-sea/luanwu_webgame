"""
清理过期数据的管理命令

用于定期清理旧的流水记录，节省数据库空间。
建议通过 cron 每天执行一次。

使用方式：
    python manage.py cleanup_old_data                    # 使用默认配置清理
    python manage.py cleanup_old_data --days 7           # 清理7天前的数据
    python manage.py cleanup_old_data --dry-run          # 模拟运行，不实际删除
    python manage.py cleanup_old_data --batch-size 5000  # 设置批量删除大小
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.config import MESSAGE

# 清理配置：模型 -> (默认保留天数, 时间字段名)
CLEANUP_CONFIG = {
    "gameplay.ResourceEvent": (30, "created_at"),  # 资源流水保留30天
    "gameplay.ArenaExchangeRecord": (30, "created_at"),  # 竞技场兑换记录保留30天
    "battle.BattleReport": (30, "created_at"),  # 战报保留30天
    "gameplay.Message": (MESSAGE.RETENTION_DAYS, "created_at"),  # 消息保留配置天数
    "guilds.GuildResourceLog": (30, "created_at"),  # 帮会资源流水保留30天
    "guilds.GuildDonationLog": (60, "donated_at"),  # 帮会捐献记录保留60天
    "guilds.GuildExchangeLog": (60, "exchanged_at"),  # 帮会兑换记录保留60天
}


class Command(BaseCommand):
    help = "清理过期的流水记录数据，节省数据库空间"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="覆盖所有模型的保留天数（默认使用各模型的配置）",
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
            help="只清理指定模型（如 gameplay.ResourceEvent）",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        override_days = options["days"]
        target_model = options["model"]

        if dry_run:
            self.stdout.write(self.style.WARNING("=== 模拟运行模式，不会实际删除数据 ===\n"))

        total_deleted = 0

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
