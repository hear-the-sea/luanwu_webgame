from collections.abc import Callable
from typing import Any

from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import DEFAULT_DB_ALIAS, transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views.decorators.http import require_http_methods

from core.admin_cascade import account_message_cascade_scope
from core.admin_i18n import apply_common_field_labels
from core.admin_records import OperationLogAdmin  # noqa: F401
from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS

from .admin_login import ClearLoginRestrictionsForm, LoginRestrictionsClearError, clear_login_restrictions
from .models import EmailProviderDailyQuota, EmailSendQuota, EmailVerificationDelivery, User

apply_common_field_labels(User, labels={"username": "用户名", "title": "称号"})

admin.site.site_header = "江湖游戏后台管理"
admin.site.site_title = "江湖游戏后台"
admin.site.index_title = "运营管理"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    change_list_template = "admin/accounts/user/change_list.html"
    _base_fieldsets: tuple[Any, ...] = tuple(DjangoUserAdmin.fieldsets or ())
    fieldsets = _base_fieldsets + (
        ("游戏信息", {"fields": ("title",)}),
        ("验证状态", {"fields": ("email_verified", "email_verification_last_provider")}),
    )
    list_display = (
        "username",
        "email",
        "email_verified",
        "email_verification_last_provider",
        "title",
        "is_staff",
        "date_joined",
    )
    list_filter = (*DjangoUserAdmin.list_filter, "email_verified")
    search_fields = ("username", "email", "title")

    def get_deleted_objects(self, objs, request):
        objects = list(objs)
        if not all(
            (obj._state.db or DEFAULT_DB_ALIAS) == DEFAULT_DB_ALIAS and self.has_delete_permission(request, obj)
            for obj in objects
        ):
            return super().get_deleted_objects(objects, request)
        from gameplay.models import Manor

        manor_ids = (
            Manor.objects.using(DEFAULT_DB_ALIAS)
            .filter(user_id__in=[obj.pk for obj in objects])
            .values_list("pk", flat=True)
        )
        with account_message_cascade_scope(request, manor_ids):
            return super().get_deleted_objects(objects, request)

    def _delete_accounts(self, request, objects, delete: Callable[[], None]) -> None:
        from gameplay.models import Message
        from gameplay.models.items import _allow_prevalidated_message_deletions

        if not all(
            (obj._state.db or DEFAULT_DB_ALIAS) == DEFAULT_DB_ALIAS and self.has_delete_permission(request, obj)
            for obj in objects
        ):
            raise PermissionDenied
        with transaction.atomic(using=DEFAULT_DB_ALIAS):
            _deleted, _counts, permissions_needed, protected = self.get_deleted_objects(objects, request)
            if permissions_needed or protected:
                raise PermissionDenied
            message_ids = (
                Message.objects.using(DEFAULT_DB_ALIAS)
                .filter(manor__user_id__in=[obj.pk for obj in objects])
                .values_list("pk", flat=True)
            )
            with _allow_prevalidated_message_deletions(DEFAULT_DB_ALIAS, message_ids):
                delete()

    def delete_model(self, request, obj):
        self._delete_accounts(request, [obj], lambda: super(UserAdmin, self).delete_model(request, obj))

    def delete_queryset(self, request, queryset):
        objects = list(queryset)
        selected = queryset.filter(pk__in=[obj.pk for obj in objects])
        self._delete_accounts(request, objects, lambda: super(UserAdmin, self).delete_queryset(request, selected))

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(
            request,
            extra_context={
                **(extra_context or {}),
                "can_clear_login_restrictions": self.has_change_permission(request),
            },
        )

    def get_urls(self):
        return [
            path(
                "clear-login-restrictions/",
                self.admin_site.admin_view(require_http_methods(["GET", "POST"])(self.clear_login_restrictions_view)),
                name="accounts_user_clear_login_restrictions",
            ),
        ] + super().get_urls()

    def clear_login_restrictions_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        form = ClearLoginRestrictionsForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            username = form.cleaned_data["username"]
            ip = form.cleaned_data["ip"]
            try:
                clear_login_restrictions(username=username, ip=ip)
            except (*CACHE_INFRASTRUCTURE_EXCEPTIONS, LoginRestrictionsClearError):
                form.add_error(None, "缓存服务异常，未能完成解除，请稍后重试。")
            else:
                target = "，".join(
                    value for value in (f"用户名：{username}" if username else "", f"IP：{ip}" if ip else "") if value
                )
                LogEntry.objects.create(
                    user_id=request.user.pk,
                    content_type_id=ContentType.objects.get_for_model(User).pk,
                    object_repr=target[:200],
                    action_flag=CHANGE,
                    change_message=f"解除登录限制（{target}）",
                )
                self.message_user(request, f"已解除登录限制（{target}）。", level=messages.SUCCESS)
                return redirect(reverse("admin:accounts_user_changelist", current_app=self.admin_site.name))
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            "admin/accounts/user/clear_login_restrictions.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": "解除登录限制",
                "form": form,
                "media": self.media + form.media,
            },
        )


@admin.register(EmailSendQuota)
class EmailSendQuotaAdmin(admin.ModelAdmin):
    list_display = ("month", "sent_count", "updated_at")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailProviderDailyQuota)
class EmailProviderDailyQuotaAdmin(admin.ModelAdmin):
    list_display = ("day", "provider", "sent_count", "updated_at")
    list_filter = ("provider", "day")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailVerificationDelivery)
class EmailVerificationDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "provider",
        "status",
        "attempts",
        "fallback_used",
        "last_error_code",
        "created_at",
        "sent_at",
    )
    list_filter = ("status", "provider", "fallback_used")
    search_fields = ("user__username", "user__email", "last_error_code")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_attempt_at",
        "sent_at",
    )
