import math

from django import template

_DEFAULT_BUILDING_IMAGE = "images/buildings/xianxuan.webp"
_BUILDING_IMAGE_PATHS = {
    "farm": "images/buildings/农田.webp",
    "tax_office": "images/buildings/税务司.webp",
    "bathhouse": "images/buildings/澡堂.webp",
    "latrine": "images/buildings/茅厕.webp",
    "granary": "images/buildings/粮仓.webp",
    "silver_vault": "images/buildings/银库.webp",
    "treasury": "images/buildings/藏宝阁.webp",
    "ranch": "images/buildings/畜牧场.webp",
    "smithy": "images/buildings/冶炼坊.webp",
    "stable": "images/buildings/马房.webp",
    "forge": "images/buildings/铁匠铺.webp",
    "juxianzhuang": "images/buildings/聚贤庄.webp",
    "xiaoyao_shanzhuang": "images/buildings/逍遥山庄.webp",
    "jiadingfang": "images/buildings/家丁房.webp",
    "lianggongchang": "images/buildings/练功场.webp",
    "tavern": "images/buildings/酒楼.webp",
    "citang": "images/buildings/祠堂.webp",
    "youxibaota": "images/buildings/悠嘻宝塔.webp",
    "jail": "images/buildings/监牢.webp",
    "oath_grove": "images/buildings/结义林.webp",
    "wall": "images/buildings/城墙.webp",
    "arrow_tower": "images/buildings/箭塔.webp",
}
_DEFAULT_WORK_IMAGE = "images/works/酒楼.webp"
_WORK_IMAGE_PATHS = {
    "jiulou": "images/works/酒楼.webp",
    "yiguan": "images/works/医馆.webp",
    "yizhan": "images/works/驿站.webp",
    "shuyuan": "images/works/书院.webp",
    "chaguan": "images/works/茶馆.webp",
    "matou": "images/works/码头.webp",
    "wuguan": "images/works/武馆.webp",
    "shanghang": "images/works/商行.webp",
    "guanfu": "images/works/官府.webp",
    "qianzhuang": "images/works/钱庄.webp",
    "biaoju": "images/works/镖局.webp",
    "jingwumeng": "images/works/精武盟.webp",
    "shenfengyi": "images/works/神风驿.webp",
    "guozijian": "images/works/国子监.webp",
}

register = template.Library()

GUILD_CARD_TECHNOLOGY_ICONS = {
    "guild_slave_card": 26,
    "guild_theft_card": 27,
    "guild_injury_card": 28,
    "guild_grain_boost_card": 19,
    "guild_silver_boost_card": 20,
}
ITEM_ICON_STATIC_OVERRIDES = {
    "guild_sheep_wrath": "images/guild_item_icons/guild_sheep_wrath.webp",
    "yijinjing": "images/item_icons/yijinjing.webp",
}


@register.inclusion_tag("gameplay/includes/item_icon.html")
def item_icon(item_key, image="", label="", icon=""):
    from django.templatetags.static import static

    image_url = image if isinstance(image, str) else image.url if image else ""
    if item_key in ITEM_ICON_STATIC_OVERRIDES:
        image_url = static(ITEM_ICON_STATIC_OVERRIDES[item_key])
    return {
        "technology_icon_index": GUILD_CARD_TECHNOLOGY_ICONS.get(item_key),
        "image_url": image_url,
        "label": label,
        "icon": icon or str(label)[:1],
    }


@register.filter
def duration_hours_minutes(seconds):
    """按小时和分钟显示耗时，不足一分钟向上取整。"""
    total_minutes = math.ceil(max(0, seconds) / 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}小时{minutes}分钟"


@register.filter
def get_label(mapping, key):
    if isinstance(mapping, dict):
        return mapping.get(key, "未知项目")
    return "未知项目"


@register.filter
def get_item(mapping, key):
    """Get item from dictionary by key"""
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None


@register.filter
def drop_value(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if number < 1:
        return f"{int(number * 100)}% 概率"
    if number.is_integer():
        return str(int(number))
    return str(number)


@register.filter
def guest_key(entry):
    """
    从 enemy_guests 条目中提取门客 key。

    支持两种格式：
    - 字符串: 直接返回
    - 字典: 返回 entry["key"]

    用法: {{ entry|guest_key }}
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("key", "")
    return ""


@register.filter
def guest_label(entry, labels):
    """
    从 enemy_guests 条目中提取展示名称。

    支持两种格式：
    - 字典: 优先使用 entry["label"]，否则回退到 entry["key"]
    - 字符串: 直接使用 key
    """
    key = ""
    if isinstance(entry, dict):
        label = entry.get("label")
        if label:
            return label
        key = entry.get("key", "")
    elif isinstance(entry, str):
        key = entry
    if isinstance(labels, dict):
        return labels.get(key, "未知门客")
    return "未知门客"


@register.filter
def building_desc(building_key):
    """
    从 YAML 配置获取建筑描述。

    用法: {{ building.building_type.key|building_desc }}
    """
    from gameplay.services.buildings.base import get_building_description

    return get_building_description(building_key)


@register.filter
def building_image(building_key):
    """根据建筑 key 返回建筑图片（webp）静态相对路径。"""
    if not building_key:
        return _DEFAULT_BUILDING_IMAGE
    return _BUILDING_IMAGE_PATHS.get(str(building_key), _DEFAULT_BUILDING_IMAGE)


@register.filter
def work_image(work_key):
    """根据打工 key 返回工作图片（webp）静态相对路径。"""
    if not work_key:
        return _DEFAULT_WORK_IMAGE
    return _WORK_IMAGE_PATHS.get(str(work_key), _DEFAULT_WORK_IMAGE)
