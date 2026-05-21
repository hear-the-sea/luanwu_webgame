import pytest
from django.core.cache import cache
from django.db import DatabaseError
from django.urls import reverse

from core.exceptions import GuestAllocationResetError, ItemNotUsableError
from gameplay.models import InventoryItem, ItemTemplate


@pytest.mark.django_db
class TestInventoryGuestItemActions:
    def test_use_rebirth_card_rejects_non_positive_guest_id(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_rebirth_card_item",
            name="门客重生卡",
            effect_payload={"action": "rebirth_guest"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
        called = {"count": 0}

        def _unexpected_call(*args, **kwargs):
            called["count"] += 1
            return {}

        monkeypatch.setattr("gameplay.views.inventory.use_guest_rebirth_card", _unexpected_call)

        response = client.post(
            reverse("gameplay:use_rebirth_card", kwargs={"pk": item.pk}),
            {"guest_id": -1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "请选择要重生的门客" in payload["error"]
        assert called["count"] == 0

    def test_use_rebirth_card_rejects_non_mapping_effect_payload(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_rebirth_card_bad_payload",
            name="门客重生卡坏结构",
            effect_payload=False,
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
        called = {"count": 0}

        def _unexpected_call(*args, **kwargs):
            called["count"] += 1
            return {}

        monkeypatch.setattr("gameplay.views.inventory.use_guest_rebirth_card", _unexpected_call)

        with pytest.raises(AssertionError, match="invalid inventory target-guest item effect_payload"):
            client.post(
                reverse("gameplay:use_rebirth_card", kwargs={"pk": item.pk}),
                {"guest_id": 1},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        assert called["count"] == 0

    def test_use_xisuidan_rejects_non_positive_guest_id(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_xisuidan_item",
            name="洗髓丹",
            effect_payload={"action": "reroll_growth"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
        called = {"count": 0}

        def _unexpected_call(*args, **kwargs):
            called["count"] += 1
            return {}

        monkeypatch.setattr("gameplay.views.inventory.use_xisuidan", _unexpected_call)

        response = client.post(
            reverse("gameplay:use_xisuidan", kwargs={"pk": item.pk}),
            {"guest_id": -1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "请选择要洗髓的门客" in payload["error"]
        assert called["count"] == 0

    def test_use_xidianka_rejects_non_positive_guest_id(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_xidianka_item",
            name="洗点卡",
            effect_payload={"action": "reset_allocation"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
        called = {"count": 0}

        def _unexpected_call(*args, **kwargs):
            called["count"] += 1
            return {}

        monkeypatch.setattr("gameplay.views.inventory.use_xidianka", _unexpected_call)

        response = client.post(
            reverse("gameplay:use_xidianka", kwargs={"pk": item.pk}),
            {"guest_id": -1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "请选择要洗点的门客" in payload["error"]
        assert called["count"] == 0

    def test_use_xidianka_known_error_returns_400(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_xidianka_item_known_error",
            name="洗点卡已知错误",
            effect_payload={"action": "reset_allocation"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_xidianka",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(GuestAllocationResetError(message="无需使用洗点卡")),
        )

        response = client.post(
            reverse("gameplay:use_xidianka", kwargs={"pk": item.pk}),
            {"guest_id": 1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "无需使用洗点卡" in payload["error"]

    def test_use_guest_rarity_upgrade_rejects_non_positive_guest_id(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_rarity_upgrade_item",
            name="《上将的自我修养》残卷1",
            effect_payload={"action": "upgrade_guest_rarity"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
        called = {"count": 0}

        def _unexpected_call(*args, **kwargs):
            called["count"] += 1
            return {}

        monkeypatch.setattr("gameplay.views.inventory.use_guest_rarity_upgrade_item", _unexpected_call)

        response = client.post(
            reverse("gameplay:use_guest_rarity_upgrade", kwargs={"pk": item.pk}),
            {"guest_id": -1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "请选择要升阶的门客" in payload["error"]
        assert called["count"] == 0

    def test_use_soul_container_rejects_non_positive_guest_id(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_soul_container_item",
            name="灵魂容器",
            effect_payload={"action": "soul_fusion"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)
        called = {"count": 0}

        def _unexpected_call(*args, **kwargs):
            called["count"] += 1
            return {}

        monkeypatch.setattr("gameplay.views.inventory.use_soul_container", _unexpected_call)

        response = client.post(
            reverse("gameplay:use_soul_container", kwargs={"pk": item.pk}),
            {"guest_id": 0},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "请选择要融合的门客" in payload["error"]
        assert called["count"] == 0

    def test_use_item_ajax_handles_known_error(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(key="view_use_item_known_error", name="普通道具")
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_inventory_item",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ItemNotUsableError("普通道具", message="use blocked")),
        )

        response = client.post(
            reverse("gameplay:use_item", kwargs={"pk": item.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert "use blocked" in payload["error"]

    def test_use_item_allows_fifty_requests_per_minute(self, manor_with_user, monkeypatch):
        cache.clear()
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(key="view_use_item_rate_limit", name="限流道具", is_usable=True)
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr("gameplay.views.inventory.use_inventory_item", lambda *_args, **_kwargs: {"_message": "ok"})

        url = reverse("gameplay:use_item", kwargs={"pk": item.pk})
        try:
            for _ in range(50):
                response = client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                assert response.status_code == 200

            response = client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            assert response.status_code == 429
            assert "频繁" in response.json()["error"]
        finally:
            cache.clear()

    def test_use_item_ajax_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(key="view_use_item_legacy_error", name="旧式异常道具")
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_inventory_item",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy use blocked")),
        )

        with pytest.raises(ValueError, match="legacy use blocked"):
            client.post(
                reverse("gameplay:use_item", kwargs={"pk": item.pk}),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

    def test_use_item_ajax_database_error_returns_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(key="view_use_item_database_error", name="数据库异常道具")
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_inventory_item",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:use_item", kwargs={"pk": item.pk}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 500
        payload = response.json()
        assert payload["success"] is False
        assert "操作失败，请稍后重试" in payload["error"]

    def test_use_item_ajax_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(key="view_use_item_runtime_error", name="运行时异常道具")
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_inventory_item",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                reverse("gameplay:use_item", kwargs={"pk": item.pk}),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

    def test_use_item_ajax_malformed_success_message_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(key="view_use_item_bad_message", name="坏消息道具", is_usable=True)
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_inventory_item",
            lambda *_args, **_kwargs: {"_message": {"bad": "payload"}},
        )

        with pytest.raises(AssertionError, match="invalid inventory use_item success message"):
            client.post(
                reverse("gameplay:use_item", kwargs={"pk": item.pk}),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

    def test_use_rebirth_card_database_error_returns_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_rebirth_card_item_unexpected",
            name="门客重生卡异常",
            effect_payload={"action": "rebirth_guest"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_guest_rebirth_card",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:use_rebirth_card", kwargs={"pk": item.pk}),
            {"guest_id": 1},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert response.status_code == 500
        payload = response.json()
        assert payload["success"] is False
        assert "操作失败，请稍后重试" in payload["error"]

    def test_use_rebirth_card_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_rebirth_card_item_runtime",
            name="门客重生卡运行时异常",
            effect_payload={"action": "rebirth_guest"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_guest_rebirth_card",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                reverse("gameplay:use_rebirth_card", kwargs={"pk": item.pk}),
                {"guest_id": 1},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

    def test_use_rebirth_card_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        template = ItemTemplate.objects.create(
            key="view_rebirth_card_item_value_error",
            name="门客重生卡旧式异常",
            effect_payload={"action": "rebirth_guest"},
        )
        item = InventoryItem.objects.create(manor=manor, template=template, quantity=1)

        monkeypatch.setattr(
            "gameplay.views.inventory.use_guest_rebirth_card",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy rebirth")),
        )

        with pytest.raises(ValueError, match="legacy rebirth"):
            client.post(
                reverse("gameplay:use_rebirth_card", kwargs={"pk": item.pk}),
                {"guest_id": 1},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
