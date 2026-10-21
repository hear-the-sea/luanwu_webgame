"""赵云招募卷轴不可交易，已有模板同步后保留其他属性。"""

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from core.exceptions import TradeValidationError
from gameplay.models import ItemTemplate
from trade.services.market_listing_helpers import load_tradeable_item_template

rules = import_module("gameplay.migrations.0236_zhaoyun_scroll_untradeable")


def test_zhaoyun_scroll_config_is_untradeable():
    path = Path(__file__).resolve().parent.parent / "data" / "item_templates.yaml"
    items = yaml.safe_load(path.read_text(encoding="utf-8"))["items"]
    scroll = next(item for item in items if item["key"] == "zhaoyun_guest_scroll")
    assert scroll["tradeable"] is False


@pytest.mark.django_db
def test_migration_disables_existing_scroll_trading_without_changing_other_fields():
    scroll = ItemTemplate.objects.create(key="zhaoyun_guest_scroll", name="赵云招募卷轴", tradeable=True)
    other = ItemTemplate.objects.create(key="diaochan_guest_scroll", name="貂蝉招募卷轴", tradeable=True)
    before = ItemTemplate.objects.filter(pk=scroll.pk).values().get()

    for _ in range(2):
        rules.make_zhaoyun_scroll_untradeable(apps, SimpleNamespace(connection=connection))

    assert ItemTemplate.objects.filter(pk=scroll.pk).values().get() == {**before, "tradeable": False}
    other.refresh_from_db()
    assert other.tradeable is True
    with pytest.raises(TradeValidationError, match="该物品不可交易"):
        load_tradeable_item_template(item_template_model=ItemTemplate, item_key=scroll.key)
