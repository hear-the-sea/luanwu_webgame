from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import transaction
from django.test import RequestFactory

from gameplay.admin import GlobalMailCampaignAdmin, GlobalMailCampaignForm
from gameplay.models import GlobalMailCampaign, Message


@pytest.mark.django_db
def test_global_mail_campaign_form_initializes_attachment_split_fields():
    campaign = GlobalMailCampaign.objects.create(
        key="admin_form_init_campaign",
        kind=Message.Kind.REWARD,
        title="初始化附件活动",
        attachments={"resources": {"silver": 100}, "items": {"peace_shield_small": 2}},
    )

    form = GlobalMailCampaignForm(instance=campaign)
    assert form.fields["attachment_resources"].initial == {"silver": 100}
    assert form.fields["attachment_items"].initial == {"peace_shield_small": 2}


@pytest.mark.django_db
def test_global_mail_campaign_form_save_merges_attachment_fields():
    form = GlobalMailCampaignForm(
        data={
            "key": "admin_form_save_campaign",
            "kind": Message.Kind.REWARD,
            "title": "保存附件活动",
            "body": "test",
            "is_active": True,
            "attachment_resources": {"grain": 50, "silver": 200},
            "attachment_items": {"peace_shield_small": 1},
        }
    )
    assert form.is_valid(), form.errors

    campaign = form.save()
    assert campaign.attachments == {
        "resources": {"grain": 50, "silver": 200},
        "items": {"peace_shield_small": 1},
    }


@pytest.mark.django_db
def test_global_mail_campaign_form_rejects_invalid_attachment_amount():
    form = GlobalMailCampaignForm(
        data={
            "key": "admin_form_invalid_amount_campaign",
            "kind": Message.Kind.REWARD,
            "title": "非法附件活动",
            "is_active": True,
            "attachment_resources": {"silver": 0},
            "attachment_items": {},
        }
    )

    assert not form.is_valid()
    assert "attachment_resources.silver 必须大于 0" in str(form.errors)


@pytest.mark.django_db
def test_global_mail_campaign_admin_save_model_enqueues_backfill_with_batch(
    monkeypatch, django_capture_on_commit_callbacks
):
    admin_obj = GlobalMailCampaignAdmin(GlobalMailCampaign, AdminSite())
    messages: list[str] = []
    enqueued: list[tuple[int, int]] = []

    def _fake_enqueue(campaign_id: int, *, batch_size: int = 500):
        enqueued.append((campaign_id, batch_size))
        return True

    monkeypatch.setattr("gameplay.admin.messages.enqueue_global_mail_backfill", _fake_enqueue)
    monkeypatch.setattr(admin_obj, "message_user", lambda _request, message, **_kwargs: messages.append(str(message)))

    form = GlobalMailCampaignForm(
        data={
            "key": "admin_save_model_campaign",
            "kind": Message.Kind.REWARD,
            "title": "后台保存补发",
            "body": "",
            "is_active": True,
            "attachment_resources": {"silver": 88},
            "attachment_items": {},
            "send_to_existing_now": True,
            "backfill_batch_size": 321,
        }
    )
    assert form.is_valid(), form.errors

    obj = form.save(commit=False)
    with django_capture_on_commit_callbacks(execute=True):
        admin_obj.save_model(SimpleNamespace(), obj, form, change=False)
        assert enqueued == []

    assert obj.pk is not None
    assert enqueued == [(obj.pk, 321)]
    assert any("已提交补发任务" in message and "batch_size=321" in message for message in messages)


@pytest.mark.django_db
def test_global_mail_campaign_admin_actions_enqueue_and_toggle(monkeypatch):
    admin_obj = GlobalMailCampaignAdmin(GlobalMailCampaign, AdminSite())
    messages: list[str] = []
    enqueued_ids: list[int] = []

    campaign_a = GlobalMailCampaign.objects.create(
        key="admin_action_campaign_a",
        kind=Message.Kind.REWARD,
        title="活动A",
        is_active=False,
    )
    campaign_b = GlobalMailCampaign.objects.create(
        key="admin_action_campaign_b",
        kind=Message.Kind.REWARD,
        title="活动B",
        is_active=False,
    )

    def _fake_enqueue(campaign_id: int, *, batch_size: int = 500):
        del batch_size
        enqueued_ids.append(campaign_id)
        return True

    monkeypatch.setattr("gameplay.admin.messages.enqueue_global_mail_backfill", _fake_enqueue)
    monkeypatch.setattr(admin_obj, "message_user", lambda _request, message, **_kwargs: messages.append(str(message)))

    queryset = GlobalMailCampaign.objects.filter(pk__in=[campaign_a.pk, campaign_b.pk]).order_by("pk")
    admin_obj.backfill_selected_campaigns(SimpleNamespace(), queryset)
    assert enqueued_ids == [campaign_a.pk, campaign_b.pk]
    assert any("已提交 2 个补发任务" in message for message in messages)

    admin_obj.activate_selected_campaigns(SimpleNamespace(), queryset)
    assert GlobalMailCampaign.objects.filter(pk__in=enqueued_ids, is_active=True).count() == 2

    admin_obj.deactivate_selected_campaigns(SimpleNamespace(), queryset)
    assert GlobalMailCampaign.objects.filter(pk__in=enqueued_ids, is_active=False).count() == 2


def test_global_mail_campaign_admin_readonly_fields_handle_unsaved_obj():
    admin_obj = GlobalMailCampaignAdmin(GlobalMailCampaign, AdminSite())

    assert admin_obj.deliveries_count(None) == 0
    assert admin_obj.deliveries_count(GlobalMailCampaign()) == 0
    assert "未保存" in str(admin_obj.runtime_status_badge(None))
    assert "{}" in str(admin_obj.attachments_preview(None))


@pytest.mark.django_db
def test_global_mail_campaign_counts_use_annotation(django_assert_num_queries):
    GlobalMailCampaign.objects.bulk_create(
        [GlobalMailCampaign(key=f"query_campaign_{index}", title="查询测试") for index in range(5)]
    )
    admin_obj = GlobalMailCampaignAdmin(GlobalMailCampaign, AdminSite())
    request = RequestFactory().get("/admin/gameplay/globalmailcampaign/")

    with django_assert_num_queries(1):
        assert [admin_obj.deliveries_count(obj) for obj in admin_obj.get_queryset(request)] == [0] * 5


@pytest.mark.django_db
def test_global_mail_backfill_is_not_dispatched_after_save_rollback(monkeypatch, django_capture_on_commit_callbacks):
    admin_obj = GlobalMailCampaignAdmin(GlobalMailCampaign, AdminSite())
    enqueued = []
    monkeypatch.setattr("gameplay.admin.messages.enqueue_global_mail_backfill", lambda *a, **kw: enqueued.append(a))
    campaign = GlobalMailCampaign(key="rollback_campaign", title="回滚活动")
    form = SimpleNamespace(cleaned_data={"send_to_existing_now": True})

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                admin_obj.save_model(SimpleNamespace(), campaign, form, change=False)
                raise RuntimeError("rollback")

    assert callbacks == []
    assert enqueued == []
    assert not GlobalMailCampaign.objects.filter(key="rollback_campaign").exists()
