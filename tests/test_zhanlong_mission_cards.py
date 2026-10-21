"""赵云系列任务卡配置同步、页面入口与扣卡上限回归。"""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, MissionExtraAttempt, MissionTemplate
from tests.mission_views.support import response_messages

rules = import_module("gameplay.migrations.0231_zhanlong_mission_cards")
base = import_module("gameplay.migrations.0220_zhanlong_quest_chain")


@pytest.mark.django_db
def test_card_migration_only_updates_four_mission_limits_and_preserves_usage(manor_with_user):
    manor, _client = manor_with_user
    for key, defaults in base.MISSION_DEFAULTS.items():
        MissionTemplate.objects.update_or_create(key=key, defaults=defaults)
    unrelated = MissionTemplate.objects.create(
        key="zhanlong_unrelated_card_limit", name="其他任务", mission_card_daily_limit=0
    )
    mission = MissionTemplate.objects.get(key="zhanlong_yanshan_shizi")
    extra = MissionExtraAttempt.objects.create(manor=manor, mission=mission, date=timezone.localdate(), extra_count=2)
    before = {row["key"]: row for row in MissionTemplate.objects.filter(key__in=rules.MISSION_UPDATES).values()}
    extra_before = MissionExtraAttempt.objects.filter(pk=extra.pk).values().get()

    for _ in range(2):
        rules.enable_zhanlong_mission_cards(apps, SimpleNamespace(connection=connection))

    for row in MissionTemplate.objects.filter(key__in=rules.MISSION_UPDATES).values():
        assert row == {**before[row["key"]], "mission_card_daily_limit": 5}
    unrelated.refresh_from_db()
    assert unrelated.mission_card_daily_limit == 0
    assert MissionExtraAttempt.objects.filter(pk=extra.pk).values().get() == extra_before


@pytest.mark.django_db
@pytest.mark.parametrize("key", rules.MISSION_UPDATES)
def test_each_stage_allows_five_cards_and_rejects_sixth_without_consuming_stock(manor_with_user, key):
    manor, client = manor_with_user
    mission, _ = MissionTemplate.objects.update_or_create(key=key, defaults=base.MISSION_DEFAULTS[key])
    rules.enable_zhanlong_mission_cards(apps, SimpleNamespace(connection=connection))
    card, _ = ItemTemplate.objects.get_or_create(key="mission_card", defaults={"name": "任务卡", "effect_type": "tool"})
    stock, _ = InventoryItem.objects.update_or_create(
        manor=manor, template=card, defaults={"quantity": 6, "bound_quantity": 0}
    )
    detail_url = f"{reverse('gameplay:tasks')}?mission={key}"

    response = client.get(detail_url)
    assert response.context["mission_card_daily_limit"] == 5
    assert 'class="inline js-mission-card-form"' in response.content.decode()
    for used in range(1, 6):
        response = client.post(reverse("gameplay:use_mission_card"), {"mission_key": key})
        assert response.status_code == 302
        assert (
            MissionExtraAttempt.objects.get(manor=manor, mission=mission, date=timezone.localdate()).extra_count == used
        )
        stock.refresh_from_db()
        assert stock.quantity == 6 - used

    response = client.post(reverse("gameplay:use_mission_card"), {"mission_key": key})
    assert response.status_code == 302
    assert any("该任务今日最多使用 5 张任务卡" in message for message in response_messages(response))
    stock.refresh_from_db()
    assert stock.quantity == 1
    response = client.get(detail_url)
    assert response.context["selected_daily_limit"] == 6
    assert response.context["selected_mission_card_uses"] == 5
    assert "任务卡 5 / 5" in response.content.decode()
