"""离线渲染长名称、绑定标记及多装备位的门客装备栏。"""

import os
from types import SimpleNamespace

import django
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def item(pk, name, bound=False):
    return SimpleNamespace(
        id=pk,
        is_bound=bound,
        template=SimpleNamespace(name=name, rarity="orange", description="装备属性说明", required_level=30),
    )


panels = {
    "helmet": SimpleNamespace(equipped=[item(1, "传说·九霄龙吟玄天鎏金战盔", True)]),
    "armor": SimpleNamespace(equipped=[item(2, "玄甲")]),
    "weapon": SimpleNamespace(equipped=[item(3, "LegendaryDragonSlayerBladeWithoutSpaces", True)]),
    "shoes": SimpleNamespace(equipped=[]),
    "ornament": SimpleNamespace(positions=[item(4, "无双·紫金盘龙护心长命佩", True), item(5, "玉佩"), None]),
    "device": SimpleNamespace(positions=[item(6, "天工·百炼千机连环破阵机关匣"), None, None]),
    "mount": SimpleNamespace(equipped=[item(7, "踏雪乌骓")]),
}
card = render_to_string(
    "guests/partials/detail_equipment_card.html",
    {"guest": SimpleNamespace(id=1), "slot_panels_map": panels, "gear_set_map": {}, "csrf_token": "preview-token"},
)
print(
    '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<body><section class="dashboard guest-detail"><div class="detail-board">'
    '<div class="board-card">形象</div><div class="board-card">基础</div>'
    f'<div class="board-card">属性</div>{card}</div></section></body></html>'
)
