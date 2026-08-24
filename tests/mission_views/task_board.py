from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, MissionExtraAttempt, MissionRun, MissionTemplate
from guests.models import GuestTemplate


@pytest.mark.django_db
class TestTaskBoardPage:
    def test_task_board_page(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:tasks"))
        assert response.status_code == 200
        assert "missions" in response.context

    def test_task_board_page_loads_external_page_script_without_inline_logic(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/dashboard.js" in body
        assert "js/tasks-page.js" in body
        assert "const maxSquadSize" not in body

    def test_task_board_hides_active_runs_summary(self, manor_with_user):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_active_run",
            name="进行中任务",
            difficulty=MissionTemplate.Difficulty.JUNIOR,
            daily_limit=3,
        )
        run = MissionRun.objects.create(
            manor=manor,
            mission=mission,
            status=MissionRun.Status.ACTIVE,
            travel_time=300,
            return_at=timezone.now() + timedelta(minutes=10),
        )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        refresh_url = reverse("gameplay:refresh_mission_runs_api")
        assert "当前出征" not in body
        assert "出征：进行中任务" not in body
        assert "防守：进行中任务" not in body
        assert f'data-refresh-url="{refresh_url}"' not in body
        assert 'data-refresh-method="post"' not in body
        assert reverse("gameplay:mission_retreat", kwargs={"pk": run.pk}) not in body

    def test_task_board_tolerates_resource_sync_error(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.views.mission_page_context.project_manor_activity_for_read",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("sync failed")),
        )

        response = client.get(reverse("gameplay:tasks"))
        assert response.status_code == 200
        assert "missions" in response.context

    def test_task_board_with_mission_selected(self, manor_with_user):
        _manor, client = manor_with_user
        response = client.get(reverse("gameplay:tasks") + "?mission=huashan_lunjian")
        assert response.status_code == 200

    def test_task_board_renders_mission_card_confirmation_metadata(self, manor_with_user):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"task_board_card_confirm_{manor.id}",
            name="任务卡确认任务",
            difficulty=MissionTemplate.Difficulty.JUNIOR,
            daily_limit=3,
        )
        card_template, _ = ItemTemplate.objects.get_or_create(
            key="mission_card",
            defaults={"name": "任务卡", "effect_type": ItemTemplate.EffectType.TOOL},
        )
        InventoryItem.objects.update_or_create(
            manor=manor,
            template=card_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": 3},
        )
        MissionExtraAttempt.objects.create(
            manor=manor,
            mission=mission,
            date=timezone.localdate(),
            extra_count=2,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        assert response.context["selected_mission_card_uses"] == 2
        assert response.context["mission_card_daily_limit"] == 5
        body = response.content.decode("utf-8")
        assert "任务卡 2 / 5" in body
        assert 'class="inline js-mission-card-form"' in body
        assert 'data-mission-name="任务卡确认任务"' in body
        assert 'data-card-count="3"' in body
        assert 'data-used-count="2"' in body
        assert 'data-daily-limit="5"' in body

    def test_task_board_disables_mission_card_button_at_daily_limit(self, manor_with_user):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"task_board_card_limit_{manor.id}",
            name="任务卡满额任务",
            difficulty=MissionTemplate.Difficulty.JUNIOR,
            daily_limit=3,
        )
        card_template, _ = ItemTemplate.objects.get_or_create(
            key="mission_card",
            defaults={"name": "任务卡", "effect_type": ItemTemplate.EffectType.TOOL},
        )
        InventoryItem.objects.update_or_create(
            manor=manor,
            template=card_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": 1},
        )
        MissionExtraAttempt.objects.create(
            manor=manor,
            mission=mission,
            date=timezone.localdate(),
            extra_count=5,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "任务卡 5 / 5" in body
        assert re.search(r'class="tw-btn-add"[^>]+disabled', body)
        assert "该任务今日最多使用5张任务卡" in body

    def test_task_board_hides_mission_card_form_when_mission_disables_cards(self, manor_with_user):
        manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key=f"task_board_card_disabled_{manor.id}",
            name="禁用任务卡任务",
            difficulty=MissionTemplate.Difficulty.JUNIOR,
            daily_limit=1,
            mission_card_daily_limit=0,
        )
        card_template, _ = ItemTemplate.objects.get_or_create(
            key="mission_card",
            defaults={"name": "任务卡", "effect_type": ItemTemplate.EffectType.TOOL},
        )
        InventoryItem.objects.update_or_create(
            manor=manor,
            template=card_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": 3},
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        assert response.context["mission_card_daily_limit"] == 0
        assert response.context["selected_daily_limit"] == 1
        body = response.content.decode("utf-8")
        assert "本任务不可使用任务卡" in body
        assert "js-mission-card-form" not in body

    def test_task_board_renders_non_silver_drops_as_drop_icons(self, manor_with_user, settings, tmp_path):
        _manor, client = manor_with_user
        settings.MEDIA_ROOT = tmp_path
        image_item = ItemTemplate.objects.create(
            key="task_board_drop_icon_image",
            name="图标物品",
            image=SimpleUploadedFile("drop-icon.png", b"drop-image", content_type="image/png"),
            rarity="blue",
        )
        placeholder_item = ItemTemplate.objects.create(
            key="task_board_drop_icon_placeholder",
            name="占位物品",
            rarity="green",
        )
        mission = MissionTemplate.objects.create(
            key="task_board_drop_icon_mission",
            name="掉落图标任务",
            difficulty=MissionTemplate.Difficulty.JUNIOR,
            drop_table={
                "silver": 500,
                image_item.key: 2,
                placeholder_item.key: 0.25,
            },
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert image_item.image.url in body
        assert 'class="tw-drop-icon rarity-blue"' in body
        assert '<span class="tw-drop-count">×2</span>' in body
        assert '<span class="tw-drop-placeholder" aria-hidden="true">占</span>' in body
        assert ">银两 ×500<" in body

    def test_task_board_renders_choice_pool_items_as_individual_drop_icons(self, manor_with_user, settings, tmp_path):
        _manor, client = manor_with_user
        settings.MEDIA_ROOT = tmp_path
        helmet = ItemTemplate.objects.create(
            key="task_board_choice_pool_helmet",
            name="青龙盔图纸",
            image=SimpleUploadedFile("qinglong-helmet.png", b"helmet", content_type="image/png"),
            rarity="blue",
        )
        armor = ItemTemplate.objects.create(
            key="task_board_choice_pool_armor",
            name="青龙甲图纸",
            image=SimpleUploadedFile("qinglong-armor.png", b"armor", content_type="image/png"),
            rarity="blue",
        )
        mission = MissionTemplate.objects.create(
            key="task_board_choice_pool_icons",
            name="选择池图标任务",
            difficulty=MissionTemplate.Difficulty.INTERMEDIATE,
            drop_table={
                "blueprint_top_blue": {
                    "chance": 0.45,
                    "choices": [helmet.key, armor.key],
                }
            },
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert helmet.image.url in body
        assert armor.image.url in body
        assert 'title="青龙盔图纸 ×1"' in body
        assert 'title="青龙甲图纸 ×1"' in body
        assert "青龙盔图纸/青龙甲图纸" not in body

    def test_task_board_displays_fixed_and_probability_drops_in_one_preview_grid(self, manor_with_user):
        _manor, client = manor_with_user
        probability_item = ItemTemplate.objects.create(
            key="task_board_probability_preview_item",
            name="概率预览物品",
            rarity="green",
        )
        mission = MissionTemplate.objects.create(
            key="task_board_combined_drop_preview",
            name="合并掉落预览任务",
            difficulty=MissionTemplate.Difficulty.JUNIOR,
            drop_table={"silver": 500},
            probability_drop_table={probability_item.key: 1},
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert ">银两 ×500<" in body
        assert 'title="概率预览物品 ×1"' in body
        assert "概率掉落：" not in body
        assert body.count('class="tw-drop-grid"') == 1

    def test_task_board_empty_fixed_drops_keeps_heading_without_unconfigured_copy(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_empty_fixed_drops",
            name="无固定掉落任务",
            difficulty=MissionTemplate.Difficulty.JUNIOR,
            drop_table={},
            probability_drop_table={},
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "掉落预览" in body
        assert "未配置" not in body

    def test_task_board_selects_matching_difficulty_tab_for_selected_mission(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_selected_intermediate",
            name="指定中级任务",
            difficulty=MissionTemplate.Difficulty.INTERMEDIATE,
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert '<button class="tw-trade-tab active" data-tab="intermediate">中级任务</button>' in body
        assert '<div id="tab-intermediate" class="mission-tab-content active">' in body

    def test_task_board_preserves_requested_tab_without_selected_mission(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:tasks") + "?tab=advanced")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert '<button class="tw-trade-tab active" data-tab="advanced">高级任务</button>' in body
        assert '<div id="tab-advanced" class="mission-tab-content active">' in body

    def test_task_board_detail_close_returns_to_selected_mission_tab(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_close_advanced",
            name="关闭回高级任务",
            difficulty=MissionTemplate.Difficulty.ADVANCED,
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        expected_url = f'{reverse("gameplay:tasks")}?tab=advanced'
        assert f'href="{expected_url}" class="tw-btn-secondary tw-btn-sm">关闭</a>' in body
        assert f'href="{expected_url}" class="tw-btn-secondary">取消</a>' in body

    def test_task_board_mission_names_link_to_details_for_every_difficulty(self, manor_with_user):
        _manor, client = manor_with_user
        missions = [
            ("task_board_name_link_junior", "名称链接初级", "junior"),
            ("task_board_name_link_intermediate", "名称链接中级", "intermediate"),
            ("task_board_name_link_advanced", "名称链接高级", "advanced"),
        ]

        for key, name, difficulty in missions:
            MissionTemplate.objects.create(
                key=key,
                name=name,
                difficulty=difficulty,
                daily_limit=3,
            )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        for key, name, _difficulty in missions:
            pattern = rf'<a[^>]+href="\?mission={key}"[^>]*>{name}</a>'
            assert re.search(pattern, body)

    def test_task_board_only_lists_missions_available_for_current_weekday(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        MissionTemplate.objects.create(
            key="task_board_weekly_monday",
            name="周一出现任务",
            difficulty="junior",
            available_weekdays=[1],
        )
        MissionTemplate.objects.create(
            key="task_board_weekly_tuesday",
            name="周二出现任务",
            difficulty="junior",
            available_weekdays=[2],
        )
        MissionTemplate.objects.create(
            key="task_board_weekly_everyday",
            name="每日出现任务",
            difficulty="junior",
            available_weekdays=[],
        )
        monkeypatch.setattr(
            "gameplay.views.mission_helpers.timezone.localdate",
            lambda: date(2026, 5, 18),
        )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "周一出现任务" in body
        assert "每日出现任务" in body
        assert "周二出现任务" not in body

    def test_task_board_mission_name_links_do_not_render_underlines(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_name_link_no_underline",
            name="名称无下划线",
            difficulty="junior",
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        pattern = (
            rf'<a[^>]+class="[^"]*tw-mission-name-link[^"]*no-underline[^"]*hover:no-underline[^"]*"[^>]+'
            rf'href="\?mission={mission.key}"[^>]*>{mission.name}</a>'
        )
        assert re.search(pattern, body)

    def test_task_board_displays_entry_cost_and_disables_launch_when_missing(self, manor_with_user):
        manor, client = manor_with_user
        token = ItemTemplate.objects.create(
            key="task_board_entry_token",
            name="任务入场令",
            effect_type="resource",
            rarity="blue",
            tradeable=False,
            price=0,
            storage_space=1,
            is_usable=False,
        )
        mission = MissionTemplate.objects.create(
            key="task_board_entry_cost",
            name="入场消耗展示任务",
            difficulty="advanced",
            entry_cost={token.key: 2},
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "入场消耗" in body
        assert "任务入场令 ×2" in body
        assert "持有 0" in body
        assert "信物不足" in body

        InventoryItem.objects.create(
            manor=manor,
            template=token,
            quantity=2,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        body = response.content.decode("utf-8")
        assert "持有 2" in body
        assert "信物不足" not in body

    def test_task_board_uses_responsive_mission_table_column_classes(self, manor_with_user):
        _manor, client = manor_with_user
        MissionTemplate.objects.create(
            key="task_board_responsive_columns",
            name="移动端列宽任务",
            difficulty="junior",
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert 'class="tw-mission-name-col"' in body
        assert 'class="tw-mission-meta-col text-center"' in body
        assert 'class="tw-mission-action-col text-center"' in body

    def test_task_board_all_difficulty_tabs_share_junior_table_markup(self, manor_with_user):
        _manor, client = manor_with_user
        missions = [
            ("task_board_same_markup_junior", "初级统一任务", "junior"),
            ("task_board_same_markup_intermediate", "中级统一任务", "intermediate"),
            ("task_board_same_markup_advanced", "高级统一任务", "advanced"),
        ]

        for key, name, difficulty in missions:
            MissionTemplate.objects.create(
                key=key,
                name=name,
                difficulty=difficulty,
                daily_limit=3,
            )

        response = client.get(reverse("gameplay:tasks"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert body.count('class="tw-mission-name-col">任务名称</th>') == 3
        assert body.count('class="tw-mission-meta-col text-center">类型</th>') == 3
        assert body.count('class="tw-mission-meta-col text-center">今日剩余</th>') == 3
        assert body.count('class="tw-mission-action-col text-center">操作</th>') == 3
        for key, name, _difficulty in missions:
            pattern = (
                rf'<td class="tw-mission-name-col">\s*'
                rf'<a class="tw-mission-name-link font-bold text-text-primary no-underline hover:no-underline" '
                rf'href="\?mission={key}">{name}</a>'
            )
            assert re.search(pattern, body)

    def test_task_board_selected_mission_hides_enemy_guest_names_below_cards(
        self,
        manor_with_user,
    ):
        _manor, client = manor_with_user
        GuestTemplate.objects.create(
            key="task_board_enemy_gray",
            name="灰阶敌将",
            archetype="military",
            rarity="gray",
        )
        GuestTemplate.objects.create(
            key="task_board_enemy_black",
            name="黑阶敌将",
            archetype="civil",
            rarity="black",
        )
        MissionTemplate.objects.create(
            key="task_board_enemy_rarity",
            name="测试敌方颜色",
            enemy_guests=[
                {"key": "task_board_enemy_gray", "label": "灰阶敌将"},
                {"key": "task_board_enemy_black", "label": "黑阶敌将"},
            ],
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks") + "?mission=task_board_enemy_rarity")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert body.count("tw-enemy-entry") >= 2
        assert "tw-enemy-meta" not in body
        assert '<span class="tw-guest-name-sm rarity-text-gray">灰阶敌将</span>' not in body
        assert '<span class="tw-guest-name-sm rarity-text-black">黑阶敌将</span>' not in body

    def test_task_board_selected_mission_omits_dynamic_travel_time_note(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_without_travel_time_note",
            name="隐藏行军提示任务",
            difficulty="junior",
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "双程时间按行军速度动态计算，最低 300 秒。" not in body

    def test_task_board_selected_defense_mission_omits_fixed_arrival_note(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_without_fixed_arrival_note",
            name="隐藏来袭提示任务",
            difficulty="junior",
            daily_limit=3,
            is_defense=True,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "敌军来袭时间固定，等待战报回传。" not in body

    def test_task_board_selected_high_end_mission_hides_elite_enemy_name_row(
        self,
        manor_with_user,
        mission_templates,
    ):
        _manor, client = manor_with_user
        GuestTemplate.objects.update_or_create(
            key="task_barbarian_chanyu",
            defaults={
                "name": "蛮族单于",
                "archetype": "military",
                "rarity": "orange",
            },
        )

        response = client.get(reverse("gameplay:tasks") + "?mission=manzu_ruqin")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "单于" in body
        assert "tw-enemy-entry" in body
        assert "tw-enemy-meta" not in body

    def test_task_board_selected_mission_groups_detail_sections_with_shared_frames(self, manor_with_user):
        _manor, client = manor_with_user
        mission = MissionTemplate.objects.create(
            key="task_board_detail_sections",
            name="分区样式任务",
            description="测试详情分区边框",
            difficulty="junior",
            daily_limit=3,
        )

        response = client.get(reverse("gameplay:tasks") + f"?mission={mission.key}")

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "任务简介" in body
        assert body.count("tw-task-detail-section") >= 3
