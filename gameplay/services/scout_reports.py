"""侦察报告文案与折叠详情，始终使用已采集的情报快照。"""

from __future__ import annotations

from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.utils import safe_non_negative_int

from .scout_intel_rules import SCOUT_INTEL_SCHEMA_VERSION, get_scout_intel_progress, normalize_scout_level

SCOUT_DEFENSE_DETAILS_SEPARATOR = "\n\n【防守详情】\n"
SCOUT_DEFENSE_SECTION_TITLES = ("门客本事", "护院武艺", "城防情况")


def _label(value: Any, default: str = "未知") -> str:
    return " ".join(str(value or default).splitlines()).strip() or default


def _number(value: Any) -> str:
    return f"{safe_non_negative_int(value, 0):,}"


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _guest_label(guest: dict[str, Any]) -> str:
    return f"{_label(guest.get('name'))}〔{_label(guest.get('rarity'))}·{_number(guest.get('level'))}级〕"


def _warehouse_lines(summary: dict[str, Any]) -> list[str]:
    if not summary.get("has_loot"):
        return ["【仓库概况】暂无可掠夺物资"]
    overview = (
        f"【仓库概况】装备 {_number(summary.get('equipment_count'))} 件，"
        f"材料 {_number(summary.get('material_type_count'))} 种"
    )
    if summary.get("other_type_count"):
        overview += f"，其他物资 {_number(summary['other_type_count'])} 种"
    lines = [overview]
    qualities = [
        f"{_label(row.get('name'))} {_number(row.get('count'))} 件"
        for row in _rows(summary.get("equipment_rarities"))[:2]
    ]
    if summary.get("equipment_other_count"):
        qualities.append(f"其余 {_number(summary['equipment_other_count'])} 件")
    if qualities:
        lines.append(f"装备品质：{'、'.join(qualities)}")
    return lines


def _defense_sections(intel: dict[str, Any]) -> list[dict[str, str]]:
    guest_lines = []
    for guest in _rows(intel.get("defending_guests")):
        guest_lines.append(_guest_label(guest))
        attributes = guest.get("attributes")
        if isinstance(attributes, dict):
            guest_lines.append(
                "  "
                + "，".join(
                    f"{label} {_number(attributes.get(key))}"
                    for key, label in (
                        ("attack", "攻击"),
                        ("defense", "防御"),
                        ("max_hp", "生命上限"),
                        ("force", "武力"),
                        ("intellect", "智力"),
                        ("agility", "敏捷"),
                        ("luck", "运势"),
                        ("troop_capacity", "可携带护院"),
                    )
                )
            )
        skills = _rows(guest.get("skills"))
        if not skills:
            guest_lines.append("  技能：暂无")
        for skill in skills:
            guest_lines.append(
                f"  {_label(skill.get('name'))}（{_label(skill.get('kind'))}）："
                f"{_label(skill.get('description'), '暂无说明')}"
            )
    martial_lines = [
        f"{_label(row.get('name'))}：{_number(row.get('level'))}级" for row in _rows(intel.get("martial_technologies"))
    ]
    city_lines = [
        f"{_label(row.get('name'))}：{_number(row.get('level'))}级" for row in _rows(intel.get("city_defenses"))
    ]
    return [
        {"title": "门客本事", "body": "\n".join(guest_lines) or "暂无防守门客"},
        {"title": "护院武艺", "body": "\n".join(martial_lines) or "暂无相关护院武艺"},
        {"title": "城防情况", "body": "\n".join(city_lines) or "尚未建造城防"},
    ]


def build_scout_report_body(defender_name: str, intel: dict[str, Any]) -> str:
    name = _label(defender_name)
    # 已有报告保留原有信息和文案，不按玩家现在的等级补充内容。
    if intel.get("intel_schema_version") != SCOUT_INTEL_SCHEMA_VERSION:
        return (
            f"探子已成功潜入 {name}，获取到以下情报：\n\n"
            f"【护院情况】{_label(intel.get('troop_description'))}\n"
            f"【门客数量】{_number(intel.get('guest_count'))} 人\n"
            f"【门客等级】平均 {_number(intel.get('avg_guest_level'))} 级\n"
            f"【资产状况】{_label(intel.get('asset_level'))}"
        )

    level = normalize_scout_level(intel.get("scout_level"))
    lines = [f"探子已平安归来，{name}的底细打听到了。", ""]
    if level >= 50:
        lines.append(f"【护院情况】共 {_number(intel.get('total_troops'))} 名")
        troop_lines = [
            f"{_label(row.get('name'))} {_number(row.get('count'))} 名" for row in _rows(intel.get("troops"))
        ]
        lines.append("，".join(troop_lines) or "暂无护院")
    else:
        lines.append(f"【护院情况】{_label(intel.get('troop_description'))}")
    lines.extend(
        ["", f"【门客情况】共 {_number(intel.get('guest_count'))} 人，平均 {_number(intel.get('avg_guest_level'))} 级"]
    )
    if level >= 100:
        guests = _rows(intel.get("defending_guests"))
        lines.append("【防守门客】" + ("、".join(_guest_label(guest) for guest in guests) or "暂无"))
    lines.extend(["", f"【资产状况】{_label(intel.get('asset_level'))}"])
    if level >= 150:
        lines.append(f"银两 {_number(intel.get('silver'))}，仓库粮食 {_number(intel.get('warehouse_grain'))}")
        summary = intel.get("warehouse_summary")
        lines.extend(_warehouse_lines(summary if isinstance(summary, dict) else {}))
    if level >= 200:
        lines.extend(["", "【防守情况】门客本事、护院武艺和城防都已查明，可展开查看。"])
    try:
        collected_at = parse_datetime(str(intel.get("scouted_at") or ""))
    except ValueError:
        collected_at = None
    if collected_at is not None and timezone.is_aware(collected_at):
        collected_at = timezone.localtime(collected_at)
    time_label = collected_at.strftime("%Y-%m-%d %H:%M") if collected_at is not None else "时间不详"
    lines.extend(["", f"【情报时间】{time_label}", "情报采集后，庄内情况可能有所变化。"])
    next_unlock = get_scout_intel_progress(level)["next_unlock"]
    if next_unlock:
        lines.extend(["", f"侦查术达到 {next_unlock['level']} 级，可查明{next_unlock['name']}。"])
    body = "\n".join(lines)
    if level >= 200:
        body += SCOUT_DEFENSE_DETAILS_SEPARATOR + "\n\n".join(
            f"【{section['title']}】\n{section['body']}" for section in _defense_sections(intel)
        )
    return body


def split_scout_report_details(*, kind: str, title: str, body: str) -> dict[str, Any]:
    """从持久化正文提取防守详情，普通消息和旧报告保持原样。"""
    result: dict[str, Any] = {"body": body, "defense_sections": []}
    if kind != "system" or not title.startswith("侦察报告 - "):
        return result
    overview, separator, details = body.partition(SCOUT_DEFENSE_DETAILS_SEPARATOR)
    if not separator:
        return result
    sections = []
    for section_title in SCOUT_DEFENSE_SECTION_TITLES:
        heading = f"【{section_title}】\n"
        if not details.startswith(heading):
            return result
        remaining = details[len(heading) :]
        section_body, _, details = remaining.partition("\n\n")
        sections.append({"title": section_title, "body": section_body})
    if details:
        return result
    return {"body": overview, "defense_sections": sections}
