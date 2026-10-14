from django.contrib import admin
from django.contrib.admin.models import LogEntry


class ReadOnlyAdmin(admin.ModelAdmin):
    actions = None
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(LogEntry)
class OperationLogAdmin(ReadOnlyAdmin):
    list_display = ("action_time", "user", "content_type", "object_repr", "operation", "summary")
    list_filter = ("action_flag", "content_type", "action_time")
    search_fields = ("user__username", "object_repr", "change_message")
    list_select_related = ("user", "content_type")
    date_hierarchy = "action_time"
    ordering = ("-action_time", "-pk")
    exclude = ("change_message",)

    def get_readonly_fields(self, request, obj=None):
        return tuple(name for name in super().get_readonly_fields(request, obj) if name != "change_message") + (
            "summary",
        )

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    @admin.display(description="操作类型", ordering="action_flag")
    def operation(self, obj):
        return obj.get_action_flag_display()

    @admin.display(description="操作详情")
    def summary(self, obj):
        return obj.get_change_message()
