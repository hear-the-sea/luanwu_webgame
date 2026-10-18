from __future__ import annotations

from datetime import timedelta
from html.parser import HTMLParser

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.models import PlayerTroop, TroopBankStorage, TroopRecruitment
from gameplay.services.manor.core import ensure_manor
from gameplay.services.manor.troop_capacity import MANOR_TROOP_CAPACITY

pytestmark = pytest.mark.django_db


@pytest.fixture
def bank_player(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="bulk_bank_player", password="pass12345")
    manor = ensure_manor(user)
    client.force_login(user)
    monkeypatch.setattr("trade.selectors.get_bank_info", lambda *_args: {"exchange_available": True})
    templates = {}
    for key, name in [("dao_ke", "刀客"), ("gong_shou", "弓手")]:
        templates[key] = TroopTemplate.objects.create(
            key=key, name=name, base_attack=10, base_defense=10, base_hp=10, default_count=0
        )
    return manor, client, templates


class BankForms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current = self.forms.setdefault(attributes.get("action"), [])
        elif self.current is not None and tag in {"input", "button"}:
            self.current.append((tag, attributes))

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


@pytest.mark.parametrize(
    "route,source,destination",
    [
        ("deposit_all_troops_to_bank", PlayerTroop, TroopBankStorage),
        ("withdraw_all_troops_from_bank", TroopBankStorage, PlayerTroop),
    ],
)
def test_bulk_bank_view_transfers_only_selected_category_and_preserves_redirect(
    bank_player, route, source, destination
):
    manor, client, templates = bank_player
    source.objects.create(manor=manor, troop_template=templates["dao_ke"], count=20)
    source.objects.create(manor=manor, troop_template=templates["gong_shou"], count=30)

    response = client.post(reverse(f"trade:{route}"), {"troop_category": "dao"})

    assert response.status_code == 302
    assert response.url == reverse("trade:trade") + "?tab=bank&troop_category=dao"
    assert destination.objects.get(manor=manor, troop_template=templates["dao_ke"]).count == 20
    assert source.objects.get(manor=manor, troop_template=templates["gong_shou"]).count == 30
    assert not destination.objects.filter(manor=manor, troop_template=templates["gong_shou"]).exists()
    assert any("20 名护院" in str(message) for message in get_messages(response.wsgi_request))


def test_bulk_bank_view_reports_partial_transfer_from_current_capacity(bank_player):
    manor, client, templates = bank_player
    PlayerTroop.objects.create(manor=manor, troop_template=templates["dao_ke"], count=20)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["gong_shou"], count=4997)

    response = client.post(reverse("trade:deposit_all_troops_to_bank"), {"troop_category": "all", "quantity": "999999"})

    assert response.status_code == 302
    assert PlayerTroop.objects.get(manor=manor, troop_template=templates["dao_ke"]).count == 17
    assert TroopBankStorage.objects.get(manor=manor, troop_template=templates["dao_ke"]).count == 3
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("已一键存入 3 名" in message and "剩余 17 名护院仍保留在庄园" in message for message in messages)


@pytest.mark.parametrize("route", ["deposit_all_troops_to_bank", "withdraw_all_troops_from_bank"])
def test_bulk_bank_view_rejects_invalid_category_and_get(bank_player, route):
    manor, client, templates = bank_player
    PlayerTroop.objects.create(manor=manor, troop_template=templates["dao_ke"], count=20)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["dao_ke"], count=30)

    assert client.get(reverse(f"trade:{route}")).status_code == 405
    response = client.post(reverse(f"trade:{route}"), {"troop_category": "bad-category"})

    assert response.status_code == 302
    assert any("有效的护院分类" in str(message) for message in get_messages(response.wsgi_request))
    assert PlayerTroop.objects.get(manor=manor, troop_template=templates["dao_ke"]).count == 20
    assert TroopBankStorage.objects.get(manor=manor, troop_template=templates["dao_ke"]).count == 30


@pytest.mark.parametrize("route", ["deposit_all_troops_to_bank", "withdraw_all_troops_from_bank"])
def test_bulk_bank_view_requires_login(client, route):
    response = client.post(reverse(f"trade:{route}"))
    assert response.status_code == 302
    assert "/login" in response.url


@pytest.mark.parametrize(
    "route,service",
    [
        ("deposit_all_troops_to_bank", "deposit_available_troops_to_bank"),
        ("withdraw_all_troops_from_bank", "withdraw_available_troops_from_bank"),
    ],
)
def test_bulk_bank_view_handles_database_errors(bank_player, monkeypatch, route, service):
    _, client, _ = bank_player

    def unavailable(*_args):
        raise DatabaseError("database unavailable")

    monkeypatch.setattr(f"trade.views.troop_bank_service.{service}", unavailable)
    response = client.post(reverse(f"trade:{route}"), {"troop_category": "dao"})

    assert response.status_code == 302
    assert "troop_category=dao" in response.url
    assert any("操作失败，请稍后重试" in str(message) for message in get_messages(response.wsgi_request))


def test_bank_page_limits_manual_and_bulk_quantities_to_remaining_capacity(bank_player):
    manor, client, templates = bank_player
    PlayerTroop.objects.create(manor=manor, troop_template=templates["dao_ke"], count=MANOR_TROOP_CAPACITY - 5)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["dao_ke"], count=4990)

    response = client.get(reverse("trade:trade"), {"tab": "bank", "troop_category": "dao"})

    assert response.status_code == 200
    assert response.context["troop_bank_deposit_max"] == 10
    assert response.context["troop_bank_withdraw_max"] == 5
    assert response.context["troop_bank_rows"][0]["deposit_max"] == 10
    assert response.context["troop_bank_rows"][0]["withdraw_max"] == 5
    forms = BankForms(response.content.decode()).forms
    for route, maximum in [("deposit_troop_to_bank", "10"), ("withdraw_troop_from_bank", "5")]:
        fields = forms[reverse(f"trade:{route}")]
        quantity = next(attrs for tag, attrs in fields if tag == "input" and attrs.get("name") == "quantity")
        assert quantity["max"] == maximum
        assert "disabled" not in quantity


def test_bank_page_disables_withdrawals_when_pending_recruitment_reserves_all_space(bank_player):
    manor, client, templates = bank_player
    PlayerTroop.objects.create(manor=manor, troop_template=templates["dao_ke"], count=MANOR_TROOP_CAPACITY - 10)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["dao_ke"], count=20)
    TroopRecruitment.objects.create(
        manor=manor,
        troop_key="dao_ke",
        troop_name="刀客",
        quantity=10,
        equipment_costs={},
        retainer_cost=10,
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() + timedelta(minutes=1),
    )

    response = client.get(reverse("trade:trade"), {"tab": "bank"})

    assert response.status_code == 200
    assert response.context["manor_troop_reserved"] == 10
    assert response.context["troop_bank_withdraw_max"] == 0
    forms = BankForms(response.content.decode()).forms
    for route in ["withdraw_all_troops_from_bank", "withdraw_troop_from_bank"]:
        assert all("disabled" in attrs for tag, attrs in forms[reverse(f"trade:{route}")] if tag == "button")
    assert "正在招募的 10 名护院已预留位置" in response.content.decode()


def test_bank_page_disables_deposits_when_bank_is_full(bank_player):
    manor, client, templates = bank_player
    PlayerTroop.objects.create(manor=manor, troop_template=templates["dao_ke"], count=10)
    TroopBankStorage.objects.create(manor=manor, troop_template=templates["dao_ke"], count=5000)

    response = client.get(reverse("trade:trade"), {"tab": "bank"})

    assert response.status_code == 200
    assert response.context["troop_bank_deposit_max"] == 0
    forms = BankForms(response.content.decode()).forms
    for route in ["deposit_all_troops_to_bank", "deposit_troop_to_bank"]:
        assert all("disabled" in attrs for tag, attrs in forms[reverse(f"trade:{route}")] if tag == "button")
    assert "钱庄已满，可先取出护院再存入" in response.content.decode()
