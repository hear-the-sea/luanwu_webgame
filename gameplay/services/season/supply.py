"""据点补给真实消耗；缺粮只安全撤军，不偷偷改变战斗属性。"""

from datetime import datetime, timedelta
from math import ceil
from typing import Any

from django.utils.dateparse import parse_datetime

from gameplay.models import SeasonExpedition

from .development_rules import feature_rules
from .operation_costs import battle_grain, run_operation_rules, settle_garrison_cost_locked


def settle_supplies_locked(season: Any, now: datetime) -> None:
    from .expeditions import _start_return
    from .recovery import settle_auto_repairs_locked

    settle_auto_repairs_locked(season, now)
    config = feature_rules(season, "activities")
    if not config["enabled"]:
        return
    rules = config["transport"]
    seconds = rules["supply_tick_seconds"]
    for site in season.sites.exclude(owner=""):
        stationed = list(
            SeasonExpedition.objects.filter(
                member__season=season,
                node_key=site.key,
                member__faction=site.owner,
                kind="garrison",
                status="stationed",
            ).order_by("launched_at", "pk")
        )
        modern = [run for run in stationed if run_operation_rules(run)]
        for run in modern:
            if not settle_garrison_cost_locked(run, site, now):
                _start_return(run, now, "城内粮草不足，驻军安全返程；请先运输补给后再驻防")
        stationed = [run for run in stationed if not run_operation_rules(run)]
        previous = site.supply_updated_at or now
        if site.supply_updated_at is None:
            site.supply_updated_at = now
            site.save(update_fields=["supply_updated_at"])
        if not stationed:
            site.supply_updated_at = now
            site.save(update_fields=["supply_updated_at"])
            continue
        earliest = min(
            parse_datetime(run.result.get("stationed_at", ""))
            or run.launched_at + timedelta(seconds=run.travel_seconds)
            for run in stationed
        )
        previous = max(previous, earliest)
        account_until = min(now, max(run.due_at for run in stationed))
        ticks = max(0, int((account_until - previous).total_seconds()) // seconds)
        if not ticks:
            continue
        for index in range(ticks):
            tick = previous + timedelta(seconds=seconds * (index + 1))
            for run in stationed:
                arrived = parse_datetime(run.result.get("stationed_at", "")) or run.launched_at + timedelta(
                    seconds=run.travel_seconds
                )
                if run.status != "stationed" or arrived > tick or run.due_at < tick:
                    continue
                cost = max(1, ceil(sum(run.troops.values()) / 100)) * rules["garrison_grain_per_100"]
                if site.supplies.get("grain", 0) < cost:
                    _start_return(run, now, "城内粮草不足，驻军安全返程；请先运输补给后再驻防")
                else:
                    site.supplies = {**site.supplies, "grain": site.supplies.get("grain", 0) - cost}
            if not any(run.status == "stationed" for run in stationed):
                break
        site.supply_updated_at = previous + timedelta(seconds=ticks * seconds)
        site.save(update_fields=["supplies", "supply_updated_at"])


def prepare_defense_locked(
    site: Any, runs: list[Any], now: datetime, *, allocations: dict[int, dict[str, int]] | None = None
) -> list[Any]:
    from .expeditions import _start_return

    config = feature_rules(site.season, "activities")
    if not config["enabled"]:
        return runs
    kept = []
    for run in runs:
        modern = bool(run_operation_rules(run))
        if modern:
            if not settle_garrison_cost_locked(run, site, now):
                _start_return(run, now, "城内粮草不足，驻军安全返程；请先运输补给后再驻防")
                continue
            cost = ceil(battle_grain(run, allocations.get(run.pk, {}) if allocations is not None else run.troops))
        else:
            cost = max(1, ceil(sum(run.troops.values()) / 100)) * config["transport"]["battle_grain_per_100"]
        if site.supplies.get("grain", 0) >= cost:
            site.supplies = {**site.supplies, "grain": site.supplies.get("grain", 0) - cost}
            kept.append(run)
            if modern:
                from .operation_costs import _record

                _record(run, "defense_battle", {"grain": cost}, wave=run.battles + 1)
        else:
            _start_return(run, now, "本波战备粮不足，队伍安全撤回，未承受战斗损伤")
    site.save(update_fields=["supplies"])
    return kept
