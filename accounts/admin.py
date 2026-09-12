from typing import Any

from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views.decorators.http import require_http_methods

from core.admin_i18n import apply_common_field_labels
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
