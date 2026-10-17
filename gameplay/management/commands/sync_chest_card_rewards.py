"""Synchronize only approved chest card rewards, without unrelated migrations."""

from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from gameplay.models import ItemTemplate


class Command(BaseCommand):
    help = "核对宝箱任务卡奖励；指定 --apply 后仅同步这六类宝箱，不执行其他迁移。"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="将已批准的卡片奖励配置应用到当前数据库。")

    def handle(self, *args, **options):
        rules = import_module("gameplay.migrations.0200_reduce_chest_card_rewards")
        with transaction.atomic():
            changed = sum(
                rules.rebalance_payload(item.key, item.effect_payload) != item.effect_payload
                for item in ItemTemplate.objects.select_for_update().filter(
                    key__in=rules.CHEST_CARD_CHANCES, effect_type="loot_box"
                )
            )
            if options["apply"]:
                rules.sync_chest_card_rewards(apps, SimpleNamespace(connection=connection))
            action = "已更新" if options["apply"] else "待更新（只读核对）"
            self.stdout.write(f"{action}：{changed} 类宝箱")
