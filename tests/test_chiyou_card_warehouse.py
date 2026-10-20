from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.selectors.warehouse import get_warehouse_context
from guests.models import Guest, GuestTemplate, WorldUniqueGuest

pytestmark = pytest.mark.django_db


@pytest.fixture
def card_world(manor_with_user):
    manor, client = manor_with_user
    choices = []
    templates = []
    for key, name in (("hero_yang_zheng_purple", "杨铮"), ("hero_di_qinglin", "狄青麟")):
        template, _ = GuestTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": name,
                "rarity": "purple",
                "archetype": "military",
                "is_world_unique": True,
                "flavor": f"{name}的真实人物介绍",
            },
        )
        templates.append(template)
        choices.append({"template_key": key, "label": "不能信任的配置称号", "weight": 1})
    card, _ = ItemTemplate.objects.update_or_create(
        key="chiyou_card",
        defaults={
            "name": "蚩尤卡",
            "effect_type": "tool",
            "is_usable": True,
            "effect_payload": {
                "action": "summon_guest",
                "player_choice": True,
                "required_items": {"chiyou_card": 99},
                "choices": choices,
            },
        },
    )
    # 此组覆盖尚未建立归属行的只读页面；迁移已预建在野状态时先隔离夹具。
    WorldUniqueGuest.objects.filter(template__in=templates, status="wild").delete()
    stock = InventoryItem.objects.create(manor=manor, template=card, quantity=100)
    return manor, client, stock, templates


def _use_forms(client, stock):
    response = client.get(reverse("gameplay:warehouse"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    return soup.find_all("form", action=reverse("gameplay:use_item", args=[stock.pk]))


def test_choice_form_requires_explicit_selection_and_shows_real_templates(card_world):
    _manor, client, stock, templates = card_world

    form = _use_forms(client, stock)[0]

    options = form.find_all("input", attrs={"name": "guest_template_key", "type": "radio"})
    assert [option["value"] for option in options] == [template.key for template in templates]
    for template, option in zip(templates, options):
        label = option.find_parent("label")
        assert template.name in label.get_text()
        assert "全服唯一" in label.get_text() and "在野" in label.get_text()
        assert option.has_attr("required") and not option.has_attr("checked")
        assert not option.has_attr("disabled")
        assert template.flavor in form.get_text()
    assert "不能信任的配置称号" not in form.get_text()
    assert "每次消耗100张蚩尤卡" in form.get_text()
    assert form.find("button", type="submit").get_text() == "确认兑换"
    assert form.find("button", type="submit").has_attr("disabled")
    assert form.find("input", attrs={"name": "csrfmiddlewaretoken"}) is not None
    modal = form.find_parent(attrs={"role": "dialog"})
    assert modal["aria-hidden"] == "true" and "display: none" in modal["style"]


def test_warehouse_card_row_only_has_a_use_button(card_world):
    _manor, client, stock, _templates = card_world
    soup = BeautifulSoup(client.get(reverse("gameplay:warehouse")).content, "html.parser")
    for button in soup.select(".js-open-player-choice-modal"):
        row = button.find_parent("tr")
        actions = row.select_one(".tw-warehouse-actions")
        assert [entry.get_text(strip=True) for entry in actions.find_all("button")] == ["使用"]
        assert not actions.find_all(["form", "select", "details"])
        assert "每次消耗" not in actions.get_text()
        assert soup.find(id=button["data-player-choice-modal"]) is not None
    assert soup.select(".js-open-player-choice-modal")


def test_selector_reads_do_not_create_unique_states_or_modify_inventory(card_world):
    manor, _client, stock, _templates = card_world
    before_states = WorldUniqueGuest.objects.count()
    before_guests = Guest.objects.count()

    with CaptureQueriesContext(connection) as queries:
        context = get_warehouse_context(manor, "warehouse", "all")

    assert not any(
        query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in queries.captured_queries
    )
    row = next(row for row in context["inventory_items"] if row.pk == stock.pk)
    assert row.player_choice_cost == 100
    assert all(choice["is_world_unique"] for choice in row.player_choice_options)
    assert WorldUniqueGuest.objects.count() == before_states
    assert Guest.objects.count() == before_guests
    stock.refresh_from_db()
    assert stock.quantity == 100
    assert not hasattr(row, "world_unique_use_status_summary")


def test_serving_choice_is_disabled_without_selecting_remaining_guest(card_world, manor_factory):
    _manor, client, stock, templates = card_world
    owner, _ = manor_factory(username="claimed_choice_owner")
    guest = Guest.objects.create(manor=owner, template=templates[0])
    WorldUniqueGuest.objects.create(template=templates[0], status="serving", owner_manor=owner, owner_guest=guest)

    form = _use_forms(client, stock)[0]

    taken = form.find("input", value=templates[0].key)
    available = form.find("input", value=templates[1].key)
    assert taken.has_attr("disabled") and "仕官" in taken.find_parent("label").get_text()
    assert not available.has_attr("disabled")
    assert not form.find("input", attrs={"name": "guest_template_key", "checked": True})


def test_missing_template_is_disabled_and_never_substituted(card_world):
    _manor, client, stock, templates = card_world
    templates[0].delete()

    form = _use_forms(client, stock)[0]

    missing = form.find("input", value=templates[0].key)
    assert missing.has_attr("disabled")
    assert "配置缺失" in missing.find_parent("label").get_text()
    assert not form.find("input", value=templates[1].key).has_attr("checked")


@pytest.mark.parametrize("quantity,bound,enabled", [(99, 0, False), (100, 40, True)])
def test_card_requirement_uses_total_stock_across_binding_display_rows(card_world, quantity, bound, enabled):
    _manor, client, stock, _templates = card_world
    stock.quantity = quantity
    stock.bound_quantity = bound
    stock.save(update_fields=["quantity", "bound_quantity"])

    forms = _use_forms(client, stock)

    assert len(forms) == (2 if bound else 1)
    for form in forms:
        assert (form["data-has-stock"] == "true") is enabled
        assert form.find("button", type="submit").has_attr("disabled")
        assert "每次消耗100张蚩尤卡" in form.get_text()
        assert f"仓库共{quantity}张" in form.get_text()
        if not enabled:
            assert "数量不足100张" in form.get_text()


def test_all_unavailable_choices_disable_redemption(card_world, manor_factory):
    _manor, client, stock, templates = card_world
    owner, _ = manor_factory(username="all_choice_owner")
    for template in templates:
        guest = Guest.objects.create(manor=owner, template=template)
        WorldUniqueGuest.objects.create(template=template, status="serving", owner_manor=owner, owner_guest=guest)

    form = _use_forms(client, stock)[0]

    assert form.find("button", type="submit").has_attr("disabled")
    assert "暂无可兑换的门客" in form.get_text()
    assert all(option.has_attr("disabled") for option in form.find_all("input", attrs={"name": "guest_template_key"}))


@pytest.mark.parametrize("choice_index", [0, 1])
def test_form_posts_explicit_template_key_to_existing_use_endpoint(card_world, choice_index):
    manor, client, stock, templates = card_world
    form = _use_forms(client, stock)[0]
    target = templates[choice_index]
    post_data = {"binding_mode": form.find("input", attrs={"name": "binding_mode"})["value"]}
    post_data[form.find("input", attrs={"name": "guest_template_key"})["name"]] = target.key

    response = client.post(form["action"], post_data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 200, response.content
    assert response.json()["success"] is True
    assert Guest.objects.filter(manor=manor, template=target).count() == 1
    assert not Guest.objects.filter(manor=manor, template=templates[1 - choice_index]).exists()
    assert not InventoryItem.objects.filter(pk=stock.pk).exists()
