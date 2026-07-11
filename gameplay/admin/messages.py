import json
from contextlib import contextmanager

from django import forms
from django.contrib import admin
from django.contrib.admin.actions import delete_selected as django_delete_selected
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import DEFAULT_DB_ALIAS, router
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html

from ..models import GlobalMailCampaign, GlobalMailDelivery, Manor, Message
from ..services.utils.messages import MessageDeleteResult, create_message, delete_message_queryset
from ..tasks.global_mail import enqueue_global_mail_backfill

User = get_user_model()

_MESSAGE_BULK_DELETE_PERMISSION_ATTR = "_message_admin_bulk_delete_permission"
_MESSAGE_BULK_DELETE_PERMISSION_TOKEN = object()
_MISSING_REQUEST_ATTR = object()


@contextmanager
def _allow_message_bulk_delete_permission_check(request):
    previous_value = getattr(request, _MESSAGE_BULK_DELETE_PERMISSION_ATTR, _MISSING_REQUEST_ATTR)
    setattr(request, _MESSAGE_BULK_DELETE_PERMISSION_ATTR, _MESSAGE_BULK_DELETE_PERMISSION_TOKEN)
    try:
        yield
    finally:
        if previous_value is _MISSING_REQUEST_ATTR:
            delattr(request, _MESSAGE_BULK_DELETE_PERMISSION_ATTR)
        else:
            setattr(request, _MESSAGE_BULK_DELETE_PERMISSION_ATTR, previous_value)


class SendMessageForm(forms.ModelForm):
    """发送消息的自定义表单"""

    recipients = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        required=False,
        widget=FilteredSelectMultiple("玩家", False),
        label="指定玩家（不选则发送给所有人）",
        help_text="按住 Ctrl 多选",
    )

    attachment_resources = forms.JSONField(
        required=False,
        initial={},
        label="附件资源",
        help_text='格式：{"grain": 100, "silver": 200}',
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
    )

    attachment_items = forms.JSONField(
        required=False,
        initial={},
        label="附件道具",
        help_text='格式：{"item_key": 数量}，例如：{"experience_peach": 5}',
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
    )

    class Meta:
        model = Message
        fields = ["kind", "title", "body"]


class GlobalMailCampaignForm(forms.ModelForm):
    attachment_resources = forms.JSONField(
        required=False,
        initial={},
        label="附件资源",
        help_text='格式：{"grain": 100, "silver": 200}',
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
    )
    attachment_items = forms.JSONField(
        required=False,
        initial={},
        label="附件道具",
        help_text='格式：{"item_key": 数量}，例如：{"peace_shield_small": 1}',
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
    )
    send_to_existing_now = forms.BooleanField(
        required=False,
        label="保存后补发给现有玩家",
        help_text="仅补发当前生效活动，已收到过的玩家不会重复收到。",
    )
    backfill_batch_size = forms.IntegerField(
        required=False,
        min_value=50,
        max_value=5000,
        initial=500,
        label="补发批大小",
        help_text="仅在勾选\u201c保存后补发给现有玩家\u201d时生效，推荐 200~1000。",
    )

    class Meta:
        model = GlobalMailCampaign
        fields = ["key", "kind", "title", "body", "is_active", "start_at", "end_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        attachments = self.instance.attachments if getattr(self.instance, "pk", None) else {}
        if isinstance(attachments, dict):
            self.fields["attachment_resources"].initial = attachments.get("resources") or {}
            self.fields["attachment_items"].initial = attachments.get("items") or {}

    @staticmethod
    def _normalize_attachment_bucket(value, field_name: str) -> dict[str, int]:
        bucket = value or {}
        if not isinstance(bucket, dict):
            raise forms.ValidationError({field_name: f"{field_name} 必须为对象"})

        normalized: dict[str, int] = {}
        for raw_key, raw_amount in bucket.items():
            key = str(raw_key).strip()
            if not key:
                raise forms.ValidationError({field_name: f"{field_name} 的 key 必须为非空字符串"})
            try:
                amount = int(raw_amount)
            except (TypeError, ValueError) as exc:
                raise forms.ValidationError({field_name: f"{field_name}.{key} 必须为整数"}) from exc
            if amount <= 0:
                raise forms.ValidationError({field_name: f"{field_name}.{key} 必须大于 0"})
            normalized[key] = amount
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        resources = self._normalize_attachment_bucket(cleaned_data.get("attachment_resources"), "attachment_resources")
        items = self._normalize_attachment_bucket(cleaned_data.get("attachment_items"), "attachment_items")

        attachments = {}
        if resources:
            attachments["resources"] = resources
        if items:
            attachments["items"] = items
        cleaned_data["attachments"] = attachments
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.attachments = self.cleaned_data.get("attachments", {})
        if commit:
            instance.save()
        return instance


class CampaignRuntimeStatusFilter(admin.SimpleListFilter):
    title = "运行状态"
    parameter_name = "runtime_status"

    def lookups(self, request, model_admin):
        return (
            ("active", "进行中"),
            ("pending", "未开始"),
            ("ended", "已结束"),
            ("inactive", "已停用"),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        value = self.value()
        if value == "inactive":
            return queryset.filter(is_active=False)
        if value == "pending":
            return queryset.filter(is_active=True, start_at__isnull=False, start_at__gt=now)
        if value == "active":
            return (
                queryset.filter(is_active=True)
                .filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
                .filter(Q(end_at__isnull=True) | Q(end_at__gte=now))
            )
        if value == "ended":
            return queryset.filter(is_active=True, end_at__isnull=False, end_at__lt=now)
        return queryset


@admin.register(GlobalMailCampaign)
class GlobalMailCampaignAdmin(admin.ModelAdmin):
    form = GlobalMailCampaignForm
    list_display = (
        "key",
        "title",
        "kind",
        "runtime_status_badge",
        "is_active",
        "start_at",
        "end_at",
        "attachments_summary",
        "deliveries_count",
        "created_at",
    )
    list_filter = ("kind", "is_active", CampaignRuntimeStatusFilter)
    search_fields = ("key", "title", "body")
    readonly_fields = ("runtime_status_badge", "attachments_preview", "deliveries_count", "created_at", "updated_at")
    date_hierarchy = "created_at"
    save_on_top = True
    actions = ["backfill_selected_campaigns", "activate_selected_campaigns", "deactivate_selected_campaigns"]
    fieldsets = (
        ("基础信息", {"fields": ("key", "kind", "title", "body", "runtime_status_badge")}),
        ("生效时间", {"fields": ("is_active", "start_at", "end_at")}),
        ("附件配置", {"fields": ("attachment_resources", "attachment_items", "attachments_preview")}),
        ("补发操作", {"fields": ("send_to_existing_now", "backfill_batch_size")}),
        ("统计", {"fields": ("deliveries_count", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_deliveries_count=Count("deliveries"))

    @admin.display(description="已投递", ordering="_deliveries_count")
    def deliveries_count(self, obj):
        if obj is None or not getattr(obj, "pk", None):
            return 0
        return int(getattr(obj, "_deliveries_count", obj.deliveries.count()))

    @admin.display(description="运行状态")
    def runtime_status_badge(self, obj):
        if obj is None or not getattr(obj, "pk", None):
            return format_html('<span style="color:#6b7280;">{}</span>', "未保存")
        now = timezone.now()
        if not obj.is_active:
            return format_html('<span style="color:#6b7280;">{}</span>', "已停用")
        if obj.start_at and obj.start_at > now:
            return format_html('<span style="color:#d97706;">{}</span>', "未开始")
        if obj.end_at and obj.end_at < now:
            return format_html('<span style="color:#2563eb;">{}</span>', "已结束")
        return format_html('<span style="color:#059669;font-weight:600;">{}</span>', "进行中")

    @admin.display(description="附件摘要")
    def attachments_summary(self, obj):
        attachments = obj.attachments if isinstance(obj.attachments, dict) else {}
        resources = attachments.get("resources") if isinstance(attachments.get("resources"), dict) else {}
        items = attachments.get("items") if isinstance(attachments.get("items"), dict) else {}
        resource_total = sum(int(v) for v in resources.values()) if resources else 0
        item_total = sum(int(v) for v in items.values()) if items else 0
        if not resources and not items:
            return "无附件"
        return f"资源 {len(resources)} 类/{resource_total}，道具 {len(items)} 类/{item_total}"

    @admin.display(description="附件预览")
    def attachments_preview(self, obj):
        attachments = obj.attachments if obj and isinstance(obj.attachments, dict) else {}
        pretty = json.dumps(attachments or {}, ensure_ascii=False, indent=2)
        return format_html("<pre style='max-width:760px;white-space:pre-wrap;'>{}</pre>", pretty)

    @admin.action(description="批量启用活动")
    def activate_selected_campaigns(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"已启用 {updated} 个活动")

    @admin.action(description="批量停用活动")
    def deactivate_selected_campaigns(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"已停用 {updated} 个活动")

    @admin.action(description="补发给当前所有玩家（幂等）")
    def backfill_selected_campaigns(self, request, queryset):
        queued = 0
        for campaign in queryset:
            async_result = enqueue_global_mail_backfill(campaign.id)
            queued += 1
            self.message_user(request, f"活动 {campaign.key} 已提交补发任务（task_id={async_result.id}）")
        if queued > 1:
            self.message_user(request, f"已提交 {queued} 个补发任务")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get("send_to_existing_now"):
            batch_size = int(form.cleaned_data.get("backfill_batch_size") or 500)
            async_result = enqueue_global_mail_backfill(obj.id, batch_size=batch_size)
            self.message_user(
                request,
                f"已提交补发任务（task_id={async_result.id}，batch_size={batch_size}）",
            )


@admin.register(GlobalMailDelivery)
class GlobalMailDeliveryAdmin(admin.ModelAdmin):
    list_display = ("campaign", "manor", "message", "created_at")
    list_filter = ("campaign", ("created_at", admin.DateFieldListFilter))
    search_fields = ("campaign__key", "campaign__title", "manor__user__username", "manor__name", "message__title")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("campaign", "manor", "message")
    list_select_related = ("campaign", "manor__user", "message")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("title", "manor", "kind", "has_attachments", "is_claimed", "is_read", "created_at")
    list_filter = ("kind", "is_read", "is_claimed", "created_at")
    search_fields = ("title", "body", "manor__user__username")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("manor",)

    @admin.display(boolean=True, description="有附件")
    def has_attachments(self, obj):
        return obj.has_attachments

    def has_delete_permission(self, request, obj=None):
        is_safe_bulk_delete = (
            getattr(request, _MESSAGE_BULK_DELETE_PERMISSION_ATTR, None) is _MESSAGE_BULK_DELETE_PERMISSION_TOKEN
        )
        if obj is not None and obj.has_protected_attachments and not is_safe_bulk_delete:
            return False
        return super().has_delete_permission(request, obj)

    def _report_delete_result(self, request, result: MessageDeleteResult) -> None:
        self.message_user(
            request,
            f"已删除 {result.deleted_count} 条消息，{result.protected_count} 条未领取附件消息已保留",
        )

    def delete_model(self, request, obj):
        database_alias = obj._state.db or router.db_for_write(Message, instance=obj)
        if database_alias != DEFAULT_DB_ALIAS:
            raise PermissionDenied
        result = delete_message_queryset(Message.objects.using(database_alias).filter(pk=obj.pk))
        if result.protected_count > 0 or result.deleted_count != 1:
            raise PermissionDenied
        return result

    def delete_queryset(self, request, queryset):
        if queryset.db != DEFAULT_DB_ALIAS:
            raise PermissionDenied
        result = delete_message_queryset(
            queryset,
            before_delete_batch=lambda eligible_queryset: self.log_deletions(request, eligible_queryset),
        )
        self._report_delete_result(request, result)
        return result

    @admin.action(permissions=["delete"], description="删除所选消息")
    def delete_selected(self, request, queryset):
        if queryset.db != DEFAULT_DB_ALIAS:
            raise PermissionDenied
        if request.POST.get("post"):
            with _allow_message_bulk_delete_permission_check(request):
                _objects, _model_count, perms_needed, protected = self.get_deleted_objects(queryset, request)
            if perms_needed:
                raise PermissionDenied
            if protected:
                with _allow_message_bulk_delete_permission_check(request):
                    return django_delete_selected(self, request, queryset)
            self.delete_queryset(request, queryset)
            return None
        with _allow_message_bulk_delete_permission_check(request):
            return django_delete_selected(self, request, queryset)

    def get_form(self, request, obj=None, **kwargs):
        """为添加消息使用自定义表单"""
        if obj is None:  # 只在创建时使用自定义表单
            kwargs["form"] = SendMessageForm
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        """保存消息时处理批量发送和附件"""
        if change:  # 编辑现有消息
            super().save_model(request, obj, form, change)
            return

        # 新建消息 - 批量发送
        recipients = form.cleaned_data.get("recipients")
        attachment_resources = form.cleaned_data.get("attachment_resources") or {}
        attachment_items = form.cleaned_data.get("attachment_items") or {}

        # 构建附件数据
        attachments = {}
        if attachment_resources:
            attachments["resources"] = attachment_resources
        if attachment_items:
            attachments["items"] = attachment_items

        # 如果没有指定接收人，发送给所有人
        if not recipients:
            recipients = User.objects.filter(is_active=True)

        # 批量创建消息
        for user in recipients:
            manor = Manor.objects.filter(user=user).first()
            if manor:
                create_message(
                    manor=manor,
                    kind=obj.kind,
                    title=obj.title,
                    body=obj.body,
                    attachments=attachments if attachments else None,
                )

        # 显示成功消息
        self.message_user(request, f"已向 {recipients.count()} 位玩家发送消息")

    @admin.action(description="标记为已读")
    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)
        self.message_user(request, f"已标记 {queryset.count()} 条消息为已读")

    @admin.action(description="标记为未读")
    def mark_as_unread(self, request, queryset):
        queryset.update(is_read=False)
        self.message_user(request, f"已标记 {queryset.count()} 条消息为未读")

    actions = ["mark_as_read", "mark_as_unread", "delete_selected"]
