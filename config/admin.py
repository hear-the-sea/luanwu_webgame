from django.contrib.admin.apps import AdminConfig


class GameAdminConfig(AdminConfig):
    default_site = "core.admin_site.GameAdminSite"

    def ready(self):
        super().ready()
        from django.contrib import admin

        from core.admin_i18n import apply_registered_admin_labels

        apply_registered_admin_labels(admin.site)
