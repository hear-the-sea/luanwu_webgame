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


def task(key, name, value=None, label="", difficulty="advanced"):
    row = {
        "mission": SimpleNamespace(key=key, name=name, difficulty=difficulty, is_defense=False, guest_only=True),
        "remaining": 2,
        "daily_limit": 2,
        "weekly_limit": 10,
        "has_guild": True,
    }
    if value is not None:
        row["difficulty_rating"] = {
            "value": value,
            "label": label,
            "summary": f"{name}的战斗模拟参考说明。",
        }
    return row


request = RequestFactory().get("/gameplay/tasks/")
request.user = AnonymousUser()
context = {
    "request": request,
    "user": request.user,
    "active_tab": "junior",
    "mission_data": [
        task("easy", "初级演练", 1, "一星", "junior"),
        task("intermediate", "中级演练", 4, "一月", "intermediate"),
    ],
    "advanced_mission_data": [
        task("hard", "高级演练", 128, "双皇冠"),
        task("unrated", "新增演练"),
    ],
    "series_mission_data": [task("series", "系列演练", 127, "1个皇冠＋3个太阳＋3个月亮＋3颗星")],
    "guild_personal_mission_data": [task("guild", "帮会演练", 21, "1个太阳＋1个月亮＋1颗星")],
    "guild_personal_mission_status": {"has_guild": True, "guild_name": "测试帮会", "used": 0, "limit": 10},
}
document = BeautifulSoup(render_to_string("gameplay/tasks.html", context), "html.parser")
for element in document.select("script, link"):
    element.decompose()
print(document)
