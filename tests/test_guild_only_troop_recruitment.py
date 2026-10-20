from __future__ import annotations

from copy import deepcopy

import pytest
from django.contrib.messages import get_messages
from django.db import transaction
from django.urls import reverse

from battle.models import TroopTemplate
from core.exceptions import TroopRecruitmentError
from gameplay.models import Manor, PlayerTroop, TroopRecruitment
from gameplay.services.recruitment import recruitment as recruitment_service
from gameplay.services.recruitment.templates import _normalize_recruit_config


@pytest.fixture
def guild_only_templates(monkeypatch):
    data = deepcopy(recruitment_service.load_troop_templates())
    troops = {troop["key"]: troop for troop in data["troops"]}
    troops["bloodthirsty_cavalry"]["recruit"]["guild_only"] = True
    monkeypatch.setattr(recruitment_service, "load_troop_templates", lambda: data)
    monkeypatch.setattr(recruitment_service, "get_troop_template", troops.get)
    monkeypatch.setattr(Manor, "get_building_level", lambda *_args: 1)


@pytest.mark.parametrize("flag, expected", [(True, True), (False, False), (None, False)])
def test_normalize_recruit_config_preserves_explicit_guild_only(flag, expected):
    raw = {"equipment": [], "guild_only": flag}

    assert _normalize_recruit_config(raw, troop_key="guild_guard")["guild_only"] is expected


def test_normalize_recruit_config_keeps_ordinary_troops_personal():
    assert _normalize_recruit_config({"equipment": []}, troop_key="guard")["guild_only"] is False


def test_normalize_recruit_config_keeps_guild_cavalry_salvage_duration():
    config = _normalize_recruit_config(
        {
            "guild_only": True,
            "base_duration": 0,
            "battle_salvage_base_duration": 30,
        },
        troop_key="bloodthirsty_cavalry",
    )

    assert config["base_duration"] == 0
    assert config["battle_salvage_base_duration"] == 30


@pytest.mark.django_db
def test_guild_only_troops_hidden_from_personal_options_but_inventory_preserved(manor_with_user, guild_only_templates):
    manor, _client = manor_with_user
    template, _ = TroopTemplate.objects.get_or_create(key="bloodthirsty_cavalry", defaults={"name": "嗜血铁骑"})
    stock = PlayerTroop.objects.create(manor=manor, troop_template=template, count=7)

    options = recruitment_service.get_recruitment_options(manor)
    inventory = recruitment_service.get_player_troops(manor)

    assert "bloodthirsty_cavalry" not in {option["key"] for option in options}
    assert "scout" in {option["key"] for option in options}
    assert next(row for row in inventory if row["key"] == "bloodthirsty_cavalry")["count"] == 7
    stock.refresh_from_db()
    assert stock.count == 7


@pytest.mark.django_db
def test_guild_only_requirements_reject_before_equipment_and_technology_checks(manor_with_user, guild_only_templates):
    manor, _client = manor_with_user

    result = recruitment_service.check_recruitment_requirements(manor, "bloodthirsty_cavalry")

    assert result["can_recruit"] is False
    assert result["errors"] == [recruitment_service.GUILD_ONLY_RECRUITMENT_ERROR]
    assert result["equipment_status"] == {}


@pytest.mark.django_db
@pytest.mark.parametrize("entrypoint", ["quote_troop_recruitment", "start_troop_recruitment"])
def test_personal_service_entries_reject_guild_only_troops(manor_with_user, guild_only_templates, entrypoint):
    manor, _client = manor_with_user
    retainer_count = manor.retainer_count

    with pytest.raises(TroopRecruitmentError, match="由帮会统一训练"):
        getattr(recruitment_service, entrypoint)(manor, "bloodthirsty_cavalry")

    manor.refresh_from_db()
    assert manor.retainer_count == retainer_count
    assert not TroopRecruitment.objects.filter(manor=manor).exists()


@pytest.mark.django_db
def test_synchronous_recruitment_revalidates_guild_only_restriction(manor_with_user, guild_only_templates):
    manor, _client = manor_with_user
    quote = recruitment_service.TroopRecruitmentQuote(
        manor_id=manor.pk,
        troop_key="bloodthirsty_cavalry",
        troop_name="嗜血铁骑",
        quantity=1,
        equipment_costs=(),
        equipment_stock=(),
        retainer_cost=0,
        retainer_count=manor.retainer_count,
        tech_key=None,
        tech_level_required=0,
        tech_level=0,
        max_quantity=20,
        training_ground_level=1,
        ancestral_hall_level=1,
        base_duration=300,
        actual_duration=300,
    )

    with pytest.raises(TroopRecruitmentError, match="由帮会统一训练"):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            recruitment_service.recruit_troops_locked(locked_manor, quote)

    assert not TroopRecruitment.objects.filter(manor=manor).exists()
    assert not PlayerTroop.objects.filter(manor=manor).exists()


@pytest.mark.django_db
def test_personal_recruitment_page_hides_guild_only_troops(manor_with_user, guild_only_templates):
    _manor, client = manor_with_user

    response = client.get(reverse("gameplay:troop_recruitment"))

    assert response.status_code == 200
    assert "bloodthirsty_cavalry" not in {row["key"] for row in response.context["recruitment_options"]}
    assert "bloodthirsty_cavalry" not in response.content.decode()


@pytest.mark.django_db
def test_direct_personal_recruitment_post_rejects_guild_only_troops(manor_with_user, guild_only_templates):
    manor, client = manor_with_user

    response = client.post(
        reverse("gameplay:start_troop_recruitment"), {"troop_key": "bloodthirsty_cavalry", "quantity": "1"}
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:troop_recruitment")
    assert recruitment_service.GUILD_ONLY_RECRUITMENT_ERROR in [
        str(message) for message in get_messages(response.wsgi_request)
    ]
    assert not TroopRecruitment.objects.filter(manor=manor).exists()
