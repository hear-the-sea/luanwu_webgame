from datetime import date, datetime

import pytest
from django.template import engines
from django.urls import reverse
from django.utils import timezone

from gameplay.models import Manor
from guests.services.custom_guest_biography import build_custom_guest_biography


def test_biography_tracks_manor_rename_without_changing_arrival_date():
    manor = Manor(name="春秋山庄")
    arrived_on = date(2025, 3, 8)
    original = build_custom_guest_biography(manor, arrived_on)
    assert "春秋山庄的庄主，于2025年3月8日来到此庄园" in original
    manor.name = "天下第一庄"
    updated = build_custom_guest_biography(manor, arrived_on)
    assert "天下第一庄的庄主，于2025年3月8日来到此庄园" in updated
    assert "春秋山庄" not in updated
    assert "吃装不吐铁，强化不信邪" in updated


def test_biography_manor_name_is_escaped_in_html():
    text = build_custom_guest_biography(Manor(name="<script>庄园</script>"), date(2025, 3, 8))
    template = engines["django"].from_string("{{ biography|linebreaksbr }}")
    html = template.render({"biography": text})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.django_db
def test_detail_biography_reads_current_manor_name_and_original_creation_date(client, django_user_model):
    from gameplay.services.manor.core import ensure_manor
    from guests.avatar import get_guest_disguise_avatar_filenames
    from guests.services.custom_guest import create_custom_guest, ensure_custom_guest_card

    user = django_user_model.objects.create_user(username="custom_biography_owner")
    manor = ensure_manor(user)
    created_at = timezone.make_aware(datetime(2020, 5, 6, 23, 30))
    Manor.objects.filter(pk=manor.pk).update(name="最初庄园", created_at=created_at)
    manor.refresh_from_db()
    ensure_custom_guest_card(manor)
    card = manor.inventory_items.get(template__key="custom_guest_card")
    result = create_custom_guest(manor, card.pk, "庄主真身", get_guest_disguise_avatar_filenames()[0])
    client.force_login(user)
    url = reverse("guests:detail", args=[result["guest_id"]])
    response = client.get(url)
    assert response.status_code == 200
    assert "最初庄园的庄主，于2020年5月6日来到此庄园" in response.content.decode()
    Manor.objects.filter(pk=manor.pk).update(name="改名后庄园")
    response = client.get(url)
    assert "改名后庄园的庄主，于2020年5月6日来到此庄园" in response.content.decode()
    assert "最初庄园的庄主" not in response.content.decode()
