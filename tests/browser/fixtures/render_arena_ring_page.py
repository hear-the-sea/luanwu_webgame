"""无数据库渲染演武台长阵容，检查手机弹窗。"""

import os
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 采用隔离测试配置。
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from gameplay.services.arena.ring_rules import load_ring_rules  # noqa: E402

rules = load_ring_rules()
context = {
    "user": Obj(is_authenticated=False),
    "manor": Obj(silver=500000, arena_coins=0),
    "csrf_token": "preview",
    "ring_rules": rules,
    "ring_daily_max_coins": 700,
    "ring_reward_interval_minutes": 10,
    "ring_tenure_hours": 2,
    "ring_return_minutes": rules["cooldown_seconds"] // 60,
    "ring_guest_rows": [
        {
            "guest": Obj(id=index, display_name=f"门客{index}", level=30, avatar_url="", template=Obj(rarity="blue")),
            "selected": index <= 20,
            "capacity": 200,
        }
        for index in range(1, 26)
    ],
    "ring_troop_rows": [
        {
            "key": f"test_{index}",
            "name": f"护院{index}",
            "available": 1000,
            "manor_count": 600,
            "bank_count": 400,
            "selected": 0,
        }
        for index in range(1, 22)
    ],
    "ring_rows": [
        {
            "ring": Obj(key="qinglong", name="青龙擂", version=1),
            "image": "images/buildings/青龙擂.webp",
            "description": "青旗招展，等你登台。",
            "can_enter": True,
            "can_enter_action": True,
            "action_label": "登台守擂",
            "action_kind": "occupy",
            "action_token": "preview",
            "tenure": None,
        }
    ],
}
mode = os.environ.get("ARENA_RING_PREVIEW_MODE")
if mode:
    from datetime import timedelta

    from django.utils import timezone

    row = context["ring_rows"][0]
    now = timezone.now()
    row.update(
        is_mine=False,
        action_kind="challenge",
        action_label="挑战擂主",
        tenure=Obj(id=1, manor=Obj(display_name="预览擂主"), defenses=0, ends_at=now + timedelta(hours=2)),
        held_minutes=0,
        reward_streak=0,
        own_lineup={"guests": [], "troops": []},
        tenure_challenges_remaining=0 if mode == "exhausted" else (2 if mode == "cooldown" else 1),
        challenge_cooldown=600 if mode == "cooldown" else 0,
        challenge_cooldown_minutes=10,
        challenge_ready_at=now + timedelta(minutes=10),
        can_enter_action=mode == "ready",
    )
print(render_to_string("gameplay/arena/rings.html", context))
