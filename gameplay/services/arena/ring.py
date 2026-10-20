from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from battle.services import simulate_report
from gameplay.models import (
    ArenaRing,
    ArenaRingAction,
    ArenaRingDaily,
    ArenaRingInspection,
    ArenaRingMatch,
    ArenaRingTenure,
    Manor,
)

from .ring_rules import RING_DEFINITIONS as RING_DEFINITIONS
from .ring_rules import load_ring_rules, ring_challenge_state
from .ring_snapshots import (
    ArenaRingError,
    build_ring_lineup,
    build_ring_technology_payload,
    ring_battle_troops,
    ring_guest_proxies,
)


def _end(ring: ArenaRing, tenure: ArenaRingTenure, now: datetime, reason: str) -> None:
    tenure.ended_at = min(now, tenure.ends_at)
    tenure.end_reason = reason
    tenure.save(update_fields=["ended_at", "end_reason"])
    ring.current_tenure = None
    ring.version += 1
    ring.save(update_fields=["current_tenure", "version"])


def _new_tenure(ring: ArenaRing, manor: Manor, lineup: dict[str, Any], now: datetime) -> ArenaRingTenure:
    if ArenaRingTenure.objects.filter(manor=manor, ended_at__isnull=True).exists():
        raise ArenaRingError("每人最多同时占据一个擂台")
    if (
        ArenaRingTenure.objects.filter(
            ring=ring,
            manor=manor,
            ended_at__gt=now - timedelta(seconds=load_ring_rules()["cooldown_seconds"]),
        )
        .exclude(end_reason="admin_reset")
        .exists()
    ):
        raise ArenaRingError(f"下台后 {load_ring_rules()['cooldown_seconds'] // 60} 分钟内不能重新占据原擂台")
    tenure = ArenaRingTenure.objects.create(
        ring=ring,
        manor=manor,
        lineup=lineup,
        started_at=now,
        ends_at=now + timedelta(seconds=load_ring_rules()["tenure_seconds"]),
        settled_at=now,
        lineup_updated_at=now,
    )
    ring.current_tenure = tenure
    ring.version += 1
    ring.save(update_fields=["current_tenure", "version"])
    return tenure


def _lock_rings_and_manors(
    manor_id: int,
) -> tuple[list[ArenaRing], dict[int, ArenaRingTenure], dict[int, Manor]]:
    # Both rings serialize cross-ring occupation and establish a fixed lock order.
    rings = list(
        ArenaRing.objects.select_for_update().filter(key__in=[key for key, _ in RING_DEFINITIONS]).order_by("id")
    )
    tenure_ids = [r.current_tenure_id for r in rings if r.current_tenure_id]
    tenures = {t.pk: t for t in ArenaRingTenure.objects.select_for_update().filter(pk__in=tenure_ids).order_by("id")}
    ids = {manor_id, *(t.manor_id for t in tenures.values())}
    manors = {m.pk: m for m in Manor.objects.select_for_update().filter(pk__in=ids).order_by("id")}
    return rings, tenures, manors


def _action(
    kind: str,
    *,
    manor: Manor,
    ring_key: str,
    action_token: UUID | str,
    expected_version: int | str,
    expected_tenure_id: int | str | None = None,
    guest_ids: Iterable[int | str] | None = None,
    troop_loadout: Mapping[str, int | str] | None = None,
) -> dict[str, Any]:
    try:
        token = UUID(str(action_token))
        version = int(expected_version)
        expected_tenure_id = int(expected_tenure_id) if expected_tenure_id is not None else None
    except (ValueError, TypeError):
        raise ArenaRingError("操作凭证无效，请刷新页面") from None
    if ring_key not in {key for key, _ in RING_DEFINITIONS}:
        raise ArenaRingError("擂台不存在")
    with transaction.atomic():
        rings, tenures, manors = _lock_rings_and_manors(manor.pk)
        now = timezone.now()
        actor = manors[manor.pk]
        previous = ArenaRingAction.objects.filter(manor=actor, token=token).first()
        if previous:
            if previous.kind != kind or previous.result.get("ring_key") != ring_key:
                raise ArenaRingError("操作凭证已用于其他操作")
            return previous.result
        ring = next((r for r in rings if r.key == ring_key), None)
        if ring is None:
            raise ArenaRingError("擂台不存在")
        tenure = tenures.get(ring.current_tenure_id) if ring.current_tenure_id else None
        # Expired snapshots are rejected; refresh commits independently through task.
        if ring.version != version or (kind != "occupy" and (not tenure or tenure.pk != expected_tenure_id)):
            raise ArenaRingError("擂主或阵容已变化，请刷新页面")
        if tenure and tenure.ends_at <= now:
            raise ArenaRingError("本任守擂已结束，请刷新页面")
        result: dict[str, Any] = {"ring_key": ring.key}
        if kind == "occupy":
            if tenure:
                raise ArenaRingError("擂台已有擂主，请发起挑战")
            tenure = _new_tenure(
                ring,
                actor,
                build_ring_lineup(actor, guest_ids or [], troop_loadout or {}),
                now,
            )
        elif kind == "inspect":
            assert tenure is not None
            if tenure.manor_id == actor.pk:
                result["lineup"] = tenure.lineup
            else:
                receipt = ArenaRingInspection.objects.create(
                    buyer=actor,
                    tenure=tenure,
                    version=ring.version,
                    lineup=tenure.lineup,
                    created_at=now,
                )
                result["inspection_id"] = receipt.pk
        elif kind in ("leave", "update"):
            assert tenure is not None
            if tenure.manor_id != actor.pk:
                raise ArenaRingError("只有擂主可以执行此操作")
            if kind == "leave":
                _end(ring, tenure, now, "leave")
            else:
                tenure.lineup = build_ring_lineup(actor, guest_ids or [], troop_loadout or {})
                tenure.version += 1
                tenure.lineup_updated_at = now
                tenure.save(update_fields=["lineup", "version", "lineup_updated_at"])
                ring.version += 1
                ring.save(update_fields=["version"])
        elif kind == "challenge":
            assert tenure is not None
            if ArenaRingTenure.objects.filter(manor=actor, ended_at__isnull=True).exists():
                raise ArenaRingError("占台期间不能挑战其他擂台")
            if (
                ArenaRingTenure.objects.filter(
                    ring=ring,
                    manor=actor,
                    ended_at__gt=now - timedelta(seconds=load_ring_rules()["cooldown_seconds"]),
                )
                .exclude(end_reason="admin_reset")
                .exists()
            ):
                raise ArenaRingError(f"下台后 {load_ring_rules()['cooldown_seconds'] // 60} 分钟内不能重新占据原擂台")
            challenge_state = ring_challenge_state(tenure, actor, now)
            if challenge_state["tenure_challenges_remaining"] == 0:
                raise ArenaRingError(f"对本任擂主最多挑战 {load_ring_rules()['max_tenure_challenges']} 次")
            if challenge_state["challenge_cooldown"]:
                raise ArenaRingError(f"攻擂冷却中，还需等待 {challenge_state['challenge_cooldown_minutes']} 分钟")
            attacker_daily, _ = ArenaRingDaily.objects.get_or_create(manor=actor, date=timezone.localdate(now))
            if attacker_daily.challenges >= load_ring_rules()["daily_challenges"]:
                raise ArenaRingError("今日擂台挑战次数已用完")
            lineup = build_ring_lineup(actor, guest_ids or [], troop_loadout or {})
            defender = manors[tenure.manor_id]
            report = simulate_report(
                manor=actor,
                battle_type="arena",
                troop_loadout=lineup["troops"],
                fill_default_troops=False,
                attacker_guests=ring_guest_proxies(lineup),
                defender_guests=ring_guest_proxies(tenure.lineup),
                defender_setup={
                    "troop_loadout": ring_battle_troops(tenure.lineup),
                    "technology": build_ring_technology_payload(defender),
                },
                attacker_tech_levels=lineup["technology"]["levels"],
                attacker_guest_bonuses={},
                validate_attacker_troop_capacity=True,
                max_squad=lineup["guest_count"],
                defender_max_squad=tenure.lineup["guest_count"],
                auto_reward=False,
                send_message=False,
                apply_damage=False,
                use_lock=False,
                opponent_name=defender.display_name,
            )
            low, high = sorted((actor.pk, defender.pk))
            date = timezone.localdate(now)
            winner = actor if report.winner == "attacker" else (defender if report.winner == "defender" else None)
            match = ArenaRingMatch.objects.create(
                tenure=tenure,
                attacker=actor,
                defender=defender,
                winner=winner,
                battle_report=report,
                pair_low=low,
                pair_high=high,
                reward_date=date,
                created_at=now,
            )
            attacker_daily.challenges += 1
            attacker_daily.save()
            if winner == actor:
                _end(ring, tenure, now, "defeated")
                tenure = _new_tenure(ring, actor, lineup, now)
            elif winner == defender:
                tenure.defenses += 1
                tenure.save(update_fields=["defenses"])
            result.update(match_id=match.pk, battle_report_id=report.pk, won=winner == actor)
        assert tenure is not None
        result.update(tenure_id=tenure.pk, version=ring.version)
        ArenaRingAction.objects.create(manor=actor, token=token, kind=kind, result=result)
        return result


def occupy_ring(**kwargs: Any) -> dict[str, Any]:
    return _action("occupy", **kwargs)


def challenge_ring(**kwargs: Any) -> dict[str, Any]:
    return _action("challenge", **kwargs)


def update_ring_lineup(**kwargs: Any) -> dict[str, Any]:
    return _action("update", **kwargs)


def leave_ring(**kwargs: Any) -> dict[str, Any]:
    return _action("leave", **kwargs)


def inspect_ring(**kwargs: Any) -> dict[str, Any]:
    return _action("inspect", **kwargs)


def refresh_due_rings(*, now: datetime | None = None) -> int:
    with transaction.atomic():
        rings = list(
            ArenaRing.objects.select_for_update().filter(key__in=[key for key, _ in RING_DEFINITIONS]).order_by("id")
        )
        tenures = {
            t.pk: t
            for t in ArenaRingTenure.objects.select_for_update()
            .filter(pk__in=[r.current_tenure_id for r in rings if r.current_tenure_id])
            .order_by("id")
        }
        now = now or timezone.now()
        count = 0
        for ring in rings:
            tenure = tenures.get(ring.current_tenure_id) if ring.current_tenure_id else None
            if tenure:
                if tenure.ends_at <= now:
                    _end(ring, tenure, now, "expired")
                    count += 1
        return count
