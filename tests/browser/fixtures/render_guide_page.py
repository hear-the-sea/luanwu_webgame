"""离线渲染含庄园概览的完整游戏攻略，供导航和窄屏检查使用。"""

import os
from types import SimpleNamespace

import django
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

print(
    render_to_string(
        "gameplay/guide.html",
        {
            "manor": SimpleNamespace(
                display_name="青竹山庄",
                region_display="江南",
                prestige=1200,
                grain=18000,
                grain_capacity=50000,
                silver=24000,
                silver_capacity=50000,
            ),
            "guide_snapshot": {
                "action_points": 800,
                "action_points_max": 1000,
                "guest_count": 5,
                "guest_capacity": 10,
                "protection_label": "新手保护中",
                "hourly_grain": 2400,
                "hourly_silver": 1800,
                "max_squad_size": 10,
            },
        },
    )
)
