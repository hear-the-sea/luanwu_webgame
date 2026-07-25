from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from django.contrib.messages import get_messages
from django.urls import reverse

pytest_plugins = ("tests.jail_views.fixtures",)

from tests.jail_views.support import (  # noqa: E402
    build_available_guests,
    build_bond_context,
    build_manor,
    build_prisoner_context,
    jail_url,
    message_objects,
    oath_url,
)


@pytest.mark.django_db
def test_recruit_prisoner_view_rejects_when_action_lock_conflicts(monkeypatch, authenticated_client_with_manor):
    client = authenticated_client_with_manor
    called = {"count": 0}

    monkeypatch.setattr("gameplay.views.jail._acquire_jail_action_lock", lambda *_a, **_k: (False, "", None))

    def _unexpected_recruit(*_args, **_kwargs):
        called["count"] += 1

    monkeypatch.setattr("gameplay.views.jail.recruit_prisoner", _unexpected_recruit)

    response = client.post(reverse("gameplay:recruit_prisoner_view", kwargs={"prisoner_id": 1}))
    assert response.status_code == 302
    assert response.url == jail_url()
    messages = message_objects(response)
    assert any(message.level_tag == "warning" and "请求处理中，请稍候重试" in message.message for message in messages)
    assert called["count"] == 0


@pytest.mark.django_db
def test_jail_page_uses_explicit_read_helper(monkeypatch, authenticated_client_with_manor, django_user_model):
    client = authenticated_client_with_manor
    manor = build_manor(django_user_model, username="jail_view_user")
    calls = {"prepared": 0}

    monkeypatch.setattr(
        "gameplay.views.jail.get_prepared_manor_for_read",
        lambda request, **kwargs: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
    )
    monkeypatch.setattr(
        "gameplay.views.jail.get_jail_page_context",
        lambda current_manor: (
            {
                "jail_capacity": 0,
                "prisoners": [],
                "capture_rate_percent": 0,
                "recruit_loyalty_threshold": 0,
                "recruit_cost_gold_bar": 0,
            }
            if current_manor is manor
            else {}
        ),
    )

    response = client.get(jail_url())

    assert response.status_code == 200
    assert calls["prepared"] == 1


@pytest.mark.django_db
def test_jail_page_uses_selector_context(monkeypatch, authenticated_client_with_manor, django_user_model):
    client = authenticated_client_with_manor
    manor = build_manor(django_user_model, username="jail_view_user")
    calls = {"selector": 0}

    monkeypatch.setattr("gameplay.views.jail.get_prepared_manor_for_read", lambda request, **kwargs: manor)

    def _fake_selector(current_manor):
        calls["selector"] += 1
        assert current_manor is manor
        return {
            "jail_capacity": 3,
            "prisoners": build_prisoner_context(),
            "capture_rate_percent": 25,
            "recruit_loyalty_threshold": 30,
            "recruit_cost_gold_bar": 2,
        }

    monkeypatch.setattr("gameplay.views.jail.get_jail_page_context", _fake_selector)

    response = client.get(jail_url())

    assert response.status_code == 200
    assert calls["selector"] == 1
    assert response.context["prisoners"][0].display_name == "prisoner-a"


@pytest.mark.django_db
def test_oath_grove_page_uses_explicit_read_helper(monkeypatch, authenticated_client_with_manor, django_user_model):
    client = authenticated_client_with_manor
    manor = build_manor(django_user_model, username="jail_view_user")
    calls = {"prepared": 0}

    monkeypatch.setattr(
        "gameplay.views.jail.get_prepared_manor_for_read",
        lambda request, **kwargs: calls.__setitem__("prepared", calls["prepared"] + 1) or manor,
    )
    monkeypatch.setattr(
        "gameplay.views.jail.get_oath_grove_page_context",
        lambda current_manor: (
            {
                "oath_capacity": 0,
                "bonds": [],
                "available_guests": [],
            }
            if current_manor is manor
            else {}
        ),
    )

    response = client.get(oath_url())

    assert response.status_code == 200
    assert calls["prepared"] == 1


@pytest.mark.django_db
def test_oath_grove_page_uses_selector_context(monkeypatch, authenticated_client_with_manor, django_user_model):
    client = authenticated_client_with_manor
    manor = build_manor(django_user_model, username="jail_view_user")
    calls = {"selector": 0}

    monkeypatch.setattr("gameplay.views.jail.get_prepared_manor_for_read", lambda request, **kwargs: manor)

    def _fake_selector(current_manor):
        calls["selector"] += 1
        assert current_manor is manor
        return {
            "oath_capacity": 5,
            "bonds": build_bond_context(),
            "available_guests": build_available_guests(),
        }

    monkeypatch.setattr("gameplay.views.jail.get_oath_grove_page_context", _fake_selector)

    response = client.get(oath_url())

    assert response.status_code == 200
    assert calls["selector"] == 1
    assert response.context["bonds"][0].guest.display_name == "bond-a"


@pytest.mark.django_db
def test_oath_grove_page_localizes_rarity_and_omits_jail_link(
    monkeypatch,
    authenticated_client_with_manor,
    django_user_model,
):
    client = authenticated_client_with_manor
    manor = build_manor(django_user_model, username="jail_view_user")
    monkeypatch.setattr("gameplay.views.jail.get_prepared_manor_for_read", lambda request, **kwargs: manor)
    monkeypatch.setattr(
        "gameplay.views.jail.get_oath_grove_page_context",
        lambda current_manor: {
            "oath_capacity": 5,
            "bonds": build_bond_context(),
            "available_guests": build_available_guests(),
        },
    )

    response = client.get(oath_url())

    page = BeautifulSoup(response.content.decode("utf-8"), "html.parser")
    guest_option = page.select_one('select[name="guest_id"] option[value="3"]')
    bond_row = page.select_one(".tw-table tbody tr")
    assert guest_option is not None
    assert guest_option.get_text(strip=True) == "guest-a（绿，等级 5）"
    assert bond_row is not None
    assert bond_row.select("td")[1].get_text(strip=True) == "蓝"
    assert page.find("a", string="前往监牢") is None
    assert "结义只影响" not in page.get_text()


@pytest.mark.django_db
def test_remove_oath_bond_view_uses_error_message_when_guest_not_bonded(monkeypatch, authenticated_client_with_manor):
    client = authenticated_client_with_manor
    monkeypatch.setattr("gameplay.views.jail.remove_oath_bond", lambda *_args, **_kwargs: 0)

    response = client.post(reverse("gameplay:remove_oath_bond_view", kwargs={"guest_id": 1}))
    assert response.status_code == 302
    assert response.url == oath_url()
    messages = list(get_messages(response.wsgi_request))
    assert any(message.level_tag == "error" and "该门客未结义" in message.message for message in messages)
