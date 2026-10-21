"""离线渲染门客列表入口，覆盖工资按钮出现时的移动端布局。"""

import os
import sys
from types import SimpleNamespace

import django
import pytest  # noqa: F401
from django.template.loader import render_to_string

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

unpaid = sys.argv[1] == "unpaid"
print(
    render_to_string(
        "guests/roster.html",
        {
            "guests": [],
            "manor": SimpleNamespace(silver=100000),
            "unpaid_count": 20 if unpaid else 0,
            "total_unpaid_salary": 12345,
            "healable_count": 2,
            "medicine_quantity": 3,
            "csrf_token": "preview-token",
        },
    )
)
