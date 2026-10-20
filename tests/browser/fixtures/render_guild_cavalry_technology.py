"""无数据库渲染真实帮会科技模板，验证训练弹窗与管理权限展示。"""

import os
import sys
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 采用隔离测试配置。
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from guilds.services.warehouse import get_technology_daily_output_display  # noqa: E402
from guilds.technology_tree import TECHNOLOGY_ROUTES  # noqa: E402

can_manage = "--member" not in sys.argv
levels = {"wolf_bloodthirsty_cavalry": 1, "forging_equipment_forge": 10}
routes = []
for route in TECHNOLOGY_ROUTES:
    nodes = [
        {
            **node,
            "level": levels.get(node["key"], 0),
            "unlocked": not node["requires"] or route["key"] == "wolf",
            "available": False,
            "upgrade_cost": "金条×1",
            "current_effect": "已解锁" if node["key"] == "wolf_bloodthirsty_cavalry" else "未激活",
            "next_effect": "",
            "prerequisite_label": "",
            "daily_output": (
                get_technology_daily_output_display(node["key"], levels[node["key"]])
                if node["key"] in levels and node["production"]
                else None
            ),
        }
        for node in route["nodes"]
    ]
    routes.append(
        {
            **route,
            "nodes": nodes,
            "active": route["key"] == "wolf",
            "completed": False,
            "selectable": False,
            "is_default": route["key"] == "hero",
            "icon_asset": f"images/guild_technology/route_{route['key']}.png",
        }
    )

print(
    render_to_string(
        "guilds/technology.html",
        {
            "user": Obj(is_authenticated=False),
            "guild": Obj(id=1, name="铁骑训练帮", level=1),
            "member": Obj(can_manage=can_manage, get_position_display="帮主" if can_manage else "成员"),
            "technology_routes": routes,
            "public_technologies": [],
            "cavalry": {
                "stock": 0,
                "is_unlocked": True,
                "can_manage": can_manage,
                "can_recruit": can_manage,
                "max_batch": 2000,
                "seconds_per_unit": 30,
                "max_recruitable_quantity": 1250,
                "silver_per_unit": 2000,
                "grain_per_unit": 8000,
                "contribution_per_unit": 3,
                "silver": 3000000,
                "grain": 10000000,
                "training": None,
            },
            "csrf_token": "browser-test-token",
        },
    )
)
