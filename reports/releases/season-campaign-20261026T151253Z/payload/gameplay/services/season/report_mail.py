"""将真实参战战报投递到站内邮件；与结算共用事务并按玩家/战报防重。"""

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from gameplay.models import SeasonClaim
from gameplay.services.utils.messages import create_message

from .rules import NODES


def report_side(run: Any, report: Any) -> str:
    ids = set(run.guest_ids)
    return "attacker" if any(row.get("guest_id") in ids for row in report.attacker_team) else "defender"


def send_report_mail_locked(
    run: Any, report: Any, now: datetime, merit: int = 0, *, reward: dict[str, Any] | None = None
) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("赛季战报邮件必须在结算事务内投递")
    side = report_side(run, report)
    team = report.attacker_team if side == "attacker" else report.defender_team
    participating = [row for row in team if row.get("guest_id") in set(run.guest_ids)]
    if not participating:
        return
    _, created = SeasonClaim.objects.get_or_create(member=run.member, key=f"battle_mail:{report.pk}")
    if not created:
        return
    node = run.member.season.rules.get("campaign", {}).get("sites", {}).get(run.node_key, NODES.get(run.node_key, {}))
    name = node.get("name", "阵营战斗")
    role = "进攻" if side == "attacker" else "防守"
    outcome = "胜利" if report.winner == side else "平局" if report.winner not in {"attacker", "defender"} else "失利"
    names = "、".join(row.get("display_name", row.get("name", "门客")) for row in participating)
    effects = report.losses.get("season_effects", {})
    effect = next((row for row in effects.get(side, []) if row.get("run_id") == run.pk), None)
    notes = f"本场状态：{effect["label"]}\n" if effect else ""
    tower = effects.get("tower")
    if tower:
        notes += f"箭塔：剩余{tower["remaining_hp"]}/{tower["max_hp"]}耐久，本场开战齐射一次。\n"
    gate = effects.get("gate")
    if gate and side == "attacker":
        outcome = "城门攻破" if gate["remaining_hp"] <= 0 else "本次攻城结束，城门未破"
    if gate:
        notes += f"城门血量：{gate["remaining_hp"]}/{gate["max_hp"]}，本场伤害{gate["combat_damage"]}，下场继承。\n"
    if gate and gate.get("normal_round_limit"):
        notes += f"本次普通阶段最多{gate['normal_round_limit']}回合，远程、先锋另计；本次攻击结束后返程。\n"
    received_resources = (reward.get("resources") or reward.get("loot")) if reward else None
    if received_resources:
        names_by_key = {"grain": "粮草", "wood": "木材", "iron": "铁料"}
        received = "、".join(f"{names_by_key.get(key, key)}{amount}" for key, amount in received_resources.items())
        notes += f"{reward.get('resource_label', '实际获得物资')}：{received}。\n"
        if "normal_training_potential" in reward:
            notes += (
                f"本次物资最多可支持约{reward['normal_training_potential']}名普通护院训练，仍受训练时间与产能限制。\n"
            )
    create_message(
        manor=run.member.manor,
        kind="battle",
        title=f"阵营任务-{name}{role}战报",
        body=(
            f"战场：{name}\n时间：{timezone.localtime(now):%Y-%m-%d %H:%M}\n"
            f"本场身份：{role}\n本场结果：{outcome}\n参战门客：{names}\n"
            f"本场贡献：+{merit}\n{notes}门客伤势与护院损耗已按实际战斗结算，点击随信战报查看详情。"
        ),
        battle_report=report,
    )
