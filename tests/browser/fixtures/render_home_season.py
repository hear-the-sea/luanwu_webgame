"""使用真实首页模板生成离线赛季队伍场景，不连接业务数据库。"""

import os
from datetime import timedelta
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402
import pytest  # noqa: E402,F401 - 使用隔离测试设置。

django.setup()

from django.template.loader import render_to_string  # noqa: E402
from django.utils import timezone  # noqa: E402

now = timezone.now()
events = []
for index, (title, node, status, label, icon) in enumerate(
    [
        ("赛季集结", "河口古城", "集结中", "集结截止", "flag"),
        ("赛季驻军", "青石关", "驻防中", "驻守结束", "shield"),
        ("赛季采集", "山脚驿站", "采集中", "下一场战斗", "grain"),
    ]
):
    events.append(
        {
            "id": index + 1,
            "season_id": 1,
            "action_token": "bdf6d910-d310-4b9a-9f8f-11de557fe73e",
            "can_recall": True,
            "recall_label": "取消集结" if index == 0 else "撤退",
            "title": title,
            "node_name": node,
            "status_label": status,
            "deadline_label": label,
            "deadline": now + timedelta(hours=index + 2),
            "icon": icon,
            "guest_names": "赵云、关羽、张飞",
            "troop_count": 1500,
            "collection": {"wins": 2, "maximum": 5, "next_wave": 3} if index == 2 else {},
            "details_url": "/manor/season/?season_id=1#season-team-status",
        }
    )
print(
    render_to_string(
        "landing.html",
        {
            "csrf_token": "browser-fixture",
            "user": SimpleNamespace(is_authenticated=True, is_superuser=False),
            "active_season_events": events,
            "manor": SimpleNamespace(display_name="长安府", grain=300, silver=1000),
            "guest_count": 3,
        },
    )
)
