import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import pytest  # noqa: E402, F401

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.template.loader import render_to_string  # noqa: E402

from battle.view_helpers import build_rounds_display  # noqa: E402

base = {
    "actor": "浪翻云",
    "kind": "guest",
    "side": "attacker",
    "actor_state": {"side": "attacker", "kind": "guest", "percent": 70},
    "target": "刀霸",
    "damage": 987654,
    "kills": 321,
    "skills": ["覆雨剑法", "剑心通明"],
    "order": 1,
    "status_inflicted": [],
}
sub = {
    **base,
    "target": "剑圣",
    "reflect_damage": 123456,
    "reflect_kills": 0,
    "reflect_defeated": True,
    "status_inflicted": ["审计次目标眩晕"],
    "actor_state": {"side": "attacker", "kind": "guest", "percent": 0},
}
extra = {
    "type": "passive",
    "side": "attacker",
    "unit": "长风万里踏破江湖行侠仗义的门客姓名",
    "effect": "长篇被动技能名称审计",
    "healed": 1234,
    "unit_state": base["actor_state"],
}
events = [
    {"type": "passive", "effect": "嗜血狂怒", "unit": "浪翻云", "side": "attacker", "lost": 123456, "order": 1},
    {
        "type": "heal",
        "effect": "五气朝元",
        "unit": "浪翻云",
        "side": "attacker",
        "healed": 1234567,
        "heal_strength": 12345,
        "order": 1,
    },
    {**base, "additional_targets": [sub]},
    extra,
    {**base, "actor": "长风万里踏破江湖行侠仗义的门客姓名", "target": "拳圣"},
]
styles = render_to_string("battle/partials/report_detail_styles.html")
for viewer in [sys.argv[1]]:
    rows = build_rounds_display([{"events": events}], {})[0]
    content = render_to_string(
        "battle/partials/round_event_list.html",
        {"events": rows["attacker_events"], "my_side": viewer, "is_spectator": viewer == "spectator"},
    )
    html = (
        '<html><head><meta charset="utf-8">'
        + styles
        + '</head><body><main class="battle-report-page" style="width:100%;padding:8px;box-sizing:border-box"><section class="battle-rounds-card"><div class="battle-round"><div class="battle-round-table"><div class="round-action-cell attacker">'
        + content
        + '</div><div class="round-action-cell defender"></div></div></div></section></main></body></html>'
    )
    print(html)
