from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, MissionExtraAttempt, MissionTemplate
from tests.mission_views.support import assert_redirect, response_messages


@pytest.mark.django_db
class TestMissionCardView:
    def test_use_mission_card_rejects_missing_mission_key(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.post(reverse("gameplay:use_mission_card"), {})
        assert_redirect(response, reverse("gameplay:tasks"))
        assert any("请选择任务" in message for message in response_messages(response))

    def test_use_mission_card_rejects_invalid_mission_key(self, manor_with_user):
        _manor, client = manor_with_user
        mission_key = "mission_not_exists_for_card_view_test"
        response = client.post(reverse("gameplay:use_mission_card"), {"mission_key": mission_key})
        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission_key}")
        assert any("任务不存在" in message for message in response_messages(response))

    def test_use_mission_card_rejects_when_action_lock_conflicts(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_use_card_lock_conflict_{manor.id}",
            name="任务卡锁冲突",
        )
        called = {"count": 0}

        monkeypatch.setattr(
            "gameplay.views.mission_helpers.acquire_mission_action_lock",
            lambda *_a, **_k: (False, "", None),
        )

        def _unexpected_add(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.missions.add_mission_extra_attempt_with_item_cost", _unexpected_add)

        response = client.post(reverse("gameplay:use_mission_card"), {"mission_key": mission.key})
        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("任务请求处理中，请稍候重试" in message for message in response_messages(response))
        assert called["count"] == 0

    def test_use_mission_card_database_error_does_not_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_use_card_unexpected_{manor.id}",
            name="任务卡异常任务",
        )

        monkeypatch.setattr(
            "gameplay.services.inventory.core.consume_inventory_item_for_manor_locked",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:use_mission_card"), {"mission_key": mission.key})
        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("操作失败，请稍后重试" in message for message in response_messages(response))

    def test_use_mission_card_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_use_card_runtime_{manor.id}",
            name="任务卡运行时任务",
        )

        monkeypatch.setattr(
            "gameplay.services.inventory.core.consume_inventory_item_for_manor_locked",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:use_mission_card"), {"mission_key": mission.key})

    def test_use_mission_card_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_use_card_legacy_value_{manor.id}",
            name="任务卡旧异常任务",
        )

        monkeypatch.setattr(
            "gameplay.services.inventory.core.consume_inventory_item_for_manor_locked",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "gameplay.views.missions.add_mission_extra_attempt_with_item_cost",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy mission card error")),
        )

        with pytest.raises(ValueError, match="legacy mission card error"):
            client.post(reverse("gameplay:use_mission_card"), {"mission_key": mission.key})

    def test_use_mission_card_delegates_to_service_command(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_use_card_service_command_{manor.id}",
            name="任务卡服务命令任务",
        )
        called: dict[str, object] = {}

        def _fake_use(*, manor, mission, item_key, count=1):
            called["manor"] = manor
            called["mission"] = mission
            called["item_key"] = item_key
            called["count"] = count
            return 3

        monkeypatch.setattr("gameplay.views.missions.add_mission_extra_attempt_with_item_cost", _fake_use)

        response = client.post(reverse("gameplay:use_mission_card"), {"mission_key": mission.key})

        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert called == {
            "manor": manor,
            "mission": mission,
            "item_key": "mission_card",
            "count": 1,
        }

    def test_use_mission_card_rejects_sixth_card_without_consuming_inventory(self, manor_with_user):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"view_use_card_daily_limit_{manor.id}",
            name="任务卡每日上限任务",
        )
        card_template, _ = ItemTemplate.objects.get_or_create(
            key="mission_card",
            defaults={"name": "任务卡", "effect_type": ItemTemplate.EffectType.TOOL},
        )
        card_item, _ = InventoryItem.objects.update_or_create(
            manor=manor,
            template=card_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": 2},
        )
        extra = MissionExtraAttempt.objects.create(
            manor=manor,
            mission=mission,
            date=timezone.localdate(),
            extra_count=5,
        )

        response = client.post(reverse("gameplay:use_mission_card"), {"mission_key": mission.key})

        assert_redirect(response, f"{reverse('gameplay:tasks')}?mission={mission.key}")
        assert any("该任务今日最多使用 5 张任务卡" in message for message in response_messages(response))
        card_item.refresh_from_db()
        extra.refresh_from_db()
        assert card_item.quantity == 2
        assert extra.extra_count == 5
