"""无数据库渲染两台守擂历史，验证长昵称与手机折叠布局。"""

import os
from datetime import timedelta
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.utils import timezone  # noqa: E402

now = timezone.now()
groups = []
for key, name in [("qinglong", "青龙擂"), ("baihu", "白虎擂")]:
    records = []
    for index in range(10):
        records.append(
            {
                "tenure": Obj(
                    manor=Obj(
                        display_name="长风万里踏破江湖行侠仗义的庄园主人" if index == 1 else f"江湖侠客{index + 1}"
                    ),
                    started_at=now - timedelta(minutes=index * 30),
                    defenses=10 - index,
                ),
                "is_mine": index == 2,
                "active": index == 0,
                "held_minutes": 30,
                "until": None if index == 0 else now - timedelta(minutes=index * 30 - 20),
                "status": "守擂中" if index == 0 else "已下台",
                "detail_url": f"/manor/arena/rings/events/{index + 1}/",
            }
        )
    groups.append(
        {
            "ring": Obj(key=key, name=name),
            "image": f"images/buildings/{name}.webp",
            "thumbnail": f"images/buildings/{name}-thumb.webp",
            "current_holder": "长风万里踏破江湖行侠仗义的庄园主人",
            "records": records,
            "record_count": 52,
            "expanded": False,
            "page": Obj(number=1, paginator=Obj(num_pages=6), has_other_pages=True),
            "previous_url": "",
            "next_url": f"?{key}_page=2#ring-history-{key}",
        }
    )
context = {
    "user": Obj(is_authenticated=True),
    "manor": Obj(arena_coins=0),
    "ring_history_groups": groups,
    "running_tournament_card_rows": [],
    "running_coop_card_rows": [],
    "completed_event_card_rows": [],
}
print(render_to_string("gameplay/arena/events.html", context))
