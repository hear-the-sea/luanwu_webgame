"""渲染交易页面的窄屏边界数据，不访问数据库。"""

import contextlib
import io
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as Obj

with contextlib.redirect_stdout(io.StringIO()):
    fixture = runpy.run_path(str(Path(__file__).with_name("render_market_buy_page.py")))

from django.template.loader import render_to_string  # noqa: E402

context = fixture["context"]
item = fixture["items"][1]
item.key = "long-item"
listing = fixture["listings"][0]
listing.item_template = item
listing.status = "active"
listing.get_status_display = "在售中"
listing.listed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
view = sys.argv[1]
if view in ("sell", "my_listings"):
    context.update(
        market_view=view,
        tradeable_items=[Obj(template=item, quantity=999999)],
        my_listings=[listing],
        selected_status="all",
        market_duration_options=[Obj(value=24, label="一天", fee=5000)],
    )
else:
    info = Obj(winner_count=10, cutoff_price=1000, bidder_count=4, my_bid_amount=1200, is_safe=True)
    slot = Obj(id=1, item_template=item, bid_info=info, starting_price=100, min_increment=10)
    bid = Obj(slot=slot, amount=1200, status="active", get_status_display="竞拍中", created_at=listing.listed_at)
    context.update(
        current_tab="auction",
        auction_can_enter=True,
        auction_view=view,
        auction_stats=Obj(current_round=1, time_remaining=1000, available_gold_bars=1000, my_frozen_gold_bars=0),
        auction_slots=[slot],
        my_leading_slots=[slot],
        my_bids=[bid],
    )

print(render_to_string("trade/trade.html", context))
