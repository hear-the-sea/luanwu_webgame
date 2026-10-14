"""渲染真实集市模板供浏览器验证长文本与金额边界，不读写数据库。"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 使用项目测试配置，避免加载线上环境文件。
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

manor = Obj(silver=1234567890, prestige=999, display_name="买家")
items = [
    Obj(
        name="《上将的自我修养》与英雄传奇的长名称测试卷轴",
        rarity="purple",
        image=None,
        price=200,
        description="商品说明",
        equipment_effect_summary="",
        category_display="道具",
    ),
    Obj(
        name="LongEquipmentNameWithoutSpaces" * 3,
        rarity="orange",
        image=None,
        price=123456789,
        description="",
        equipment_effect_summary="",
        category_display="武器",
    ),
]
listings = [
    Obj(
        id=index + 1,
        item_template=items[index % 2],
        quantity=999999 if index % 2 else 2,
        unit_price=123456789 if index % 2 else 200,
        total_price=123456789123456 if index % 2 else 400,
        seller=Obj(display_name="这是长度达到二十个汉字的测试卖家庄园名") if index != 2 else manor,
        expires_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
        time_remaining=86400,
    )
    for index in range(3)
]
page = Obj(
    has_other_pages=True,
    has_previous=True,
    has_next=True,
    number=1234,
    paginator=Obj(num_pages=99999),
    previous_page_number=1233,
    next_page_number=1235,
)
context = {
    "manor": manor,
    "user": Obj(is_authenticated=True, username="layout_player", is_staff=False),
    "current_tab": "market",
    "market_view": "buy",
    "market_can_buy_or_list": True,
    "market_min_prestige": 100,
    "listings": listings,
    "page_obj": page,
    "selected_category": "all",
    "selected_rarity": "all",
    "selected_order": "-listed_at",
    "csrf_token": "layout-test-token",
    "categories": [{"key": "all", "label": "全部"}, {"key": "tool", "label": "道具与任务奖励物品"}],
    "tabs": [
        {"key": "shop", "name": "商店"},
        {"key": "market", "name": "集市"},
        {"key": "auction", "name": "拍卖行"},
        {"key": "bank", "name": "钱庄"},
    ],
}
print(render_to_string("trade/trade.html", context))
