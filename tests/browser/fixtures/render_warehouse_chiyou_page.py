"""无数据库渲染蚩尤卡仓库和实际兑换弹窗。"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace as Obj

import django
import pytest  # noqa: F401 - 使用隔离测试配置。
import yaml
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

scenario = sys.argv[1] if len(sys.argv) > 1 else "available"
source = Path(__file__).resolve().parents[3] / "data/guests/chiyou_exchange.yaml"
heroes = yaml.safe_load(source.read_text())["heroes"]["purple"]
options = [
    {
        "template_key": hero["key"],
        "name": hero["name"],
        "rarity": "purple",
        "rarity_label": "紫",
        "avatar_url": "",
        "flavor": hero["flavor"],
        "growth_range": hero["growth_range"],
        "is_world_unique": True,
        "is_available": scenario != "taken",
        "status_summary": "仕官于其他庄园" if scenario == "taken" else "在野",
    }
    for hero in heroes
]
template = Obj(
    key="chiyou_card",
    name="蚩尤卡",
    image=None,
    rarity="purple",
    description="集齐100张可选择唯一门客。",
    effect_type="tool",
    effect_payload={"action": "summon_guest", "player_choice": True},
)
rows = [
    Obj(
        id=101,
        template=template,
        binding_mode=mode,
        display_is_bound=mode == "bound",
        display_quantity=40 if mode == "bound" else 60,
        quantity=99 if scenario == "insufficient" else 100,
        transferable_quantity=0,
        treasury_deposit_blocked=False,
        category_display="道具",
        can_use_in_warehouse=True,
        player_choice_options=options,
        player_choice_cost=100,
        player_choice_has_stock=scenario != "insufficient",
        player_choice_has_available_guest=scenario != "taken",
    )
    for mode in ("bound", "unbound")
]
print(
    render_to_string(
        "gameplay/warehouse.html",
        {
            "user": Obj(is_authenticated=False),
            "current_tab": "warehouse",
            "selected_category": "all",
            "inventory_items": rows,
            "pagination": {"total_pages": 1},
            "csrf_token": "browser-chiyou-csrf-token",
        },
    )
)
