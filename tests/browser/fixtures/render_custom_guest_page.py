"""离线渲染真实专属门客模板，不访问数据库或启动服务。"""

import os
import sys
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 使用项目隔离测试配置。
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

guest = Obj(id=9001, display_name="春秋行者", rarity="green", status="idle")
context = {
    "user": Obj(is_authenticated=True),
    "guest": guest,
    "csrf_token": "preview-token",
    "request": Obj(path="/guests/9001/", resolver_match=Obj(url_name="detail")),
}
page = sys.argv[1] if len(sys.argv) > 1 else "panel"
if page == "confirm":
    context.update(
        confirmation="signed-preview-token",
        preview={
            "title": "升阶",
            "summary": "消耗10本易筋经，本次成功率50%",
            "detail": "成功后回到1级，清空升级属性、自由加点和技能，装备退回仓库。保留姓名、形象和成长权重。失败保留现有养成，下次成功率增加10个百分点。",
        },
    )
    print(render_to_string("guests/custom_guest_confirm.html", context))
elif page == "appearance":
    from guests.avatar import CUSTOM_GUEST_AVATAR_GROUP_LABELS, get_custom_guest_avatar_catalog

    catalog = get_custom_guest_avatar_catalog()
    context.update(
        creating=True,
        avatar_groups=[
            {
                "key": key,
                "label": label,
                "count": len(catalog[key]),
                "selected": False,
                "initial_page": 1,
            }
            for key, label in CUSTOM_GUEST_AVATAR_GROUP_LABELS.items()
        ],
        selected_avatar="",
        guest=None,
    )
    print(render_to_string("guests/custom_guest_appearance.html", context))
else:
    from guests.services.custom_guest import DAILY_FEED_LIMIT

    context["custom_guest"] = {
        "growth_min": 3,
        "growth_max": 7,
        "enhancement_level": 0,
        "total_weight": 5000,
        "feeding_used": 0,
        "feeding_limit": DAILY_FEED_LIMIT,
        "feeding_remaining": DAILY_FEED_LIMIT,
        "weight_rows": [
            {"label": label, "value": value, "percent": value / 50}
            for label, value in [("武力", 1800), ("智力", 1800), ("防御", 600), ("敏捷", 800)]
        ],
        "equipment_rows": [
            {
                "value": "100:bound",
                "item": Obj(template=Obj(name="敏捷之流云踏雪靴")),
                "quantity": 16,
                "gain": 50,
                "label": "敏捷",
                "mode": "bound",
            }
        ],
        "next_stage": {"label": "升阶", "required_weight": 5000, "chance": 50, "cost": 10},
        "material_quantity": 30,
        "fruit_quantity": 2,
        "has_resettable_weights": True,
    }
    from django.template import engines

    template = engines["django"].from_string(
        """{% extends "base.html" %}
        {% block content %}<section class="dashboard guest-detail"><h1>门客详情 · 春秋行者</h1>
        {% include "guests/partials/custom_guest_panel.html" %}</section>{% endblock %}"""
    )
    print(template.render(context))
