"""
打工系统视图测试
"""

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from core.exceptions import WorkError
from gameplay.models import ItemTemplate, WorkAssignment, WorkTemplate
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate


@pytest.mark.django_db
class TestWorkViews:
    """打工系统视图测试"""

    @staticmethod
    def _create_work_data(
        manor,
        suffix: str,
        *,
        tier: str = WorkTemplate.Tier.JUNIOR,
        display_order: int = 0,
    ) -> tuple[Guest, WorkTemplate]:
        for key, name in (
            ("work_chest_small", "打工宝箱（小）"),
            ("work_chest_medium", "打工宝箱（中）"),
            ("work_chest_large", "打工宝箱（大）"),
        ):
            ItemTemplate.objects.get_or_create(
                key=key,
                defaults={
                    "name": name,
                    "effect_type": ItemTemplate.EffectType.LOOT_BOX,
                },
            )
        guest_template = GuestTemplate.objects.create(
            key=f"view_work_guest_tpl_{suffix}_{manor.id}",
            name=f"打工门客模板{suffix}",
            archetype=GuestArchetype.CIVIL,
            rarity=GuestRarity.GRAY,
        )
        guest = Guest.objects.create(
            manor=manor,
            template=guest_template,
            status=GuestStatus.IDLE,
        )
        work_template = WorkTemplate.objects.create(
            key=f"view_work_template_{suffix}_{manor.id}",
            name=f"打工模板{suffix}",
            tier=tier,
            required_level=1,
            required_force=0,
            required_intellect=0,
            reward_silver=100,
            work_duration=3600,
            display_order=display_order,
        )
        return guest, work_template

    def test_work_page(self, manor_with_user):
        """打工页面"""
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:work"))
        assert response.status_code == 200
        assert "works" in response.context
        body = response.content.decode("utf-8")
        assert "js/work-page.js" in body
        assert "document.querySelectorAll('.recall-form')" not in body

    def test_work_page_shows_assignment_in_matching_work_card(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "inline_assignment")
        guest.status = GuestStatus.WORKING
        guest.save(update_fields=["status"])
        WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() + timezone.timedelta(minutes=30),
        )

        response = client.get(reverse("gameplay:work"))
        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "执行门客" in body
        assert guest.display_name in body
        assert "打工中 (" not in body
        assert reverse("gameplay:refresh_work_assignments_api") in body
        assert 'data-refresh-method="post"' in body

    def test_work_page_does_not_refresh_overdue_assignment_or_release_guest(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "expired_assignment")
        guest.status = GuestStatus.WORKING
        guest.save(update_fields=["status"])
        assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() - timezone.timedelta(minutes=5),
        )

        response = client.get(reverse("gameplay:work"))

        assert response.status_code == 200
        assignment.refresh_from_db()
        guest.refresh_from_db()
        assert assignment.status == WorkAssignment.Status.WORKING
        assert assignment.reward_claimed is False
        assert guest.status == GuestStatus.WORKING

    def test_work_tier_filter(self, manor_with_user):
        """打工等级过滤"""
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:work") + "?tier=senior")
        assert response.status_code == 200
        assert response.context["current_tier"] == "senior"

    def test_work_page_shows_tier_action_point_cost(self, manor_with_user):
        manor, client = manor_with_user
        self._create_work_data(manor, "senior_action_cost", tier=WorkTemplate.Tier.SENIOR)

        response = client.get(reverse("gameplay:work") + "?tier=senior")

        assert response.status_code == 200
        assert response.context["works"][0].action_point_cost == 30
        body = response.content.decode("utf-8")
        assert "行动力" in body
        assert "30 点" in body

    def test_work_page_uses_explicit_read_helper(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"prepared": 0}

        monkeypatch.setattr(
            "gameplay.views.work.get_prepared_manor_for_read",
            lambda request, **kwargs: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
        )
        monkeypatch.setattr(
            "gameplay.views.work.get_work_page_context",
            lambda current_manor, *, current_tier, page: (
                {
                    "work_tiers": [],
                    "current_tier": current_tier,
                    "current_tier_config": {"key": current_tier, "name": "测试"},
                    "works": [],
                    "page_obj": [],
                    "is_paginated": False,
                }
                if current_manor is manor and page == 1
                else {}
            ),
        )

        response = client.get(reverse("gameplay:work"))

        assert response.status_code == 200
        assert calls["prepared"] == 1

    def test_work_page_uses_selector_context(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        calls = {"selector": 0}

        monkeypatch.setattr("gameplay.views.work.get_prepared_manor_for_read", lambda request, **kwargs: manor)

        def _fake_selector(current_manor, *, current_tier, page):
            calls["selector"] += 1
            assert current_manor is manor
            assert current_tier == "senior"
            assert page == 2
            return {
                "work_tiers": [{"key": "senior", "name": "高级工作区"}],
                "current_tier": "senior",
                "current_tier_config": {"key": "senior", "name": "高级工作区"},
                "works": ["work-a"],
                "page_obj": [],
                "is_paginated": True,
            }

        monkeypatch.setattr("gameplay.views.work.get_work_page_context", _fake_selector)

        response = client.get(reverse("gameplay:work") + "?tier=senior&page=2")

        assert response.status_code == 200
        assert calls["selector"] == 1
        assert response.context["current_tier"] == "senior"
        assert response.context["works"] == ["work-a"]

    def test_work_page_paginates_four_works_per_tier(self, manor_with_user):
        manor, client = manor_with_user
        for index in range(5):
            self._create_work_data(
                manor,
                f"page_{index}",
                tier=WorkTemplate.Tier.SENIOR,
                display_order=index + 1,
            )

        response = client.get(reverse("gameplay:work") + "?tier=senior")
        assert response.status_code == 200
        assert len(response.context["works"]) == 4
        assert response.context["page_obj"].number == 1
        assert response.context["is_paginated"] is True
        body = response.content.decode("utf-8")
        assert "打工模板page_0" in body
        assert "打工模板page_3" in body
        assert "打工模板page_4" not in body
        assert "?tier=senior&page=2" in body

        second_page = client.get(reverse("gameplay:work") + "?tier=senior&page=2")
        assert second_page.status_code == 200
        assert len(second_page.context["works"]) == 1
        assert second_page.context["page_obj"].number == 2
        second_body = second_page.content.decode("utf-8")
        assert "打工模板page_4" in second_body
        assert "打工模板page_0" not in second_body

    def test_work_page_shows_requirements_and_qualification_options(self, manor_with_user):
        manor, client = manor_with_user
        eligible, work_template = self._create_work_data(manor, "requirements")
        eligible.custom_name = "恰好胜任"
        eligible.level = 16
        eligible.intellect = 105
        eligible.agility = 60
        eligible.save(update_fields=["custom_name", "level", "intellect", "agility"])
        ineligible = Guest.objects.create(
            manor=manor,
            template=eligible.template,
            custom_name="仍需培养",
            level=16,
            intellect=100,
            agility=55,
            status=GuestStatus.IDLE,
        )
        work_template.required_level = 16
        work_template.required_intellect = 105
        work_template.required_agility = 60
        work_template.save(update_fields=["required_level", "required_intellect", "required_agility"])

        response = client.get(reverse("gameplay:work"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "要求：" in body
        assert "等级 16" in body
        assert "智力 ≥ 105" in body
        assert "敏捷 ≥ 60" in body
        assert "可派遣" in body
        assert "暂不符合" in body
        assert "tw-work-card--no-match" not in body
        assert f'<option value="{eligible.pk}">' in body
        assert f'<option value="{ineligible.pk}" disabled>' in body
        assert "智力 100/105，尚缺 5" in body
        assert "敏捷 55/60，尚缺 5" in body

    def test_work_page_shows_only_three_closest_guests_when_none_are_eligible(self, manor_with_user):
        manor, client = manor_with_user
        first_guest, work_template = self._create_work_data(manor, "closest")
        first_guest.custom_name = "等级不足"
        first_guest.save(update_fields=["custom_name"])
        work_template.required_level = 10
        work_template.required_force = 100
        work_template.save(update_fields=["required_level", "required_force"])
        for name, force in (("缺一", 99), ("缺二", 98), ("缺三", 97), ("缺十", 90)):
            Guest.objects.create(
                manor=manor,
                template=first_guest.template,
                custom_name=name,
                level=10,
                force=force,
                status=GuestStatus.IDLE,
            )

        response = client.get(reverse("gameplay:work"))

        body = response.content.decode("utf-8")
        closest_block = body.split('data-closest-ineligible="true"', maxsplit=1)[1].split("</section>", maxsplit=1)[0]
        assert "最接近要求" in closest_block
        assert "缺一" in closest_block
        assert "缺二" in closest_block
        assert "缺三" in closest_block
        assert "缺十" not in closest_block
        assert "等级不足" not in closest_block
        assert "tw-work-card--no-match" in body
        assert 'type="submit" class="tw-btn-primary" disabled' in body

    def test_work_page_keeps_requirements_visible_for_active_assignment(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "active_requirements")
        work_template.required_force = 100
        work_template.required_defense = 75
        work_template.save(update_fields=["required_force", "required_defense"])
        guest.status = GuestStatus.WORKING
        guest.save(update_fields=["status"])
        WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() + timezone.timedelta(minutes=30),
        )

        response = client.get(reverse("gameplay:work"))

        body = response.content.decode("utf-8")
        assert "执行门客" in body
        assert "武力 ≥ 100" in body
        assert "防御 ≥ 75" in body

    def test_assign_work_post_cannot_bypass_attribute_requirements(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "post_requirements")
        manor.action_points = 321
        manor.action_points_updated_at = timezone.now()
        manor.save(update_fields=["action_points", "action_points_updated_at"])
        guest.level = 10
        guest.agility = 99
        guest.save(update_fields=["level", "agility"])
        work_template.required_level = 10
        work_template.required_agility = 100
        work_template.save(update_fields=["required_level", "required_agility"])

        response = client.post(
            reverse("gameplay:assign_work"),
            {"guest_id": guest.pk, "work_key": work_template.key},
        )

        assert response.status_code == 302
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        assert any("敏捷不足，需要 100，当前 99" in message for message in messages)
        manor.refresh_from_db()
        assert manor.action_points == 321
        assert WorkAssignment.objects.filter(guest=guest).exists() is False

    def test_assign_work_redirects_back_to_current_page_when_next_provided(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "assign_next", tier=WorkTemplate.Tier.SENIOR)
        next_url = reverse("gameplay:work") + "?tier=senior&page=2"

        response = client.post(
            reverse("gameplay:assign_work"),
            {"guest_id": guest.id, "work_key": work_template.key, "next": next_url},
        )

        assert response.status_code == 302
        assert response.url == next_url

    def test_recall_work_view_overdue_assignment_redirects_to_claim_reward(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "recall_expired")
        guest.status = GuestStatus.WORKING
        guest.save(update_fields=["status"])
        assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        response = client.post(reverse("gameplay:recall_work", kwargs={"pk": assignment.pk}))

        assert response.status_code == 302
        assert response.url == reverse("gameplay:work")
        assignment.refresh_from_db()
        guest.refresh_from_db()
        assert assignment.status == WorkAssignment.Status.COMPLETED
        assert assignment.reward_claimed is False
        assert guest.status == GuestStatus.IDLE
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("已完成，请先领取报酬" in message for message in messages)

    def test_recall_work_redirects_back_to_current_page_when_next_provided(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "recall_next", tier=WorkTemplate.Tier.SENIOR)
        assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() + timezone.timedelta(minutes=30),
        )
        next_url = reverse("gameplay:work") + "?tier=senior&page=2"

        response = client.post(
            reverse("gameplay:recall_work", kwargs={"pk": assignment.pk}),
            {"next": next_url},
        )

        assert response.status_code == 302
        assert response.url == next_url

    def test_work_page_keeps_claim_entry_visible_when_same_work_has_new_working_assignment(self, manor_with_user):
        manor, client = manor_with_user
        completed_guest, work_template = self._create_work_data(manor, "claim_and_working")
        working_guest_template = GuestTemplate.objects.create(
            key=f"view_work_guest_tpl_claim_and_working_extra_{manor.id}",
            name="打工门客模板claim_and_working_extra",
            archetype=GuestArchetype.CIVIL,
            rarity=GuestRarity.GRAY,
        )
        working_guest = Guest.objects.create(
            manor=manor,
            template=working_guest_template,
            status=GuestStatus.WORKING,
        )
        completed_assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=completed_guest,
            work_template=work_template,
            status=WorkAssignment.Status.COMPLETED,
            complete_at=timezone.now() - timezone.timedelta(minutes=10),
        )
        WorkAssignment.objects.create(
            manor=manor,
            guest=working_guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() + timezone.timedelta(minutes=20),
        )

        response = client.get(reverse("gameplay:work"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert completed_guest.display_name in body
        assert working_guest.display_name in body
        assert reverse("gameplay:claim_work_reward", kwargs={"pk": completed_assignment.pk}) in body
        assert "已完成，可领取报酬" in body
        assert "当前另有门客正在打工" in body

    def test_claim_work_reward_redirects_back_to_current_page_when_next_provided(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "claim_next", tier=WorkTemplate.Tier.SENIOR)
        assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.COMPLETED,
            complete_at=timezone.now(),
        )
        next_url = reverse("gameplay:work") + "?tier=senior&page=2"

        response = client.post(
            reverse("gameplay:claim_work_reward", kwargs={"pk": assignment.pk}),
            {"next": next_url},
        )

        assert response.status_code == 302
        assert response.url == next_url

    def test_claim_work_reward_view_refreshes_overdue_assignment_before_claim(self, manor_with_user):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "claim_expired")
        guest.status = GuestStatus.WORKING
        guest.save(update_fields=["status"])
        manor.silver = 0
        manor.save(update_fields=["silver"])
        assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() - timezone.timedelta(minutes=1),
        )

        response = client.post(reverse("gameplay:claim_work_reward", kwargs={"pk": assignment.pk}))

        assert response.status_code == 302
        assert response.url == reverse("gameplay:work")
        assignment.refresh_from_db()
        guest.refresh_from_db()
        manor.refresh_from_db()
        assert assignment.status == WorkAssignment.Status.COMPLETED
        assert assignment.reward_claimed is True
        assert guest.status == GuestStatus.IDLE
        assert manor.silver == work_template.reward_silver
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("完成打工，获得银两" in message for message in messages)

    def test_assign_work_known_error_shows_message(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "assign_known")

        monkeypatch.setattr(
            "gameplay.views.work.assign_guest_to_work_with_refresh",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(WorkError("work blocked")),
        )

        response = client.post(
            reverse("gameplay:assign_work"),
            {"guest_id": guest.id, "work_key": work_template.key},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:work")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("work blocked" in m for m in messages)

    def test_assign_work_value_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "assign_value_error")

        monkeypatch.setattr(
            "gameplay.views.work.assign_guest_to_work_with_refresh",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad payload")),
        )

        with pytest.raises(ValueError, match="bad payload"):
            client.post(
                reverse("gameplay:assign_work"),
                {"guest_id": guest.id, "work_key": work_template.key},
            )

    def test_assign_work_database_error_does_not_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "assign_exc")

        monkeypatch.setattr(
            "gameplay.views.work.assign_guest_to_work_with_refresh",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:assign_work"),
            {"guest_id": guest.id, "work_key": work_template.key},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:work")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_assign_work_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "assign_runtime")

        monkeypatch.setattr(
            "gameplay.views.work.assign_guest_to_work_with_refresh",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                reverse("gameplay:assign_work"),
                {"guest_id": guest.id, "work_key": work_template.key},
            )

    def test_assign_work_rejects_invalid_guest_id(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        _guest, work_template = self._create_work_data(manor, "invalid_guest_id")
        called = {"count": 0}

        def _unexpected_assign(*_args, **_kwargs):
            called["count"] += 1

        monkeypatch.setattr("gameplay.views.work.assign_guest_to_work_with_refresh", _unexpected_assign)

        response = client.post(
            reverse("gameplay:assign_work"),
            {"guest_id": "abc", "work_key": work_template.key},
        )
        assert response.status_code == 302
        assert response.url == reverse("gameplay:work")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("参数错误" in m for m in messages)
        assert called["count"] == 0

    def test_recall_work_database_error_does_not_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "recall_exc")
        assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.WORKING,
            complete_at=timezone.now() + timezone.timedelta(minutes=5),
        )

        monkeypatch.setattr(
            "gameplay.views.work.recall_guest_from_work_with_refresh",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:recall_work", kwargs={"pk": assignment.pk}))
        assert response.status_code == 302
        assert response.url == reverse("gameplay:work")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_claim_work_reward_database_error_does_not_500(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        guest, work_template = self._create_work_data(manor, "claim_exc")
        assignment = WorkAssignment.objects.create(
            manor=manor,
            guest=guest,
            work_template=work_template,
            status=WorkAssignment.Status.COMPLETED,
            complete_at=timezone.now(),
        )

        monkeypatch.setattr(
            "gameplay.views.work.claim_work_reward_with_refresh",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:claim_work_reward", kwargs={"pk": assignment.pk}))
        assert response.status_code == 302
        assert response.url == reverse("gameplay:work")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)
