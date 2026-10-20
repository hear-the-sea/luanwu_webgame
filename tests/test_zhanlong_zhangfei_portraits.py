from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.core.files.storage import default_storage
from django.db import connection
from PIL import Image

from guests.models import GuestTemplate

rules = import_module("gameplay.migrations.0224_zhanlong_zhangfei_portraits")


def test_both_task_zhangfei_portraits_use_requested_asset():
    source = Path(__file__).resolve().parents[1] / "data/guests/zhanlong.yaml"
    heroes = yaml.safe_load(source.read_text())["heroes"]
    templates = {row["key"]: row for rows in heroes.values() for row in rows}
    for key in rules.TEMPLATE_KEYS:
        assert "guests/" + templates[key]["avatar"] == rules.SOURCE_IMAGE


@pytest.mark.django_db
def test_portrait_sync_updates_both_tasks_and_preserves_every_other_field(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    for key in (*rules.TEMPLATE_KEYS, "zhangfei_unrelated"):
        GuestTemplate.objects.update_or_create(
            key=key, defaults={"name": "张飞", "avatar": "guests/old.png", "base_attack": 123}
        )
    before = {row["key"]: row for row in GuestTemplate.objects.filter(key__in=rules.TEMPLATE_KEYS).values()}
    for _ in range(2):
        rules.sync_zhangfei_portraits(apps, SimpleNamespace(connection=connection))
    avatars = set()
    for template in GuestTemplate.objects.filter(key__in=rules.TEMPLATE_KEYS):
        after = GuestTemplate.objects.filter(pk=template.pk).values().get()
        original = before[template.key]
        assert {key: value for key, value in after.items() if key != "avatar"} == {
            key: value for key, value in original.items() if key != "avatar"
        }
        avatars.add(template.avatar.name)
        assert len(template.avatar.name) < GuestTemplate._meta.get_field("avatar").max_length
        assert template.avatar.name.startswith("guests/zhanlong/zhangfei_wu047_")
        with default_storage.open(template.avatar.name, "rb") as stored:
            with Image.open(stored) as image:
                assert image.format == "WEBP" and max(image.size) <= 400
    assert len(avatars) == 1
    assert GuestTemplate.objects.get(key="zhangfei_unrelated").avatar.name == "guests/old.png"
