import pytest
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate


def _create_guest_with_points(manor, key_suffix: str = "default") -> Guest:
    template = GuestTemplate.objects.create(
        key=f"alloc_points_tpl_{key_suffix}",
        name=f"加点门客{key_suffix}",
        archetype="civil",
        rarity="green",
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        force=80,
        intellect=70,
        defense_stat=60,
        agility=50,
        luck=40,
        attribute_points=2,
    )


@pytest.mark.django_db
def test_allocate_points_ajax_returns_attribute_panel_html(client, django_user_model):
    user = django_user_model.objects.create_user(username="alloc_points_ajax", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest_with_points(manor, "ajax")

    client.login(username="alloc_points_ajax", password="pass123")
    detail_url = reverse("guests:detail", args=[guest.id])
    response = client.post(
        reverse("guests:allocate_points", args=[guest.id]),
        {
            "guest": guest.id,
            "attribute": "force",
            "points": 1,
            "next": detail_url,
        },
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "attribute_panel_html" in payload
    assert "guest-attribute-panel" in payload["attribute_panel_html"]
    assert "guest-attribute-tooltip-trigger" in payload["attribute_panel_html"]
    assert 'class="stat-row guest-attribute-tooltip-trigger"' not in payload["attribute_panel_html"]
    assert '<strong class="guest-attribute-tooltip-trigger"' in payload["attribute_panel_html"]
    assert 'data-tooltip-text="武力：81"' in payload["attribute_panel_html"]
    assert ">武力：81</span>" not in payload["attribute_panel_html"]

    guest.refresh_from_db()
    assert guest.force == 81
    assert guest.attribute_points == 1
    assert payload["force"] == 81
    assert payload["attribute_points"] == 1


@pytest.mark.django_db
def test_allocate_points_accept_json_without_ajax_header_returns_json(client, django_user_model):
    user = django_user_model.objects.create_user(username="alloc_points_accept_json", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest_with_points(manor, "accept_json")

    client.login(username="alloc_points_accept_json", password="pass123")
    detail_url = reverse("guests:detail", args=[guest.id])
    response = client.post(
        reverse("guests:allocate_points", args=[guest.id]),
        {
            "guest": guest.id,
            "attribute": "defense",
            "points": 1,
            "next": detail_url,
        },
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["defense"] == 61
    assert payload["attribute_points"] == 1
    assert "attribute_panel_html" in payload


@pytest.mark.django_db
def test_allocate_points_non_ajax_keeps_redirect_behavior(client, django_user_model):
    user = django_user_model.objects.create_user(username="alloc_points_redirect", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest_with_points(manor, "redirect")

    client.login(username="alloc_points_redirect", password="pass123")
    detail_url = reverse("guests:detail", args=[guest.id])
    response = client.post(
        reverse("guests:allocate_points", args=[guest.id]),
        {
            "guest": guest.id,
            "attribute": "intellect",
            "points": 1,
            "next": detail_url,
        },
    )

    assert response.status_code == 302
    assert response.url == detail_url

    guest.refresh_from_db()
    assert guest.intellect == 71
    assert guest.attribute_points == 1


@pytest.mark.django_db
def test_allocate_points_ajax_returns_overflow_error_message(client, django_user_model):
    user = django_user_model.objects.create_user(username="alloc_points_overflow", password="pass123")
    manor = ensure_manor(user)
    guest = _create_guest_with_points(manor, "overflow")
    guest.force = 9999
    guest.attribute_points = 2
    guest.save(update_fields=["force", "attribute_points"])

    client.login(username="alloc_points_overflow", password="pass123")
    detail_url = reverse("guests:detail", args=[guest.id])
    response = client.post(
        reverse("guests:allocate_points", args=[guest.id]),
        {
            "guest": guest.id,
            "attribute": "force",
            "points": 1,
            "next": detail_url,
        },
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        HTTP_ACCEPT="application/json",
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"] == "属性值已达上限，无法继续加点"
