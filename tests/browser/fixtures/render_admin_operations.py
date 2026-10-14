"""使用真实后台模板生成不依赖数据库与端口服务的浏览器测试页面。"""

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.argv.append("test")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.contrib import admin  # noqa: E402
from django.contrib.staticfiles import finders  # noqa: E402
from django.template import engines  # noqa: E402
from django.test import RequestFactory  # noqa: E402

request = RequestFactory().get("/admin/")
request.user = SimpleNamespace(
    is_active=True,
    is_staff=True,
    is_superuser=True,
    pk=1,
    has_perm=lambda *args: True,
    has_module_perms=lambda *args: True,
)
response = admin.site.index(request)
template = engines["django"].from_string('{% extends "admin/operations_index.html" %}{% block sidebar %}{% endblock %}')
html = template.render({**response.context_data, "csrf_token": "local-preview"})


def inline_style(match):
    asset_path = finders.find(match.group(1))
    return f"<style>{Path(asset_path).read_text()}</style>"


html = re.sub(r'<link[^>]+href="/static/([^"]+)"[^>]*>', inline_style, html)
html = re.sub(r'<script[^>]+src="[^"]+"[^>]*>\s*</script>', "", html)
print(html)
