"""Raid battle message formatting and delivery (split from battle.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....models import RaidRun

from ...utils.messages import create_message
from .config import PVPConstants
from .loot import _format_battle_rewards_description, _format_capture_description, _format_loot_description
from .troops import _normalize_mapping, _normalize_positive_int_mapping


def _send_raid_capture_loss_message(run: RaidRun, capture_payload: object) -> None:
    capture_desc = _format_capture_description(capture_payload)
    if not capture_desc:
        return

    loser_manor = run.defender if run.is_attacker_victory else run.attacker
    winner_manor = run.attacker if run.is_attacker_victory else run.defender
    winner_name = getattr(winner_manor, "display_name", "对手")

    create_message(
        manor=loser_manor,
        kind="battle",
        title="门客被俘通知",
        body=f"""在本次踢馆战斗中，你方门客已被 {winner_name} 俘获，并已被押入对方监牢。

{capture_desc}""",
        battle_report=run.battle_report,
    )


def _send_raid_battle_messages(run: RaidRun) -> None:
    """发送踢馆战报消息"""
    is_victory = run.is_attacker_victory
    battle_rewards = _normalize_mapping(run.battle_rewards)
    loot_resources = _normalize_positive_int_mapping(run.loot_resources)
    loot_items = _normalize_positive_int_mapping(run.loot_items)
    battle_rewards_desc = _format_battle_rewards_description(battle_rewards)
    capture_desc = _format_capture_description(battle_rewards.get("capture"))
    defeat_protection_seconds = int(getattr(PVPConstants, "RAID_DEFEAT_PROTECTION_SECONDS", 1800) or 1800)
    defeat_protection_minutes = max(1, defeat_protection_seconds // 60)

    # 进攻方消息
    if is_victory:
        attacker_title = "踢馆战报 - 踢馆胜利"
        loot_desc = _format_loot_description(loot_resources, loot_items)
        attacker_body = f"""对 {run.defender.display_name} 的踢馆行动取得胜利！

战利品：
{loot_desc}

声望变化：+{run.attacker_prestige_change}"""
        # 胜利方获得战斗通用奖励
        if battle_rewards_desc:
            attacker_body += f"""

战斗回收：
{battle_rewards_desc}"""
        if capture_desc:
            attacker_body += f"""

俘获：
{capture_desc}"""
    else:
        attacker_title = "踢馆战报 - 踢馆失败"
        attacker_body = f"""对 {run.defender.display_name} 的踢馆行动失败了。

声望变化：{run.attacker_prestige_change}"""
        if capture_desc:
            attacker_body += f"""

损失：
{capture_desc}"""

    create_message(
        manor=run.attacker,
        kind="battle",
        title=attacker_title,
        body=attacker_body,
        battle_report=run.battle_report,
    )

    # 防守方消息
    if is_victory:
        defender_title = "踢馆战报 - 防守失败"
        loot_desc = _format_loot_description(loot_resources, loot_items)
        defender_body = f"""来自 {run.attacker.location_display} 的 {run.attacker.display_name} 踢馆成功！

损失：
{loot_desc}

声望变化：{run.defender_prestige_change}
已获得{defeat_protection_minutes}分钟战败保护"""
        if capture_desc:
            defender_body += f"""

损失：
{capture_desc}"""
    else:
        defender_title = "踢馆战报 - 防守成功"
        defender_body = f"""成功抵御了来自 {run.attacker.location_display} 的 {run.attacker.display_name} 的踢馆！

声望变化：+{run.defender_prestige_change}"""
        # 防守方胜利时获得战斗通用奖励
        if battle_rewards_desc:
            defender_body += f"""

战斗回收：
{battle_rewards_desc}"""
        if capture_desc:
            defender_body += f"""

俘获：
{capture_desc}"""

    create_message(
        manor=run.defender,
        kind="battle",
        title=defender_title,
        body=defender_body,
        battle_report=run.battle_report,
    )

    _send_raid_capture_loss_message(run, battle_rewards.get("capture"))
