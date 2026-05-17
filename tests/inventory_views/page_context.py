import pytest
from django.urls import reverse
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate
from guests.models import (
    Guest,
    GuestArchetype,
    GuestRarity,
    GuestRecruitment,
    GuestStatus,
    GuestTemplate,
    RecruitmentCandidate,
    RecruitmentPool,
)


@pytest.mark.django_db
class TestInventoryPageContext:
    def test_warehouse_page(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:warehouse"))
        assert response.status_code == 200
        assert "inventory_items" in response.context

    def test_warehouse_treasury_tab(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:warehouse") + "?tab=treasury")
        assert response.status_code == 200
        assert response.context["current_tab"] == "treasury"

    def test_warehouse_page_projects_grain_item_without_writing_inventory(self, manor_with_user):
        manor, client = manor_with_user
        grain_template, _ = ItemTemplate.objects.get_or_create(
            key="grain",
            defaults={"name": "粮食"},
        )
        if not grain_template.name:
            grain_template.name = "粮食"
            grain_template.save(update_fields=["name"])

        manor.grain = 777
        manor.resource_updated_at = timezone.now()
        manor.save(update_fields=["grain", "resource_updated_at"])
        InventoryItem.objects.filter(
            manor=manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).delete()

        response = client.get(reverse("gameplay:warehouse"))
        assert response.status_code == 200

        warehouse_grain = InventoryItem.objects.filter(
            manor=manor,
            template=grain_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).first()
        assert warehouse_grain is None
        projected_entry = next(
            (entry for entry in response.context["inventory_items"] if entry.template.key == "grain"),
            None,
        )
        assert projected_entry is not None
        assert projected_entry.display_quantity == 777
        assert projected_entry.is_projected is True

    def test_warehouse_page_renders_soul_fusion_requirements_for_current_item(self, manor_with_user):
        manor, client = manor_with_user
        guest_template = GuestTemplate.objects.create(
            key="view_soul_fusion_guest",
            name="魂器候选门客",
            rarity=GuestRarity.BLUE,
            archetype=GuestArchetype.CIVIL,
            base_attack=100,
            base_intellect=140,
            base_defense=90,
            base_agility=95,
            base_luck=70,
            base_hp=1200,
            default_gender="male",
            default_morality=60,
        )
        guest = Guest.objects.create(
            manor=manor,
            template=guest_template,
            status=GuestStatus.IDLE,
            level=66,
        )
        soul_container = ItemTemplate.objects.create(
            key="view_soul_fusion_container",
            name="蓝魂容器",
            effect_type=ItemTemplate.EffectType.TOOL,
            is_usable=True,
            effect_payload={
                "action": "soul_fusion",
                "min_level": 60,
                "allowed_rarities": ["blue", "purple"],
            },
        )
        InventoryItem.objects.create(
            manor=manor,
            template=soul_container,
            quantity=1,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )

        response = client.get(reverse("gameplay:warehouse"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'data-soul-fusion-min-level="60"' in body
        assert 'data-soul-fusion-rarities="blue,purple"' in body
        assert f'data-guest-id="{guest.id}"' in body
        assert 'data-guest-level="66"' in body
        assert 'data-guest-rarity="blue"' in body

    def test_warehouse_page_loads_external_page_script_without_inline_handlers(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:warehouse"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/warehouse-page.js" in body
        assert "const warehouseModalState" not in body
        assert "onclick=" not in body
        assert "onchange=" not in body

    def test_warehouse_page_renders_confirm_metadata_for_repeatable_guest_scroll(self, manor_with_user):
        manor, client = manor_with_user
        scroll_template = ItemTemplate.objects.create(
            key="view_edward_blue_guest_scroll",
            name="蓝色爱德华召唤卷轴",
            effect_type=ItemTemplate.EffectType.TOOL,
            is_usable=True,
            effect_payload={
                "action": "summon_guest",
                "required_items": {"gold_bar": 50},
                "confirm_title": "使用确认",
                "confirm_text": "确认使用「蓝色爱德华召唤卷轴」？将消耗 50 根金条，并获得 1 名蓝色门客「爱德华」。",
                "confirm_ok_text": "确认使用",
                "choices": [{"template_key": "orig_edward_blue", "weight": 100}],
            },
        )
        InventoryItem.objects.create(
            manor=manor,
            template=scroll_template,
            quantity=1,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )

        response = client.get(reverse("gameplay:warehouse"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'data-confirm-title="使用确认"' in body
        assert 'data-confirm-ok-text="确认使用"' in body
        assert "爱德华召唤卷轴" in body
        assert "消耗 50 根金条" in body

    def test_recruitment_hall_page(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:recruitment_hall"))
        assert response.status_code == 200
        assert "pools" in response.context
        assert "candidates_payload" in response.context
        assert "candidate_count" in response.context
        assert "guests" not in response.context
        assert "capacity" not in response.context
        assert "available_gears" not in response.context

    def test_recruitment_hall_page_loads_external_page_script_without_inline_logic(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:recruitment_hall"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/recruitment-hall.js" in body
        assert "const CHUNK_SIZE" not in body

    def test_recruitment_hall_candidate_header_does_not_render_select_all(self, manor_with_user):
        manor, client = manor_with_user
        pool = RecruitmentPool.objects.create(
            key=f"header_select_all_pool_{manor.id}",
            name="候选卡池",
            cooldown_seconds=60,
            draw_count=1,
        )
        template = GuestTemplate.objects.create(
            key=f"header_select_all_guest_{manor.id}",
            name="候选门客",
            rarity=GuestRarity.GRAY,
            archetype=GuestArchetype.CIVIL,
        )
        RecruitmentCandidate.objects.create(
            manor=manor,
            pool=pool,
            template=template,
            display_name="候选门客",
            rarity=GuestRarity.GRAY,
            archetype=GuestArchetype.CIVIL,
        )

        response = client.get(reverse("gameplay:recruitment_hall"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'id="candidate-select-all-top"' not in body

    def test_recruitment_hall_pool_images_use_uncropped_display_class(self, manor_with_user):
        _manor, client = manor_with_user
        RecruitmentPool.objects.update_or_create(
            key="cunmu",
            defaults={
                "name": "村募",
                "description": "地方招募",
                "cooldown_seconds": 60,
                "tier": RecruitmentPool.Tier.CUNMU,
            },
        )

        response = client.get(reverse("gameplay:recruitment_hall"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "recruit-pool-image" in body
        assert "pool-img-container" in body
        assert 'class="w-full h-full object-cover"' not in body

    def test_recruitment_hall_pool_images_keep_desktop_left_rail_layout(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:recruitment_hall"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "padding-left: 13.5rem" in body
        assert "min-height: 11.6rem" in body
        assert "border-right: 1px solid #e5e7eb" in body

    def test_recruitment_hall_pool_images_do_not_use_padded_backdrop(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:recruitment_hall"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "padding: 0.4rem" not in body
        assert "linear-gradient(180deg, #f8fafc 0%, #eef2f7 100%)" not in body
        assert "object-fit: cover" in body

    def test_recruitment_hall_pool_grid_uses_two_desktop_columns(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:recruitment_hall"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "grid-cols-1 md:grid-cols-2" in body
        assert "lg:grid-cols-3" not in body
        assert "xl:grid-cols-4" not in body

    def test_recruitment_hall_page_active_recruitment_has_refresh_endpoint(self, manor_with_user):
        manor, client = manor_with_user
        pool = RecruitmentPool.objects.create(
            key=f"hall_refresh_pool_{manor.id}",
            name="候选卡池",
            cooldown_seconds=60,
            draw_count=2,
        )
        GuestRecruitment.objects.create(
            manor=manor,
            pool=pool,
            cost={},
            draw_count=2,
            duration_seconds=60,
            status=GuestRecruitment.Status.PENDING,
            complete_at=timezone.now() + timezone.timedelta(minutes=1),
        )

        response = client.get(reverse("gameplay:recruitment_hall"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'data-refresh="1"' in body
        assert reverse("gameplay:refresh_recruitment_hall_api") in body
        assert 'data-refresh-method="post"' in body

    def test_recruitment_hall_page_syncs_resources_before_loading_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"sync": 0, "context": 0}

        def _fake_sync(*_args, **_kwargs):
            calls["sync"] += 1

        def _fake_context(*_args, **_kwargs):
            calls["context"] += 1
            return {
                "manor": manor,
                "pools": [],
                "candidates_payload": [],
                "candidate_count": 0,
                "records": [],
                "magnifying_glass_items": [],
            }

        monkeypatch.setattr("gameplay.views.inventory.project_resource_production_for_read", _fake_sync)
        monkeypatch.setattr("gameplay.views.inventory.get_recruitment_hall_context", _fake_context)

        response = client.get(reverse("gameplay:recruitment_hall"))
        assert response.status_code == 200
        assert calls == {"sync": 1, "context": 1}
