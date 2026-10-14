"""无数据库渲染真实仓库模板，验证绑定分组显示与操作。"""

import os
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 采用隔离测试配置。
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def row(item_id, name, mode, quantity, action=None):
    template = Obj(
        name=name,
        rarity="blue",
        image=None,
        description="测试道具说明",
        effect_type="tool" if action else "loot_box",
        effect_payload={"action": action} if action else {},
    )
    return Obj(
        id=item_id,
        template=template,
        binding_mode=mode,
        display_is_bound=mode == "bound",
        display_quantity=quantity,
        quantity=quantity,
        transferable_quantity=quantity if mode == "unbound" else 0,
        treasury_deposit_blocked=False,
        category_display="道具",
        can_use_in_warehouse=True,
    )


context = {
    "user": Obj(is_authenticated=False),
    "current_tab": "warehouse",
    "categories": [{"key": "tool", "label": "道具"}],
    "selected_category": "all",
    "inventory_items": [
        row(101, "洗髓丹", "bound", 10, "reroll_growth"),
        row(101, "洗髓丹", "unbound", 3, "reroll_growth"),
        row(102, "宝箱", "bound", 2),
        row(102, "宝箱", "unbound", 4),
    ],
    "guests_for_xisuidan": [
        Obj(
            id=201,
            display_name="孙武",
            level=100,
            xisuidan_used=0,
            force=30,
            intellect=80,
            defense_stat=30,
            template=Obj(rarity="orange"),
        )
    ],
    "pagination": {"total_pages": 1},
    "csrf_token": "browser-test-token",
}
print(render_to_string("gameplay/warehouse.html", context))
