"""
消息系统视图测试
"""

import pytest
from bs4 import BeautifulSoup
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse

from gameplay.models import ItemTemplate, Message


@pytest.mark.django_db
class TestMessageViews:
    """消息系统视图测试"""

    def test_messages_page(self, manor_with_user):
        """消息列表页面"""
        manor, client = manor_with_user
        response = client.get(reverse("gameplay:messages"))
        assert response.status_code == 200
        assert "message_list" in response.context

    def test_messages_page_get_does_not_run_message_cleanup(self, manor_with_user, monkeypatch):
        _manor, client = manor_with_user
        monkeypatch.setattr(
            "gameplay.services.utils.messages.cleanup_old_messages",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GET triggered message cleanup")),
        )

        response = client.get(reverse("gameplay:messages"))

        assert response.status_code == 200

    def test_non_object_attachments_render_safely_in_list_and_detail(self, manor_with_user):
        manor, client = manor_with_user
        messages = [
            Message.objects.create(
                manor=manor,
                kind=Message.Kind.SYSTEM,
                title=f"非对象附件 {index}",
                attachments=attachments,
            )
            for index, attachments in enumerate((["legacy"], "legacy", 7))
        ]

        list_response = client.get(reverse("gameplay:messages"))

        assert list_response.status_code == 200
        for message in messages:
            assert message.title in list_response.content.decode("utf-8")
            detail_response = client.get(reverse("gameplay:view_message", kwargs={"pk": message.pk}))
            assert detail_response.status_code == 200
            assert "delete-message-form" in detail_response.content.decode("utf-8")

    def test_nested_non_object_attachment_buckets_render_as_no_attachment(self, manor_with_user):
        manor, client = manor_with_user
        messages = [
            Message.objects.create(
                manor=manor,
                kind=Message.Kind.REWARD,
                title=f"脏附件分组 {index}",
                attachments=attachments,
            )
            for index, attachments in enumerate(({"items": "legacy"}, {"resources": ["legacy"]}))
        ]

        list_response = client.get(reverse("gameplay:messages"))

        assert list_response.status_code == 200
        for message in messages:
            detail_response = client.get(reverse("gameplay:view_message", kwargs={"pk": message.pk}))
            assert detail_response.status_code == 200
            body = detail_response.content.decode("utf-8")
            assert "delete-message-form" in body
            assert "领取附件" not in body

    def test_message_list_separates_mark_read_and_protected_delete_selection(self, manor_with_user):
        manor, client = manor_with_user
        protected = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="待领取保护消息",
            attachments={"resources": {"silver": 10}},
        )
        claimed = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="已领取可删除消息",
            attachments={"resources": {"silver": 10}},
            is_claimed=True,
        )
        plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="普通可删除消息")

        list_response = client.get(reverse("gameplay:messages"))
        list_soup = BeautifulSoup(list_response.content.decode("utf-8"), "html.parser")
        protected_row = list_soup.find("a", {"data-message-id": str(protected.pk)}).find_parent("tr")
        read_checkbox = protected_row.select_one('input.msg-read-checkbox[name="message_ids"][form="message-form"]')
        protected_delete_checkbox = protected_row.select_one(
            'input.msg-delete-checkbox[name="message_ids"][form="delete-selected-messages-form"]'
        )

        assert [header.get_text(" ", strip=True) for header in list_soup.select(".msg-table thead th")][:2] == [
            "标记已读",
            "删除",
        ]
        assert read_checkbox is not None
        assert read_checkbox.has_attr("disabled") is False
        assert read_checkbox.get("aria-label")
        assert protected_delete_checkbox is not None
        assert protected_delete_checkbox.has_attr("disabled")
        assert protected_delete_checkbox.get("aria-label")
        delete_help_id = protected_delete_checkbox.get("aria-describedby")
        assert delete_help_id
        assert protected_row.find(id=delete_help_id).get_text(" ", strip=True) == "领取附件后可删除"

        for deletable_message in (claimed, plain):
            deletable_row = list_soup.find("a", {"data-message-id": str(deletable_message.pk)}).find_parent("tr")
            delete_checkbox = deletable_row.select_one(
                'input.msg-delete-checkbox[name="message_ids"][form="delete-selected-messages-form"]'
            )
            assert delete_checkbox is not None
            assert delete_checkbox.has_attr("disabled") is False
            assert delete_checkbox.get("aria-label")

        mark_response = client.post(
            reverse("gameplay:mark_messages_read"),
            {"message_ids": [protected.pk]},
        )

        assert mark_response.status_code == 302
        protected.refresh_from_db()
        assert protected.is_read is True

        detail_response = client.get(reverse("gameplay:view_message", kwargs={"pk": protected.pk}))
        detail_soup = BeautifulSoup(detail_response.content.decode("utf-8"), "html.parser")

        assert detail_soup.find(id="delete-message-form") is None
        assert "领取附件后可删除" in detail_response.content.decode("utf-8")

    def test_delete_messages_html_reports_exact_deleted_and_protected_counts(self, manor_with_user):
        manor, client = manor_with_user
        protected = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="待领取保护消息",
            attachments={"resources": {"silver": 10}},
        )
        plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="普通可删除消息")

        response = client.post(
            reverse("gameplay:delete_messages"),
            {"message_ids": [protected.pk, plain.pk]},
        )

        assert response.status_code == 302
        feedback = [str(message) for message in get_messages(response.wsgi_request)]
        assert feedback == ["已删除 1 条消息，1 条未领取附件消息已保留"]

    def test_delete_messages_json_returns_exact_deleted_and_protected_counts(self, manor_with_user):
        manor, client = manor_with_user
        protected = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="待领取保护消息",
            attachments={"resources": {"silver": 10}},
        )
        claimed = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="已领取可删除消息",
            attachments={"resources": {"silver": 10}},
            is_claimed=True,
        )
        plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="普通可删除消息")

        response = client.post(
            reverse("gameplay:delete_messages"),
            {"message_ids": [protected.pk, claimed.pk, plain.pk]},
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted_count"] == 2
        assert payload["protected_count"] == 1
        assert Message.objects.filter(pk=protected.pk).exists() is True

    def test_delete_all_messages_json_returns_exact_deleted_and_protected_counts(self, manor_with_user):
        manor, client = manor_with_user
        protected = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="待领取保护消息",
            attachments={"resources": {"silver": 10}},
        )
        Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="普通可删除消息")

        response = client.post(reverse("gameplay:delete_all_messages"), HTTP_ACCEPT="application/json")

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted_count"] == 1
        assert payload["protected_count"] == 1
        assert Message.objects.filter(pk=protected.pk).exists() is True

    def test_messages_page_loads_external_page_script_without_inline_logic(self, manor_with_user):
        _manor, client = manor_with_user

        response = client.get(reverse("gameplay:messages"))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "js/messages-page.js" in body
        assert "function claimAttachment" not in body
        assert "onclick=" not in body

    def test_message_detail_page_loads_external_page_script_without_inline_logic(self, manor_with_user):
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="详情页脚本测试",
            body="测试详情内容",
            attachments={},
        )

        response = client.get(reverse("gameplay:view_message", kwargs={"pk": message.pk}))

        assert response.status_code == 200
        body = response.content.decode("utf-8")
        assert "css/message-detail.css" in body
        assert "js/message-detail-page.js" in body
        assert "delete-message-form" in body
        assert "<style>" not in body
        assert "document.getElementById('delete-message-form')" not in body
        assert "gameDialog.danger('确认删除这条消息吗？'" not in body

    def test_message_detail_get_does_not_mark_message_read(self, manor_with_user):
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="GET 不应标记已读",
            body="测试详情内容",
            is_read=False,
        )

        response = client.get(reverse("gameplay:view_message", kwargs={"pk": message.pk}))

        assert response.status_code == 200
        message.refresh_from_db()
        assert message.is_read is False

    def test_mark_messages_read_json_marks_selected_and_returns_unread_count(self, manor_with_user):
        manor, client = manor_with_user
        selected = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="AJAX 标记已读",
            body="测试详情内容",
            is_read=False,
        )
        Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="仍保持未读",
            body="测试详情内容",
            is_read=False,
        )

        response = client.post(
            reverse("gameplay:mark_messages_read"),
            {"message_ids": [selected.pk]},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["message_ids"] == [selected.pk]
        assert payload["unread_count"] == 1
        selected.refresh_from_db()
        assert selected.is_read is True

    def test_mark_all_read(self, manor_with_user):
        """标记全部已读"""
        manor, client = manor_with_user
        response = client.post(reverse("gameplay:mark_all_messages_read"))
        assert response.status_code == 302  # 重定向回消息列表

    def test_claim_attachment_handles_game_error(self, manor_with_user):
        """领取无附件消息时应优雅失败而不是500。"""
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="无附件测试",
            attachments={},
        )

        response = client.post(reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}))

        assert response.status_code == 302

    def test_claim_attachment_json_success(self, manor_with_user):
        """JSON 请求领取附件成功返回结构化结果。"""
        manor, client = manor_with_user
        ItemTemplate.objects.create(key="msg_json_item", name="测试道具")
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="json附件",
            attachments={"items": {"msg_json_item": 2}},
        )

        response = client.post(
            reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["message_id"] == message.pk
        assert payload["claimed"][0]["kind"] == "item"

    def test_claim_attachment_json_error(self, manor_with_user):
        """JSON 请求领取无附件时返回400错误。"""
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="json无附件",
            attachments={},
        )

        response = client.post(
            reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert payload["message_id"] == message.pk
        assert "error" in payload

    def test_view_message_json_tolerates_unread_count_database_error(self, manor_with_user, monkeypatch):
        """JSON 查看消息时 unread 计数数据库故障应降级为0而不是500。"""
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="json unread fallback",
            attachments={},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.unread_message_count",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.get(
            reverse("gameplay:view_message", kwargs={"pk": message.pk}),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["message_id"] == message.pk
        assert payload["unread_count"] == 0

    def test_view_message_json_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="json unread runtime boom",
            attachments={},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.unread_message_count",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.get(
                reverse("gameplay:view_message", kwargs={"pk": message.pk}),
                HTTP_ACCEPT="application/json",
            )

    def test_claim_attachment_json_error_tolerates_unread_count_database_error(self, manor_with_user, monkeypatch):
        """JSON 领取附件失败时 unread 计数数据库故障不应扩大为500。"""
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="json claim unread fallback",
            attachments={},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.unread_message_count",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 400
        payload = response.json()
        assert payload["success"] is False
        assert payload["message_id"] == message.pk
        assert payload["unread_count"] == 0

    def test_claim_attachment_json_database_error_tolerates_unread_count_database_error(
        self, manor_with_user, monkeypatch
    ):
        """JSON 领取附件数据库故障时 unread 计数数据库故障也应降级返回。"""
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="json claim unexpected unread fallback",
            attachments={"items": {"msg_json_item_unexpected": 1}},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.claim_message_attachments",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )
        monkeypatch.setattr(
            "gameplay.views.messages.unread_message_count",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(
            reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 500
        payload = response.json()
        assert payload["success"] is False
        assert payload["message_id"] == message.pk
        assert payload["unread_count"] == 0
        assert "操作失败，请稍后重试" in payload["error"]

    def test_claim_attachment_json_error_unread_count_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.SYSTEM,
            title="json claim unread runtime boom",
            attachments={},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.unread_message_count",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
                HTTP_ACCEPT="application/json",
            )

    def test_claim_attachment_json_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="json claim runtime boom",
            attachments={"items": {"msg_json_item_runtime": 1}},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.claim_message_attachments",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(
                reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
                HTTP_ACCEPT="application/json",
            )

    def test_claim_attachment_database_error_does_not_500(self, manor_with_user, monkeypatch):
        """普通表单领取附件数据库故障时应降级为消息提示。"""
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="claim unexpected fallback",
            attachments={"items": {"msg_item_unexpected": 1}},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.claim_message_attachments",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
        )

        response = client.post(reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}))

        assert response.status_code == 302
        assert response.url == reverse("gameplay:view_message", kwargs={"pk": message.pk})
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert any("操作失败，请稍后重试" in m for m in messages)

    def test_claim_attachment_programming_error_bubbles_up(self, manor_with_user, monkeypatch):
        manor, client = manor_with_user
        message = Message.objects.create(
            manor=manor,
            kind=Message.Kind.REWARD,
            title="claim runtime boom",
            attachments={"items": {"msg_item_runtime": 1}},
        )

        monkeypatch.setattr(
            "gameplay.views.messages.claim_message_attachments",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError, match="boom"):
            client.post(reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}))
