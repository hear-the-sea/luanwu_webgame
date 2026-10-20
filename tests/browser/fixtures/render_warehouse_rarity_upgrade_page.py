"""无数据库渲染真实仓库模板，验证升阶丸子的门客限制。"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 使用项目隔离测试配置。
import yaml
from django.db import connection
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from gameplay.models import ItemTemplate  # noqa: E402
from gameplay.services.inventory.guest_reset_helpers import get_rarity_conversion_block_reason  # noqa: E402
from guests.models import Guest, GuestStatus, GuestTemplate  # noqa: E402


def guest(guest_id, name, rarity, *, recruitable=True, used=False, key=None):
    template = GuestTemplate(
        id=guest_id + 1000,
        key=key or f"browser_rarity_guest_{guest_id}",
        name=name,
        rarity=rarity,
        archetype="military",
        recruitable=recruitable,
    )
    candidate = Guest(
        id=guest_id, manor_id=1, template=template, level=20, status=GuestStatus.IDLE, rarity_pill_used=used
    )
    candidate.rarity_upgrade_block_reason = get_rarity_conversion_block_reason(candidate) or ""
    return candidate


def item(item_id, config):
    template = ItemTemplate(
        key=config["key"],
        name=config["name"],
        description=config["description"],
        rarity=config["rarity"],
        effect_type=config["effect_type"],
        effect_payload=config["effect_payload"],
        is_usable=True,
    )
    return Obj(
        id=item_id,
        template=template,
        binding_mode="unbound",
        display_is_bound=False,
        display_quantity=3,
        quantity=3,
        transferable_quantity=3,
        treasury_deposit_blocked=False,
        category_display="道具",
        can_use_in_warehouse=True,
    )


def reject_database_query(execute, sql, params, many, context):
    raise AssertionError("仓库升阶浏览器 fixture 不应访问数据库")


scenario = sys.argv[1] if len(sys.argv) > 1 else "mixed"
if scenario not in {"mixed", "blocked", "empty"}:
    raise ValueError(f"未知场景：{scenario}")

catalog_path = Path(__file__).resolve().parents[3] / "data" / "item_templates.yaml"
catalog = {config["key"]: config for config in yaml.safe_load(catalog_path.read_text(encoding="utf-8"))["items"]}
item_keys = (
    "green_to_blue_pill",
    "blue_to_purple_pill",
    "peerless_general_upgrade_token",
    "peerless_general_upgrade_token_2",
)
guests = [
    guest(201, "可招募绿阶门客", "green"),
    guest(202, "非招募绿阶门客", "green", recruitable=False),
    guest(203, "已用丸子绿阶门客", "green", used=True),
    guest(204, "可招募蓝阶门客", "blue"),
    guest(205, "非招募蓝阶门客", "blue", recruitable=False),
    guest(206, "已用丸子蓝阶门客", "blue", recruitable=False, key="guest_rarity_conversion_blue_999"),
    guest(207, "残卷绿阶门客", "green", recruitable=False, key="hist_sljnbc_0589"),
    guest(208, "残卷蓝阶门客", "blue", recruitable=False, used=True, key="hist_sljnbc_0589_blue"),
]
if scenario == "blocked":
    guests = [candidate for candidate in guests if candidate.rarity_upgrade_block_reason]
elif scenario == "empty":
    guests = []

context = {
    "user": Obj(is_authenticated=False),
    "current_tab": "warehouse",
    "categories": [{"key": "tool", "label": "道具"}],
    "selected_category": "all",
    "inventory_items": [item(item_id, catalog[key]) for item_id, key in enumerate(item_keys, 101)],
    "guests_for_rarity_upgrade": guests,
    "pagination": {"total_pages": 1},
    "csrf_token": "browser-test-token",
}
with connection.execute_wrapper(reject_database_query):
    print(render_to_string("gameplay/warehouse.html", context))
