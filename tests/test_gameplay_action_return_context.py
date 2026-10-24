from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import DatabaseError
from django.urls import reverse

from core.exceptions import TroopRecruitmentError


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", [True, False, "database_error"])
@pytest.mark.parametrize("next_url", ["/", "https://outside.example/leave"])
def test_cancel_technology_returns_to_safe_origin(manor_with_user, monkeypatch, outcome, next_url):
    _manor, client = manor_with_user

    def cancel(*_args):
        if outcome == "database_error":
            raise DatabaseError("database unavailable")
        return outcome

    monkeypatch.setattr("gameplay.views.technology.cancel_technology_upgrade", cancel)
    response = client.post(reverse("gameplay:cancel_technology_upgrade", args=["dao_attack"]), {"next": next_url})

    assert response.status_code == 302
    assert response.url == (reverse("home") if next_url == "/" else reverse("gameplay:dashboard"))


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", ["success", "invalid_quantity", "known_error", "database_error"])
def test_troop_recruitment_keeps_category_and_page(manor_with_user, monkeypatch, outcome):
    _manor, client = manor_with_user

    def start(*_args):
        if outcome == "known_error":
            raise TroopRecruitmentError("暂时无法招募")
        if outcome == "database_error":
            raise DatabaseError("database unavailable")
        if outcome == "invalid_quantity":
            pytest.fail("invalid quantities must not start recruitment")
        return SimpleNamespace(troop_name="测试护院", quantity=1, actual_duration=60)

    monkeypatch.setattr("gameplay.services.recruitment.recruitment.start_troop_recruitment", start)
    response = client.post(
        reverse("gameplay:start_troop_recruitment"),
        {
            "troop_key": "test",
            "quantity": "bad" if outcome == "invalid_quantity" else "1",
            "category": "jian",
            "page": "2",
        },
    )

    assert response.status_code == 302
    target = urlsplit(response.url)
    assert target.path == reverse("gameplay:troop_recruitment")
    assert parse_qs(target.query) == {"category": ["jian"], "page": ["2"]}


@pytest.mark.django_db
def test_troop_recruitment_forms_include_current_page(manor_with_user):
    _manor, client = manor_with_user
    response = client.get(reverse("gameplay:troop_recruitment") + "?page=2")
    assert response.status_code == 200
    assert response.context["recruitment_page_obj"].number == 2
    assert 'name="page" value="2"' in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("outcome", ["success", "invalid_quantity", "database_error"])
def test_smithy_start_keeps_medicine_category(manor_with_user, monkeypatch, outcome):
    _manor, client = manor_with_user
    target = reverse("gameplay:smithy") + "?category=medicine&page=2"

    def start(*_args):
        if outcome == "database_error":
            raise DatabaseError("database unavailable")
        if outcome == "invalid_quantity":
            pytest.fail("invalid quantities must not start production")
        return SimpleNamespace(metal_name="测试丹药", quantity=1, actual_duration=60)

    monkeypatch.setattr("gameplay.views.production.smithy_service.start_smelting_production", start)
    response = client.post(
        reverse("gameplay:start_smelting_production"),
        {
            "metal_key": "test",
            "quantity": "bad" if outcome == "invalid_quantity" else "1",
            "next": target,
        },
    )

    assert response.status_code == 302
    assert response.url == target


@pytest.mark.django_db
@pytest.mark.parametrize("external", [False, True])
def test_smithy_cancel_keeps_safe_category(manor_with_user, monkeypatch, external):
    _manor, client = manor_with_user
    target = reverse("gameplay:smithy") + "?category=medicine&page=2"
    monkeypatch.setattr("gameplay.views.production.smithy_service.cancel_smelting_production", lambda *_args: None)
    response = client.post(
        reverse("gameplay:cancel_smelting_production", args=[1]),
        {"next": "https://outside.example/leave" if external else target},
    )

    assert response.status_code == 302
    assert response.url == (reverse("gameplay:smithy") if external else target)


@pytest.mark.django_db
def test_smithy_forms_submit_the_selected_category(manor_with_user):
    _manor, client = manor_with_user
    target = reverse("gameplay:smithy") + "?category=medicine"
    response = client.get(target)
    assert response.status_code == 200
    assert f'name="next" value="{target}"' in response.content.decode()
