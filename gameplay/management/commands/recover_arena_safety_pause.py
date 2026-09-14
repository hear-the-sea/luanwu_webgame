import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError

from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core.arena_pause_recovery import assess_arena_pause_recovery, recover_arena_pause


class Command(BaseCommand):
    help = "检查旧竞技场统计暂停；默认只读。显式 --apply 并提供 revision/原因为 CAS 恢复。"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-revision", type=int)
        parser.add_argument("--expected-pause-reason")

    def handle(self, *args, **options):
        assessment = assess_arena_pause_recovery()
        self.stdout.write(json.dumps(asdict(assessment), ensure_ascii=False))
        if not options["apply"]:
            return
        if options["expected_revision"] is None or not options["expected_pause_reason"]:
            raise CommandError("--apply requires --expected-revision and --expected-pause-reason from dry-run")
        try:
            result = recover_arena_pause(
                expected_revision=options["expected_revision"],
                expected_pause_reason=options["expected_pause_reason"],
            )
        except runtime_configs.RuntimeRoutingError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps({"changed": True, "revision": result.revision, "mode": result.maintenance_mode.value})
        )
