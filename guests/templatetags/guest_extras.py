from __future__ import annotations

from django import template
from django.utils.html import conditional_escape, format_html, format_html_join

from guests.models import GuestRarity
from guests.utils.set_bonus_parser import (
    format_set_bonus_entry_texts,
    iter_set_bonus_stat_pairs,
    parse_set_bonus_entries,
)

register = template.Library()

RARITY_CLASS_MAP = {
    GuestRarity.BLACK: "rarity-black",
    GuestRarity.GRAY: "rarity-gray",
    GuestRarity.GREEN: "rarity-green",
    GuestRarity.BLUE: "rarity-blue",
    GuestRarity.RED: "rarity-red",
    GuestRarity.PURPLE: "rarity-purple",
    GuestRarity.ORANGE: "rarity-orange",
}

RARITY_LABEL_MAP = {value: label for value, label in GuestRarity.choices}

_GEAR_STAT_LABELS = {
    "hp": "生命",
    "force": "武力",
    "intellect": "智力",
    "defense": "防御",
    "agility": "敏捷",
    "luck": "运势",
    "attack": "攻击",
    "troop_capacity": "可携带护院人数",
}


def _iter_template_stat_parts(template) -> list[str]:
    parts: list[str] = []
    atk = getattr(template, "attack_bonus", 0)
    if atk:
        parts.append(f"{_GEAR_STAT_LABELS['attack']}+{atk}")
    defense_bonus = getattr(template, "defense_bonus", 0)
    if defense_bonus:
        parts.append(f"{_GEAR_STAT_LABELS['defense']}+{defense_bonus}")
    extra_stats = getattr(template, "extra_stats", {}) or {}
    for key, value in extra_stats.items():
        if value is None or value == 0:
            continue
        parts.append(f"{_GEAR_STAT_LABELS.get(key, '未知属性')}+{value}")
    return parts


def _iter_set_bonus_entries(set_bonus) -> list[tuple[int | None, dict]]:
    return iter_set_bonus_stat_pairs(set_bonus)


@register.filter
def set_bonus_entries(set_bonus) -> list[tuple[int | None, dict]]:
    return _iter_set_bonus_entries(set_bonus)


@register.filter
def set_bonus_display_entries(set_bonus) -> list[dict]:
    entries = []
    for entry in parse_set_bonus_entries(set_bonus):
        entries.append(
            {
                "pieces": entry.get("pieces"),
                "bonus": entry.get("bonus") or {},
                "effect_description": entry.get("effect_description", ""),
            }
        )
    return entries


def _build_set_bonus_summary(set_description: str, set_bonus) -> str:
    set_texts = []
    for entry in parse_set_bonus_entries(set_bonus):
        pieces = entry.get("pieces")
        bonus_parts = format_set_bonus_entry_texts(entry, stat_labels=_GEAR_STAT_LABELS)
        piece_text = f"{pieces}件" if pieces else "套装"
        set_text = f"{set_description or '套装'}（{piece_text}）"
        if bonus_parts:
            set_text += "：" + "、".join(bonus_parts)
        set_texts.append(set_text)
    if set_texts:
        return "；".join(set_texts)
    return set_description or "套装"


def _render_set_members(lines: list, members: list, set_desc: str, esc) -> None:
    if not members:
        return
    if set_desc:
        lines.append(format_html("套装：{}", esc(set_desc)))
    else:
        lines.append("套装")

    member_texts = []
    for member in members:
        name = member.get("name") or ""
        slot = member.get("slot") or ""
        equipped = member.get("equipped")
        cls = "equipped" if equipped else "unequipped"
        member_texts.append((cls, esc(f"{slot}·{name}")))

    lines.append(
        format_html(
            "成员：{}",
            format_html_join(
                "，",
                '<span class="set-member {0}">{1}</span>',
                member_texts,
            ),
        )
    )


def _render_set_bonus_lines(lines: list, bonus_map: dict, esc) -> None:
    entries = parse_set_bonus_entries(bonus_map)
    if not entries:
        return
    for entry in entries:
        pieces = entry.get("pieces")
        entry_bonus_map = entry.get("bonus") or {}
        bonus_parts = []
        for key, value in entry_bonus_map.items():
            if key in {"pieces", "bonus", "bonuses"}:
                continue
            if value is None or value == 0:
                continue
            label = _GEAR_STAT_LABELS.get(key, "未知属性")
            bonus_parts.append(format_html("{}+{}", esc(label), value))
        effect_description = entry.get("effect_description")
        if isinstance(effect_description, str) and effect_description.strip():
            bonus_parts.append(format_html("{}", esc(effect_description.strip())))
        if bonus_parts:
            lines.append(f"{pieces}件套属性：" if pieces else "套装属性：")
            lines.extend(bonus_parts)


def _normalize_rarity(value: str | GuestRarity | None) -> GuestRarity | None:
    if not value:
        return None
    if isinstance(value, GuestRarity):
        return value
    try:
        return GuestRarity(value)
    except ValueError:
        return None


@register.filter
def rarity_class(value: str | GuestRarity | None) -> str:
    normalized = _normalize_rarity(value)
    if not normalized:
        return "rarity-default"
    return RARITY_CLASS_MAP.get(normalized, "rarity-default")


@register.filter
def rarity_label(value: str | GuestRarity | None) -> str:
    normalized = _normalize_rarity(value)
    if not normalized:
        return "未知稀有度"
    return RARITY_LABEL_MAP.get(normalized.value, "未知稀有度")


@register.filter
def gear_summary(template) -> str:
    """
    Build a short tooltip for gear: description + stat bonuses + set info.
    """
    if not template:
        return ""

    parts = []
    if hasattr(template, "required_level"):
        parts.append(f"LV{template.required_level}")
    desc = getattr(template, "description", "")
    if desc:
        parts.append(str(desc))

    stats = _iter_template_stat_parts(template)
    if stats:
        parts.append("、".join(stats))

    troop_bonus_summary = getattr(template, "troop_stat_bonus_summary", "") or ""
    if troop_bonus_summary:
        parts.append(f"护院加成：{troop_bonus_summary}")

    set_key = getattr(template, "set_key", "") or ""
    set_description = getattr(template, "set_description", "") or ""
    set_bonus = getattr(template, "set_bonus", {}) or {}
    if set_key or set_description or set_bonus:
        parts.append(_build_set_bonus_summary(set_description, set_bonus))
    return "；".join(parts)


@register.filter
def gear_tooltip(template, set_map=None) -> str:
    """
    Tooltip with description、逐行属性、套装组成与套装属性。
    """
    if not template:
        return ""
    esc = conditional_escape
    lines = []
    if hasattr(template, "required_level"):
        lines.append(format_html("LV{}", template.required_level))
    desc = getattr(template, "description", "") or ""
    if desc:
        lines.append(format_html("{}", esc(desc)))

    attrs = []
    for part in _iter_template_stat_parts(template):
        label, _, value = part.partition("+")
        attrs.append(format_html("{} +{}", esc(label), value))
    lines.extend(attrs)

    troop_bonus_summary = getattr(template, "troop_stat_bonus_summary", "") or ""
    if troop_bonus_summary:
        lines.append(format_html("护院加成：{}", esc(troop_bonus_summary)))

    tpl_set_key = getattr(template, "set_key", "") or ""
    if tpl_set_key and set_map:
        info = set_map.get(tpl_set_key) or {}
        members = info.get("members") or []
        bonus_map = info.get("bonus") or {}
        set_desc = info.get("description") or ""
        _render_set_members(lines, members, set_desc, esc)
        _render_set_bonus_lines(lines, bonus_map, esc)
    # Use format_html_join to return a SafeString without needing template `|safe`.
    return format_html_join("", "<div>{}</div>", ((line,) for line in lines))


@register.filter
def attribute_icons(value: int) -> str:
    """
    将属性值转换为图标HTML (Crown=64, Sun=16, Moon=4, Star=1)
    """
    if not value or value < 0:
        return ""

    icons: list[str] = []
    remaining = int(value)

    for icon, divisor in [("crown", 64), ("sun", 16), ("moon", 4), ("star", 1)]:
        count = remaining // divisor
        remaining %= divisor
        icons.extend([icon] * count)

    inner = format_html_join(
        "",
        '<span class="attr-icon attr-{0}" aria-hidden="true"></span>',
        ((icon,) for icon in icons),
    )
    return format_html('<span class="attr-pack">{}</span>', inner)
