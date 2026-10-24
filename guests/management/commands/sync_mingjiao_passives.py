"""定向同步明教首领的三个被动技能。"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from battle.combatants_pkg.cache import clear_guest_template_cache
from guests.management.commands.load_guest_templates import Command as TemplateLoader
from guests.services.recruitment_templates import clear_template_cache

SKILL_KEYS = frozenset({"gl_top_qiankun_shift", "gl_top_mingjiao_command", "gl_top_holy_flame_rage"})


class Command(BaseCommand):
    help = "仅同步乾坤大挪移、明教号令、圣火狂势，保留其它技能及门客数据。"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        loader = TemplateLoader(stdout=self.stdout, stderr=self.stderr)
        payload = loader._load_payload(Path(settings.BASE_DIR) / "data/arena_coop_special_skills.yaml")
        skills = [skill for skill in payload.get("skills", []) if skill.get("key") in SKILL_KEYS]
        if len(skills) != len(SKILL_KEYS) or {skill["key"] for skill in skills} != SKILL_KEYS:
            raise CommandError("明教三个被动技能必须各配置一次。")
        if any(
            skill.get("kind") != "passive" or not skill.get("passive_config", {}).get("triggers") for skill in skills
        ):
            raise CommandError("明教技能必须为具有触发配置的被动技能。")
        if options["dry_run"]:
            self.stdout.write("已验证明教三个被动技能；未修改数据库。")
            return
        with transaction.atomic():
            loader._sync_skills_and_books(skills, [], options["verbosity"])
            transaction.on_commit(clear_template_cache)
            transaction.on_commit(clear_guest_template_cache)
        self.stdout.write(self.style.SUCCESS("已同步明教三个被动技能并清理模板缓存。"))
