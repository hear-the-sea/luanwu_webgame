from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command

from gameplay.management.commands.load_item_templates import Command, _load_item_image
from gameplay.models import ItemTemplate


@pytest.mark.django_db
def test_load_item_image_oserror_is_best_effort(monkeypatch, tmp_path: Path):
    command = Command()
    template = ItemTemplate.objects.create(
        key="image_best_effort_item",
        name="测试物品",
        effect_type="none",
    )
    image_path = tmp_path / "item.png"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates.compress_and_resize_image",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("image backend down")),
    )

    _load_item_image(command, template, {"image": "item.png"}, tmp_path)

    template.refresh_from_db()
    assert not template.image


@pytest.mark.django_db
def test_load_item_image_programming_error_bubbles_up(monkeypatch, tmp_path: Path):
    command = Command()
    template = ItemTemplate.objects.create(
        key="image_programming_error_item",
        name="测试物品",
        effect_type="none",
    )
    image_path = tmp_path / "item.png"
    image_path.write_bytes(b"fake")

    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates.compress_and_resize_image",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("broken image contract")),
    )

    with pytest.raises(AssertionError, match="broken image contract"):
        _load_item_image(command, template, {"image": "item.png"}, tmp_path)


@pytest.mark.django_db
def test_load_item_templates_imports_edward_scroll_confirm_payload():
    call_command("load_item_templates", verbosity=0)

    blue_scroll = ItemTemplate.objects.get(key="edward_blue_guest_scroll")
    purple_scroll = ItemTemplate.objects.get(key="edward_purple_guest_scroll")

    assert blue_scroll.effect_type == ItemTemplate.EffectType.TOOL
    assert blue_scroll.is_usable is True
    assert blue_scroll.effect_payload["required_items"] == {"gold_bar": 50}
    assert blue_scroll.effect_payload["confirm_title"] == "使用确认"
    assert blue_scroll.effect_payload["confirm_text"] == (
        "确认使用「蓝色爱德华召唤卷轴」？将消耗 50 根金条，并获得 1 名蓝色门客「爱德华」。"
    )
    assert blue_scroll.effect_payload["confirm_ok_text"] == "确认使用"
    assert blue_scroll.effect_payload["choices"] == [{"template_key": "orig_edward_blue", "weight": 100}]

    assert purple_scroll.effect_type == ItemTemplate.EffectType.TOOL
    assert purple_scroll.is_usable is True
    assert purple_scroll.effect_payload["required_items"] == {"gold_bar": 150}
    assert purple_scroll.effect_payload["confirm_title"] == "使用确认"
    assert purple_scroll.effect_payload["confirm_text"] == (
        "确认使用「紫色爱德华召唤卷轴」？将消耗 150 根金条，并获得 1 名紫色门客「爱德华」。"
    )
    assert purple_scroll.effect_payload["confirm_ok_text"] == "确认使用"
    assert purple_scroll.effect_payload["choices"] == [{"template_key": "orig_edward_purple", "weight": 100}]
