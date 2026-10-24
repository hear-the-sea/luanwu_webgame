"""定向同步小昭，避免完整门客导入清理其它模板或技能。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from battle.combatants_pkg.cache import clear_guest_template_cache
from core.utils.image_utils import compress_and_resize_image
from guests.management.commands.load_guest_templates import Command as TemplateLoader
from guests.management.commands.sync_guest_portraits import IMAGE_VERSION
from guests.models import Guest, GuestTemplate, RecruitmentCandidate
from guests.services.recruitment_templates import clear_template_cache

TEMPLATE_KEY = "hero_jinyong_0215"


class Command(BaseCommand):
    help = "仅同步小昭紫色模板及头像；保留已有门客身份、培养属性和其它模板。"

    def add_arguments(self, parser):
        parser.add_argument("--skip-images", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        loader = TemplateLoader(stdout=self.stdout, stderr=self.stderr)
        root = Path(settings.BASE_DIR)
        main = loader._load_payload(root / "data/guest_templates.yaml")
        roster = loader._load_payload(root / "data/guests/jinyong.yaml")
        matches = [
            (rarity, entry)
            for rarity, entries in (roster.get("heroes") or roster.get("hero_roster") or {}).items()
            for entry in entries
            if entry.get("key") == TEMPLATE_KEY
        ]
        if len(matches) != 1:
            raise CommandError("小昭配置必须且只能出现一次")
        rarity, entry = matches[0]
        if rarity != "purple" or entry.get("archetype") != "civil":
            raise CommandError("小昭必须配置为紫色文门客")
        profile = main["attribute_profiles"][rarity][entry["archetype"]]
        data = loader._build_template_from_stats(entry, rarity, entry["archetype"], profile)

        image_path = root / "data/images/guests" / data["avatar"]
        if not options["skip_images"] and not image_path.is_file():
            raise CommandError(f"缺少小昭头像：{image_path}")
        if options["dry_run"]:
            self.stdout.write("已验证小昭紫色模板；未修改数据库或头像存储。")
            return

        avatar_name = None
        if not options["skip_images"]:
            # 使用与头像同步命令一致的内容寻址路径，重复执行不产生副本，也不删除旧图。
            digest = hashlib.sha256(IMAGE_VERSION + image_path.read_bytes()).hexdigest()
            avatar_name = f"guests/portrait_{digest}.webp"
            storage = GuestTemplate._meta.get_field("avatar").storage
            if not storage.exists(avatar_name):
                content, _ = compress_and_resize_image(
                    image_path, max_size=(400, 400), quality=85, convert_to_webp=True
                )
                avatar_name = storage.save(avatar_name, content)

        with transaction.atomic():
            existing = GuestTemplate.objects.select_for_update().filter(key=TEMPLATE_KEY).first()
            if existing:
                # 沿用完整导入的生命比例同步；不重新分配玩家已经获得的成长属性。
                list(Guest.objects.select_for_update().filter(template=existing).values_list("pk", flat=True))
            loader._sync_templates([data], False, True, 0)
            template = GuestTemplate.objects.get(key=TEMPLATE_KEY)
            if avatar_name and template.avatar.name != avatar_name:
                template.avatar.name = avatar_name
                template.save(update_fields=["avatar"])
            RecruitmentCandidate.objects.filter(template=template).exclude(rarity=rarity).update(rarity=rarity)
            transaction.on_commit(clear_template_cache)
            transaction.on_commit(clear_guest_template_cache)
        self.stdout.write(self.style.SUCCESS("已同步小昭紫色模板；已有门客培养属性及其它门客配置均已保留。"))
