from types import SimpleNamespace

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.urls import reverse

from core.exceptions import AttachmentAlreadyClaimedError, GameError, ItemNotFoundError
from gameplay.admin.messages import GlobalMailCampaignForm, MessageAdmin, SendMessageForm
from gameplay.models import GlobalMailCampaign, InventoryItem, ItemTemplate, Message
from gameplay.services.global_mail import backfill_campaign_to_existing_manors
from gameplay.services.utils.messages import claim_message_attachments, create_message
from gameplay.views.messages import _build_attachment_details, _format_claimed_summary


@pytest.mark.django_db
def test_mixed_binding_mail_claim_preserves_quantities_receipt_and_replay_protection(manor_factory):
    manor, _user = manor_factory()
    template = ItemTemplate.objects.create(key="bound_mail_pill", name="洗髓丹", default_bound=True)
    message = create_message(
        manor,
        Message.Kind.REWARD,
        "混合绑定附件",
        attachments={"items": {template.key: 3}, "bound_items": {template.key: 10}},
    )
    assert message.has_attachments and message.has_protected_attachments
    details = _build_attachment_details(message)
    assert [(entry["quantity"], entry["is_bound"]) for entry in details["items"]] == [(3, False), (10, True)]

    assert claim_message_attachments(message) == {
        "item_bound_mail_pill": 3,
        "bound_item_bound_mail_pill": 10,
    }
    inventory = InventoryItem.objects.get(manor=manor, template=template)
    assert inventory.quantity == 13
    assert inventory.bound_quantity == 10
    message.refresh_from_db()
    assert message.attachments["claimed"] == {
        "resources": {},
        "items": {template.key: 3},
        "bound_items": {template.key: 10},
    }
    assert not message.has_protected_attachments
    assert _build_attachment_details(message)["items"] == details["items"]
    with pytest.raises(AttachmentAlreadyClaimedError):
        claim_message_attachments(message)
    inventory.refresh_from_db()
    assert (inventory.quantity, inventory.bound_quantity) == (13, 10)
    summary, payload = _format_claimed_summary({"bound_item_bound_mail_pill": 10})
    assert summary == "洗髓丹（绑定）×10"
    assert payload == [{"kind": "item", "key": template.key, "name": "洗髓丹", "amount": 10, "is_bound": True}]


@pytest.mark.django_db
def test_bound_only_global_campaign_delivers_once_and_can_be_claimed(manor_factory):
    manor, _user = manor_factory()
    template = ItemTemplate.objects.create(key="global_bound_pill", name="洗髓丹")
    campaign = GlobalMailCampaign.objects.create(
        key="bound_pill_gift",
        title="绑定洗髓丹奖励",
        attachments={"bound_items": {template.key: 10}},
    )
    campaign.full_clean()
    assert backfill_campaign_to_existing_manors(campaign) == 1
    assert backfill_campaign_to_existing_manors(campaign) == 0
    message = Message.objects.get(manor=manor, title=campaign.title)
    assert message.is_deletion_protected
    claim_message_attachments(message)
    inventory = InventoryItem.objects.get(manor=manor, template=template)
    assert (inventory.quantity, inventory.bound_quantity) == (10, 10)


@pytest.mark.django_db
def test_campaign_form_mixed_binding_round_trip_and_old_form_compatibility():
    data = {
        "key": "form_bound_gift",
        "kind": Message.Kind.REWARD,
        "title": "洗髓丹奖励",
        "is_active": True,
        "attachment_items": {"xisuidan": 2},
        "attachment_bound_items": {"xisuidan": 10},
    }
    form = GlobalMailCampaignForm(data=data)
    assert form.is_valid(), form.errors
    campaign = form.save()
    assert campaign.attachments == {"items": {"xisuidan": 2}, "bound_items": {"xisuidan": 10}}
    restored = GlobalMailCampaignForm(instance=campaign)
    assert restored.fields["attachment_bound_items"].initial == {"xisuidan": 10}
    legacy = GlobalMailCampaignForm(
        data={key: value for key, value in data.items() if key != "attachment_bound_items"}, instance=campaign
    )
    assert legacy.is_valid(), legacy.errors
    assert legacy.cleaned_data["attachments"] == {"items": {"xisuidan": 2}}


@pytest.mark.django_db
@pytest.mark.parametrize("form_class", [GlobalMailCampaignForm, SendMessageForm])
@pytest.mark.parametrize("amount", [0, -1, 1.0, 1.5, True, "ten"])
def test_admin_forms_reject_invalid_bound_amounts(form_class, amount):
    form = form_class(
        data={
            "key": "invalid_bound_gift",
            "kind": Message.Kind.REWARD,
            "title": "无效奖励",
            "attachment_bound_items": {"xisuidan": amount},
        }
    )
    assert not form.is_valid()
    assert "attachment_bound_items" in form.errors


@pytest.mark.django_db
def test_message_admin_broadcast_includes_binding_bucket(manor_factory, monkeypatch):
    manor, user = manor_factory()
    admin = MessageAdmin(Message, AdminSite())
    monkeypatch.setattr(admin, "message_user", lambda *args, **kwargs: None)
    form = SimpleNamespace(
        cleaned_data={
            "recipients": user.__class__.objects.filter(pk=user.pk),
            "attachment_bound_items": {"xisuidan": 10},
        }
    )
    admin.save_model(RequestFactory().post("/"), Message(title="指定玩家绑定奖励"), form, change=False)
    assert Message.objects.get(manor=manor, title="指定玩家绑定奖励").attachments == {"bound_items": {"xisuidan": 10}}


@pytest.mark.django_db
@pytest.mark.parametrize("bucket", [{"review_bound_pill": amount} for amount in [0, -1, 1.0, 1.5, True, "ten"]] + [[1]])
def test_claim_rejects_invalid_bound_attachments_without_granting_other_rewards(manor_factory, bucket):
    manor, _user = manor_factory()
    template = ItemTemplate.objects.create(key="review_bound_pill", name="洗髓丹")
    original_silver = manor.silver
    message = create_message(
        manor,
        Message.Kind.REWARD,
        "非法绑定配置",
        attachments={"resources": {"silver": 100}, "items": {template.key: 2}, "bound_items": bucket},
    )
    with pytest.raises(GameError, match="绑定附件配置无效"):
        claim_message_attachments(message)
    message.refresh_from_db()
    manor.refresh_from_db()
    assert not message.is_claimed
    assert message.has_protected_attachments
    assert manor.silver == original_silver
    assert not InventoryItem.objects.filter(manor=manor, template=template).exists()


@pytest.mark.django_db
def test_missing_bound_template_preserves_whole_mail_until_catalog_is_fixed(manor_factory):
    manor, _user = manor_factory()
    template = ItemTemplate.objects.create(key="existing_bound_pill", name="洗髓丹")
    message = create_message(
        manor,
        Message.Kind.REWARD,
        "等待修复配置",
        attachments={"items": {template.key: 2}, "bound_items": {"missing_bound_pill": 10}},
    )
    with pytest.raises(ItemNotFoundError):
        claim_message_attachments(message)
    message.refresh_from_db()
    assert not message.is_claimed and message.has_protected_attachments
    assert not InventoryItem.objects.filter(manor=manor, template=template).exists()
    missing = ItemTemplate.objects.create(key="missing_bound_pill", name="绑定洗髓丹")
    claim_message_attachments(message)
    inventory = InventoryItem.objects.get(manor=manor, template=missing)
    assert (inventory.quantity, inventory.bound_quantity) == (10, 10)


@pytest.mark.django_db
def test_integer_string_bound_amount_is_normalized_in_receipt(manor_factory):
    manor, _user = manor_factory()
    template = ItemTemplate.objects.create(key="numeric_string_bound_pill", name="洗髓丹")
    message = create_message(
        manor,
        Message.Kind.REWARD,
        "兼容数字字符串",
        attachments={"bound_items": {template.key: "10"}},
    )
    assert claim_message_attachments(message) == {f"bound_item_{template.key}": 10}
    message.refresh_from_db()
    assert message.attachments["claimed"]["bound_items"] == {template.key: 10}


@pytest.mark.django_db
def test_bound_only_mail_view_claim_and_delete_protection(manor_with_user):
    manor, client = manor_with_user
    template = ItemTemplate.objects.create(key="view_bound_pill", name="洗髓丹")
    message = create_message(
        manor, Message.Kind.REWARD, "绑定附件页面", attachments={"bound_items": {template.key: 10}}
    )
    detail_url = reverse("gameplay:view_message", kwargs={"pk": message.pk})
    detail = client.get(detail_url)
    assert detail.status_code == 200
    assert "洗髓丹（绑定）" in detail.content.decode()
    assert "delete-message-form" not in detail.content.decode()
    client.post(reverse("gameplay:delete_messages"), {"message_ids": [message.pk]})
    assert Message.objects.filter(pk=message.pk).exists()
    response = client.post(
        reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}), HTTP_ACCEPT="application/json"
    )
    assert response.status_code == 200
    assert response.json()["claimed"][0]["is_bound"] is True
    assert response.json()["summary"] == "洗髓丹（绑定）×10"
    detail = client.get(detail_url)
    assert "洗髓丹（绑定）" in detail.content.decode()
    assert "delete-message-form" in detail.content.decode()
