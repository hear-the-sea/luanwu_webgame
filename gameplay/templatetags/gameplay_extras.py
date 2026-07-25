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
