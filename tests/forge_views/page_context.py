import pytest
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from gameplay.models import EquipmentProduction


@pytest.mark.django_db
class TestForgePageContext:
    def test_forge_page(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:forge"))
        assert response.status_code == 200
        assert "equipment_list" in response.context
        assert "device" in response.context["equipment_categories"]
        body = response.content.decode("utf-8")
        assert "js/forge-page.js" in body
        assert "document.querySelectorAll('.js-decompose-form')" not in body

    def test_forge_page_tolerates_resource_sync_error(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.production.project_resource_production_for_read",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("sync failed")),
        )

        response = client.get(reverse("gameplay:forge"))
        assert response.status_code == 200
        assert "equipment_list" in response.context

    def test_forge_page_active_production_has_refresh_countdown(self, manor_with_user):
        manor, client = manor_with_user
        EquipmentProduction.objects.create(
            manor=manor,
            equipment_key="test_equipment",
            equipment_name="测试装备",
            quantity=1,
            material_costs={"iron": 1},
            base_duration=60,
            actual_duration=60,
            complete_at=timezone.now() + timezone.timedelta(minutes=1),
            status=EquipmentProduction.Status.FORGING,
        )

        response = client.get(reverse("gameplay:forge"))
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/dashboard.js" in body
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_production_runtime_api") in body
        assert 'data-refresh-method="post"' in body

    def test_forge_page_uses_explicit_read_helper(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"prepared": 0}

        monkeypatch.setattr(
            "gameplay.views.production._get_prepared_production_manor",
            lambda request, *, source: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
        )
        monkeypatch.setattr(
            "gameplay.views.production.get_forge_page_context",
            lambda current_manor, **kwargs: (
                {
                    "current_mode": kwargs["current_mode"],
                    "equipment_categories": [],
                    "current_category": kwargs["current_category"],
                    "equipment_list": [],
                    "page_obj": [],
                    "decompose_page_obj": [],
                    "active_forgings": [],
                    "blueprint_synthesis_options": [],
                    "decomposable_equipment": [],
                    "speed_bonus": 0,
                    "speed_bonus_percent": 0,
                    "forging_level": 0,
                    "max_forging_quantity": 1,
                    "is_forging": False,
                }
                if current_manor is manor
                else {}
            ),
        )

        response = client.get(reverse("gameplay:forge"))

        assert response.status_code == 200
        assert calls["prepared"] == 1

    def test_forge_page_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}

        monkeypatch.setattr(
            "gameplay.views.production._get_prepared_production_manor",
            lambda request, *, source: manor,
        )

        def _fake_selector(current_manor, **kwargs):
            calls["selector"] += 1
            assert current_manor is manor
            assert kwargs["current_mode"] == "decompose"
            assert kwargs["current_category"] == "weapon"
            assert kwargs["page"] == "2"
            return {
                "current_mode": "decompose",
                "equipment_categories": ["weapon"],
                "current_category": "weapon",
                "equipment_list": ["equip-a"],
                "page_obj": [],
                "decompose_page_obj": [],
                "active_forgings": [],
                "blueprint_synthesis_options": [],
                "decomposable_equipment": [],
                "speed_bonus": 0.1,
                "speed_bonus_percent": 10,
                "forging_level": 4,
                "max_forging_quantity": 3,
                "is_forging": False,
            }

        monkeypatch.setattr("gameplay.views.production.get_forge_page_context", _fake_selector)

        response = client.get(reverse("gameplay:forge") + "?mode=decompose&category=weapon&page=2")

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert response.context["current_mode"] == "decompose"
        assert response.context["current_category"] == "weapon"

    def test_forge_decompose_mode_uses_shared_category_filter(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        captured = {}

        monkeypatch.setattr("gameplay.services.buildings.forge.get_equipment_options", lambda *_a, **_k: [])

        def _mock_get_decomposable(_manor, category=None):
            captured["category"] = category
            return []

        monkeypatch.setattr(
            "gameplay.services.buildings.forge.get_decomposable_equipment_options", _mock_get_decomposable
        )

        response = client.get(reverse("gameplay:forge") + "?mode=decompose&category=helmet")
        assert response.status_code == 200
        assert response.context["current_mode"] == "decompose"
        assert response.context["current_category"] == "helmet"
        assert captured["category"] == "helmet"

    def test_forge_decompose_mode_merges_weapon_categories(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        captured = {}

        monkeypatch.setattr("gameplay.services.buildings.forge.get_equipment_options", lambda *_a, **_k: [])

        def _mock_get_decomposable(_manor, category=None):
            captured["category"] = category
            return []

        monkeypatch.setattr(
            "gameplay.services.buildings.forge.get_decomposable_equipment_options", _mock_get_decomposable
        )

        response = client.get(reverse("gameplay:forge") + "?mode=decompose&category=sword")
        assert response.status_code == 200
        assert response.context["current_mode"] == "decompose"
        assert response.context["current_category"] == "weapon"
        assert captured["category"] == "weapon"

    def test_forge_decompose_mode_paginates_to_nine_items(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr("gameplay.services.buildings.forge.get_equipment_options", lambda *_a, **_k: [])
        monkeypatch.setattr(
            "gameplay.services.buildings.forge.get_decomposable_equipment_options",
            lambda *_a, **_k: [
                {
                    "key": f"equip_{index}",
                    "name": f"装备{index}",
                    "rarity": "green",
                    "rarity_label": "绿色",
                    "quantity": 1,
                    "effect_type": "equip_weapon",
                    "category": "weapon",
                    "category_name": "武器",
                }
                for index in range(10)
            ],
        )

        response = client.get(reverse("gameplay:forge") + "?mode=decompose&category=all")
        assert response.status_code == 200
        decompose_page_obj = response.context["decompose_page_obj"]
        assert len(decompose_page_obj.object_list) == 9
        assert decompose_page_obj.has_next()

    def test_forge_synthesize_mode_merges_weapon_categories(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        def _item(key: str, category: str) -> dict:
            return {
                "key": key,
                "name": key,
                "category": category,
                "category_name": category,
                "materials": [],
                "base_duration": 120,
                "actual_duration": 120,
                "can_afford": True,
                "required_forging": 1,
                "is_unlocked": True,
                "max_quantity": 1,
                "is_forging": False,
            }

        monkeypatch.setattr(
            "gameplay.services.buildings.forge.get_equipment_options",
            lambda *_a, **_k: [
                _item("equip_a", "sword"),
                _item("equip_b", "dao"),
                _item("equip_c", "helmet"),
            ],
        )
        monkeypatch.setattr("gameplay.services.buildings.forge.get_blueprint_synthesis_options", lambda *_a, **_k: [])
        monkeypatch.setattr(
            "gameplay.services.buildings.forge.get_decomposable_equipment_options", lambda *_a, **_k: []
        )

        response = client.get(reverse("gameplay:forge") + "?mode=synthesize&category=sword")
        assert response.status_code == 200
        assert response.context["current_mode"] == "synthesize"
        assert response.context["current_category"] == "weapon"
        page_obj = response.context["equipment_list"]
        assert {item["key"] for item in page_obj.object_list} == {"equip_a", "equip_b"}

    def test_forge_synthesize_mode_supports_device_blueprint_category(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        monkeypatch.setattr("gameplay.services.buildings.forge.get_equipment_options", lambda *_a, **_k: [])
        monkeypatch.setattr(
            "gameplay.services.buildings.forge.get_decomposable_equipment_options", lambda *_a, **_k: []
        )
        monkeypatch.setattr(
            "gameplay.services.buildings.forge.get_blueprint_synthesis_options",
            lambda *_a, **_k: [
                {
                    "blueprint_key": "bp_device",
                    "blueprint_name": "器械图纸",
                    "blueprint_count": 1,
                    "result_key": "equip_unknown_device",
                    "result_name": "器械产物",
                    "result_effect_type": "equip_device",
                    "result_quantity": 1,
                    "required_forging": 1,
                    "description": "",
                    "costs": [],
                    "max_synthesis_quantity": 1,
                    "is_unlocked": True,
                    "can_afford": True,
                    "can_synthesize": True,
                },
                {
                    "blueprint_key": "bp_helmet",
                    "blueprint_name": "头盔图纸",
                    "blueprint_count": 1,
                    "result_key": "equip_unknown_helmet",
                    "result_name": "头盔产物",
                    "result_effect_type": "equip_helmet",
                    "result_quantity": 1,
                    "required_forging": 1,
                    "description": "",
                    "costs": [],
                    "max_synthesis_quantity": 1,
                    "is_unlocked": True,
                    "can_afford": True,
                    "can_synthesize": True,
                },
            ],
        )

        response = client.get(reverse("gameplay:forge") + "?mode=synthesize&category=device")
        assert response.status_code == 200
        assert response.context["current_mode"] == "synthesize"
        assert response.context["current_category"] == "device"
        options = response.context["blueprint_synthesis_options"]
        assert len(options) == 1
        assert options[0]["blueprint_key"] == "bp_device"
        assert options[0]["result_category"] == "device"

    def test_forge_all_category_prioritizes_forgeable_high_requirement(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        def _item(key: str, required_forging: int, is_unlocked: bool, can_afford: bool) -> dict:
            return {
                "key": key,
                "name": key,
                "category": "helmet",
                "category_name": "头盔",
                "materials": [],
                "base_duration": 120,
                "actual_duration": 120,
                "can_afford": can_afford,
                "required_forging": required_forging,
                "is_unlocked": is_unlocked,
                "max_quantity": 1,
                "is_forging": False,
            }

        mocked_options = [
            _item("unaffordable_r10", 10, True, False),
            _item("forgeable_r5", 5, True, True),
            _item("locked_r9", 9, False, True),
            _item("forgeable_r7", 7, True, True),
            _item("forgeable_r1", 1, True, True),
        ]

        monkeypatch.setattr("gameplay.services.buildings.forge.get_equipment_options", lambda *_a, **_k: mocked_options)

        response = client.get(reverse("gameplay:forge") + "?category=all")
        assert response.status_code == 200

        page_obj = response.context["equipment_list"]
        ordered_keys = [item["key"] for item in page_obj.object_list]
        assert ordered_keys == ["forgeable_r7", "forgeable_r5", "forgeable_r1", "unaffordable_r10", "locked_r9"]

    def test_forge_specific_category_prioritizes_forgeable_high_requirement(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user

        def _item(key: str, required_forging: int, is_unlocked: bool, can_afford: bool) -> dict:
            return {
                "key": key,
                "name": key,
                "category": "helmet",
                "category_name": "头盔",
                "materials": [],
                "base_duration": 120,
                "actual_duration": 120,
                "can_afford": can_afford,
                "required_forging": required_forging,
                "is_unlocked": is_unlocked,
                "max_quantity": 1,
                "is_forging": False,
            }

        mocked_options = [
            _item("unaffordable_r10", 10, True, False),
            _item("forgeable_r3", 3, True, True),
            _item("locked_r9", 9, False, True),
            _item("forgeable_r7", 7, True, True),
        ]

        def _mock_get_equipment_options(*_args, **kwargs):
            assert kwargs.get("category") == "helmet"
            return mocked_options

        monkeypatch.setattr("gameplay.services.buildings.forge.get_equipment_options", _mock_get_equipment_options)

        response = client.get(reverse("gameplay:forge") + "?category=helmet")
        assert response.status_code == 200

        page_obj = response.context["equipment_list"]
        ordered_keys = [item["key"] for item in page_obj.object_list]
        assert ordered_keys == ["forgeable_r7", "forgeable_r3", "unaffordable_r10", "locked_r9"]
