from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.django_db
def test_load_item_templates_imports_large_work_chest_chunqiu_coin_rewards():
    call_command("load_item_templates", verbosity=0)

    large_chest = ItemTemplate.objects.get(key="work_chest_large")
    coin_groups = [
        group
        for group in large_chest.effect_payload["random_item_groups"]
        if group["choices"] == [{"item_key": "chunqiu_coin", "weight": 1}]
    ]

    assert coin_groups == [
        {
            "chance": 0.1,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": [{"item_key": "chunqiu_coin", "weight": 1}],
        },
        {
            "chance": 0.01,
            "min_quantity": 10,
            "max_quantity": 10,
            "choices": [{"item_key": "chunqiu_coin", "weight": 1}],
        },
    ]


@pytest.mark.django_db
def test_load_item_templates_synchronizes_imported_equipment(monkeypatch, tmp_path: Path):
    payload_path = tmp_path / "items.yaml"
    payload_path.write_text(
        """
items:
  - key: sync_hook_helmet
    name: 同步钩子头盔
    effect_type: equip_helmet
    rarity: blue
    effect_payload:
      hp: 120
""".strip(),
        encoding="utf-8",
    )
    captured: list[list[str]] = []

    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates.synchronize_equipment_templates",
        lambda keys: captured.append(list(keys)) or SimpleNamespace(changed=False),
    )

    call_command("load_item_templates", file=str(payload_path), verbosity=0)

    assert captured == [["sync_hook_helmet"]]


@pytest.mark.django_db
def test_load_item_templates_does_not_repair_grain_ledger_by_default(monkeypatch, tmp_path: Path):
    payload_path = tmp_path / "items.yaml"
    payload_path.write_text(
        """
items:
  - key: grain
    name: 粮食
    effect_type: resource
""".strip(),
        encoding="utf-8",
    )
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates.call_command",
        lambda name, **kwargs: calls.append((name, kwargs)),
    )
    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates.synchronize_equipment_templates",
        lambda keys: SimpleNamespace(changed=False),
    )

    call_command("load_item_templates", file=str(payload_path), verbosity=0)

    assert calls == []


@pytest.mark.django_db
def test_load_item_templates_repairs_grain_ledger_when_requested(monkeypatch, tmp_path: Path):
    payload_path = tmp_path / "items.yaml"
    payload_path.write_text(
        """
items:
  - key: grain
    name: 粮食
    effect_type: resource
""".strip(),
        encoding="utf-8",
    )
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates.call_command",
        lambda name, **kwargs: calls.append((name, kwargs)),
    )
    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates.synchronize_equipment_templates",
        lambda keys: SimpleNamespace(changed=False),
    )

    call_command("load_item_templates", file=str(payload_path), verbosity=0, repair_grain_ledger=True)

    assert calls == [("repair_grain_warehouse_ledger", {"verbosity": 0})]
