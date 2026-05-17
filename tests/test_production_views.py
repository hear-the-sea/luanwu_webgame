"""
生产系统视图测试
"""

from types import SimpleNamespace

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from core.exceptions import ProductionStartError
from gameplay.models import HorseProduction, LivestockProduction, SmeltingProduction
from gameplay.services.buildings import ranch as ranch_service
from gameplay.services.buildings import smithy as smithy_service
from gameplay.services.buildings import stable as stable_service


@pytest.mark.django_db
class TestProductionViews:
    """生产系统视图测试"""

    def test_stable_page(self, manor_with_user):
        """马房页面"""
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:stable"))
        assert response.status_code == 200
        assert "horse_options" in response.context
        body = response.content.decode("utf-8")
        assert "js/production-cost-preview.js" in body
        assert "onchange=" not in body
        assert "function updateTotalCost" not in body

    def test_stable_page_reads_latest_runtime_stable_config(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        try:
            monkeypatch.setattr(
                stable_service,
                "load_yaml_data",
                lambda *args, **kwargs: {
                    "production": {
                        "runtime_horse": {
                            "grain_cost": 321,
                            "base_duration": 180,
                            "required_horsemanship": 1,
                        }
                    }
                },
            )
            stable_service.clear_stable_production_cache()

            response = client.get(reverse("gameplay:stable"))

            assert response.status_code == 200
            assert len(response.context["horse_options"]) == 1
            option = response.context["horse_options"][0]
            assert option["key"] == "runtime_horse"
            assert option["name"] == "runtime_horse"
            assert option["grain_cost"] == 321
            assert option["base_duration"] == 180
            assert option["required_horsemanship"] == 1
        finally:
            monkeypatch.undo()
            stable_service.clear_stable_production_cache()

    def test_stable_page_tolerates_resource_sync_error(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.production.project_resource_production_for_read",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("sync failed")),
        )

        response = client.get(reverse("gameplay:stable"))
        assert response.status_code == 200
        assert "horse_options" in response.context

    def test_stable_page_active_production_has_refresh_countdown(self, manor_with_user):
        manor, client = manor_with_user
        HorseProduction.objects.create(
            manor=manor,
            horse_key="test_horse",
            horse_name="测试马",
            quantity=1,
            grain_cost=10,
            base_duration=60,
            actual_duration=60,
            complete_at=timezone.now() + timezone.timedelta(minutes=1),
            status=HorseProduction.Status.PRODUCING,
        )

        response = client.get(reverse("gameplay:stable"))
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/dashboard.js" in body
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_production_runtime_api") in body
        assert 'data-refresh-method="post"' in body

    def test_ranch_page(self, manor_with_user):
        """畜牧场页面"""
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:ranch"))
        assert response.status_code == 200
        assert "livestock_options" in response.context
        body = response.content.decode("utf-8")
        assert "js/production-cost-preview.js" in body
        assert "onchange=" not in body
        assert "function updateTotalCost" not in body

    def test_ranch_page_reads_latest_runtime_ranch_config(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        try:
            monkeypatch.setattr(
                ranch_service,
                "load_yaml_data",
                lambda *args, **kwargs: {
                    "production": {
                        "runtime_livestock": {
                            "grain_cost": 222,
                            "base_duration": 240,
                            "required_animal_husbandry": 1,
                        }
                    }
                },
            )
            ranch_service.clear_ranch_production_cache()

            response = client.get(reverse("gameplay:ranch"))

            assert response.status_code == 200
            assert len(response.context["livestock_options"]) == 1
            option = response.context["livestock_options"][0]
            assert option["key"] == "runtime_livestock"
            assert option["name"] == "runtime_livestock"
            assert option["grain_cost"] == 222
            assert option["base_duration"] == 240
            assert option["required_animal_husbandry"] == 1
        finally:
            monkeypatch.undo()
            ranch_service.clear_ranch_production_cache()

    def test_stable_page_uses_explicit_read_helper(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"prepared": 0}

        monkeypatch.setattr(
            "gameplay.views.production._get_prepared_production_manor",
            lambda request, *, source: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
        )
        monkeypatch.setattr(
            "gameplay.views.production.get_stable_page_context",
            lambda current_manor: (
                {
                    "horse_options": [],
                    "active_productions": [],
                    "speed_bonus": 0,
                    "speed_bonus_percent": 0,
                    "horsemanship_level": 0,
                    "max_production_quantity": 1,
                    "is_producing": False,
                }
                if current_manor is manor
                else {}
            ),
        )

        response = client.get(reverse("gameplay:stable"))

        assert response.status_code == 200
        assert calls["prepared"] == 1

    def test_stable_page_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}

        monkeypatch.setattr(
            "gameplay.views.production._get_prepared_production_manor",
            lambda request, *, source: manor,
        )

        def _fake_selector(current_manor):
            calls["selector"] += 1
            assert current_manor is manor
            return {
                "horse_options": ["horse-a"],
                "active_productions": [],
                "speed_bonus": 0.2,
                "speed_bonus_percent": 20,
                "horsemanship_level": 3,
                "max_production_quantity": 8,
                "is_producing": False,
            }

        monkeypatch.setattr("gameplay.views.production.get_stable_page_context", _fake_selector)

        response = client.get(reverse("gameplay:stable"))

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert response.context["horse_options"] == ["horse-a"]

    def test_ranch_page_active_production_has_refresh_countdown(self, manor_with_user):
        manor, client = manor_with_user
        LivestockProduction.objects.create(
            manor=manor,
            livestock_key="test_livestock",
            livestock_name="测试家畜",
            quantity=1,
            grain_cost=8,
            base_duration=60,
            actual_duration=60,
            complete_at=timezone.now() + timezone.timedelta(minutes=1),
            status=LivestockProduction.Status.PRODUCING,
        )

        response = client.get(reverse("gameplay:ranch"))
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/dashboard.js" in body
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_production_runtime_api") in body
        assert 'data-refresh-method="post"' in body

    def test_smithy_page(self, manor_with_user):
        """冶炼坊页面"""
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:smithy"))
        assert response.status_code == 200
        assert "metal_options" in response.context
        body = response.content.decode("utf-8")
        assert "js/production-cost-preview.js" in body
        assert "onchange=" not in body
        assert "function updateTotalCost" not in body

    def test_smithy_page_switches_between_metal_and_medicine_categories(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(f"{reverse('gameplay:smithy')}?category=medicine")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'href="?category=metal"' in body
        assert 'href="?category=medicine"' in body
        assert 'value="zhixuesan"' in body
        assert 'value="tong"' not in body

    def test_smithy_medicine_quantity_input_allows_batch_quantity_without_smelting_tech(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(f"{reverse('gameplay:smithy')}?category=medicine")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'id="quantity-zhixuesan"' in body
        assert 'id="quantity-zhixuesan"\n                                       value="1" min="1" max="100"' in body

    def test_smithy_quantity_input_updates_duration_preview(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(f"{reverse('gameplay:smithy')}?category=medicine")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'id="duration-zhixuesan"' in body
        assert 'data-unit-duration="' in body
        assert 'data-duration-target="duration-zhixuesan"' in body

    def test_smithy_page_reads_latest_runtime_smithy_config(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        try:
            monkeypatch.setattr(
                smithy_service,
                "load_yaml_data",
                lambda *args, **kwargs: {
                    "production": {
                        "runtime_medicine": {
                            "cost_type": "silver",
                            "cost_amount": 77,
                            "base_duration": 150,
                            "required_smithy": 1,
                            "category": "medicine",
                        }
                    }
                },
            )
            smithy_service.clear_smithy_production_cache()

            response = client.get(reverse("gameplay:smithy"))

            assert response.status_code == 200
            assert len(response.context["metal_options"]) == 1
            option = response.context["metal_options"][0]
            assert option["key"] == "runtime_medicine"
            assert option["name"] == "runtime_medicine"
            assert option["cost_type"] == "silver"
            assert option["cost_amount"] == 77
            assert option["base_duration"] == 150
            assert option["required_level"] == 1
            assert option["required_type_label"] == "冶炼坊"
            assert option["category"] == "medicine"
        finally:
            monkeypatch.undo()
            smithy_service.clear_smithy_production_cache()

    def test_smithy_page_active_production_has_refresh_countdown(self, manor_with_user):
        manor, client = manor_with_user
        SmeltingProduction.objects.create(
            manor=manor,
            metal_key="test_metal",
            metal_name="测试物品",
            quantity=1,
            cost_type="silver",
            cost_amount=10,
            base_duration=60,
            actual_duration=60,
            complete_at=timezone.now() + timezone.timedelta(minutes=1),
            status=SmeltingProduction.Status.PRODUCING,
        )

        response = client.get(reverse("gameplay:smithy"))
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/dashboard.js" in body
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_production_runtime_api") in body
        assert 'data-refresh-method="post"' in body

    def test_start_horse_production_database_error_does_not_500(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.production.start_horse_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:start_horse_production"), {"horse_key": "any", "quantity": "1"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:stable")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_start_horse_production_known_error_shows_message(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.production.start_horse_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ProductionStartError("horse blocked")),
        )

        response = client.post(reverse("gameplay:start_horse_production"), {"horse_key": "any", "quantity": "1"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:stable")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("horse blocked" in m for m in messages)

    def test_start_horse_production_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.production.start_horse_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:start_horse_production"), {"horse_key": "any", "quantity": "1"})

    def test_start_horse_production_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.production.start_horse_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy horse blocked")),
        )

        with pytest.raises(ValueError, match="legacy horse blocked"):
            client.post(reverse("gameplay:start_horse_production"), {"horse_key": "any", "quantity": "1"})

    def test_start_horse_production_malformed_result_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.views.production.start_horse_production",
            lambda *_args, **_kwargs: SimpleNamespace(horse_name="测试马", quantity=1, actual_duration="bad"),
        )

        with pytest.raises(AssertionError, match="invalid production result actual_duration"):
            client.post(reverse("gameplay:start_horse_production"), {"horse_key": "any", "quantity": "1"})

    def test_start_horse_production_rejects_invalid_quantity(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        called = {"count": 0}

        def _unexpected_start(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.production.start_horse_production", _unexpected_start)

        response = client.post(reverse("gameplay:start_horse_production"), {"horse_key": "any", "quantity": "-1"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:stable")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("无效的数量" in m for m in messages)
        assert called["count"] == 0

    def test_start_horse_production_rejects_missing_horse_key(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        called = {"count": 0}

        def _unexpected_start(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.production.start_horse_production", _unexpected_start)

        response = client.post(reverse("gameplay:start_horse_production"), {"quantity": "1"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:stable")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("请选择马匹类型" in m for m in messages)
        assert called["count"] == 0

    def test_start_livestock_production_database_error_does_not_500(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.ranch.start_livestock_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:start_livestock_production"), {"livestock_key": "any", "quantity": "1"}
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:ranch")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_start_livestock_production_known_error_shows_message(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.ranch.start_livestock_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ProductionStartError("livestock blocked")),
        )

        response = client.post(
            reverse("gameplay:start_livestock_production"), {"livestock_key": "any", "quantity": "1"}
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:ranch")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("livestock blocked" in m for m in messages)

    def test_start_livestock_production_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.ranch.start_livestock_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy livestock blocked")),
        )

        with pytest.raises(ValueError, match="legacy livestock blocked"):
            client.post(reverse("gameplay:start_livestock_production"), {"livestock_key": "any", "quantity": "1"})

    def test_start_livestock_production_malformed_result_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.ranch.start_livestock_production",
            lambda *_args, **_kwargs: SimpleNamespace(livestock_name="测试家畜", quantity="bad", actual_duration=60),
        )

        with pytest.raises(AssertionError, match="invalid production result quantity"):
            client.post(reverse("gameplay:start_livestock_production"), {"livestock_key": "any", "quantity": "1"})

    def test_start_livestock_production_rejects_invalid_quantity(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        called = {"count": 0}

        def _unexpected_start(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.services.buildings.ranch.start_livestock_production", _unexpected_start)

        response = client.post(
            reverse("gameplay:start_livestock_production"),
            {"livestock_key": "any", "quantity": "bad"},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:ranch")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("无效的数量" in m for m in messages)
        assert called["count"] == 0

    def test_start_smelting_production_database_error_does_not_500(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.smithy.start_smelting_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:start_smelting_production"), {"metal_key": "any", "quantity": "1"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:smithy")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_start_smelting_production_known_error_shows_message(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.smithy.start_smelting_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ProductionStartError("smelting blocked")),
        )

        response = client.post(reverse("gameplay:start_smelting_production"), {"metal_key": "any", "quantity": "1"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:smithy")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("smelting blocked" in m for m in messages)

    def test_start_smelting_production_legacy_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.smithy.start_smelting_production",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("legacy smelting blocked")),
        )

        with pytest.raises(ValueError, match="legacy smelting blocked"):
            client.post(reverse("gameplay:start_smelting_production"), {"metal_key": "any", "quantity": "1"})

    def test_start_smelting_production_malformed_result_bubbles_up(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr(
            "gameplay.services.buildings.smithy.start_smelting_production",
            lambda *_args, **_kwargs: SimpleNamespace(metal_name=None, quantity=1, actual_duration=60),
        )

        with pytest.raises(AssertionError, match="invalid smelting production result metal_name"):
            client.post(reverse("gameplay:start_smelting_production"), {"metal_key": "any", "quantity": "1"})

    def test_start_smelting_production_rejects_invalid_quantity(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        called = {"count": 0}

        def _unexpected_start(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.services.buildings.smithy.start_smelting_production", _unexpected_start)

        response = client.post(reverse("gameplay:start_smelting_production"), {"metal_key": "any", "quantity": "0"})
        assert response.status_code == 302
        assert response.url == reverse("gameplay:smithy")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("无效的数量" in m for m in messages)
        assert called["count"] == 0
