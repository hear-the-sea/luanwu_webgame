"""
视图层权限测试
"""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestPermissions:
    """权限测试"""

    def test_protected_pages_redirect(self, client):
        """受保护页面重定向到登录"""
        protected_urls = [
            reverse("gameplay:dashboard"),
            reverse("gameplay:tasks"),
            reverse("gameplay:warehouse"),
            reverse("gameplay:messages"),
            reverse("gameplay:technology"),
            reverse("gameplay:work"),
            reverse("gameplay:stable"),
            reverse("gameplay:map"),
        ]
        for url in protected_urls:
            response = client.get(url)
            assert response.status_code == 302, f"{url} should redirect"

    def test_api_requires_login(self, client):
        """API需要登录"""
        api_urls = [
            reverse("gameplay:map_search_api"),
            reverse("gameplay:raid_status_api"),
            reverse("gameplay:protection_status_api"),
        ]
        for url in api_urls:
            response = client.get(url)
            assert response.status_code == 302, f"{url} should redirect"

    def test_post_api_requires_login(self, client):
        """受保护的 POST API 需要登录"""
        for url in [
            reverse("gameplay:map_backfill_request_api"),
            reverse("gameplay:refresh_raid_activity_api"),
        ]:
            response = client.post(url)
            assert response.status_code == 302
