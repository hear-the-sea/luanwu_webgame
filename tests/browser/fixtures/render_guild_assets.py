"""无数据库渲染真实帮会资产模板。"""

import os
import sys
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 使用隔离测试配置。
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

empty = "--empty" in sys.argv
troops = (
    []
    if empty
    else [
        dict(key="dao_ke", name="刀客", stationed=100, deployed=17, redeemable=0, training=0, total=117),
        dict(key="qiang_ke", name="枪客", stationed=50, deployed=7, redeemable=0, training=0, total=57),
        dict(
            key="bloodthirsty_cavalry", name="嗜血铁骑", stationed=0, deployed=0, redeemable=20, training=10, total=20
        ),
    ]
)
items = (
    []
    if empty
    else [
        dict(
            item_key="experience_fruit",
            display_name="经验果",
            display_description="门客历练所需的经验果。",
            display_quantity=1500,
            exchange_unit_cost=1,
            exchange_unit=1,
            exchange_max_quantity=100,
            can_exchange=True,
            weekly_personal_limit=0,
        ),
        dict(
            item_key="bloodthirsty_cavalry",
            display_name="嗜血铁骑",
            display_description="帮会统一训练的精锐护院。",
            display_quantity=20,
            exchange_unit_cost=3,
            exchange_unit=1,
            exchange_max_quantity=20,
            can_exchange=True,
            weekly_personal_limit=0,
        ),
    ]
)
print(
    render_to_string(
        "guilds/warehouse.html",
        {
            "user": Obj(is_authenticated=False),
            "guild": Obj(id=1, name="资产详情帮", level=3),
            "member": Obj(can_manage=True, current_contribution=100, get_position_display="帮主"),
            "troop_assets": {
                "rows": troops,
                "kinds": len(troops),
                "total": 0 if empty else 194,
                "stationed": 0 if empty else 150,
                "deployed": 0 if empty else 24,
                "redeemable": 0 if empty else 20,
                "training": 0 if empty else 10,
            },
            "cavalry": {
                "stock": 0 if empty else 20,
                "is_unlocked": True,
                "can_manage": True,
                "contribution_per_unit": 3,
                "training": None,
            },
            "warehouse_items": items,
            "pagination": {"total_count": len(items), "page": 1, "total_pages": 1},
            "csrf_token": "browser-test-token",
        },
    )
)
