import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import pytest  # noqa: E402, F401

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from bs4 import BeautifulSoup  # noqa: E402
from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402
from django.test import RequestFactory  # noqa: E402

scenario = sys.argv[1]
mission = SimpleNamespace(
    key="quick_launch_fixture",
    name="快速出征演练",
    difficulty="junior",
    is_defense=scenario == "defense",
    guest_only=scenario in {"guests", "few_guests"},
    description="验证门客与护院快速派遣。",
    enemy_guests=[],
    enemy_troops={},
)
guests = [
    SimpleNamespace(
        id=101 + index,
        display_name=f"演练门客{index + 1}",
        avatar_url="",
        template=SimpleNamespace(rarity="green"),
        level=10,
        current_hp=100,
        max_hp=100,
        troop_capacity=10,
    )
    for index in range(2 if scenario == "few_guests" else 5)
]
request = RequestFactory().get("/gameplay/tasks/?mission=quick_launch_fixture")
request.user = AnonymousUser()
remaining = 0 if scenario == "exhausted" else 2
context = {
    "request": request,
    "user": request.user,
    "csrf_token": "quick-launch-fixture-csrf",
    "active_tab": "junior",
    "mission_data": [{"mission": mission, "remaining": remaining, "daily_limit": 2}],
    "selected_mission": mission,
    "selected_remaining": remaining,
    "selected_daily_limit": 2,
    "selected_attempts": 2 - remaining,
    "can_afford_entry_cost": scenario != "unaffordable",
    "available_guests": [] if scenario == "no_guests" else guests,
    "max_squad": 3,
    "task_board_close_url": "/gameplay/tasks/",
    "mission_card_daily_limit": 0,
    "mission_card_count": 0,
    "selected_entry_cost_items": (
        [{"label": "出征信物", "required": 1, "owned": 0}] if scenario == "unaffordable" else []
    ),
    "player_troops": [
        {
            "key": "sword",
            "name": "剑兵",
            "count": 20,
            "manor_count": 20,
            "bank_count": 0,
        },
        {"key": "bow", "name": "弓兵", "count": 8, "manor_count": 0, "bank_count": 8},
        {
            "key": "empty",
            "name": "空闲兵种",
            "count": 0,
            "manor_count": 0,
            "bank_count": 0,
        },
    ],
}
document = BeautifulSoup(render_to_string("gameplay/tasks.html", context), "html.parser")
# 测试按真实脚本依赖顺序加载，避免静态地址与全局导航干扰表单提交观测。
for element in document.select("script, link"):
    element.decompose()
print(document)
