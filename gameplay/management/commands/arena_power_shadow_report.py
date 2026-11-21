from __future__ import annotations

from django.core.management.base import BaseCommand

from gameplay.models import ArenaVirtualDemand
from gameplay.services.arena.virtual_lineups import compare_power_formulas
from gameplay.services.arena.virtual_reserve_pool import _profile_lineup_snapshots


class Command(BaseCommand):
    help = "Read-only v1/v2 arena power comparison for active virtual-player demands."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--demand-id", type=int, default=None)
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options) -> None:
        demands = ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE).order_by("id")
        if options.get("demand_id") is not None:
            demands = demands.filter(id=int(options["demand_id"]))
        demand_rows = list(demands[: max(1, int(options.get("limit") or 20))])
        if not demand_rows:
            self.stdout.write("no active demands")
            return
        self.stdout.write("demand_id\tformula\tmember_id\tv1\tv2\tv2/v1")
        for demand in demand_rows:
            members = demand.reserve_members.select_related("profile").order_by("id")[
                : max(1, int(options.get("limit") or 20))
            ]
            for member in members:
                snapshots = _profile_lineup_snapshots(member.profile)
                result = compare_power_formulas(snapshots)
                ratio = (result["v2"] / result["v1"]) if result["v1"] else 0.0
                self.stdout.write(
                    f"{demand.id}\t{demand.power_formula_version}\t{member.id}\t"
                    f"{result['v1']}\t{result['v2']}\t{ratio:.3f}"
                )
