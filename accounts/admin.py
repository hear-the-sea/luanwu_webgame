from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from core.admin_i18n import apply_common_field_labels

from .models import EmailSendQuota, User

apply_common_field_labels(User, labels={"username": "用户名", "title": "称号"})

admin.site.site_header = "江湖游戏后台管理"
admin.site.site_title = "江湖游戏后台"
admin.site.index_title = "运营管理"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    _base_fieldsets: tuple[Any, ...] = tuple(DjangoUserAdmin.fieldsets or ())
    fieldsets = _base_fieldsets + (
        ("游戏信息", {"fields": ("title",)}),
        ("验证状态", {"fields": ("email_verified",)}),
    )
    list_display = ("username", "email", "email_verified", "title", "is_staff", "date_joined")
    list_filter = (*DjangoUserAdmin.list_filter, "email_verified")
    search_fields = ("username", "email", "title")


@admin.register(EmailSendQuota)
class EmailSendQuotaAdmin(admin.ModelAdmin):
    list_display = ("month", "sent_count", "updated_at")
    readonly_fields = ("created_at", "updated_at")
