import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection
from django.template import Context, Template
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.templatetags.gameplay_extras import GUILD_CARD_TECHNOLOGY_ICONS
from guilds.technology_tree import TECHNOLOGY_ROUTES


@pytest.mark.parametrize(
    "item_key,node_key",
    [
        ("guild_slave_card", "forging_slave_card"),
        ("guild_theft_card", "forging_theft_card"),
        ("guild_injury_card", "forging_injury_card"),
        ("guild_grain_boost_card", "sheep_grain_card"),
        ("guild_silver_boost_card", "sheep_silver_card"),
    ],
)
def test_card_icon_reuses_corresponding_technology_node(item_key, node_key):
    nodes = {node["key"]: node for route in TECHNOLOGY_ROUTES for node in route["nodes"]}
    index = nodes[node_key]["icon_index"]
    assert GUILD_CARD_TECHNOLOGY_ICONS[item_key] == index
    html = Template("{% load gameplay_extras %}{% item_icon key image label %}").render(
        Context({"key": item_key, "image": "/media/old.png", "label": "卡片"})
    )
    assert "images/guild_technology/talent_icon_atlas.png" in html
    assert f"tw-tech-node-icon--{index}" in html
    assert "/media/old.png" not in html


def test_wrath_icon_reuses_technology_illustration_and_regular_items_keep_their_image():
    template = Template("{% load gameplay_extras %}{% item_icon key image label %}")
    html = template.render(Context({"key": "guild_sheep_wrath", "image": "", "label": "羊的愤怒"}))
    assert "images/guild_item_icons/guild_sheep_wrath.webp" in html
    html = template.render(Context({"key": "yijinjing", "image": "/media/items/yijinjing.png", "label": "易筋经"}))
    assert 'src="/static/images/item_icons/yijinjing.webp"' in html
    assert "/media/items/yijinjing.png" not in html
    html = template.render(Context({"key": "other_item", "image": "/media/regular.webp", "label": "普通道具"}))
    assert 'src="/media/regular.webp"' in html
    html = template.render(Context({"key": "other_item", "image": "", "label": "普通道具"}))
    assert "tw-icon-placeholder" in html


@pytest.mark.django_db
def test_equalization_template_migration_matches_catalog():
    migration = importlib.import_module("gameplay.migrations.0213_guild_resource_equalization_cards")
    catalog = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/item_templates.yaml").read_text())
    for resource in ("grain", "silver"):
        ItemTemplate.objects.update_or_create(
            key=f"guild_{resource}_boost_card",
            defaults={"name": "旧道具", "description": "旧说明", "effect_payload": {"action": "old_action"}},
        )
    migration.update_equalization_cards(apps, SimpleNamespace(connection=connection))
    for resource in ("grain", "silver"):
        key = f"guild_{resource}_boost_card"
        template = ItemTemplate.objects.get(key=key)
        entry = next(item for item in catalog["items"] if item["key"] == key)
        assert template.effect_payload == entry["effect_payload"]
        assert template.description == entry["description"]


@pytest.mark.django_db
@pytest.mark.parametrize("resource,name", [("grain", "均粮卡"), ("silver", "均贫卡")])
def test_warehouse_resource_cards_show_technology_icon_and_target_selection(manor_with_user, resource, name):
    manor, client = manor_with_user
    template, _ = ItemTemplate.objects.update_or_create(
        key=f"guild_{resource}_boost_card",
        defaults={
            "name": name,
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": True,
            "effect_payload": {"action": f"transfer_target_{resource}"},
            "image": "",
        },
    )
    InventoryItem.objects.create(manor=manor, template=template, quantity=1)
    response = client.get(reverse("gameplay:warehouse"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'class="tw-btn-primary tw-btn-sm js-open-target-item-modal"' in body
    assert 'id="target-item-use-form"' in body
    assert 'name="target_manor_id"' in body
    assert f"tw-tech-node-icon--{GUILD_CARD_TECHNOLOGY_ICONS[template.key]}" in body


@pytest.mark.django_db
def test_guild_assets_reuse_all_six_technology_icons(manor_with_user):
    from guilds.models import Guild, GuildMember, GuildWarehouse

    manor, client = manor_with_user
    guild = Guild.objects.create(name="复用卡片图标帮", founder=manor.user, is_active=True)
    GuildMember.objects.create(guild=guild, user=manor.user, position="leader", is_active=True)
    for key in (*GUILD_CARD_TECHNOLOGY_ICONS, "guild_sheep_wrath"):
        GuildWarehouse.objects.create(guild=guild, item_key=key, quantity=2, contribution_cost=200)
    response = client.get(reverse("guilds:warehouse"))
    assert response.status_code == 200
    body = response.content.decode()
    for index in GUILD_CARD_TECHNOLOGY_ICONS.values():
        assert f"tw-tech-node-icon--{index}" in body
    assert "images/guild_item_icons/guild_sheep_wrath.webp" in body
